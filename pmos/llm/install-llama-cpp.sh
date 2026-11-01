#!/bin/sh
# Install the handset's local LLM backend. Run as root ON THE DEVICE:
#
#     sudo sh pmos/llm/install-llama-cpp.sh
#
# Builds llama.cpp FROM SCRATCH at a pinned commit, downloads LFM3-1.7B and
# VERIFIES it against a pinned sha256, installs llama-server as taOS core's
# `llama-cpp` service (router mode, port 7835, models dir inside the
# controller's data dir), starts it, and proves it with a real tool-call turn.
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed in.
#
# Why build: Alpine's llama.cpp 0.6.0 (2026-10-06) knows lfm2 and lfm2moe but
# not lfm3, and core's scripts/install-llama-cpp.sh fetches a glibc release
# binary, which does not run on musl. LFM3 landed upstream in #31252; the pin
# below is at or past v0.8.0 (8642541b2), which also carries LFM3-Omni/Audio
# and the realtime tool calls, so voice or vision later needs no rebuild.
#
# Idempotent: a re-run with everything present skips the rebuild and the
# download, but re-verifies the model hash and only restarts the service if
# something it runs from changed.
#
# Options (environment):
#   TAOS_LLAMA_MODEL=q4_0|q8_0   which LFM3-1.7B quant (default q4_0, the QAD build)
#   TAOS_LLAMA_VULKAN=1          also build the Vulkan backend (default: CPU only)
#   TAOS_LLAMA_THREADS=N         llama-server threads (default 4, the big cores)
#   TAOS_LLAMA_PREFIX            install prefix (default /opt/taos-llm/llama.cpp)
#   TAOS_LLAMACPP_PORT, TAOS_LLAMACPP_MODELS_DIR   core's names, same defaults
#
# Fails loudly: set -eu, and no `|| true` on anything that matters.
set -eu

# ---- pins ----------------------------------------------------------------
LLAMA_URL=https://github.com/ggml-org/llama.cpp.git
LLAMA_TAG=b12020
LLAMA_COMMIT=0ec4027a2a0183de87c007069adddc06e665f7de
HF=https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF/resolve/main
case "${TAOS_LLAMA_MODEL:-q4_0}" in
  q4_0) MODEL_FILE=LFM3-1.7B-QAD-Q4_0.gguf
        MODEL_SHA256=43a7d09a0e9cba91b63eb7052488b9564569bddfd65754d2e426465e572a7452 ;;
  q8_0) MODEL_FILE=LFM3-1.7B-Q8_0.gguf
        MODEL_SHA256=8f6b24182258205e9e01e21f1f24a1f208c1e8115578825d202ce39976af20b6 ;;
  *) echo "install-llama-cpp: FAIL: TAOS_LLAMA_MODEL must be q4_0 or q8_0" >&2; exit 1 ;;
esac
MODEL_NAME=${MODEL_FILE%.gguf}

PREFIX="${TAOS_LLAMA_PREFIX:-/opt/taos-llm/llama.cpp}"
CACHE=/var/cache/taos-llm
SVC=llama-cpp
THREADS="${TAOS_LLAMA_THREADS:-4}"
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

# Sourced by the tests to reach verify_sha256 alone.
if [ "${INSTALL_LLAMA_SOURCE_ONLY:-0}" = 1 ]; then return 0; fi

[ "$(id -u)" -eq 0 ] || die "run as root"
case $THREADS in ''|*[!0-9]*) die "TAOS_LLAMA_THREADS must be a number" ;; esac
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
PORT="${TAOS_LLAMACPP_PORT:-$(ctl_env TAOS_LLAMACPP_PORT)}"; PORT=${PORT:-7835}
MODELS_DIR="${TAOS_LLAMACPP_MODELS_DIR:-$(ctl_env TAOS_LLAMACPP_MODELS_DIR)}"
DATA_DIR="$(ctl_env TAOS_DATA_DIR)"; DATA_DIR=${DATA_DIR:-$CTL_CWD/data}
MODELS_DIR=${MODELS_DIR:-$DATA_DIR/llama-cpp/models}
[ -d "$DATA_DIR" ] || die "controller data dir $DATA_DIR does not exist"
say "controller: user $CTL_USER, data dir $DATA_DIR"
say "core llama.cpp contract: port $PORT, models dir $MODELS_DIR"

# ---- 1. build dependencies (never touches the kernel) -----------------------
FEATURES=cpu
deps="build-base cmake samurai git linux-headers openssl-dev curl python3"
if [ "${TAOS_LLAMA_VULKAN:-0}" = 1 ]; then
  FEATURES=cpu+vulkan
  deps="$deps vulkan-loader-dev vulkan-headers glslang shaderc spirv-headers"
fi
need=""
for p in $deps; do apk info -e "$p" >/dev/null 2>&1 || need="$need $p"; done
if [ -n "$need" ]; then
  say "apk add$need"
  # shellcheck disable=SC2086
  apk add --no-progress $need
fi

# The models dir sits inside the controller's 0700 data dir and is the controller's.
mkdir -p "$PREFIX" "$CACHE/build" "$CACHE/dl"
install -d -o "$CTL_USER" -g "$(id -gn "$CTL_USER")" -m 0755 "$(dirname "$MODELS_DIR")" "$MODELS_DIR"

