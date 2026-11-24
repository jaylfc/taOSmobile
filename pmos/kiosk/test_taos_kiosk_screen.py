"""taos-kiosk-screen powers the panel off only once the glass is black.

Nothing paints while the output is off, so the next wake shows whatever frame
the panel held when the power went. A fixed sleep cut the page's closing
animation short and that half-faded frame flashed on every wake.

Run: python3 -m pytest -q pmos/kiosk/test_taos_kiosk_screen.py
"""
import os
import pathlib
import subprocess
import time

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "bin" / "taos-kiosk-screen"

BRIGHT = b"P6\n8 4\n255\n" + b"\x80" * 96
BLACK = b"P6\n8 4\n255\n" + b"\x00" * 96


# The strip with ONE white row left in it: the line the closing animation ends on.
ONE_ROW = b"P6\n8 4\n255\n" + b"\x00" * 24 + b"\xff" * 24 + b"\x00" * 48


def run(tmp_path, bright_for_s, cap_ms=None, grim_ok=True, lit=True, grim_hangs=False, bright=BRIGHT,
        query_fails=False, wake_at_s=None, wake_in_gap=False, bright_looks=None, framelog=None):
    """Stub grim shows a bright screen for `bright_for_s` seconds, then black.
    Returns (ordered log lines, stderr, seconds taken)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "log"
    log.write_text("")
    start = tmp_path / "start"
    (tmp_path / "bright.ppm").write_bytes(bright)
    (tmp_path / "black.ppm").write_bytes(BLACK)

    # Real commands for stubs that need to delegate.
    real_date = subprocess.run(["sh", "-c", "command -v date"], capture_output=True, text=True).stdout.strip()
    real_sleep = subprocess.run(["sh", "-c", "command -v sleep"], capture_output=True, text=True).stdout.strip()

    # Fake time infrastructure: all stubs use a controlled clock so tests are
    # deterministic and fast. The clock advances only when the stub `sleep`
    # is called, by the requested sleep duration.
    FAKE_TIME_BASE = 1000000000000000000
    fake_time_ns = tmp_path / "fake_time_ns"
    fake_time_ns.write_text(str(FAKE_TIME_BASE))
    start.write_text(str(FAKE_TIME_BASE))

    # date stub: returns fake time for +%s%N and +%s, delegates otherwise.
    (bindir / "date").write_text(
        '#!/bin/sh\n'
        'case "$1" in\n'
        '  +%%s%%N) cat "%s" ;;\n'
        '  +%%s) awk \'{printf "%%d", $1/1e9}\' "%s" ;;\n'
        '  *) exec %s "$@" ;;\n'
        'esac\n' % (fake_time_ns, fake_time_ns, real_date))
    os.chmod(bindir / "date", 0o755)

    if grim_ok and bright_looks is not None:
        # Bright for a fixed NUMBER of looks, then black: independent of how
        # long the script takes to start on a slow machine.
        (bindir / "grim").write_text(
            '#!/bin/sh\n'
            ': > "%s/probed"\n'
            'n=$(( $(cat "%s/looks" 2>/dev/null || echo 0) + 1 )); echo $n > "%s/looks"\n'
            'if [ $n -le %d ]; then echo "grim bright" >> "%s"; cat "%s/bright.ppm"\n'
            'else echo "grim black" >> "%s"; cat "%s/black.ppm"; fi\n'
            % (tmp_path, tmp_path, tmp_path, bright_looks, log, tmp_path, log, tmp_path))
    elif grim_ok:
        (bindir / "grim").write_text(
            '#!/bin/sh\n'
            ': > "%s/probed"\n'
            'now=$(date +%%s%%N); t0=$(cat "%s")\n'
            'if [ $(( (now - t0) / 1000000 )) -lt %d ]; then echo "grim bright" >> "%s"; cat "%s/bright.ppm"\n'
            'else echo "grim black" >> "%s"; cat "%s/black.ppm"; fi\n'
            % (tmp_path, start, int(bright_for_s * 1000), log, tmp_path, log, tmp_path))
    else:
        (bindir / "grim").write_text('#!/bin/sh\necho "grim failed" >> "%s"\nexit 1\n' % log)
    power = tmp_path / "power"
    power.write_text("true" if lit else "false")
    # get_outputs answers from a state file that `output * power off` flips,
    # so the script sees the panel the way sway would report it.
    (bindir / "swaymsg").write_text(
        '#!/bin/sh\n'
        'case "$*" in\n'
        '  *get_outputs*) printf \'[{"name":"DSI-1","power": %%s}]\\n\' "$(cat "%s")"; exit 0 ;;\n'
        '  *"power off"*) echo false > "%s"\n'
        # A wake that stamped just after the off's last check and lit the
        # panel just before this power-off: the gap no check before it closes.
        '     [ -e "%s/wake_in_gap" ] && echo gap-wake > "%s/taos-screen.wake" ;;\n'
        'esac\n'
        'echo "swaymsg $*" >> "%s"\n' % (power, power, tmp_path, tmp_path, log))
    if grim_hangs:
        # What grim really does on a dark output: never returns. Advance fake
        # time by 1s (the timeout duration) and exit as if killed by timeout,
        # so the script's `timeout 1 grim` sees immediate failure and the cap
        # check (500ms fake time) is exceeded on the first iteration.
        (bindir / "grim").write_text(
            '#!/bin/sh\n'
            'echo "grim hung" >> "%s"\n'
            'cur=$(cat "%s")\n'
            'echo $((cur + 1000000000)) > "%s"\n'
            'exit 143\n' % (log, fake_time_ns, fake_time_ns))
    (bindir / "curl").write_text('#!/bin/sh\necho "curl $*" >> "%s"\n' % log)
    # A sleep that is logged and advances fake time, so the order of looks and
    # waits can be read and the clock is controlled. Use flock on a lock file
    # to serialize access to fake_time_ns since multiple processes (main script,
    # framelog) may call sleep concurrently.
    (bindir / "sleep").write_text(
        '#!/bin/sh\n'
        'echo "sleep $*" >> "%s"\n'
        'ns=$(awk -v s="$1" \'BEGIN {printf "%%d", s * 1e9}\')\n'
        'lock="%s.lock"\n'
        'exec 9>"$lock"\n'
        'flock 9\n'
        'cur=$(cat "%s")\n'
        'echo $((cur + ns)) > "%s"\n'
        'flock -u 9\n'
        '# Do not actually sleep; fake time advances instantly.\n'
        % (log, fake_time_ns, fake_time_ns, fake_time_ns))
    for n in ("grim", "swaymsg", "curl", "sleep", "date"):
        os.chmod(bindir / n, 0o755)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), XDG_RUNTIME_DIR=str(tmp_path))
    # No frame logger unless a test asks for one: the grim path stays what the
    # older tests measure, whatever is installed on the machine running them.
    env["TAOS_FRAMELOG"] = str(tmp_path / "no-framelog")
    if framelog is not None:
        fl = bindir / "taos-framelog"
        if framelog == "ok":
            # Watches every frame: exits 0 the moment the stub screen is black,
            # 1 when its --duration runs out first.
            body = ('dur=$(echo "$*" | sed -n "s/.*--duration \\([0-9.]*\\).*/\\1/p")\n'
                    'echo "framelog $*" >> "%s"\n'
                    ': > "%s/probed"\n'
                    'end=$(awk -v d="$dur" -v n="$(date +%%s%%N)" \'BEGIN{printf "%%.0f", n + d*1e9}\')\n'
                    'while :; do now=$(date +%%s%%N); t0=$(cat "%s")\n'
                    '  [ $(( (now - t0) / 1000000 )) -ge %d ] && { echo "framelog black" >> "%s"; exit 0; }\n'
                    '  [ "$now" -ge "$end" ] && { echo "framelog timeout" >> "%s"; exit 1; }\n'
                    '  sleep 0.008; done\n'
                    % (log, tmp_path, start, int(bright_for_s * 1000), log, log))
        elif framelog == "broken":
            body = 'echo "framelog broken" >> "%s"\nexit 1\n' % log
        elif framelog == "dark":
            body = 'echo "framelog no frame" >> "%s"\nexit 3\n' % log
        fl.write_text("#!/bin/sh\n" + body)
        os.chmod(fl, 0o755)
        env["TAOS_FRAMELOG"] = str(fl)
    if query_fails:
        (bindir / "swaymsg").write_text(
            '#!/bin/sh\ncase "$*" in *get_outputs*) exit 1 ;; esac\necho "swaymsg $*" >> "%s"\n' % log)
        os.chmod(bindir / "swaymsg", 0o755)
    if cap_ms is not None:
        env["TAOS_BLACK_WAIT_MS"] = str(cap_ms)
    if wake_in_gap:
        (tmp_path / "wake_in_gap").write_text("")
    t0 = time.time()
    if wake_at_s is None:
        r = subprocess.run(["sh", str(SCRIPT), "off"], env=env, capture_output=True, text=True, timeout=15)
        rc, err = r.returncode, r.stderr
    else:
        p = subprocess.Popen(["sh", str(SCRIPT), "off"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        # Not a sleep: the wake goes in only once the off is provably inside
        # its wait (it has looked at the glass), however slowly it started.
        deadline = time.time() + 10
        while not (tmp_path / "probed").exists():
            assert time.time() < deadline, "the off never probed the glass"
            time.sleep(0.01)
        w = subprocess.run(["sh", str(SCRIPT), "on"], env=env, capture_output=True, text=True, timeout=15)
        assert w.returncode == 0, w.stderr
        _, err = p.communicate(timeout=15)
        rc = p.returncode
    assert rc == 0, err
    fake_elapsed_ns = int(fake_time_ns.read_text().strip()) - FAKE_TIME_BASE
    took = fake_elapsed_ns / 1e9
    return log.read_text().splitlines(), err, took


def run_on(tmp_path):
    """One wake, with a `date` that has no sub-second precision at all."""
    bindir = tmp_path / "onbin"
    bindir.mkdir(exist_ok=True)
    for n in ("swaymsg", "curl"):
        (bindir / n).write_text("#!/bin/sh\nexit 0\n")
        os.chmod(bindir / n, 0o755)
    (bindir / "date").write_text("#!/bin/sh\necho 1700000000\n")
    os.chmod(bindir / "date", 0o755)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), XDG_RUNTIME_DIR=str(tmp_path))
    r = subprocess.run(["sh", str(SCRIPT), "on"], env=env, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr


def power_off_index(lines):
    hits = [i for i, l in enumerate(lines) if "power off" in l]
    assert len(hits) == 1, lines
    return hits[0]


def test_power_goes_off_only_after_the_screen_is_black(tmp_path):
    # Black arrives at 1.0 s: later than the old fixed 0.55 s sleep.
    lines, err, took = run(tmp_path, bright_for_s=1.0)
    i = power_off_index(lines)
    before = lines[:i]
    probes = [l for l in before if l.startswith("grim")]
    assert probes[-1] == "grim black", before[-4:]
    assert probes.count("grim black") == 1   # it stopped looking at the first black
    assert "grim bright" in before          # it did look while the screen was lit
    assert any(l.startswith("curl") and "lock-screen-off" in l for l in before)
    assert took >= 1.0 and err == ""
    # Touch is gated before the power goes, and after the wait.
    gate = [k for k, l in enumerate(lines) if "events disabled" in l]
    assert gate and before.index("grim black") < gate[0] < i


def test_an_already_black_screen_does_not_wait_for_the_cap(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=0.0)
    power_off_index(lines)
    assert took < 1.0 and err == ""


def test_a_screen_that_never_blackens_still_powers_off_and_says_so(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=60, cap_ms=600)
    power_off_index(lines)
    assert "grim black" not in lines
    assert "not black" in err and "powering off anyway" in err
    assert 0.6 <= took < 3.0
    # Control: the same cap with a screen that does go black says nothing.
    lines, err, took = run(tmp_path, bright_for_s=0.2, cap_ms=600)
    power_off_index(lines)
    assert err == ""


def test_a_failing_probe_cannot_stop_the_screen_going_off(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=0, cap_ms=500, grim_ok=False)
    power_off_index(lines)
    assert "powering off anyway" in err and took < 3.0


def test_a_panel_that_is_already_off_is_not_probed(tmp_path):
    """The idle timer and the power key both ask for off on a dark panel. grim
    blocks forever there; the stuck request then finished the moment the panel
    next came on and switched it off again, so the phone could not be woken."""
    lines, err, took = run(tmp_path, bright_for_s=0, lit=False, grim_hangs=True)
    power_off_index(lines)                       # still idempotently off
    assert not any(l.startswith("grim") for l in lines), lines
    assert not any(l.startswith("curl") for l in lines), lines
    assert took < 1.0 and err == ""
    # Control: the same script on a lit panel does probe.
    lines, err, took = run(tmp_path, bright_for_s=0.0)
    assert any(l.startswith("grim") for l in lines)


def test_a_probe_that_never_returns_is_cut_off(tmp_path):
    """Belt and braces for the same failure: even on a lit panel a grim that
    hangs must cost one second a look, not the screen-off."""
    lines, err, took = run(tmp_path, bright_for_s=0, cap_ms=500, grim_hangs=True)
    power_off_index(lines)
    assert "grim hung" in lines
    assert "powering off anyway" in err
    assert took < 4.0


def test_one_remaining_line_is_not_black(tmp_path):
    """Counting the header with an allowance let a single white row through:
    the screen went off with the animation's last line still on it."""
    lines, err, took = run(tmp_path, bright_for_s=0.9, bright=ONE_ROW)
    i = power_off_index(lines)
    probes = [l for l in lines[:i] if l.startswith("grim")]
    assert "grim bright" in probes and probes[-1] == "grim black", probes
    assert took >= 0.9 and err == ""
    # Control: the same run with a truly black strip from the start is quick.
    lines, err, took = run(tmp_path, bright_for_s=0.0, bright=ONE_ROW)
    assert took < 1.0


