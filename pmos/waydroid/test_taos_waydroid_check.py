"""taos-waydroid-check refuses in every direction it names, and hardens.

The script is the ExecStartPre of waydroid-container.service on the handset.
Each test builds a fake /dev, /sys, /proc tree that is EXACTLY the live r9
handset's shape (measured 2026-10-31), proves the script passes on it, then
breaks one fact and proves the script fails ON THAT FACT. A check that only
ever saw the good tree is a check that was never seen red.

Run: python3 -m pytest -q -p no:cacheprovider pmos/waydroid/test_taos_waydroid_check.py
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CHECK = HERE / "bin" / "taos-waydroid-check"
EXTRA = HERE / "lxc-taos.conf"
ALLOW = "lxc.cgroup2.devices.allow = c *:* rwm"
DENY = "lxc.cgroup2.devices.deny = b *:* rwm"


def good_tree(root: Path, driver: str = "msm_dpu") -> None:
    for n in ("binder", "hwbinder", "vndbinder"):
        # mknod needs root; the script tests `-c`, so a real char device is
        # borrowed by symlink. /dev/null is the one every host has.
        (root / "dev").mkdir(exist_ok=True)
        os.symlink("/dev/null", root / "dev" / n)
    ue = root / "sys/class/drm/renderD128/device"
    ue.mkdir(parents=True)
    (ue / "uevent").write_text(f"DRIVER={driver}\nOF_NAME=display-controller\n")
    (root / "proc/pressure").mkdir(parents=True)
    (root / "proc/pressure/memory").write_text("some avg10=0.00\n")
    (root / "proc/mounts").write_text(
        "proc /proc proc rw 0 0\ncgroup2 /sys/fs/cgroup cgroup2 rw,nosuid 0 0\n")
    (root / "lxc").mkdir()
    (root / "lxc/config").write_text("lxc.rootfs.path = /var/lib/waydroid/rootfs\n")


def run(root: Path, **env_over: str) -> subprocess.CompletedProcess:
    env = dict(os.environ,
               TAOS_WAYDROID_SYSROOT=str(root),
               TAOS_WAYDROID_LXC_CONFIG=str(root / "lxc/config"),
               TAOS_WAYDROID_LXC_EXTRA=str(EXTRA))
    env.update(env_over)
    return subprocess.run(["sh", str(CHECK)], env=env, capture_output=True, text=True)


def test_good_tree_passes_and_appends_deny_line(tmp_path):
    good_tree(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "FAIL" not in r.stdout
    assert "PASS: render node renderD128 (msm_dpu)" in r.stdout
    text = (tmp_path / "lxc/config").read_text()
    assert DENY in text and ALLOW in text
    # The char allow must precede the block deny: LXC's cgroup2 device filter
    # is an allowlist once any rule exists (measured 2026-10-31).
    assert text.index(ALLOW) < text.index(DENY)
    assert "(2 appended now)" in r.stdout
    # Second run: idempotent, nothing appended twice.
    r2 = run(tmp_path)
    assert r2.returncode == 0
    text2 = (tmp_path / "lxc/config").read_text()
    assert text2.count(DENY) == 1 and text2.count(ALLOW) == 1
    assert "(0 appended now)" in r2.stdout


def test_plain_msm_driver_also_passes(tmp_path):
    good_tree(tmp_path, driver="msm")
    assert run(tmp_path).returncode == 0


@pytest.mark.parametrize("node", ["binder", "hwbinder", "vndbinder"])
def test_missing_binder_node_fails(tmp_path, node):
    good_tree(tmp_path)
    (tmp_path / "dev" / node).unlink()
    r = run(tmp_path)
    assert r.returncode == 1
    assert f"FAIL: /dev/{node} missing" in r.stdout


def test_render_node_with_other_driver_fails_and_names_it(tmp_path):
    good_tree(tmp_path, driver="panthor")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "FAIL: no msm render node; found: renderD128=panthor" in r.stdout


def test_no_render_node_fails(tmp_path):
    good_tree(tmp_path)
    (tmp_path / "sys/class/drm/renderD128/device/uevent").unlink()
    r = run(tmp_path)
    assert r.returncode == 1
    assert "FAIL: no render node under /sys/class/drm" in r.stdout


def test_cgroup1_fails(tmp_path):
    good_tree(tmp_path)
    (tmp_path / "proc/mounts").write_text("cgroup /sys/fs/cgroup tmpfs rw 0 0\n")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "FAIL: cgroup2 is not mounted" in r.stdout


def test_missing_psi_fails(tmp_path):
    good_tree(tmp_path)
    (tmp_path / "proc/pressure/memory").unlink()
    r = run(tmp_path)
    assert r.returncode == 1
    assert "FAIL: /proc/pressure/memory missing" in r.stdout


def test_uninitialised_waydroid_fails_and_creates_nothing(tmp_path):
    good_tree(tmp_path)
    (tmp_path / "lxc/config").unlink()
    r = run(tmp_path)
    assert r.returncode == 1
    assert "not initialised" in r.stdout
    assert not (tmp_path / "lxc/config").exists()


def test_missing_extra_file_fails(tmp_path):
    good_tree(tmp_path)
    r = run(tmp_path, TAOS_WAYDROID_LXC_EXTRA=str(tmp_path / "nope.conf"))
    assert r.returncode == 1
    assert "FAIL: " in r.stdout and "nope.conf missing" in r.stdout


def test_extra_file_with_only_comments_fails(tmp_path):
    good_tree(tmp_path)
    empty = tmp_path / "empty.conf"
    empty.write_text("# nothing here\n\n")
    r = run(tmp_path, TAOS_WAYDROID_LXC_EXTRA=str(empty))
    assert r.returncode == 1
    assert "measured nothing" in r.stdout


def test_shipped_extra_holds_char_allow_then_block_deny():
    lines = [l for l in EXTRA.read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert lines == [ALLOW, DENY]


def test_dropin_runs_the_check_and_stops_cleanly():
    text = (HERE / "systemd/waydroid-container.service.d/10-taos.conf").read_text()
    assert "ExecStartPre=/usr/local/bin/taos-waydroid-check" in text
    assert "ExecStop=/usr/bin/waydroid container stop" in text
    assert "After=nftables.service" in text


def test_nft_file_accepts_the_bridge_both_ways():
    text = (HERE / "51_waydroid.nft").read_text()
    assert 'iifname "waydroid0" accept' in text
    assert text.count('iifname "waydroid0" accept') == 2, "input and forward"
    assert 'oifname "waydroid0"' in text
    assert "table inet filter" in text