# ---- 2. llama.cpp, from source at the pinned commit ----------------------------
# `.features` is part of the skip check: a CPU build must be rebuilt, not
# skipped, when Vulkan is asked for (and the other way round).
if [ -x "$PREFIX/bin/llama-server" ] &&
   [ "$(cat "$PREFIX/.commit" 2>/dev/null || true)" = "$LLAMA_COMMIT" ] &&
   [ "$(cat "$PREFIX/.features" 2>/dev/null || true)" = "$FEATURES" ]; then
  say "llama.cpp $LLAMA_TAG ($FEATURES) already built, skipping the build"
else
  say "building llama.cpp $LLAMA_TAG ($LLAMA_COMMIT, $FEATURES) in $CACHE/build"
  B0=$(date +%s)
  src="$CACHE/build/llama.cpp"
  rm -rf "$src"
  git clone -q --depth 1 --branch "$LLAMA_TAG" "$LLAMA_URL" "$src"
  got=$(git -C "$src" rev-parse HEAD)
  [ "$got" = "$LLAMA_COMMIT" ] || die "tag $LLAMA_TAG is $got, pinned $LLAMA_COMMIT"
  vk=OFF; [ "$FEATURES" = cpu ] || vk=ON
  # GGML_NATIVE: build for this CPU (A78 dotprod/i8mm, which Q4_0 uses).
  cmake -S "$src" -B "$src/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DGGML_NATIVE=ON -DGGML_VULKAN=$vk \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_OPENSSL=ON
  # niced so the UI stays responsive while the cores compile
  nice -n 15 ninja -C "$src/build" -j6 llama-server llama-bench
  rm -f "$PREFIX/.commit" "$PREFIX/.features"
  rm -rf "$PREFIX/bin" "$PREFIX/lib"
  mkdir -p "$PREFIX/bin" "$PREFIX/lib"
  install -m 0755 "$src/build/bin/llama-server" "$src/build/bin/llama-bench" "$PREFIX/bin/"
  find "$src/build/bin" -maxdepth 1 -name '*.so*' -exec cp -a {} "$PREFIX/lib/" \;
  find "$src/build" -name 'lib*.so*' ! -path "$src/build/bin/*" -exec cp -a {} "$PREFIX/lib/" \;
  install -m 0644 "$src/LICENSE" "$PREFIX/LICENSE"
  printf '%s\n' "$LLAMA_COMMIT" > "$PREFIX/.commit"
  printf '%s\n' "$FEATURES" > "$PREFIX/.features"
  CHANGED=1
  say "build took $(( $(date +%s) - B0 )) s"
fi
# every dependency must resolve, or the service dies at start
if LD_LIBRARY_PATH="$PREFIX/lib" ldd "$PREFIX/bin/llama-server" 2>&1 | grep -q 'not found'; then
  LD_LIBRARY_PATH="$PREFIX/lib" ldd "$PREFIX/bin/llama-server" >&2
  die "llama-server has unresolved dependencies"
fi
LD_LIBRARY_PATH="$PREFIX/lib" "$PREFIX/bin/llama-server" --version 2>&1 | grep -q "commit $(printf %.7s "$LLAMA_COMMIT")" ||
  die "llama-server --version does not report the pinned commit"

# ---- 3. the model: verify, fetch only if needed, verify again -----------------
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

# ---- 4. the unit (a template, filled in here) --------------------------------
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@MODELS_DIR@|$MODELS_DIR|g" -e "s|@PORT@|$PORT|g" \
    -e "s|@THREADS@|$THREADS|g" -e "s|@USER@|$CTL_USER|g" \
  "$HERE/../systemd/llama-cpp.service" > "$CACHE/llama-cpp.service.rendered"
! grep -q '@[A-Z_]*@' "$CACHE/llama-cpp.service.rendered" || die "unit has an unfilled placeholder"
if ! cmp -s "$CACHE/llama-cpp.service.rendered" /etc/systemd/system/llama-cpp.service 2>/dev/null; then
  install -m 0644 "$CACHE/llama-cpp.service.rendered" /etc/systemd/system/llama-cpp.service
  CHANGED=1
fi

# ---- 5. start ------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SVC.service"
if [ "$CHANGED" -eq 1 ] || ! systemctl is-active --quiet "$SVC.service"; then
  say "(re)starting $SVC"
  systemctl restart "$SVC.service" || { journalctl -u "$SVC" -n 40 --no-pager >&2; die "$SVC failed to start"; }
else
  say "nothing changed and $SVC is active, not restarting"
fi
# core's own probe: GET /health answers 200 in router mode (llamacpp_is_running)
i=0
until curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -le 60 ] || { journalctl -u "$SVC" -n 40 --no-pager >&2; die "/health did not answer 200 in 60 s"; }
  sleep 1
done
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/v1/models" | grep -q "\"$MODEL_NAME\"" ||
  die "router does not list $MODEL_NAME in /v1/models"

# ---- 6. smoke: one tool-call turn through the model's own chat template ---------
# A tool call must come back STRUCTURED (message.tool_calls), not as <tool_call>
# text: that is the template + --jinja path the agent harnesses depend on.
PORT=$PORT MODEL_NAME=$MODEL_NAME python3 - <<'PYEOF' || die "smoke tool-call turn failed"
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

say "done in $(( $(date +%s) - START )) s: $SVC on 127.0.0.1:$PORT serving $MODEL_NAME"
