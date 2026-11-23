"""install-framelog.sh builds taos-framelog in a container and installs it.

docker and id are stubbed on PATH, so nothing is built and nothing needs root.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "install-framelog.sh"
CONTINUITY = HERE / "install-kiosk-continuity.sh"


def rig(tmp_path, docker_body, ldd_body='echo "\tlibc.so => /lib/libc.so"\n'):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "id").write_text('#!/bin/sh\n[ "${1:-}" = -u ] && echo 0\n')
    if docker_body is not None:
        (bindir / "docker").write_text("#!/bin/sh\n" + docker_body)
    for f in bindir.iterdir():
        f.chmod(0o755)
    # Only the tools the script needs, so a host docker can never leak in.
    if ldd_body is not None:
        (bindir / "ldd").write_text("#!/bin/sh\n" + ldd_body)
        (bindir / "ldd").chmod(0o755)
    for tool in ("sh", "dirname", "mktemp", "rm", "cp", "cmp", "install", "mkdir", "chmod", "cat", "grep"):
        real = shutil.which(tool, path="/usr/bin:/bin")
        if real and not (bindir / tool).exists():
            (bindir / tool).symlink_to(real)
    dest = tmp_path / "taos-framelog"
    env = dict(os.environ, PATH=str(bindir), TAOS_FRAMELOG_DEST=str(dest))
    return env, dest


# A docker that "builds": finds the -v SRC:/src mount and writes the binary there.
BUILDS = '''
while [ "$#" -gt 0 ]; do
  if [ "$1" = -v ]; then src="${2%%:/src}"; fi
  shift
done
printf '#!/bin/sh\\necho framelog %s\\n' "${FRAMELOG_VERSION:-1}" > "$src/taos-framelog"
chmod 755 "$src/taos-framelog"
'''


def run(env, **extra):
    return subprocess.run(["sh", str(SCRIPT)], env=dict(env, **extra),
                          capture_output=True, text=True, timeout=30)


def test_a_build_installs_the_binary(tmp_path):
    env, dest = rig(tmp_path, BUILDS)
    r = run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert dest.exists() and os.access(dest, os.X_OK)
    assert "PASS" in r.stdout


def test_a_rerun_with_the_same_build_changes_nothing(tmp_path):
    env, dest = rig(tmp_path, BUILDS)
    assert run(env).returncode == 0
    before = dest.stat().st_mtime_ns
    r = run(env)
    assert r.returncode == 0
    assert "unchanged" in r.stdout
    assert dest.stat().st_mtime_ns == before
    # Control: a different build does replace it.
    r = run(env, FRAMELOG_VERSION="2")
    assert "installed" in r.stdout and "framelog 2" in dest.read_text()


def test_a_failed_build_fails_clearly_and_leaves_the_old_binary(tmp_path):
    env, dest = rig(tmp_path, "echo boom >&2\nexit 1\n")
    dest.write_text("old\n")
    r = run(env)
    assert r.returncode == 1
    assert "did not build" in r.stderr
    assert dest.read_text() == "old\n"


def test_no_docker_fails_clearly(tmp_path):
    env, dest = rig(tmp_path, None)
    r = run(env)
    assert r.returncode == 1 and "docker" in r.stdout
    assert not dest.exists()


def test_the_script_is_executable_because_the_installer_calls_it_directly():
    # install-kiosk-continuity.sh runs "$HERE/install-framelog.sh" without sh:
    # a non-executable file would fail every time and be reported as a build failure.
    mode = subprocess.run(["git", "ls-files", "-s", str(SCRIPT)], capture_output=True,
                          text=True, cwd=HERE).stdout.split()[0]
    assert mode == "100755", mode
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_the_kiosk_install_carries_on_when_the_build_fails():
    text = CONTINUITY.read_text()
    assert 'if "$HERE/install-framelog.sh"; then' in text


def test_a_binary_with_missing_libraries_never_replaces_a_working_one(tmp_path):
    env, dest = rig(tmp_path, BUILDS, ldd_body='echo "\tlibwayland-client.so.0 => not found"\n')
    dest.write_text("old\n")
    r = run(env)
    assert r.returncode == 1
    assert "libraries" in r.stderr and "not found" in r.stderr
    assert dest.read_text() == "old\n"


def test_a_binary_that_does_not_run_never_replaces_a_working_one(tmp_path):
    broken = BUILDS.replace("echo framelog", "exit 9; echo framelog")
    env, dest = rig(tmp_path, broken)
    dest.write_text("old\n")
    r = run(env)
    assert r.returncode == 1 and "does not run" in r.stderr
    assert dest.read_text() == "old\n"
