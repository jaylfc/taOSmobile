#!/bin/sh
# Install the handset's OPT-IN on-device model. Run as root ON THE DEVICE:
#
#     sudo sh pmos/llm/install-llama-cpp.sh
#
# Phone agents use the taOS host's models over the network by default. LFM3-1.7B
# is the one supported on-device model, for offline use and quick replies, so
# this installer INSTALLS but never enables or starts anything. Start it with
# `sudo taos-llm start`; it stops itself after TAOS_LLM_IDLE_MIN idle minutes.
#
# It installs:
#   - Alpine's llama-server + libggml-cpu packages (llama.cpp 1.0.0 on edge,
#     which has LFM3 support; CPU only, no GPU backend),
#   - LFM3-1.7B, downloaded and VERIFIED against a pinned sha256, into the
#     controller's models dir,
#   - llama-cpp.service (taOS core's name; router mode, port 7835, ctx 16384,
#     2 threads on cpu5,6), never enabled,
#   - /usr/local/sbin/taos-llm (start|stop need root, status|ask do not),
#     /etc/sudoers.d/50-taos-llm (the controller user may run exactly
#     `sudo -n taos-llm start` and `stop`), taos-llm-thermal (SIGSTOP at 80 C, SIGCONT
#     at 75 C, stop at 88 C) and taos-llm-idle (idle auto-stop) with their units,
#   - /usr/share/taos/models/lfm3-1.7b.json, the licence the controller shows
#     in Settings.
#
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed in.
#
# Idempotent. A re-run never starts the service; if it is running while its unit
# or binary changed, the script says so and leaves the restart to you.
#
# Options (environment):
#   TAOS_LLAMA_MODEL=q4_0|q8_0   which LFM3-1.7B quant (default q4_0, the QAD build)
#   TAOS_LLM_IDLE_MIN=N          idle minutes before the auto-stop (default 10)
#   TAOS_LLM_SMOKE=1             after installing, start the model, run one
#                                tool-call turn, and stop it again (default: off)
#   TAOS_LLAMACPP_PORT, TAOS_LLAMACPP_MODELS_DIR   core's names, same defaults
#
# Fails loudly: set -eu, and no `|| true` on anything that matters.
set -eu

# ---- pins ----------------------------------------------------------------
HF=https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/main
case "${TAOS_LLAMA_MODEL:-q4_0}" in
  q4_0) MODEL_FILE=LFM3-1.7B-QAD-Q4_0.gguf
        MODEL_SHA256=43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452 ;;
  q8_0) MODEL_FILE=LFM3-1.7B-Q8_0.gguf
        MODEL_SHA256=8f6b24182258205e9e01e21f1f24a1f208c1e8115578825d202ce39976af20b6 ;;
  *) echo "install-llama-cpp: FAIL: TAOS_LLAMA_MODEL must be q4_0 or q8_0" >&2; exit 1 ;;
esac
MODEL_NAME=${MODEL_FILE%.gguf}

CACHE=/var/cache/taos-llm
SVC=llama-cpp
IDLE_MIN="${TAOS_LLM_IDLE_MIN:-10}"
HERE="$(cd "$(dirname "$0")" && pwd)"

die() { echo "install-llama-cpp: FAIL: $*" >&2; exit 1; }
say() { echo "install-llama-cpp: $*"; }

# verify_sha256 FILE WANT_HEX64 -> 0 if FILE hashes to WANT, else 1 (loudly).
verify_sha256() {
  _f=$1; _want=$2
  case $_want in
    *[!0-9a-f]*|"") echo "verify_sha256: bad pinned hash '$_want'" >&2; return 1 ;;
  esac
  [ "${#_want}" -eq 64 ] || { echo "verify_sha256: pinned hash is not 64 hex chars" >&2; return 1; }
  [ -f "$_f" ] || { echo "verify_sha256: $_f is missing" >&2; return 1; }
  _got=$(sha256sum "$_f") || return 1
  _got=${_got%% *}
  if [ "$_got" != "$_want" ]; then
    echo "verify_sha256: MISMATCH $_f: want $_want got $_got" >&2
    return 1
  fi
  return 0
}

