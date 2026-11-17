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


def run(tmp_path, bright_for_s, cap_ms=None, grim_ok=True, lit=True, grim_hangs=False, bright=BRIGHT):
    """Stub grim shows a bright screen for `bright_for_s` seconds, then black.
    Returns (ordered log lines, stderr, seconds taken)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "log"
    log.write_text("")
    start = tmp_path / "start"
    (tmp_path / "bright.ppm").write_bytes(bright)
    (tmp_path / "black.ppm").write_bytes(BLACK)
    if grim_ok:
        (bindir / "grim").write_text(
            '#!/bin/sh\n'
            'now=$(date +%%s%%N); t0=$(cat "%s")\n'
            'if [ $(( (now - t0) / 1000000 )) -lt %d ]; then echo "grim bright" >> "%s"; cat "%s/bright.ppm"\n'
            'else echo "grim black" >> "%s"; cat "%s/black.ppm"; fi\n'
            % (start, int(bright_for_s * 1000), log, tmp_path, log, tmp_path))
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
        '  *"power off"*) echo false > "%s" ;;\n'
        'esac\n'
        'echo "swaymsg $*" >> "%s"\n' % (power, power, log))
    if grim_hangs:
        # What grim really does on a dark output: never returns.
        (bindir / "grim").write_text('#!/bin/sh\necho "grim hung" >> "%s"\nsleep 60\n' % log)
    (bindir / "curl").write_text('#!/bin/sh\necho "curl $*" >> "%s"\n' % log)
    for n in ("grim", "swaymsg", "curl"):
        os.chmod(bindir / n, 0o755)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]))
    if cap_ms is not None:
        env["TAOS_BLACK_WAIT_MS"] = str(cap_ms)
    start.write_text(str(time.time_ns()))
    t0 = time.time()
    r = subprocess.run(["sh", str(SCRIPT), "off"], env=env, capture_output=True, text=True, timeout=15)
    took = time.time() - t0
    assert r.returncode == 0, r.stderr
    return log.read_text().splitlines(), r.stderr, took


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
