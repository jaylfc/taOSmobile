"""taos-kiosk-idle takes the screen timeout from one file, parsed and bounded.

Run: python3 -m pytest -q pmos/kiosk/test_taos_kiosk_idle.py
"""
import os
import pathlib
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "bin" / "taos-kiosk-idle"


def run(tmp_path, conf_text=None, env_secs=None):
    """Run the script with stub swayidle/pkill; return (swayidle argv, stderr)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    out = tmp_path / "swayidle.args"
    out.unlink(missing_ok=True)
    (bindir / "swayidle").write_text('#!/bin/sh\nprintf "%%s\\n" "$@" > "%s"\n' % out)
    (bindir / "pkill").write_text("#!/bin/sh\nexit 0\n")
    for n in ("swayidle", "pkill"):
        os.chmod(bindir / n, 0o755)
    conf = tmp_path / "display.conf"
    if conf_text is None:
        conf.unlink(missing_ok=True)
    else:
        conf.write_text(conf_text)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), TAOS_DISPLAY_CONF=str(conf))
    env.pop("TAOS_KIOSK_IDLE_SECS", None)
    if env_secs is not None:
        env["TAOS_KIOSK_IDLE_SECS"] = env_secs
    r = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return out.read_text().splitlines(), r.stderr


def timeout_of(args):
    return args[args.index("timeout") + 1] if "timeout" in args else None


def test_the_file_sets_the_timeout_and_beats_the_environment(tmp_path):
    args, err = run(tmp_path, "SCREEN_OFF_SECONDS=60\n", env_secs="30")
    assert timeout_of(args) == "60" and err == ""
    # Control: another value gives another timeout, so 60 is read, not built in.
    args, _ = run(tmp_path, "# comment\nSCREEN_OFF_SECONDS=120\n")
    assert timeout_of(args) == "120"


def test_without_the_file_the_launcher_value_then_30_applies(tmp_path):
    assert timeout_of(run(tmp_path, None, env_secs="45")[0]) == "45"
    assert timeout_of(run(tmp_path, None)[0]) == "30"


def test_zero_means_never_but_still_blanks_before_sleep(tmp_path):
    args, _ = run(tmp_path, "SCREEN_OFF_SECONDS=0\n")
    assert "timeout" not in args
    assert "before-sleep" in args


@pytest.mark.parametrize("text", [
    "SCREEN_OFF_SECONDS=2\n",          # too short to reach the setting again
    "SCREEN_OFF_SECONDS=99999\n",
    "SCREEN_OFF_SECONDS=99999999999999999999\n",   # too large for shell arithmetic
    "SCREEN_OFF_SECONDS=00000000000000000060\n",   # long, though numerically in range: refused, not parsed
    "SCREEN_OFF_SECONDS=60; touch /tmp/x\n",
    "SCREEN_OFF_SECONDS=$(id)\n",
    "SCREEN_OFF_SECONDS=-5\n",
    "garbage\n",
])
def test_a_bad_value_falls_back_to_30_and_says_so(tmp_path, text):
    args, err = run(tmp_path, text)
    assert timeout_of(args) == "30"
    assert "taos-kiosk-idle:" in err


def test_a_malformed_file_means_30_not_the_launcher_value(tmp_path):
    # The launcher value 0 would switch the timeout off; a broken file must not.
    args, err = run(tmp_path, "garbage\n", env_secs="0")
    assert timeout_of(args) == "30" and "using 30" in err
    args, _ = run(tmp_path, "garbage\n", env_secs="45")
    assert timeout_of(args) == "30"
    # Control: with no file at all the launcher value does apply.
    assert "timeout" not in run(tmp_path, None, env_secs="0")[0]


def test_installer_range_check_survives_a_number_too_large_for_the_shell(tmp_path):
    """`[ "$V" -ne 0 ]` errors on an integer the shell cannot hold, and the error
    used to skip the range test, so the installer reported the value as fine."""
    text = (HERE / "install-display.sh").read_text()
    start = text.index("# Length first")
    end = text.index("fi\n", start) + 3
    snippet = text[start:end]

    def verdict(v):
        r = subprocess.run(["sh", "-c", 'V="$1"; ' + snippet + "\necho ACCEPTED", "x", v],
                           capture_output=True, text=True)
        return r.returncode, r.stdout

    for bad in ("99999999999999999999", "99999", "4", "3601"):
        rc, out = verdict(bad)
        assert rc == 1 and "FAIL" in out and "ACCEPTED" not in out, (bad, rc, out)
    for good in ("0", "5", "60", "3600"):
        rc, out = verdict(good)
        assert rc == 0 and out.strip() == "ACCEPTED", (good, rc, out)


def test_installer_validates_the_same_range_as_the_idle_script():
    text = (HERE / "install-display.sh").read_text()
    idle = SCRIPT.read_text()
    for needle in ('-lt 5', '-gt 3600'):
        assert needle in text and needle in idle


def test_the_file_is_parsed_never_executed(tmp_path):
    marker = tmp_path / "ran"
    run(tmp_path, 'SCREEN_OFF_SECONDS=60\ntouch "%s"\n' % marker)
    assert not marker.exists()


def test_the_shipped_default_is_one_minute():
    text = (HERE / "etc" / "display.conf").read_text()
    assert "SCREEN_OFF_SECONDS=60\n" in text
