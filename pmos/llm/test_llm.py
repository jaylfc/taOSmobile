"""Tests for the opt-in on-device LLM (llama.cpp + LFM3-1.7B), no root, no device.

The scripts take their paths from environment knobs, so they run unmodified
against a fake thermal directory and a state dir; systemctl, curl and logger are
stubs on PATH that log their arguments. Each stub's effect is asserted (a call
log that is empty because the stub never ran would pass every "did nothing"
case).

Run: python3 -m pytest -q -p no:cacheprovider pmos/llm/
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BIN = HERE / "bin"
UNITS = HERE.parent / "systemd"
SERVICE = UNITS / "llama-cpp.service"
INSTALLER = HERE / "install-llama-cpp.sh"


def active_lines(path: Path) -> list[str]:
    """The file's lines with comments stripped, so a comment cannot satisfy or trip a check."""
    out = []
    for ln in path.read_text().splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def exec_start(path: Path) -> str:
    lines = active_lines(path)
    i = next(n for n, ln in enumerate(lines) if ln.startswith("ExecStart="))
    cmd = lines[i]
    while cmd.endswith("\\"):
        i += 1
        cmd = cmd[:-1] + " " + lines[i]
    return cmd


# ---- the service unit -------------------------------------------------------

def test_service_pins_two_big_cores_and_two_threads():
    lines = active_lines(SERVICE)
    assert "CPUAffinity=5 6" in lines
    cmd = exec_start(SERVICE)
    assert re.search(r"(^| )-t 2( |$)", cmd), cmd
    assert re.search(r"(^| )-tb 2( |$)", cmd), cmd
    assert "@THREADS@" not in SERVICE.read_text()


def test_service_context_is_16384():
    cmd = exec_start(SERVICE)
    assert re.search(r"--ctx-size 16384( |$)", cmd), cmd


def test_service_is_cpu_only_and_sandboxed_from_devices():
    lines = active_lines(SERVICE)
    assert "PrivateDevices=yes" in lines
    body = "\n".join(lines).lower()
    for bad in ("vulkan", "-ngl", "--n-gpu-layers", "--device", "gpu", "i8mm"):
        assert bad not in body, bad
    # the comments too: the wrong i8mm claim must be gone from the file
    assert "i8mm" not in SERVICE.read_text().lower()


def test_service_is_not_installable_and_pulls_its_guards():
    lines = active_lines(SERVICE)
    assert not any(ln == "[Install]" or ln.startswith("WantedBy=") for ln in lines)
    wants = " ".join(ln for ln in lines if ln.startswith("Wants="))
    assert "taos-llm-thermal.service" in wants and "taos-llm-idle.service" in wants


@pytest.mark.parametrize("name", ["taos-llm-thermal.service", "taos-llm-idle.service"])
def test_guard_units_die_with_the_model(name):
    lines = active_lines(UNITS / name)
    assert "BindsTo=llama-cpp.service" in lines
    assert "After=llama-cpp.service" in lines
    assert not any(ln.startswith("WantedBy=") for ln in lines)


def test_thermal_unit_resumes_the_model_if_the_guard_dies():
    lines = active_lines(UNITS / "taos-llm-thermal.service")
    assert any(ln.startswith("ExecStopPost=") and "SIGCONT" in ln and "llama-cpp.service" in ln
               for ln in lines)


# ---- the installer ----------------------------------------------------------

def test_installer_never_enables_or_starts_the_service():
    body = "\n".join(active_lines(INSTALLER))
    assert not re.search(r"systemctl[ \t]+([^\n]* )?(enable|start|restart|reload-or-restart|try-restart)\b", body), body
    assert "--now" not in body
    assert "WantedBy" not in body


def test_installer_uses_the_alpine_package_not_a_source_build():
    body = "\n".join(active_lines(INSTALLER))
    assert re.search(r"apk add .*llama-server", body)
    for gone in ("cmake", "git clone", "ninja", "GGML_NATIVE", "VULKAN", "vulkan"):
        assert gone not in body, gone


def test_installer_keeps_the_model_pins():
    body = INSTALLER.read_text()
    assert "43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452" in body
    assert "8f6b24182258205e9e01e21f1f24a1f208c1e8115578825d202ce39976af20b6" in body


def test_installer_installs_helper_guards_units_and_licence():
    body = INSTALLER.read_text()
    for needle in ("/usr/local/sbin/taos-llm", "taos-llm-thermal", "taos-llm-idle",
                   "/usr/share/taos/models/lfm3-1.7b.json"):
        assert needle in body, needle


