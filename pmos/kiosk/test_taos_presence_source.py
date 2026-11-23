"""The screen and charger helpers publish screen and power state for presence beats.

The controller watches $XDG_RUNTIME_DIR/taos-presence with inotify and beats on
each change; see docs/presence-source.md for the contract.

Run: python3 -m pytest -q -p no:cacheprovider pmos/kiosk/test_taos_presence_source.py
"""
import os
import pathlib
import subprocess
import time

import pytest

from test_taos_kiosk_screen import run as screen_off, run_on as screen_on

HERE = pathlib.Path(__file__).resolve().parent
SCREEN = HERE / "bin" / "taos-kiosk-screen"
CHARGER = HERE / "bin" / "taos-kiosk-charger"
LIB = HERE / "bin" / "taos-presence-lib.sh"


def presence(tmp_path, name):
    p = tmp_path / "taos-presence" / name
    return p.read_text() if p.exists() else None


# ---- screen -----------------------------------------------------------------

def test_screen_on_writes_on(tmp_path):
    screen_on(tmp_path)
    assert presence(tmp_path, "screen") == "on\n"


def test_screen_off_writes_off_once_the_panel_is_down(tmp_path):
    lines, err, took = screen_off(tmp_path, bright_for_s=0.2)
    assert any("power off" in l for l in lines), lines
    assert presence(tmp_path, "screen") == "off\n"


def test_an_off_that_stands_down_for_a_wake_does_not_write_off(tmp_path):
    # Wake lands while the off waits for black: the wake's own `on` writes on.
    lines, err, took = screen_off(tmp_path, bright_for_s=1.5, wake_at_s=True)
    assert not any("power off" in l for l in lines), lines
    assert presence(tmp_path, "screen") == "on\n"
    # Wake lands after the power-off: the off puts the panel back and stands
    # down. Nothing ran `on`, so nothing may say anything: above all not "off".
    d = tmp_path / "gap"
    d.mkdir()
    lines, err, took = screen_off(d, bright_for_s=0.2, wake_in_gap=True)
    assert "powering it back on" in err
    assert presence(d, "screen") is None
    # Control: the same off without a wake does write off.
    c = tmp_path / "ctl"
    c.mkdir()
    screen_off(c, bright_for_s=0.2)
    assert presence(c, "screen") == "off\n"


def test_writes_are_atomic_and_skip_identical_values(tmp_path):
    screen_on(tmp_path)
    f = tmp_path / "taos-presence" / "screen"
    assert f.read_text() == "on\n"
    ino, mt = f.stat().st_ino, f.stat().st_mtime_ns
    screen_on(tmp_path)
    assert (f.stat().st_ino, f.stat().st_mtime_ns) == (ino, mt)
    assert sorted(p.name for p in f.parent.iterdir()) == ["screen"]
    assert oct(f.parent.stat().st_mode & 0o777) == "0o700"


def stub_env(tmp_path, swaymsg_body="exit 0"):
    bindir = tmp_path / "b"
    bindir.mkdir(exist_ok=True)
    (bindir / "swaymsg").write_text("#!/bin/sh\n%s\n" % swaymsg_body)
    (bindir / "curl").write_text("#!/bin/sh\nexit 0\n")
    for n in ("swaymsg", "curl"):
        os.chmod(bindir / n, 0o755)
    return dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), XDG_RUNTIME_DIR=str(tmp_path))


