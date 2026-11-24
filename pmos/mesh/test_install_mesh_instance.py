"""install-mesh-instance.sh installs the taOS per-person mesh template on the phone.

id, su, systemctl, tailscale, ip and the taOS python are all stubs on PATH, so
nothing touches a real tailnet, service or device. Every stub appends its argv
to one log file; the tests assert on that log.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "install-mesh-instance.sh"

STATUS_OK = json.dumps({"BackendState": "Running",
                        "Self": {"TailscaleIPs": ["100.64.0.7", "fd7a::7"]},
                        "Peer": {"k": {"TailscaleIPs": ["100.64.0.9"]}}})
STATUS_CHANGED = json.dumps({"BackendState": "Running",
                             "Self": {"TailscaleIPs": ["100.64.9.9"]}})


def rig(tmp_path, uid=0, slot=0, status_seq=None, route_seq=None, restart_heals=True):
    """status_seq / route_seq: outputs for successive calls (last one repeats).
    After a `systemctl restart tailscaled`, the first answer is used again when
    restart_heals is true (the restart restores the original state)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    run_root = tmp_path / "run"
    run_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    status_seq = status_seq or [STATUS_OK]
    route_seq = route_seq or ["default dev tailscale0 table 52"]
    for name, seq in (("status", status_seq), ("route", route_seq)):
        for i, v in enumerate(seq):
            (state / f"{name}.{i}").write_text(v)
    (state / "restarted").write_text("0")

    def w(name, body):
        (bindir / name).write_text("#!/bin/sh\n" + body)
        (bindir / name).chmod(0o755)

    w("id", f'[ "${{1:-}}" = -u ] && echo {uid}\n')
    w("su", f'echo "su $*" >> {log}\n'
            'while [ "$#" -gt 0 ]; do [ "$1" = -c ] && { shift; exec sh -c "$1"; }; shift; done\n')
    sock_make = f'touch {run_root}/alice.sock' if True else ''
    w("systemctl", f'echo "systemctl $*" >> {log}\n'
                   f'case "$*" in "enable --now taos-mesh@"*) {sock_make};; '
                   f'"restart tailscaled") echo 1 > {state}/restarted;; esac\nexit 0\n')
    heal = "1" if restart_heals else "0"
    pick = ('n=$(cat {s}/{k}.n 2>/dev/null || echo 0); echo $((n+1)) > {s}/{k}.n; '
            '[ "$(cat {s}/restarted)" = 1 ] && [ {h} = 1 ] && n=0; '
            '[ -f {s}/{k}.$n ] || n=$(ls {s}/{k}.[0-9] | wc -l | sed "s/ //g"; ); '
            'f={s}/{k}.$n; [ -f "$f" ] || f=$(ls {s}/{k}.[0-9] | tail -n1); cat "$f"\n')
    w("tailscale", f'echo "tailscale $*" >> {log}\n'
                   + pick.format(s=state, k="status", h=heal))
    w("ip", f'echo "ip $*" >> {log}\n' + pick.format(s=state, k="route", h=heal))
    py = tmp_path / "fakepython"
    py.write_text(f'#!/bin/sh\necho "python $*" >> {log}\n'
                  'for a in "$@"; do last="$a"; done\n'
                  'case "$*" in *" allocate "*) '
                  f'echo \'{{"person": "\'"$last"\'", "slot": {slot}, "new": true, "skipped": []}}\';; esac\nexit 0\n')
    py.chmod(0o755)
    for tool in ("sh", "sed", "grep", "cat", "tr", "mktemp", "rm", "sleep", "ls", "wc", "tail",
                 "head", "cmp", "echo", "touch", "date", "mkdir", "dirname"):
        real = shutil.which(tool, path="/usr/bin:/bin")
        if real and not (bindir / tool).exists():
            (bindir / tool).symlink_to(real)
    env = dict(os.environ, PATH=str(bindir), TAOS_PYTHON=str(py),
               TAOS_MESH_RUN_ROOT=str(run_root), TAOS_MESH_SETTLE_SECS="2")
    return env, log


def run(env, *args):
    return subprocess.run(["sh", str(SCRIPT), *args], env=env,
                          capture_output=True, text=True, timeout=60)


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_not_root_is_refused(tmp_path):
    env, log = rig(tmp_path, uid=1000)
    r = run(env, "alice")
    assert r.returncode != 0
    assert "root" in (r.stdout + r.stderr)
    assert calls(log) == []