def test_a_wake_during_the_wait_is_not_undone(tmp_path):
    """A double tap just after the screen began to go off: the off was still
    waiting for black, then finished and switched the panel off under the wake."""
    lines, err, took = run(tmp_path, bright_for_s=1.5, wake_at_s=True)
    assert not any("power off" in l for l in lines), lines
    assert any("power on" in l for l in lines)
    assert "a wake arrived" in err
    # Touch must not be left disabled by the abandoned off.
    last_touch = [l for l in lines if "events" in l][-1]
    assert "enabled" in last_touch, lines
    # Control: with no wake the same off does power the panel down.
    lines, err, took = run(tmp_path, bright_for_s=0.4)
    power_off_index(lines)


def test_a_failed_status_query_is_not_taken_for_a_dark_panel(tmp_path):
    """If sway cannot be asked, the panel may well be lit: still tell the page
    and still wait, as a lit panel would."""
    lines, err, took = run(tmp_path, bright_for_s=0.5, query_fails=True)
    power_off_index(lines)
    assert any(l.startswith("curl") and "lock-screen-off" in l for l in lines), lines
    probes = [l for l in lines if l.startswith("grim")]
    assert probes and probes[-1] == "grim black", lines


def test_a_wake_between_the_last_check_and_the_power_off_is_put_back(tmp_path):
    """The off has decided to go ahead; a wake stamps and lights the panel; the
    off then powers it down. It must notice and restore the panel and touch."""
    lines, err, took = run(tmp_path, bright_for_s=0.3, wake_in_gap=True)
    i = power_off_index(lines)
    after = lines[i + 1:]
    assert any("power on" in l for l in after), lines
    assert [l for l in lines if "events" in l][-1].endswith("enabled"), lines
    assert "powering it back on" in err
    # Control: no wake, and the panel stays off with touch disabled.
    lines, err, took = run(tmp_path, bright_for_s=0.3)
    i = power_off_index(lines)
    assert not any("power on" in l for l in lines[i:]), lines
    assert [l for l in lines if "events" in l][-1].endswith("disabled"), lines