def test_licence_json_matches_the_model_card():
    j = json.loads((HERE / "lfm3-1.7b.json").read_text())
    assert j == {
        "id": "lfm3-1.7b",
        "name": "LFM3 1.7B",
        "licence": "LFM Open License v1.0",
        "licence_note": "Commercial use allowed only below USD 10 million annual revenue",
        "thinking_default": "off",
        "source": "https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF",
        "offline": True,
        "performance_note": j["performance_note"],
    }
    # Settings shows this to the owner: it must carry the measured numbers.
    assert "2 cores" in j["performance_note"] and "3 minutes" in j["performance_note"]


# ---- stubs ------------------------------------------------------------------

@pytest.fixture
def rig(tmp_path: Path):
    bindir = tmp_path / "stubs"
    bindir.mkdir()
    calls = tmp_path / "calls"
    (bindir / "systemctl").write_text(
        '#!/bin/sh\n'
        f'echo "systemctl $*" >> "{calls}"\n'
        'if [ "$1" = is-active ]; then [ -e "$STUB_ACTIVE" ]; exit $?; fi\n'
        'exit "${STUB_SYSTEMCTL_RC:-0}"\n')
    (bindir / "curl").write_text(
        '#!/bin/sh\n'
        f'echo "curl $*" >> "{calls}"\n'
        # the last argument is the URL; serve the body file named for its path
        'for a; do u=$a; done\n'
        'f="$STUB_CURL_DIR/$(printf %s "$u" | sed "s|^http://127.0.0.1:[0-9]*||" | tr "/?=" "___")"\n'
        '[ -r "$f" ] || exit 22\n'
        'cat "$f"\n')
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for p in bindir.iterdir():
        p.chmod(0o755)
    curl_dir = tmp_path / "curl"
    curl_dir.mkdir()
    active = tmp_path / "active"

    def run(script: str, *args: str, env: dict | None = None, active_on: bool = False):
        if active_on:
            active.write_text("")
        elif active.exists():
            active.unlink()
        e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
                 STUB_ACTIVE=str(active), STUB_CURL_DIR=str(curl_dir),
                 TAOS_LLM_ENV=str(tmp_path / "absent.env"), TAOS_LLM_TEST="1", TAOS_LLM_TEST_PATH=str(bindir))
        e.update(env or {})
        p = subprocess.run(["sh", str(BIN / script), *args], env=e,
                           capture_output=True, text=True, timeout=30)
        got = calls.read_text().splitlines() if calls.exists() else []
        return p, got

    def reset():
        if calls.exists():
            calls.unlink()

    run.reset = reset
    run.curl_dir = curl_dir
    run.tmp = tmp_path
    return run


# ---- taos-llm ---------------------------------------------------------------

needs_user = pytest.mark.skipif(os.geteuid() == 0, reason="the test hooks are ignored when root")
HELPER = BIN / "taos-llm"
INSTALLED = "/usr/local/sbin/taos-llm"
SUDO_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


def test_status_running_json(rig):
    (rig.curl_dir / "_health").write_text('{"status":"ok"}')
    p, calls = rig("taos-llm", "status", active_on=True)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"running": True, "healthy": True,
                                    "model": "LFM3-1.7B-QAD-Q4_0", "port": 7835}
    assert calls[0] == "systemctl is-active --quiet llama-cpp.service"


def test_status_running_but_not_yet_healthy(rig):
    p, _ = rig("taos-llm", "status", active_on=True)
    assert json.loads(p.stdout)["running"] is True
    assert json.loads(p.stdout)["healthy"] is False


def test_status_stopped_json_never_asks_health(rig):
    (rig.curl_dir / "_health").write_text('{"status":"ok"}')
    p, calls = rig("taos-llm", "status", active_on=False)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"running": False, "healthy": False,
                                    "model": "LFM3-1.7B-QAD-Q4_0", "port": 7835}
    assert calls == ["systemctl is-active --quiet llama-cpp.service"]


def test_status_reads_port_and_model_from_the_env_file(rig):
    envf = rig.tmp / "llm.env"
    envf.write_text("TAOS_LLAMACPP_PORT=7900\nTAOS_LLM_MODEL=LFM3-1.7B-Q8_0\n")
    p, _ = rig("taos-llm", "status", env={"TAOS_LLM_ENV": str(envf)}, active_on=True)
    j = json.loads(p.stdout)
    assert (j["model"], j["port"]) == ("LFM3-1.7B-Q8_0", 7900)


def env_status(rig, text: str):
    envf = rig.tmp / "llm.env"
    envf.write_text(text)
    sentinel = rig.tmp / "SENTINEL"
    p, _ = rig("taos-llm", "status", env={"TAOS_LLM_ENV": str(envf)}, active_on=True)
    assert not sentinel.exists(), "the env file was executed"
    return json.loads(p.stdout)