# check_port VALUE: a TCP port, digits only, 1..65535, no leading zero. The value
# lands in /etc/taos-llm.env and the unit file, so anything else is refused.
check_port() {
  case $1 in
    ''|*[!0-9]*|0*) die "port '$1' is not a plain number 1-65535" ;;
  esac
  [ "${#1}" -le 5 ] && [ "$1" -le 65535 ] || die "port '$1' is not in 1-65535"
}

# check_models_dir VALUE: an absolute path of [A-Za-z0-9._/-] with no '..'
# component. It is substituted into the unit file, so nothing sed or systemd
# would read as syntax (and no newline) gets through.
check_models_dir() {
  case $1 in
    /*) ;;
    *) die "models dir '$1' is not an absolute path" ;;
  esac
  case $1 in
    *[!A-Za-z0-9._/-]*) die "models dir has characters outside A-Za-z0-9._/-" ;;
  esac
  case "$1/" in
    */../*) die "models dir '$1' has a '..' component" ;;
  esac
  [ "$1" != / ] || die "models dir cannot be /"
}

# Sourced by the tests to reach verify_sha256, check_port and check_models_dir alone.
if [ "${INSTALL_LLAMA_SOURCE_ONLY:-0}" = 1 ]; then return 0; fi

[ "$(id -u)" -eq 0 ] || die "run as root"
case $IDLE_MIN in ''|*[!0-9]*|0) die "TAOS_LLM_IDLE_MIN must be a positive number of minutes" ;; esac
START=$(date +%s)
CHANGED=0

# ---- 0. the controller: its user, data dir and core's llama.cpp settings -------
# Core's defaults (installers/llamacpp_installer.py, scripts/install-llama-cpp.sh):
# port $TAOS_LLAMACPP_PORT else 7835, models dir $TAOS_LLAMACPP_MODELS_DIR else
# <project>/data/llama-cpp/models. <project> is the controller's working dir.
CTL_PID=$(systemctl show -p MainPID --value tinyagentos.service 2>/dev/null || true)
[ -n "$CTL_PID" ] && [ "$CTL_PID" != 0 ] || die "tinyagentos.service is not running"
ctl_env() { # ctl_env NAME: the controller's environment value, or nothing
  [ -r "/proc/$CTL_PID/environ" ] || return 0
  tr '\0' '\n' < "/proc/$CTL_PID/environ" | sed -n "s/^$1=//p" | head -n 1
}
CTL_CWD=$(readlink "/proc/$CTL_PID/cwd") || die "cannot read the controller's working dir"
CTL_USER=$(systemctl show -p User --value tinyagentos.service)
[ -n "$CTL_USER" ] && [ "$CTL_USER" != root ] || die "controller user is '${CTL_USER:-}', expected a non-root user"
# It is written into sudoers: only a plain account name, nothing sudoers would read as syntax.
case $CTL_USER in
  *[!A-Za-z0-9_.-]*|-*) die "controller user '$CTL_USER' is not a plain account name; refusing to write sudoers" ;;
esac
PORT="${TAOS_LLAMACPP_PORT:-$(ctl_env TAOS_LLAMACPP_PORT)}"; PORT=${PORT:-7835}
MODELS_DIR="${TAOS_LLAMACPP_MODELS_DIR:-$(ctl_env TAOS_LLAMACPP_MODELS_DIR)}"
DATA_DIR="$(ctl_env TAOS_DATA_DIR)"; DATA_DIR=${DATA_DIR:-$CTL_CWD/data}
MODELS_DIR=${MODELS_DIR:-$DATA_DIR/llama-cpp/models}
check_port "$PORT"
check_models_dir "$MODELS_DIR"
[ -d "$DATA_DIR" ] || die "controller data dir $DATA_DIR does not exist"
say "controller: user $CTL_USER, data dir $DATA_DIR"
say "core llama.cpp contract: port $PORT, models dir $MODELS_DIR"

# ---- 1. llama.cpp: Alpine's package (never touches the kernel) --------------------
# Alpine edge ships llama.cpp 1.0.0, which knows LFM3; the old pinned source
# build is gone. The CPU backend is a separate package. The Vulkan backend is
# deliberately not installed: this service is CPU only.
if ! apk info -e llama-server >/dev/null 2>&1 || ! apk info -e libggml-cpu >/dev/null 2>&1; then
  say "apk add llama-server libggml-cpu"
  apk add --no-interactive llama-server libggml-cpu ||
    die "apk could not install llama-server (it is in Alpine edge community; is that repository enabled?)"
  CHANGED=1
fi
[ -x /usr/bin/llama-server ] || die "/usr/bin/llama-server is missing after apk add"
say "$(/usr/bin/llama-server --version 2>&1 | head -n 2 | tr '\n' ' ')"

# The models dir sits inside the controller's 0700 data dir and is the controller's.
mkdir -p "$CACHE/dl"
install -d -o "$CTL_USER" -g "$(id -gn "$CTL_USER")" -m 0755 "$(dirname "$MODELS_DIR")" "$MODELS_DIR"

# ---- 2. the model: verify, fetch only if needed, verify again -----------------
MODEL="$MODELS_DIR/$MODEL_FILE"
if verify_sha256 "$MODEL" "$MODEL_SHA256" 2>/dev/null; then
  say "$MODEL_FILE present with the pinned hash, skipping the download"
else
  say "downloading $HF/$MODEL_FILE"
  curl -fsSL --retry 3 --retry-delay 5 -o "$CACHE/dl/$MODEL_FILE" "$HF/$MODEL_FILE"
  verify_sha256 "$CACHE/dl/$MODEL_FILE" "$MODEL_SHA256" || die "model hash mismatch"
  install -m 0644 -o "$CTL_USER" -g "$(id -gn "$CTL_USER")" "$CACHE/dl/$MODEL_FILE" "$MODEL.new"
  mv -f "$MODEL.new" "$MODEL"
  rm -f "$CACHE/dl/$MODEL_FILE"
  CHANGED=1
fi
verify_sha256 "$MODEL" "$MODEL_SHA256" || die "$MODEL failed verification"
say "model hash verified: $MODEL_NAME"

# ---- 3. helper, guards, settings file, licence ---------------------------------
# /usr/local/sbin does not exist on a fresh postmarketOS install.
install -d -m 0755 /usr/local/sbin /usr/local/bin /usr/share/taos/models
install -m 0755 "$HERE/bin/taos-llm" /usr/local/sbin/taos-llm
for f in taos-llm-thermal taos-llm-idle; do
  install -m 0755 "$HERE/bin/$f" "/usr/local/bin/$f"
done
rm -f /usr/local/bin/taos-llm   # an earlier install put the helper here
install -m 0644 "$HERE/lfm3-1.7b.json" /usr/share/taos/models/lfm3-1.7b.json
# Read by taos-llm (port, model) and, as EnvironmentFile, by taos-llm-idle.
printf 'TAOS_LLAMACPP_PORT=%s\nTAOS_LLM_MODEL=%s\nTAOS_LLM_IDLE_MIN=%s\n' \
  "$PORT" "$MODEL_NAME" "$IDLE_MIN" > "$CACHE/taos-llm.env.rendered"
install -m 0644 "$CACHE/taos-llm.env.rendered" /etc/taos-llm.env

# ---- 3b. sudoers: the controller may start and stop the model, nothing else ----
# CTL_USER came from tinyagentos.service above (the installer already refused
# without one). Validated with visudo before it is put in place: a broken file
# in /etc/sudoers.d breaks sudo for everyone.
T="$CACHE/50-taos-llm.rendered"
cat > "$T.in" <<'SUDO'
# taOS controller -> the on-device model. Exactly these two commands, these arguments.
# The tool runs as root: sudo gives it a fixed PATH whatever the caller had.
Defaults!/usr/local/sbin/taos-llm secure_path=/usr/sbin:/usr/bin:/sbin:/bin
@CTL_USER@ ALL=(root) NOPASSWD: /usr/local/sbin/taos-llm start, /usr/local/sbin/taos-llm stop
SUDO
sed "s|@CTL_USER@|$CTL_USER|" "$T.in" > "$T"
rm -f "$T.in"
chmod 0440 "$T"
command -v visudo >/dev/null 2>&1 || die "visudo is missing; refusing to install sudoers unchecked"
visudo -c -f "$T" >/dev/null || die "the sudoers file for $CTL_USER does not validate; not installed"
install -m 0440 -o root -g root "$T" /etc/sudoers.d/50-taos-llm
rm -f "$T"

# ---- 4. the units (a template, filled in here) -------------------------------
sed -e "s|@MODELS_DIR@|$MODELS_DIR|g" -e "s|@PORT@|$PORT|g" -e "s|@USER@|$CTL_USER|g" \
  "$HERE/../systemd/llama-cpp.service" > "$CACHE/llama-cpp.service.rendered"
! grep -q '@[A-Z_]*@' "$CACHE/llama-cpp.service.rendered" || die "unit has an unfilled placeholder"
if ! cmp -s "$CACHE/llama-cpp.service.rendered" /etc/systemd/system/llama-cpp.service 2>/dev/null; then
  install -m 0644 "$CACHE/llama-cpp.service.rendered" /etc/systemd/system/llama-cpp.service
  CHANGED=1
fi
for u in taos-llm-thermal taos-llm-idle; do
  install -m 0644 "$HERE/../systemd/$u.service" "/etc/systemd/system/$u.service"
done
systemctl daemon-reload
# NOT enabled and NOT started: opt-in. `sudo taos-llm start` brings it up.
if systemctl is-active --quiet "$SVC.service" && [ "$CHANGED" -eq 1 ]; then
  say "$SVC is running and something it runs from changed; restart it with: sudo taos-llm stop && sudo taos-llm start"
fi

# ---- 5. optional smoke: one tool-call turn, then stopped again ------------------
# A tool call must come back STRUCTURED (message.tool_calls), not as <tool_call>
# text: that is the template + --jinja path the agent harnesses depend on.
if [ "${TAOS_LLM_SMOKE:-0}" = 1 ]; then
  was_active=0; systemctl is-active --quiet "$SVC.service" && was_active=1
  say "smoke: taos-llm start"
  /usr/local/sbin/taos-llm start >/dev/null || { journalctl -u "$SVC" -n 40 --no-pager >&2; die "$SVC failed to start"; }
  curl -fsS --max-time 5 "http://127.0.0.1:$PORT/v1/models" | grep -q "\"$MODEL_NAME\"" ||
    { [ "$was_active" -eq 1 ] || /usr/local/sbin/taos-llm stop >/dev/null; die "router does not list $MODEL_NAME in /v1/models"; }
  rc=0
  PORT=$PORT MODEL_NAME=$MODEL_NAME python3 - <<'PYEOF' || rc=$?
import http.client, json, os, time
body = {
    "model": os.environ["MODEL_NAME"],
    "messages": [{"role": "user", "content": "What is the weather in Paris right now? Use the tool."}],
    "tools": [{"type": "function", "function": {
        "name": "get_weather", "description": "Current weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                       "required": ["city"]}}}],
    "max_tokens": 128,
}
t0 = time.time()
c = http.client.HTTPConnection("127.0.0.1", int(os.environ["PORT"]), timeout=300)
c.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
r = c.getresponse()
raw = r.read()
assert r.status == 200, "POST /v1/chat/completions -> %d %r" % (r.status, raw[:400])
msg = json.loads(raw)["choices"][0]["message"]
calls = msg.get("tool_calls") or []
assert calls, "no structured tool call; content was %r" % (msg.get("content"),)
fn = calls[0]["function"]
assert fn["name"] == "get_weather", "called %r" % fn["name"]
args = json.loads(fn["arguments"])
assert "paris" in str(args.get("city", "")).lower(), "arguments %r" % args
print("install-llama-cpp: smoke tool call: %s(%s) in %.1f s" % (fn["name"], fn["arguments"], time.time() - t0))
PYEOF
  [ "$was_active" -eq 1 ] || /usr/local/sbin/taos-llm stop >/dev/null
  [ "$rc" -eq 0 ] || die "smoke tool-call turn failed"
fi

say "done in $(( $(date +%s) - START )) s: $SVC installed, NOT enabled or started; run: sudo taos-llm start"
