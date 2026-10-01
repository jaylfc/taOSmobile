"""taos-power-apply maps the lock screen's usb-* verbs onto taos-usb-mode.

The helper runs as root with its request path and the CLI path written in, so
the test runs a COPY with exactly those two paths pointed into a temp dir. The
substitutions are asserted to have happened: a copy that still read
/run/taos-power/request would pass every refusal case by reading nothing.

Run: python3 -m pytest -q -p no:cacheprovider pmos/kiosk/test_taos_power_apply.py
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).with_name("bin") / "taos-power-apply"
REAL_REQ = "REQ=/run/taos-power/request"
REAL_CLI = "/usr/local/bin/taos-usb-mode"


@pytest.fixture
def rig(tmp_path: Path):
    req = tmp_path / "request"
    calls = tmp_path / "calls"
    cli = tmp_path / "taos-usb-mode"
    cli.write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n')
    cli.chmod(0o755)
    # logger would write to the host's journal; a no-op one on PATH stops that.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "logger").write_text("#!/bin/sh\nexit 0\n")
    (bindir / "logger").chmod(0o755)

    text = SRC.read_text()
    assert text.count(REAL_REQ) == 1 and text.count(REAL_CLI) == 1
    copy = tmp_path / "taos-power-apply"
    copy.write_text(text.replace(REAL_REQ, f"REQ={req}").replace(REAL_CLI, str(cli)))
    copy.chmod(0o755)
    assert REAL_REQ not in copy.read_text() and REAL_CLI not in copy.read_text()

    def run(verb: str):
        req.write_text(verb + "\n")
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
        p = subprocess.run([str(copy)], env=env, capture_output=True, text=True)
        got = calls.read_text().splitlines() if calls.exists() else []
        return p.returncode, got, req.exists()

    return run


@pytest.mark.parametrize("mode", ["ncm", "charging", "mtp"])
def test_usb_verb_sets_that_mode_persistently(rig, mode):
    rc, calls, left = rig(f"usb-{mode}")
    assert rc == 0
    # Exactly `set MODE`: a trailing --now would switch without saving, and the
    # pull-down promises the choice survives a reboot.
    assert calls == [f"set {mode}"]
    assert not left


@pytest.mark.parametrize("verb", ["usb-rndis", "usb-adb", "usb-", "usb-ncm-x", "usb"])
def test_other_usb_verbs_are_refused_without_calling_the_cli(rig, verb):
    rc, calls, left = rig(verb)
    assert rc == 1
    assert calls == []
    assert not left