def test_two_wakes_in_the_same_second_leave_different_stamps(tmp_path):
    seen = set()
    for _ in range(3):
        run_on(tmp_path)
        seen.add((tmp_path / "taos-screen.wake").read_text())
    assert len(seen) == 3, seen


def test_an_already_black_screen_is_looked_at_before_any_wait(tmp_path):
    """The wait used to sleep 150 ms before its first look, on every screen-off."""
    lines, err, took = run(tmp_path, bright_for_s=0.0)
    i = power_off_index(lines)
    before = lines[:i]
    assert "grim black" in before
    assert not any(l.startswith("sleep") for l in before), before
    # Control: a lit screen does wait between looks. Bright for two looks by
    # count, not by time, so a slow start cannot turn it black early.
    (tmp_path / "looks").unlink(missing_ok=True)
    lines, err, took = run(tmp_path, bright_for_s=0, bright_looks=2)
    i = power_off_index(lines)
    first_look = next(n for n, l in enumerate(lines) if l.startswith("grim"))
    assert any(l.startswith("sleep") for l in lines[first_look:i]), lines
    assert not any(l.startswith("sleep") for l in lines[:first_look]), lines


def test_with_the_frame_logger_black_is_seen_on_the_frame_it_happens(tmp_path):
    """No grim look at all: the logger watches every frame and exits on the first black one."""
    lines, err, took = run(tmp_path, bright_for_s=0.5, framelog="ok")
    i = power_off_index(lines)
    before = lines[:i]
    assert "framelog black" in before, lines
    assert not any(l.startswith("grim") for l in lines), lines
    assert 0.5 <= took < 1.0, took
    assert err == ""
    flags = next(l for l in lines if l.startswith("framelog --"))
    assert "--until-black" in flags and "--region 268,0,4x1200" in flags and "--duration 2.000" in flags