@pytest.mark.parametrize("bad", [
    "7835; touch {s}", "$(touch {s})", "`touch {s}`", "7835 7836", "0", "65536", "123456", "007835", "-1", "",
])
def test_helper_never_executes_the_env_file_and_ignores_a_bad_port(rig, bad):
    j = env_status(rig, "TAOS_LLAMACPP_PORT=%s\n" % bad.format(s=rig.tmp / "SENTINEL"))
    assert j["port"] == 7835


@pytest.mark.parametrize("bad", ["x; touch {s}", "$(touch {s})", "a b", "../x", '"quoted"', ""])
def test_helper_ignores_a_bad_model_name(rig, bad):
    j = env_status(rig, "TAOS_LLM_MODEL=%s\n" % bad.format(s=rig.tmp / "SENTINEL"))
    assert j["model"] == "LFM3-1.7B-QAD-Q4_0"


def test_helper_env_file_newline_injection_and_stray_lines_are_ignored(rig):
    s = rig.tmp / "SENTINEL"
    j = env_status(rig, (
        "TAOS_LLAMACPP_PORT=7900\n"
        f"PATH=/tmp/evil\ntouch {s}\n$(touch {s})\nexport X=1; touch {s}\n"
        f"TAOS_LLM_MODEL=LFM3-1.7B-Q8_0\n  TAOS_LLM_MODEL=evil\nTAOS_LLM_IDLE_MIN=5; touch {s}\n"))
    assert (j["model"], j["port"]) == ("LFM3-1.7B-Q8_0", 7900)


def test_helper_reads_a_last_line_without_a_newline(rig):
    assert env_status(rig, "TAOS_LLAMACPP_PORT=7901")["port"] == 7901


def test_helper_never_sources_the_env_file():
    assert not [ln for ln in active_lines(HELPER) if re.match(r"\s*(\.|source)\s", ln) or "&& . " in ln]


@needs_user
def test_status_works_without_root_and_without_the_test_hook(rig):
    """The real path a settings card takes: no TAOS_LLM_TEST, not root."""
    # No hook means the real systemctl and curl (stubs are ignored): whatever state this box
    # is in, status answers as a plain user, with valid JSON and no refusal.
    p, calls = rig("taos-llm", "status", env={"TAOS_LLM_TEST": ""}, active_on=True)
    assert p.returncode == 0, p.stderr
    assert set(json.loads(p.stdout)) == {"running", "healthy", "model", "port"}
    assert "root" not in p.stderr and calls == []


@needs_user
@pytest.mark.parametrize("sub", ["start", "stop"])
def test_start_and_stop_refuse_without_root(rig, sub):
    p, calls = rig("taos-llm", sub, env={"TAOS_LLM_TEST": ""}, active_on=True)
    assert p.returncode == 1
    assert "needs root" in p.stderr
    assert p.stdout == ""
    assert calls == [], "a refused start/stop must not reach systemctl"


@pytest.mark.parametrize("args", [(), ("restart",), ("enable",), ("--help",), ("",),
                                  ("start", "x"), ("stop", "x"), ("status", "x")])
def test_helper_refuses_unknown_subcommands(rig, args):
    p, calls = rig("taos-llm", *args)
    assert p.returncode == 2
    assert "usage" in p.stderr
    assert calls == [], "an unknown subcommand must not reach systemctl"
    assert p.stdout == ""


def test_start_starts_waits_for_health_then_reports_running(rig):
    (rig.curl_dir / "_health").write_text('{"status":"ok"}')
    p, calls = rig("taos-llm", "start", active_on=True)
    assert p.returncode == 0, p.stderr
    assert calls[0] == "systemctl start llama-cpp.service"
    assert any("http://127.0.0.1:7835/health" in c for c in calls)
    assert json.loads(p.stdout)["running"] is True


def test_start_loads_the_model_so_the_idle_stop_can_see_it(rig):
    """In router mode /slots is unreadable until the model is loaded, and the
    idle stop counts unreadable as busy: start must load the model itself."""
    (rig.curl_dir / "_health").write_text('{"status":"ok"}')
    p, calls = rig("taos-llm", "start", active_on=True)
    assert p.returncode == 0, p.stderr
    health = next(i for i, c in enumerate(calls) if "/health" in c)
    warm = [i for i, c in enumerate(calls) if "/v1/chat/completions" in c]
    assert warm and warm[0] > health, calls
    assert '"max_tokens":1' in calls[warm[0]] and '"enable_thinking":false' in calls[warm[0]]


def test_start_fails_when_health_never_answers(rig):
    p, calls = rig("taos-llm", "start", env={"TAOS_LLM_START_WAIT": "2"}, active_on=True)
    assert p.returncode == 1
    assert "health" in p.stderr