def test_a_blocked_presence_dir_never_fails_the_screen_helper(tmp_path):
    (tmp_path / "taos-presence").write_text("a file where the directory should be")
    screen_on(tmp_path)  # asserts rc == 0
    lines, err, took = screen_off(tmp_path, bright_for_s=0.2)
    assert any("power off" in l for l in lines), lines
    # And a runtime dir that does not exist at all.
    env = stub_env(tmp_path)
    env["XDG_RUNTIME_DIR"] = str(tmp_path / "nope" / "deeper")
    for arg in ("on", "sync"):
        r = subprocess.run(["sh", str(SCREEN), arg], env=env, capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, (arg, r.stderr)


# ---- sync -------------------------------------------------------------------

def run_sync(tmp_path, outputs):
    log = tmp_path / "sync.log"
    env = stub_env(tmp_path,
                   'case "$*" in *get_outputs*) printf \'%s\\n\' \'' + outputs + '\' ;;\n'
                   '*) echo "swaymsg $*" >> "' + str(log) + '" ;; esac')
    r = subprocess.run(["sh", str(SCREEN), "sync"], env=env, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    return log.read_text() if log.exists() else ""


def test_sync_writes_the_real_panel_state_and_changes_nothing_else(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    assert run_sync(a, '[{"name":"DSI-1","power": true}]') == ""
    assert presence(a, "screen") == "on\n"
    b = tmp_path / "b2"
    b.mkdir()
    assert run_sync(b, '[{"name":"DSI-1","power": false}]') == ""
    assert presence(b, "screen") == "off\n"
    c = tmp_path / "c"
    c.mkdir()
    assert run_sync(c, '[{"name":"DSI-1","dpms": false}]') == ""
    assert presence(c, "screen") == "off\n"
    # An answer that cannot be read is unknown: nothing is written.
    d = tmp_path / "d"
    d.mkdir()
    run_sync(d, 'garbage')
    assert presence(d, "screen") is None


# ---- the shared lib ---------------------------------------------------------

def test_the_lib_can_be_sourced_without_side_effects(tmp_path):
    assert LIB.exists()
    env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path))
    r = subprocess.run(["sh", "-c", '. "%s"; type presence_write >/dev/null' % LIB], env=env,
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_the_installer_installs_the_lib_and_the_sway_config_syncs():
    inst = (HERE / "install-kiosk-continuity.sh").read_text()
    assert "taos-presence-lib.sh" in inst
    assert "taos-kiosk-screen sync" in (HERE / "etc" / "sway-kiosk.conf").read_text()


# ---- charger ----------------------------------------------------------------

class Charger:
    def __init__(self, tmp_path, usb_online):
        self.t = tmp_path
        self.sup = tmp_path / "ps"
        for n, typ, on in (("bat", "Battery", None), ("usb", "USB", usb_online), ("wls", "Wireless", "0")):
            d = self.sup / n
            d.mkdir(parents=True)
            (d / "type").write_text(typ + "\n")
            if on is not None:
                (d / "online").write_text(on + "\n")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        real = subprocess.run(["sh", "-c", "command -v sleep"], capture_output=True, text=True).stdout.strip()
        (bindir / "sleep").write_text('#!/bin/sh\nexec %s 0.05\n' % real)
        (bindir / "play").write_text('#!/bin/sh\necho played >> "%s/played"\n' % tmp_path)
        for n in ("sleep", "play"):
            os.chmod(bindir / n, 0o755)
        self.env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
                        XDG_RUNTIME_DIR=str(tmp_path), TAOS_POWER_SUPPLY_DIR=str(self.sup),
                        TAOS_CHARGE_PLAY=str(bindir / "play"))

    def start(self):
        self.p = subprocess.Popen(["sh", str(CHARGER)], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)

    def set_usb(self, v):
        (self.sup / "usb" / "online").write_text(v + "\n")

    def wait_for(self, want):
        end = time.time() + 10
        while time.time() < end:
            if presence(self.t, "power") == want:
                return
            time.sleep(0.02)
        raise AssertionError("power never became %r, is %r" % (want, presence(self.t, "power")))

    def stop(self):
        self.p.kill()
        self.p.communicate()


@pytest.mark.parametrize("start_online,baseline,flip,after", [("0", "0\n", "1", "1\n"), ("1", "1\n", "0", "0\n")])
def test_charger_writes_the_baseline_and_both_edges(tmp_path, start_online, baseline, flip, after):
    c = Charger(tmp_path, start_online)
    c.start()
    try:
        c.wait_for(baseline)
        # The baseline is not a plug: nothing animates at startup.
        time.sleep(0.3)
        assert not (tmp_path / "played").exists()
        c.set_usb(flip)
        c.wait_for(after)
        c.set_usb(start_online)
        c.wait_for(baseline)
    finally:
        c.stop()


def test_charger_battery_supply_does_not_count_as_power(tmp_path):
    c = Charger(tmp_path, "0")
    (c.sup / "bat" / "online").write_text("1\n")
    c.start()
    try:
        c.wait_for("0\n")
    finally:
        c.stop()


def test_a_blocked_presence_dir_never_stops_the_charger(tmp_path):
    (tmp_path / "taos-presence").write_text("a file where the directory should be")
    c = Charger(tmp_path, "0")
    c.start()
    try:
        time.sleep(0.3)
        c.set_usb("1")
        end = time.time() + 10
        while not (tmp_path / "played").exists():
            assert time.time() < end, "the plug edge never reached the animation"
            assert c.p.poll() is None
            time.sleep(0.02)
    finally:
        c.stop()


def test_a_failed_power_on_is_not_published_as_on(tmp_path):
    # swaymsg fails only the power-on itself; everything else succeeds.
    env = stub_env(tmp_path, 'case "$*" in *"power on"*) exit 1 ;; esac\nexit 0')
    r = subprocess.run(["sh", str(SCREEN), "on"], env=env, capture_output=True, text=True, timeout=20)
    assert presence(tmp_path, "screen") is None, r.stderr
    # Control: the same wake with a working power-on publishes "on".
    env = stub_env(tmp_path, "exit 0")
    subprocess.run(["sh", str(SCREEN), "on"], env=env, capture_output=True, text=True, timeout=20)
    assert presence(tmp_path, "screen") == "on\n"


def test_a_failed_edge_write_is_repaired_on_the_next_poll(tmp_path):
    c = Charger(tmp_path, "0")
    c.start()
    try:
        c.wait_for("0\n")
        # The plug edge's write is lost (the file is reset behind the charger's
        # back, as a failed publication would leave it) while power stays on.
        c.set_usb("1")
        c.wait_for("1\n")
        (tmp_path / "taos-presence" / "power").write_text("0\n")
        c.wait_for("1\n")
    finally:
        c.stop()