def test_a_frame_logger_that_cannot_answer_hands_over_to_grim(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=0.3, framelog="broken")
    i = power_off_index(lines)
    assert "framelog broken" in lines
    assert "grim black" in lines[:i], lines
    assert took < 1.5


def test_a_frame_logger_that_sees_no_frame_means_the_panel_is_dark(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=5, framelog="dark")
    power_off_index(lines)
    assert not any(l.startswith("grim") for l in lines), lines
    assert took < 1.0


def test_the_frame_logger_wait_is_capped_once(tmp_path):
    """Out of time is the cap, not a failure: no second wait with grim after it."""
    lines, err, took = run(tmp_path, bright_for_s=10, cap_ms=600, framelog="ok")
    power_off_index(lines)
    assert "framelog timeout" in lines
    assert not any(l.startswith("grim") for l in lines), lines
    assert "powering off anyway" in err
    assert took < 1.6, took


def test_a_wake_during_the_frame_logger_wait_is_not_undone(tmp_path):
    lines, err, took = run(tmp_path, bright_for_s=3, wake_at_s=True, framelog="ok")
    assert not any("power off" in l for l in lines), lines
    assert "a wake arrived" in err
    # The wake ends the wait at once, not when the 2 s cap runs out.
    assert took < 1.2, took


def test_a_logger_that_fails_at_once_is_not_taken_for_the_cap_even_with_a_small_cap(tmp_path):
    """With a 100 ms cap a slack of 100 ms read an instant failure as 'ran out
    of time' and powered off a lit screen without looking at it."""
    lines, err, took = run(tmp_path, bright_for_s=0.05, cap_ms=100, framelog="broken")
    i = power_off_index(lines)
    assert "framelog broken" in lines
    assert any(l.startswith("grim") for l in lines[:i]), lines