def test_stop_stops_and_reports_not_running(rig):
    p, calls = rig("taos-llm", "stop", active_on=False)
    assert p.returncode == 0
    assert calls[0] == "systemctl stop llama-cpp.service"
    assert json.loads(p.stdout)["running"] is False


def test_the_helper_fixes_its_own_path_before_any_command():
    code = [ln for ln in HELPER.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert code[0] == "set -u"
    assert code[1] == "PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH"


def test_the_test_hooks_are_only_read_when_not_root():
    src = HELPER.read_text()
    guard = 'if [ "$(id -u)" != 0 ] && [ -n "${TAOS_LLM_TEST:-}" ]; then'
    assert src.count(guard) == 1
    head, tail = src.split(guard)
    for hook in ("TAOS_LLM_ENV", "TAOS_LLM_START_WAIT", "TAOS_LLM_TEST_PATH"):
        assert hook not in head, hook
        assert hook in tail.split("\nfi\n")[0]
        assert hook not in tail.split("\nfi\n", 1)[1]


@needs_user
def test_a_planted_systemctl_and_curl_on_the_callers_path_are_never_run(tmp_path):
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "marker"
    for name in ("systemctl", "curl", "python3", "id"):
        (evil / name).write_text('#!/bin/sh\necho %s >> "%s"\nexit 0\n' % (name, marker))
        (evil / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TAOS_LLM")}
    env["PATH"] = "%s:%s" % (evil, env["PATH"])
    p = subprocess.run(["sh", str(HELPER), "start"], env=env, capture_output=True, text=True, timeout=10)
    assert p.returncode == 1 and "needs root" in p.stderr
    assert not marker.exists(), marker.read_text() if marker.exists() else ""
    p = subprocess.run(["sh", str(HELPER), "status"], env=env, capture_output=True, text=True, timeout=10)
    assert "id" not in (marker.read_text().split() if marker.exists() else [])


# ---- the sudoers file ---------------------------------------------------------

def sudoers_text() -> list[str]:
    """The installer's sudoers heredoc, lines as written (user placeholder left in)."""
    src = INSTALLER.read_text()
    m = re.search(r"<<'SUDO'\n(.*?)\nSUDO\n", src, re.S)
    assert m, "no SUDO heredoc in the installer"
    return m.group(1).splitlines()


def test_sudoers_allows_exactly_start_and_stop_with_a_fixed_path():
    rules = [ln for ln in sudoers_text() if not ln.startswith("#") and ln.strip()]
    assert rules == [
        "Defaults!/usr/local/sbin/taos-llm secure_path=/usr/sbin:/usr/bin:/sbin:/bin",
        "@CTL_USER@ ALL=(root) NOPASSWD: /usr/local/sbin/taos-llm start, /usr/local/sbin/taos-llm stop",
    ]
    allow = rules[1].split("NOPASSWD:")[1]
    assert "*" not in allow and "ALL" not in allow
    assert "status" not in allow and "ask" not in allow


def test_installer_writes_sudoers_only_after_visudo_validates_it():
    body = "\n".join(active_lines(INSTALLER))
    assert "/etc/sudoers.d/50-taos-llm" in body
    v = body.index("visudo -c -f")
    assert v < body.index("install -m 0440 -o root -g root", v)
    # the user comes from tinyagentos.service and the install refuses without one
    assert "CTL_USER" in body
    assert 'die "controller user' in body
    # nothing installs the file before the user is known
    assert body.index('die "controller user') < body.index("50-taos-llm")
    assert "|| true" not in body[v - 200:v + 300]


def test_installer_puts_the_helper_in_usr_local_sbin_only():
    body = INSTALLER.read_text()
    assert "/usr/local/sbin/taos-llm" in body
    installs = [ln for ln in active_lines(INSTALLER) if ln.startswith("install ")]
    assert not [ln for ln in installs if "/usr/local/bin/taos-llm" in ln and "taos-llm-" not in ln]
    assert 'install -m 0755 "$HERE/bin/taos-llm" /usr/local/sbin/taos-llm' in installs
    assert "install -d -m 0755 /usr/local/sbin" in body


def test_user_placeholder_is_filled_with_a_real_user_name():
    """The rendered rule must be a plain user token: refuse anything sudoers would read as syntax."""
    body = INSTALLER.read_text()
    assert re.search(r"case \$CTL_USER in\s+\*\[!A-Za-z0-9_.-\]\*", body)


def installer_check(fn: str, value: str):
    """Run one of the installer's check functions on VALUE (installer sourced, main skipped)."""
    return subprocess.run(
        ["sh", "-c", '. "$0"; ' + fn + ' "$1"', str(INSTALLER), value],
        env=dict(os.environ, INSTALL_LLAMA_SOURCE_ONLY="1"), capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("ok", ["7835", "1", "80", "65535"])
def test_installer_accepts_a_real_port(ok):
    assert installer_check("check_port", ok).returncode == 0


@pytest.mark.parametrize("bad", [
    "7835; touch /tmp/x", "$(id)", "`id`", "7835\nFOO=1", "7835 ", "0", "65536", "123456", "007835",
    "-1", "", "7a", "7835|x",
])
def test_installer_refuses_a_bad_port(bad):
    p = installer_check("check_port", bad)
    assert p.returncode != 0 and "FAIL" in p.stderr and "port" in p.stderr


@pytest.mark.parametrize("ok", [
    "/srv/models", "/home/taos/data/llama-cpp/models", "/opt/a_b-c.d/m", "/a/b..c", "/a/..b",
])
def test_installer_accepts_a_plain_models_dir(ok):
    assert installer_check("check_models_dir", ok).returncode == 0


@pytest.mark.parametrize("bad", [
    "models", "./models", "", "/", "/srv/../etc", "/srv/..", "/../srv", "/srv/models/..",
    "/srv/$(id)", "/srv/`id`", "/srv; touch /tmp/x", "/srv/a|b", "/srv/a&b", "/srv/a b",
    "/srv/a\nb", "/srv/a\nExecStartPre=/bin/sh", "/srv/a\\b", "/srv/a'b", '/srv/a"b', "/srv/a*", "/srv/é",
])
def test_installer_refuses_a_bad_models_dir(bad):
    p = installer_check("check_models_dir", bad)
    assert p.returncode != 0 and "FAIL" in p.stderr and "models dir" in p.stderr


def test_installer_gives_owner_and_mode_to_the_models_dir_only():
    lines = active_lines(INSTALLER)
    owned = [ln for ln in lines if ln.startswith("install -d") and re.search(r"\s-[ogm]\s", ln)]
    # exactly one operand-carrying install -d with -o/-g/-m, and it names only the models dir
    models = [ln for ln in owned if "MODELS_DIR" in ln]
    assert len(models) == 1
    assert models[0].endswith('-m 0755 "$MODELS_DIR"')
    assert "dirname" not in models[0] and "PARENT" not in models[0]
    # the parent is created with a plain install -d, and only when it is missing
    assert 'install -d "$MODELS_PARENT"' in "\n".join(lines)
    body = "\n".join(lines)
    guard = body.index('if [ ! -d "$MODELS_PARENT" ]')
    assert guard < body.index('install -d "$MODELS_PARENT"') < body.index("fi", guard)
    assert body.index("chown", guard) < body.index(models[0])
    # no other directory-creating call hands the parent an owner or mode
    assert not [ln for ln in owned if ln is not models[0] and "PARENT" in ln]


def test_installer_validates_before_it_writes_anything():
    body = "\n".join(active_lines(INSTALLER))
    main = body.index("PORT=${PORT:-7835}")
    assert body.index('check_port "$PORT"') > main
    assert body.index('check_models_dir "$MODELS_DIR"') > body.index("MODELS_DIR=${MODELS_DIR:-$DATA_DIR")
    for sink in ("taos-llm.env.rendered", "install -d -o", "llama-cpp.service.rendered"):
        assert body.index('check_models_dir "$MODELS_DIR"') < body.index(sink)
        assert body.index('check_port "$PORT"') < body.index(sink)


# ---- taos-llm ask -------------------------------------------------------------

import http.server  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402


@pytest.fixture
def server():
    """A stub llama-server: serves POST /v1/chat/completions with whatever the test queues."""
    seen = []
    reply = {"status": 200, "body": {}}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.path, json.loads(self.rfile.read(n))))
            raw = json.dumps(reply["body"]).encode()
            self.send_response(reply["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield type("S", (), dict(port=srv.server_address[1], seen=seen, reply=reply))
    srv.shutdown()


def chat(content, reasoning="", finish="stop", tokens=7):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"completion_tokens": tokens}}


def ask(rig, server, *args, stdin=None, active=True, port=None):
    env = {"TAOS_LLAMACPP_PORT": str(port or server.port)}
    if active:
        (rig.tmp / "active").write_text("")
    elif (rig.tmp / "active").exists():
        (rig.tmp / "active").unlink()
    e = dict(os.environ, PATH=f"{rig.tmp / 'stubs'}:{os.environ['PATH']}",
             STUB_ACTIVE=str(rig.tmp / "active"), TAOS_LLM_TEST="1",
             TAOS_LLM_TEST_PATH=str(rig.tmp / "stubs"),
             TAOS_LLM_ENV=str(rig.tmp / "absent.env"), **env)
    return subprocess.run(["sh", str(HELPER), "ask", *args], env=e, input=stdin,
                          capture_output=True, text=True, timeout=30)


def test_ask_returns_the_answer(rig, server):
    server.reply["body"] = chat("Hello there, how are you", tokens=9)
    p = ask(rig, server, "--prompt", "Say hello in five words")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {"ok": True, "answer": "Hello there, how are you",
                                    "reasoning_used": False, "tokens": 9}
    path, req = server.seen[0]
    assert path == "/v1/chat/completions"
    assert req["messages"] == [{"role": "user", "content": "Say hello in five words"}]
    assert req["max_tokens"] == 512
    assert req["chat_template_kwargs"] == {"enable_thinking": False}
    assert req["model"] == "LFM3-1.7B-QAD-Q4_0"


def test_ask_reports_when_reasoning_was_used(rig, server):
    server.reply["body"] = chat("Paris", reasoning="the capital of France", tokens=30)
    j = json.loads(ask(rig, server, "--thinking", "on", "--prompt", "capital of France?").stdout)
    assert j["ok"] is True and j["reasoning_used"] is True
    assert server.seen[0][1]["chat_template_kwargs"] == {"enable_thinking": True}


def test_ask_reads_the_prompt_from_stdin_and_honours_max_tokens(rig, server):
    server.reply["body"] = chat("ok")
    p = ask(rig, server, "--max-tokens", "64", stdin="what is 2+2?\n")
    assert p.returncode == 0, p.stderr
    req = server.seen[0][1]
    assert req["messages"][0]["content"] == "what is 2+2?"
    assert req["max_tokens"] == 64


def test_ask_prompt_with_quotes_and_newlines_arrives_intact(rig, server):
    server.reply["body"] = chat("ok")
    prompt = 'say "hi"\nthen \'bye\' $(id) `id` \\n'
    assert ask(rig, server, "--prompt", prompt).returncode == 0
    assert server.seen[0][1]["messages"][0]["content"] == prompt


def test_ask_budget_exhausted_is_exit_4_never_an_empty_answer(rig, server):
    server.reply["body"] = chat("", reasoning="let me think about greetings", finish="length", tokens=20)
    p = ask(rig, server, "--max-tokens", "20", "--thinking", "on", "--prompt", "Say hello in five words")
    assert p.returncode == 4
    assert json.loads(p.stdout) == {"ok": False, "error": "reasoning budget exhausted before an answer",
                                    "finish_reason": "length"}


def test_ask_empty_content_with_a_normal_stop_is_a_plain_failure(rig, server):
    server.reply["body"] = chat("", finish="stop")
    p = ask(rig, server, "--prompt", "hi")
    assert p.returncode == 1
    j = json.loads(p.stdout)
    assert j["ok"] is False and j["error"] == "empty answer"


def test_ask_truncated_but_non_empty_answer_is_still_an_answer(rig, server):
    server.reply["body"] = chat("Hello there", finish="length")
    p = ask(rig, server, "--prompt", "hi")
    assert p.returncode == 0 and json.loads(p.stdout)["ok"] is True


def test_ask_server_error_and_garbage_are_exit_1(rig, server):
    server.reply.update(status=500, body={"error": "boom"})
    p = ask(rig, server, "--prompt", "hi")
    assert p.returncode == 1 and json.loads(p.stdout)["ok"] is False
    server.reply.update(status=200, body={"nope": 1})
    p = ask(rig, server, "--prompt", "hi")
    assert p.returncode == 1 and json.loads(p.stdout)["error"] == "unreadable server reply"


def test_ask_unit_not_active_is_exit_3_and_sends_nothing(rig, server):
    p = ask(rig, server, "--prompt", "hi", active=False)
    assert p.returncode == 3
    assert json.loads(p.stdout) == {"ok": False, "error": "not running"}
    assert server.seen == []


def test_ask_unit_active_but_nothing_listening_is_exit_3(rig, server):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    p = ask(rig, server, "--prompt", "hi", port=dead)
    assert p.returncode == 3
    assert json.loads(p.stdout) == {"ok": False, "error": "not running"}


@pytest.mark.parametrize("args", [
    ("--bogus", "x"), ("--thinking", "maybe", "--prompt", "hi"), ("--thinking",),
    ("--max-tokens", "abc", "--prompt", "hi"), ("--max-tokens", "0", "--prompt", "hi"),
    ("--max-tokens", "-5", "--prompt", "hi"), ("--prompt",), ("extra", "--prompt", "hi"),
])
def test_ask_unknown_or_malformed_options_are_exit_2_and_send_nothing(rig, server, args):
    p = ask(rig, server, *args, stdin="")
    assert p.returncode == 2, (p.stdout, p.stderr)
    assert server.seen == []
    assert p.stdout == ""


def test_ask_needs_no_root_and_never_calls_start_or_stop(rig, server):
    server.reply["body"] = chat("ok")
    ask(rig, server, "--prompt", "hi")
    calls = (rig.tmp / "calls").read_text() if (rig.tmp / "calls").exists() else ""
    assert "systemctl start" not in calls and "systemctl stop" not in calls


# ---- taos-llm-thermal -------------------------------------------------------

class Thermal:
    """A fake /sys/class/thermal. set() writes millidegrees like the kernel."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(exist_ok=True)

    def zone(self, n: int, typ: str, temp: str | None):
        d = self.root / f"thermal_zone{n}"
        d.mkdir(exist_ok=True)
        (d / "type").write_text(typ + "\n")
        if temp is None:
            (d / "temp").unlink(missing_ok=True)
        else:
            (d / "temp").write_text(temp + "\n")


@pytest.fixture
def thermal(rig):
    t = Thermal(rig.tmp / "thermal")
    state = rig.tmp / "tstate"

    def tick(deg: float | None, **kw):
        """Set the hottest cpu zone, run one guard decision, return (proc, signal calls)."""
        if deg is not None:
            t.zone(1, "cpu-1-0-thermal", str(int(deg * 1000)))
        rig.reset()
        p, calls = rig("taos-llm-thermal", "--once", env={
            "TAOS_LLM_THERMAL_DIR": str(t.root), "TAOS_LLM_THERMAL_STATE": str(state)})
        assert p.returncode == 0, p.stderr
        return p, [c for c in calls if c.startswith("systemctl")]

    tick.fake = t
    t.zone(0, "cpu-0-0-thermal", "40000")
    t.zone(2, "gpuss-0", "99000")        # not a cpu zone: must never count
    t.zone(3, "pm7325", "99000")
    return tick


STOP = "systemctl kill --signal=SIGSTOP llama-cpp.service"
CONT = "systemctl kill --signal=SIGCONT llama-cpp.service"
HALT = "systemctl stop llama-cpp.service"


def test_thermal_below_cap_does_nothing(thermal):
    p, calls = thermal(79.9)
    assert calls == []
    assert p.stderr == ""


def test_thermal_pauses_at_80_and_logs(thermal):
    p, calls = thermal(80)
    assert calls == [STOP]
    assert "pausing" in p.stderr and "80 C" in p.stderr


def test_thermal_hysteresis_then_resumes_at_75(thermal):
    assert thermal(81)[1] == [STOP]
    assert thermal(78)[1] == [], "between 75 and 80 a paused model stays paused"
    assert thermal(75.1)[1] == []
    p, calls = thermal(75)
    assert calls == [CONT]
    assert "resuming" in p.stderr
    # and it does not resume twice, nor pause again below 80
    assert thermal(74)[1] == []
    assert thermal(79)[1] == []


def test_thermal_does_not_signal_twice_while_hot(thermal):
    assert thermal(82)[1] == [STOP]
    assert thermal(85)[1] == []


def test_thermal_aborts_at_88(thermal):
    p, calls = thermal(88)
    assert calls == [HALT]
    assert "stopping" in p.stderr


def test_thermal_aborts_from_paused(thermal):
    assert thermal(81)[1] == [STOP]
    assert thermal(89)[1] == [HALT]
    # a model started again afterwards is not treated as still paused
    assert thermal(60)[1] == []
    assert thermal(80)[1] == [STOP]


def test_thermal_uses_the_hottest_cpu_zone_and_ignores_other_types(thermal):
    thermal.fake.zone(4, "cpu-5-1-thermal", "81000")
    thermal.fake.zone(5, "cpuss-0", "50000")
    p, calls = thermal(60)
    assert calls == [STOP], "the hottest of several cpu zones decides"
    assert "81 C" in p.stderr


def test_thermal_skips_an_unreadable_zone(thermal):
    thermal.fake.zone(4, "cpu-5-1-thermal", None)          # no temp file
    thermal.fake.zone(5, "cpu-6-0-thermal", "garbage")      # not a number
    thermal.fake.zone(6, "cpu-7-0-thermal", "-274000")      # not usable
    assert thermal(80)[1] == [STOP], "a bad zone must not hide the readable hot one"


def test_thermal_no_readable_zone_logs_once_and_does_nothing(thermal):
    thermal.fake.zone(0, "cpu-0-0-thermal", None)
    p, calls = thermal(None)
    assert calls == []
    assert p.stderr.count("no readable cpu thermal zone") == 1
    p, calls = thermal(None)
    assert calls == []
    assert p.stderr == "", "logged once, not on every poll"


def test_thermal_no_zone_directory_at_all(rig):
    p, calls = rig("taos-llm-thermal", "--once", env={
        "TAOS_LLM_THERMAL_DIR": str(rig.tmp / "nothing"),
        "TAOS_LLM_THERMAL_STATE": str(rig.tmp / "ts")})
    assert p.returncode == 0
    assert calls == []
    assert "no readable cpu thermal zone" in p.stderr


def test_thermal_never_pauses_forever_when_zones_vanish(thermal):
    assert thermal(82)[1] == [STOP]
    thermal.fake.zone(0, "cpu-0-0-thermal", None)
    thermal.fake.zone(1, "cpu-1-0-thermal", None)
    p, calls = thermal(None)
    assert calls == [CONT]
    assert "resuming" in p.stderr


# ---- taos-llm-idle ----------------------------------------------------------

BUSY = '[{"id":0,"is_processing":true}]'
IDLE = '[{"id":0,"is_processing":false}]'


@pytest.fixture
def idle(rig):
    state = rig.tmp / "istate"

    def tick(now: int, body: str | None, minutes: str = "10"):
        for f in rig.curl_dir.iterdir():
            f.unlink()
        if body is not None:
            (rig.curl_dir / "_slots").write_text(body)
        rig.reset()
        p, calls = rig("taos-llm-idle", "--once", env={
            "TAOS_LLM_IDLE_STATE": str(state), "TAOS_LLM_NOW": str(now),
            "TAOS_LLM_IDLE_MIN": minutes})
        assert p.returncode == 0, p.stderr
        assert any(c.startswith("curl") for c in calls), "the poll must actually read /slots"
        return p, [c for c in calls if c.startswith("systemctl")]

    tick.state = state
    return tick


def test_idle_stops_after_ten_idle_minutes(idle):
    assert idle(1000, IDLE)[1] == []
    assert idle(1000 + 599, IDLE)[1] == []
    p, calls = idle(1000 + 600, IDLE)
    assert calls == [HALT]
    assert "stopping" in p.stderr


def test_idle_busy_slot_restarts_the_clock(idle):
    idle(1000, IDLE)
    assert idle(1500, BUSY)[1] == []
    assert idle(1500 + 599, IDLE)[1] == []
    assert idle(1500 + 600, IDLE)[1] == [HALT]


def test_idle_honours_the_configured_minutes(idle):
    idle(0, IDLE, minutes="2")
    assert idle(119, IDLE, minutes="2")[1] == []
    assert idle(120, IDLE, minutes="2")[1] == [HALT]


def test_idle_bad_minutes_fall_back_to_ten(idle):
    idle(0, IDLE, minutes="soon")
    assert idle(599, IDLE, minutes="soon")[1] == []
    assert idle(600, IDLE, minutes="soon")[1] == [HALT]


def test_idle_unreadable_slots_means_busy_and_never_stops(idle):
    idle(0, None)
    p, calls = idle(100000, None)
    assert calls == []


def test_idle_slots_without_the_field_means_busy(idle):
    idle(0, '{"error":"model not loaded"}')
    assert idle(100000, '{"error":"model not loaded"}')[1] == []


def test_idle_one_busy_slot_among_idle_ones_is_busy(idle):
    both = '[{"id":0,"is_processing":false},{"id":1,"is_processing": true}]'
    idle(0, both)
    assert idle(100000, both)[1] == []


# A request that starts and ends between two polls: is_processing is false at
# both polls but the slots' work counters (id_task, n_decoded) moved.
def slots(task: int, decoded: int = 0, proc: bool = False) -> str:
    return json.dumps([{"id": 0, "id_task": task, "is_processing": proc,
                        "next_token": [{"n_decoded": decoded}]}])


def test_idle_a_request_between_polls_restarts_the_clock(idle):
    idle(1000, slots(5, 0))
    assert idle(1500, slots(6, 12))[1] == []          # ran and finished between polls
    assert idle(1500 + 599, slots(6, 12))[1] == []    # clock restarted at 1500
    assert idle(1500 + 600, slots(6, 12))[1] == [HALT]


def test_idle_tokens_decoded_alone_count_as_activity(idle):
    idle(1000, slots(5, 0))
    idle(1100, slots(5, 3))
    assert idle(1100 + 599, slots(5, 3))[1] == []
    assert idle(1100 + 600, slots(5, 3))[1] == [HALT]


def test_idle_unchanged_counters_for_the_full_limit_stop(idle):
    idle(1000, slots(5, 7))
    assert idle(1000 + 300, slots(5, 7))[1] == []
    assert idle(1000 + 599, slots(5, 7))[1] == []
    assert idle(1000 + 600, slots(5, 7))[1] == [HALT]


def test_idle_first_poll_with_no_stored_counters_never_stops(idle):
    # an old last_busy but no counters file: nothing to compare, so no stop
    idle.state.mkdir()
    (idle.state / "last_busy").write_text("0")
    assert idle(100000, slots(5, 7))[1] == []
    assert idle(100000 + 599, slots(5, 7))[1] == []
