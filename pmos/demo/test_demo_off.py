"""demo-off.sh takes the handset out of demo mode (Jay 2026-10-16).

Every system command is a stub that logs its argv, and the drop-in directory
is a tmp dir, so the test runs anywhere and touches nothing real.
"""
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "demo/demo-off.sh"
STUBS = ("systemctl", "tailscale", "curl")


def run(tmp_path, curl_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    for name in STUBS:
        rc = curl_rc if name == "curl" else 0
        stub = bindir / name
        stub.write_text('#!/bin/sh\necho "%s $*" >> "%s"\nexit %d\n' % (name, log, rc))
        stub.chmod(0o755)
    dropins = tmp_path / "tinyagentos.service.d"
    dropins.mkdir()
    demo = dropins / "10-lock-demo.conf"
    demo.write_text('[Service]\nEnvironment="TAOS_LOCK_DEMO_AGENTS=x:y:z"\n')
    other = dropins / "20-keep.conf"
    other.write_text("[Service]\n")
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
               TAOS_DROPIN_DIR=str(dropins), TAOS_DEMO_OFF_WAIT="0")
    res = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)
    calls = log.read_text().splitlines() if log.exists() else []
    return res, calls, demo, other


def test_removes_the_demo_dropin_and_only_it(tmp_path):
    res, calls, demo, other = run(tmp_path)
    assert res.returncode == 0, res.stderr
    assert not demo.exists()
    assert other.exists()


def test_stops_demod_and_its_serve_then_restarts_the_controller(tmp_path):
    res, calls, _, _ = run(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "systemctl disable --now taos-demod.service" in calls
    assert "tailscale serve --https=443 off" in calls
    reload_at = calls.index("systemctl daemon-reload")
    restart_at = calls.index("systemctl restart tinyagentos.service")
    assert reload_at < restart_at


def test_fails_when_the_controller_does_not_come_back(tmp_path):
    res, calls, _, _ = run(tmp_path, curl_rc=7)
    assert res.returncode != 0
    assert "systemctl restart tinyagentos.service" in calls