@pytest.mark.parametrize("bad", ["Alice", "../x", "", "a" * 32, "1abc", "a_b", "a b"])
def test_bad_person_refused_before_anything_runs(tmp_path, bad):
    env, log = rig(tmp_path)
    r = run(env, bad) if bad != "" else run(env)
    assert r.returncode != 0
    assert calls(log) == []


def test_thirty_one_chars_is_accepted(tmp_path):
    env, log = rig(tmp_path)
    assert run(env, "a" * 31).returncode == 0


def test_install_template_then_allocate_as_taos(tmp_path):
    env, log = rig(tmp_path)
    r = run(env, "alice")
    assert r.returncode == 0, r.stdout + r.stderr
    c = calls(log)
    i = next(n for n, l in enumerate(c) if "install-template" in l)
    a = next(n for n, l in enumerate(c) if " allocate alice" in l and l.startswith("python"))
    assert "-m tinyagentos.taosnet.mesh_instances install-template" in c[i]
    assert i < a
    su = [l for l in c if l.startswith("su ")]
    assert su and su[0].split()[:4] == ["su", "-s", "/bin/sh", "taos"]
    assert "-c" in su[0].split()
    assert "allocate alice" in su[0]
    # install-template is the owner step: not run through su
    assert not any(l.startswith("su ") and "install-template" in l for l in c)


def test_default_python_path_is_the_phone_venv(tmp_path):
    assert "/root/tinyagentos/.venv/bin/python" in SCRIPT.read_text()


def test_slot_other_than_zero_fails(tmp_path):
    env, log = rig(tmp_path, slot=3)
    r = run(env, "alice")
    assert r.returncode != 0
    assert "slot" in (r.stdout + r.stderr)
    assert not any("enable" in l for l in calls(log))


def test_inert_by_default(tmp_path):
    env, log = rig(tmp_path)
    r = run(env, "alice")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not [l for l in calls(log) if l.startswith("systemctl") and
                ("enable" in l or "start" in l)]


def test_start_enables_now_and_waits_for_the_socket(tmp_path):
    env, log = rig(tmp_path)
    r = run(env, "alice", "--start")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "systemctl enable --now taos-mesh@alice" in calls(log)
    assert "alice.sock" in r.stdout


def test_start_fails_if_socket_never_appears(tmp_path):
    env, log = rig(tmp_path)
    env["TAOS_MESH_SOCKET_WAIT_SECS"] = "2"
    # person whose socket the stub does not create
    r = run(env, "bob", "--start")
    assert r.returncode != 0
    assert "socket" in (r.stdout + r.stderr)


def test_personal_tailnet_unchanged_means_no_restart(tmp_path):
    env, log = rig(tmp_path)
    r = run(env, "alice", "--start")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "systemctl restart tailscaled" not in calls(log)
    assert "PASS" in r.stdout and "table 52" in r.stdout


def test_changed_after_install_restarts_and_passes_if_recovered(tmp_path):
    env, log = rig(tmp_path, status_seq=[STATUS_OK, STATUS_CHANGED],
                   route_seq=["default dev tailscale0 table 52", "different"])
    r = run(env, "alice")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "systemctl restart tailscaled" in calls(log)
    assert "PASS" in r.stdout


def test_changed_and_not_recovered_fails(tmp_path):
    env, log = rig(tmp_path, status_seq=[STATUS_OK, STATUS_CHANGED], restart_heals=False)
    r = run(env, "alice")
    assert r.returncode != 0
    assert "systemctl restart tailscaled" in calls(log)
    assert "FAIL" in (r.stdout + r.stderr)


def test_route_table_change_alone_triggers_restart(tmp_path):
    env, log = rig(tmp_path, route_seq=["default dev tailscale0 table 52", ""])
    r = run(env, "alice")
    assert "systemctl restart tailscaled" in calls(log)


def test_no_mutating_tailscale_call(tmp_path):
    env, log = rig(tmp_path, status_seq=[STATUS_OK, STATUS_CHANGED], restart_heals=False)
    run(env, "alice", "--start")
    ts = [l for l in calls(log) if l.startswith("tailscale")]
    assert ts
    for l in ts:
        words = l.split()
        assert words[1] == "status", l
        for bad in ("up", "login", "logout", "set", "down", "--socket"):
            assert bad not in words, l


def test_script_never_names_kiosk_or_personal_config(tmp_path):
    body = SCRIPT.read_text()
    for needle in ("sway-kiosk", "/etc/default/tailscaled", "tailscale up",
                   "tailscale login", "tailscale logout", "tailscale set"):
        assert needle not in body
