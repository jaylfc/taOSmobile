"""taos-switch-os is the only root door between the taOS shell and the other systems.

It takes one word from a web request's far end, so the malformed words come first.
"""
import gzip
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TOOL = HERE / "taos-switch-os"


@pytest.fixture
def box(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("the test hooks are ignored when root, so these tests would run against the real "
                    "system; run as a non-root user")
    d = tmp_path / "dualboot"
    (d / "guest").mkdir(parents=True)
    log = tmp_path / "launched"
    launcher = tmp_path / "kexec-guest.sh"
    launcher.write_text('#!/bin/sh\necho "$#:$*" >> "%s"\n' % log)
    launcher.chmod(0o755)
    identity = tmp_path / "build-identity.sh"

    def kernel_is(matches):
        identity.write_text("dualboot_stream_matches_running() { cat >/dev/null; return %d; }\n"
                            % (0 if matches else 1))

    kernel_is(True)
    env = dict(os.environ, TAOS_SWITCH_OS_TEST="1", TAOS_SWITCH_OS_DIR=str(d),
               TAOS_SWITCH_OS_LAUNCHER=str(launcher))

    def run(*args, env=env):
        return subprocess.run(["sh", str(TOOL), *args], env=env, capture_output=True, text=True, timeout=10)

    def install(*ids):
        (d / "guest" / "kernel").write_bytes(gzip.compress(b"k"))
        (d / "guest" / "ramdisk").write_bytes(b"r")
        for i in ids:
            (d / {"omarchy": "arch-root.img", "luma": "luma-root.img"}[i]).write_bytes(b"i")

    return type("Box", (), dict(run=staticmethod(run), install=staticmethod(install), log=log, d=d,
                                launcher=launcher, kernel_is=staticmethod(kernel_is)))


@pytest.mark.parametrize("args", [
    ("go",),                              # no id
    ("go", ""),
    ("go", "omarchy;reboot"),
    ("go", "omarchy luma"),               # one argument holding two words
    ("go", "omarchy\nluma"),
    ("go", "../luma"),
    ("go", "Omarchy"),                    # the display name is not the id
    ("go", "--guest"),
    ("go", "$(id)"),
    ("go", "luma", "extra"),              # a third word is refused, not ignored
    ("go", "luma", "--kernel", "/tmp/x.apk"),
    ("list", "omarchy"),
    ("stop",),
    (),
])
def test_anything_but_the_two_fixed_forms_is_refused_and_nothing_launches(box, args):
    box.install("omarchy", "luma")
    r = box.run(*args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert not box.log.exists()
    assert r.stdout == ""


def test_control_a_valid_go_launches_exactly_that_system(box):
    box.install("omarchy", "luma")
    r = box.run("go", "luma")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"ok": True, "id": "luma", "in_seconds": 2}
    assert box.log.read_text() == "3:--guest luma --go\n"


def test_list_says_what_is_installed_and_why_not(box):
    out = json.loads(box.run("list").stdout)
    assert [(s["id"], s["ready"], s["reason"]) for s in out] == [
        ("omarchy", False, "not installed"), ("luma", False, "not installed")]
    (box.d / "arch-root.img").write_bytes(b"i")
    out = json.loads(box.run("list").stdout)
    assert (out[0]["ready"], out[0]["reason"]) == (False, "guest kernel not staged")
    box.install("omarchy")
    out = json.loads(box.run("list").stdout)
    assert out == [{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""},
                   {"id": "luma", "name": "Luma", "ready": False, "reason": "not installed"}]
    box.launcher.chmod(0o644)
    assert json.loads(box.run("list").stdout)[0]["reason"] == "launcher missing"


def test_a_stale_guest_kernel_is_not_ready_and_not_launched(box):
    box.install("omarchy", "luma")
    box.kernel_is(False)
    out = json.loads(box.run("list").stdout)
    assert [(s["ready"], s["reason"]) for s in out] == [
        (False, "guest kernel is stale: run refresh-guest-boot.sh")] * 2
    r = box.run("go", "luma")
    assert r.returncode == 3 and "stale" in r.stderr and r.stdout == ""
    assert not box.log.exists()
    box.kernel_is(True)
    assert all(s["ready"] for s in json.loads(box.run("list").stdout))
    assert box.run("go", "luma").returncode == 0
    assert box.log.read_text() == "3:--guest luma --go\n"


def test_a_missing_build_identity_is_launcher_missing(box):
    box.install("omarchy")
    (box.launcher.parent / "build-identity.sh").unlink()
    assert json.loads(box.run("list").stdout)[0]["reason"] == "launcher missing"
    assert box.run("go", "omarchy").returncode == 3
    assert not box.log.exists()


def test_a_system_that_is_not_ready_is_not_launched(box):
    box.install("omarchy")
    r = box.run("go", "luma")
    assert r.returncode == 3 and "not installed" in r.stderr
    assert not box.log.exists()


def test_without_the_test_hook_go_needs_root(box):
    if os.getuid() == 0:
        pytest.skip("needs a non-root user")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAOS_SWITCH_OS")}
    r = box.run("go", "omarchy", env=env)
    assert r.returncode == 1 and "needs root" in r.stderr
    assert not box.log.exists()


def test_the_test_hooks_are_only_read_when_not_root():
    """Cannot run as root here, so pin the guard itself: one place, before any hook is read."""
    src = TOOL.read_text()
    guard = 'if [ "$(id -u)" != 0 ] && [ -n "${TAOS_SWITCH_OS_TEST:-}" ]; then'
    assert src.count(guard) == 1
    head, tail = src.split(guard)
    assert "TAOS_SWITCH_OS_DIR" not in head and "TAOS_SWITCH_OS_LAUNCHER" not in head
    block = tail.split("\nelse\n")[0]
    assert "TAOS_SWITCH_OS_DIR" in block and "TAOS_SWITCH_OS_LAUNCHER" in block
    assert "TAOS_SWITCH_OS_DIR" not in tail.split("\nfi\n", 1)[1]


def test_sudoers_allows_exactly_the_two_go_forms():
    src = (HERE / "install-switch-os.sh").read_text()
    rules = [l for l in src.splitlines() if l.startswith("taos ALL=")]
    assert rules == ["taos ALL=(root) NOPASSWD: /usr/local/sbin/taos-switch-os go omarchy, "
                     "/usr/local/sbin/taos-switch-os go luma"]
    assert "*" not in rules[0] and "ALL" not in rules[0].split("NOPASSWD:")[1]
    defaults = [l for l in src.splitlines() if l.startswith("Defaults")]
    assert defaults == ["Defaults!/usr/local/sbin/taos-switch-os secure_path=/usr/sbin:/usr/bin:/sbin:/bin"]


def test_the_tool_fixes_its_own_path_before_any_command():
    src = TOOL.read_text().splitlines()
    code = [l for l in src if l.strip() and not l.lstrip().startswith("#")]
    assert code[0] == "set -eu"
    assert code[1] == "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH"


def test_a_planted_id_and_systemd_run_on_the_callers_path_are_never_run(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("needs a non-root user")
    bin_ = tmp_path / "evil"
    bin_.mkdir()
    marker = tmp_path / "marker"
    (bin_ / "id").write_text("#!/bin/sh\necho 0\n")
    (bin_ / "systemd-run").write_text('#!/bin/sh\necho ran > "%s"\n' % marker)
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAOS_SWITCH_OS")}
    env["PATH"] = "%s:%s" % (bin_, env["PATH"])
    r = subprocess.run(["sh", str(TOOL), "go", "omarchy"], env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 1 and "needs root" in r.stderr
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["abc", "5", "123456", "-30", "30s", ""])
def test_return_after_takes_only_sane_seconds(tmp_path, bad):
    r = subprocess.run(["sh", str(HERE / "kexec-guest.sh"), "--return-after", bad],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode in (1, 2)          # refused before anything is staged
    assert "return-after" in (r.stdout + r.stderr)


def test_the_guest_gets_a_return_to_taos_entry(tmp_path):
    r = subprocess.run(["bash", "-c", '. "$1"; dualboot_return_entry "$2/bin" /etc/dualboot "$2/apps"',
                        "_", str(HERE / "guest-units.sh"), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    script = tmp_path / "bin" / "taos-return"
    assert os.access(script, os.X_OK)
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    assert "systemctl reboot" in script.read_text()
    entry = (tmp_path / "apps" / "taos-return.desktop").read_text()
    assert "Name=Return to taOS" in entry and "Exec=/etc/dualboot/taos-return" in entry
    assert "Type=Application" in entry and "NoDisplay" not in entry
