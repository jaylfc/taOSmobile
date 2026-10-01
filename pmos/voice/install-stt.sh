#!/bin/sh
# Install the handset's local speech-to-text engine. Run as root ON THE DEVICE:
#
#     sudo sh pmos/voice/install-stt.sh
#
# Builds sherpa-onnx FROM SCRATCH at a pinned commit (with TTS: install-tts.sh
# reuses this library rather than building a second copy), downloads the Parakeet-TDT
# 0.6b v3 int8 model and VERIFIES it against pinned sha256s, installs the
# daemon and its systemd unit, starts it, and proves it with a real decode.
# A FILE in this repo, not a hand-edit: a reflash wipes anything typed in.
#
# Idempotent: a re-run with everything present skips the rebuild and the
# download, but re-verifies every hash and only restarts the daemon if
# something it runs from changed.
#
# Fails loudly: set -eu, and no `|| true` on anything that matters.
#
# Layout (every root is overridable by an environment variable):
#   $TAOS_VOICE_PREFIX  (default /opt/taos-voice/stt)
#       lib/ bin/ test/ NOTICE stt.env   the engine, daemon, smoke wav
#   $TAOS_MODELS_ROOT/sherpa-onnx/parakeet/parakeet-tdt-0.6b-v3-int8/
#       tokens.txt + 3 *.int8.onnx       the controller's UNIFIED MODEL STORE
#                                        (tinyagentos installers/model_paths.py:
#                                        <root>/<backend>/<family>/<id>/<file>)
#   $TAOS_DATA_DIR/voice/stt/manifest.json   for the controller to read
#   /var/cache/taos-voice/               build tree + download scratch. NOT
#                                        /tmp: /tmp is a RAM tmpfs on this phone
#
# TAOS_DATA_DIR and TAOS_MODELS_ROOT are resolved from the RUNNING controller
# (its environment, else its working directory + /data and /models, which is
# what tinyagentos itself does) unless you set them; the install fails if it
# cannot resolve them.
set -eu

# ---- pins ----------------------------------------------------------------
SHERPA_URL=https://github.com/k2-fsa/sherpa-onnx.git
SHERPA_TAG=v1.13.8
SHERPA_COMMIT=11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf
MODEL_NAME=parakeet-tdt-0.6b-v3-int8
MODEL_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2
MODEL_ARCHIVE_SHA256=5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf
SHA_tokens_txt=d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d
SHA_encoder_int8_onnx=acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247
SHA_decoder_int8_onnx=179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e
SHA_joiner_int8_onnx=3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3
# the archive's 24 kHz English sample, kept only as the install smoke test
SHA_test_en_wav=148b936b43ce7c546a866e64da059f0458aee2d65e617f16e9d94f06e8d99ed6
PORT=6975

PREFIX="${TAOS_VOICE_PREFIX:-/opt/taos-voice/stt}"
CACHE=/var/cache/taos-voice
SVC=taos-sttd
HERE="$(cd "$(dirname "$0")" && pwd)"

die() { echo "install-stt: FAIL: $*" >&2; exit 1; }
say() { echo "install-stt: $*"; }

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
if [ "${INSTALL_STT_SOURCE_ONLY:-0}" = 1 ]; then return 0; fi

[ "$(id -u)" -eq 0 ] || die "run as root"
START=$(date +%s)
CHANGED=0

# ---- 0. where the controller keeps its data and its models --------------------
# tinyagentos: data_dir = $TAOS_DATA_DIR, else <project>/data; models_root =
# $TAOS_MODELS_ROOT, else <project>/models (app.py resolve_data_dir,
# installers/model_paths.py models_root). <project> is the controller's working
# directory.
CTL_PID=$(systemctl show -p MainPID --value tinyagentos.service 2>/dev/null || true)
ctl_env() { # ctl_env NAME: the controller's environment value, or nothing
  [ -n "$CTL_PID" ] && [ "$CTL_PID" != 0 ] && [ -r "/proc/$CTL_PID/environ" ] || return 0
  tr '\0' '\n' < "/proc/$CTL_PID/environ" | sed -n "s/^$1=//p" | head -n 1
}
ctl_cwd() { [ -n "$CTL_PID" ] && [ "$CTL_PID" != 0 ] && readlink "/proc/$CTL_PID/cwd" || true; }
if [ -z "${TAOS_DATA_DIR:-}" ]; then
  TAOS_DATA_DIR=$(ctl_env TAOS_DATA_DIR)
  [ -n "$TAOS_DATA_DIR" ] || { c=$(ctl_cwd); [ -z "$c" ] || TAOS_DATA_DIR=$c/data; }
fi
if [ -z "${TAOS_MODELS_ROOT:-}" ]; then
  TAOS_MODELS_ROOT=$(ctl_env TAOS_MODELS_ROOT)
  [ -n "$TAOS_MODELS_ROOT" ] || { c=$(ctl_cwd); [ -z "$c" ] || TAOS_MODELS_ROOT=$c/models; }
fi
[ -n "$TAOS_DATA_DIR" ] && [ -d "$TAOS_DATA_DIR" ] ||
  die "cannot resolve the controller's data dir (got '${TAOS_DATA_DIR:-}'); set TAOS_DATA_DIR"
[ -n "$TAOS_MODELS_ROOT" ] && [ -d "$TAOS_MODELS_ROOT" ] ||
  die "cannot resolve the controller's model store (got '${TAOS_MODELS_ROOT:-}'); set TAOS_MODELS_ROOT"
DATA_OWNER=$(stat -c %U:%G "$TAOS_DATA_DIR")
MODELS_OWNER=$(stat -c %U:%G "$TAOS_MODELS_ROOT")
MODEL_DIR="$TAOS_MODELS_ROOT/sherpa-onnx/parakeet/$MODEL_NAME"
MANIFEST_DIR="$TAOS_DATA_DIR/voice/stt"
say "controller data dir:  $TAOS_DATA_DIR ($DATA_OWNER)"
say "controller model store: $TAOS_MODELS_ROOT ($MODELS_OWNER) -> $MODEL_DIR"

# ---- 1. build dependencies (never touches the kernel) -----------------------
need=""
for p in build-base cmake samurai git linux-headers onnxruntime onnxruntime-dev python3 curl bzip2 tar; do
  apk info -e "$p" >/dev/null 2>&1 || need="$need $p"
done
if [ -n "$need" ]; then
  say "apk add$need"
  # shellcheck disable=SC2086
  apk add --no-progress $need
fi

# ---- 2. the service user ---------------------------------------------------
if ! getent passwd taos-stt >/dev/null; then
  say "creating system user taos-stt"
  adduser -S -D -H -h /var/empty -s /sbin/nologin taos-stt
fi
id taos-stt >/dev/null || die "user taos-stt does not exist"
[ "$(id -u taos-stt)" -ne 0 ] || die "taos-stt resolved to uid 0"

mkdir -p "$PREFIX/lib" "$PREFIX/test" "$PREFIX/bin" "$MODEL_DIR" "$MANIFEST_DIR" "$CACHE/build" "$CACHE/dl"
chmod 0755 "$PREFIX" "$PREFIX/lib" "$PREFIX/test" "$PREFIX/bin"
# the model store is the controller's: its files are owned by the controller user
chown "$MODELS_OWNER" "$TAOS_MODELS_ROOT/sherpa-onnx" "$TAOS_MODELS_ROOT/sherpa-onnx/parakeet" "$MODEL_DIR"
chmod 0755 "$MODEL_DIR"
chown "$DATA_OWNER" "$TAOS_DATA_DIR/voice" "$MANIFEST_DIR"
chmod 0755 "$TAOS_DATA_DIR/voice" "$MANIFEST_DIR"

# ---- 3. sherpa-onnx, from source at the pinned commit -----------------------
LIB="$PREFIX/lib/libsherpa-onnx-c-api.so"
# The library is SHARED with taos-ttsd (install-tts.sh loads this same file and
# never builds its own), so it is built with TTS ON. `.features` records that:
# a library built before TTS was switched on carries the right `.commit` but no
# `.features`, and must be rebuilt rather than skipped, because its TTS entry
# points are stubs that log "TTS is not enabled" and return NULL.
SHERPA_FEATURES=tts
if [ -f "$LIB" ] && [ "$(cat "$PREFIX/lib/.commit" 2>/dev/null || true)" = "$SHERPA_COMMIT" ] &&
   [ "$(cat "$PREFIX/lib/.features" 2>/dev/null || true)" = "$SHERPA_FEATURES" ]; then
  say "sherpa-onnx $SHERPA_COMMIT ($SHERPA_FEATURES) already built, skipping the build"
else
  say "building sherpa-onnx $SHERPA_TAG ($SHERPA_COMMIT) from scratch in $CACHE/build"
  B0=$(date +%s)
  rm -rf "$CACHE/build/sherpa-onnx"
  git clone -q --depth 1 --branch "$SHERPA_TAG" "$SHERPA_URL" "$CACHE/build/sherpa-onnx"
  got=$(git -C "$CACHE/build/sherpa-onnx" rev-parse HEAD)
  [ "$got" = "$SHERPA_COMMIT" ] || die "tag $SHERPA_TAG is $got, pinned $SHERPA_COMMIT"
  # The C API as a shared library on the system onnxruntime, with TTS (for
  # taos-ttsd; this pulls espeak-ng and piper-phonemize in STATICALLY, at the
  # hashes sherpa's own cmake pins). No CLI binaries, no python/websocket/portaudio.
  SHERPA_ONNXRUNTIME_INCLUDE_DIR=/usr/include/onnxruntime SHERPA_ONNXRUNTIME_LIB_DIR=/usr/lib \
  cmake -S "$CACHE/build/sherpa-onnx" -B "$CACHE/build/sherpa-onnx/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="-include cstdint" -DBUILD_SHARED_LIBS=ON \
    -DSHERPA_ONNX_ENABLE_C_API=ON -DSHERPA_ONNX_ENABLE_BINARY=OFF -DSHERPA_ONNX_ENABLE_TTS=ON \
    -DSHERPA_ONNX_ENABLE_PYTHON=OFF -DSHERPA_ONNX_ENABLE_TESTS=OFF \
    -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF \
    -DSHERPA_ONNX_USE_PRE_INSTALLED_ONNXRUNTIME_IF_AVAILABLE=ON
  # niced so the UI stays responsive while 6 cores compile
  nice -n 15 ninja -C "$CACHE/build/sherpa-onnx/build" -j6 sherpa-onnx-c-api
  built="$CACHE/build/sherpa-onnx/build/lib/libsherpa-onnx-c-api.so"
  [ -f "$built" ] || die "build finished but $built is missing"
  rm -f "$PREFIX/lib/.commit" "$PREFIX/lib/.features"
  install -m 0755 "$built" "$LIB.new"
  mv -f "$LIB.new" "$LIB"
  printf '%s\n' "$SHERPA_COMMIT" > "$PREFIX/lib/.commit"
  printf '%s\n' "$SHERPA_FEATURES" > "$PREFIX/lib/.features"
  chmod 0644 "$PREFIX/lib/.commit" "$PREFIX/lib/.features"
  CHANGED=1
  say "build took $(( $(date +%s) - B0 )) s"
fi
# every dependency of the library must resolve, or the daemon dies at load
if ldd "$LIB" 2>&1 | grep -q 'not found'; then
  ldd "$LIB" >&2
  die "$LIB has unresolved dependencies"
fi
ORT=$(ldd "$LIB" | awk '/libonnxruntime/ {print $3}')
[ -n "$ORT" ] || die "$LIB does not link libonnxruntime"
# the TTS-OFF build compiles its TTS stubs with this message; the real code never has it
! grep -q "TTS is not enabled" "$LIB" || die "$LIB was built without TTS (taos-ttsd needs it)"

# ---- 4. the model: verify, fetch only if needed, verify again -----------------
# name:hash pairs, with the directory each lives in
model_pairs() {
  echo "$MODEL_DIR/tokens.txt:$SHA_tokens_txt $MODEL_DIR/encoder.int8.onnx:$SHA_encoder_int8_onnx" \
       "$MODEL_DIR/decoder.int8.onnx:$SHA_decoder_int8_onnx $MODEL_DIR/joiner.int8.onnx:$SHA_joiner_int8_onnx" \
       "$PREFIX/test/test_en.wav:$SHA_test_en_wav"
}
verify_all() { # verify_all [quiet]
  for pair in $(model_pairs); do
    if [ "${1:-}" = quiet ]; then verify_sha256 "${pair%:*}" "${pair##*:}" 2>/dev/null || return 1
    else verify_sha256 "${pair%:*}" "${pair##*:}" || return 1; fi
  done
}
if verify_all quiet; then
  say "model files present with the pinned hashes, skipping the download"
else
  say "downloading $MODEL_URL"
  rm -rf "$CACHE/dl/x"; mkdir -p "$CACHE/dl/x"
  curl -fsSL --retry 3 --retry-delay 5 -o "$CACHE/dl/model.tar.bz2" "$MODEL_URL"
  verify_sha256 "$CACHE/dl/model.tar.bz2" "$MODEL_ARCHIVE_SHA256" || die "model archive hash mismatch"
  tar -xjf "$CACHE/dl/model.tar.bz2" -C "$CACHE/dl/x"
  src="$CACHE/dl/x/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
  [ -d "$src" ] || die "archive did not contain the expected directory"
  for f in tokens.txt encoder.int8.onnx decoder.int8.onnx joiner.int8.onnx; do
    install -m 0644 -o "${MODELS_OWNER%:*}" -g "${MODELS_OWNER#*:}" "$src/$f" "$MODEL_DIR/$f.new"
    mv -f "$MODEL_DIR/$f.new" "$MODEL_DIR/$f"
  done
  install -m 0644 "$src/test_wavs/en.wav" "$PREFIX/test/test_en.wav"
  rm -rf "$CACHE/dl/x" "$CACHE/dl/model.tar.bz2"
  CHANGED=1
fi
# the loud pass, always: a re-run re-verifies even when it skipped the download
verify_all || die "a model file failed verification"
say "model hashes verified"

# ---- 5. daemon, unit, notice -------------------------------------------------
put() { # put SRC DST MODE : install if different, flag a change
  if ! cmp -s "$1" "$2" 2>/dev/null; then install -D -m "$3" "$1" "$2"; CHANGED=1; fi
}
put "$HERE/taos-sttd" "$PREFIX/bin/taos-sttd" 0755
put "$HERE/NOTICE-STT.md" "$PREFIX/NOTICE" 0644
# the unit is a template: prefix, model dir and port are filled in here
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@MODEL_DIR@|$MODEL_DIR|g" -e "s|@PORT@|$PORT|g" \
  "$HERE/../systemd/taos-sttd.service" > "$CACHE/taos-sttd.service.rendered"
! grep -q '@[A-Z_]*@' "$CACHE/taos-sttd.service.rendered" || die "unit has an unfilled placeholder"
put "$CACHE/taos-sttd.service.rendered" /etc/systemd/system/taos-sttd.service 0644

# ---- 6. manifest, for the controller (in its data dir, readable by it) ---------
SHA_tokens_txt=$SHA_tokens_txt SHA_encoder=$SHA_encoder_int8_onnx SHA_decoder=$SHA_decoder_int8_onnx \
SHA_joiner=$SHA_joiner_int8_onnx SHERPA_COMMIT=$SHERPA_COMMIT PORT=$PORT MODEL_NAME=$MODEL_NAME \
MODEL_URL=$MODEL_URL MODEL_ARCHIVE_SHA256=$MODEL_ARCHIVE_SHA256 ORT=$ORT PREFIX=$PREFIX \
MODEL_DIR=$MODEL_DIR MANIFEST_DIR=$MANIFEST_DIR DATA_OWNER=$DATA_OWNER \
python3 - <<'PYEOF'
import json, os, shutil
e = os.environ
m = {
    "engine": "sherpa-onnx",
    "model": e["MODEL_NAME"],
    "files": {
        "tokens.txt": e["SHA_tokens_txt"],
        "encoder.int8.onnx": e["SHA_encoder"],
        "decoder.int8.onnx": e["SHA_decoder"],
        "joiner.int8.onnx": e["SHA_joiner"],
    },
    "port": int(e["PORT"]),
    "sherpa_commit": e["SHERPA_COMMIT"],
    "model_dir": e["MODEL_DIR"],
    "install_prefix": e["PREFIX"],
    "model_url": e["MODEL_URL"],
    "model_archive_sha256": e["MODEL_ARCHIVE_SHA256"],
    "onnxruntime": os.path.realpath(e["ORT"]),
    "license": "CC-BY-4.0 (model); see NOTICE",
}
tmp = e["MANIFEST_DIR"] + "/manifest.json.new"
with open(tmp, "w") as f:
    json.dump(m, f, indent=2, sort_keys=True)
    f.write("\n")
os.chmod(tmp, 0o644)
user, group = e["DATA_OWNER"].split(":")
shutil.chown(tmp, user, group)
os.replace(tmp, e["MANIFEST_DIR"] + "/manifest.json")
PYEOF

# ---- 7. start ------------------------------------------------------------------
systemctl daemon-reload
systemctl enable "$SVC.service"
if [ "$CHANGED" -eq 1 ] || ! systemctl is-active --quiet "$SVC.service"; then
  say "(re)starting $SVC"
  # restart, not `enable --now`: --now leaves a running OLD daemon serving
  systemctl restart "$SVC.service" || { journalctl -u "$SVC" -n 40 --no-pager >&2; die "$SVC failed to start"; }
else
  say "nothing changed and $SVC is active, not restarting"
fi

# Type=notify returns once the model is loaded; still prove it on the wire.
i=0
until curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -le 60 ] || { journalctl -u "$SVC" -n 40 --no-pager >&2; die "/health did not answer 200 in 60 s"; }
  sleep 1
done
curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health"; echo

# ---- 8. smoke decode: a real transcription through the real engine -------------
# (a ctypes layout that drifted from the pinned c-api.h would fail HERE)
TEXT=$(PORT=$PORT WAV="$PREFIX/test/test_en.wav" python3 - <<'PYEOF'
import array, http.client, json, os, wave
w = wave.open(os.environ["WAV"])
assert w.getnchannels() == 1 and w.getsampwidth() == 2, "smoke wav is not mono 16-bit"
rate, a = w.getframerate(), array.array("h", w.readframes(w.getnframes()))
n = int(len(a) * 16000 / rate)               # linear resample to 16 kHz
out = array.array("h")
for i in range(n):
    x = i * rate / 16000.0
    j = int(x); f = x - j
    b = a[j + 1] if j + 1 < len(a) else a[j]
    out.append(int(a[j] * (1 - f) + b * f))
c = http.client.HTTPConnection("127.0.0.1", int(os.environ["PORT"]), timeout=60)
c.request("POST", "/stt", out.tobytes())
r = c.getresponse()
body = r.read()
assert r.status == 200, "POST /stt -> %d %r" % (r.status, body)
print(json.loads(body)["text"])
PYEOF
) || die "smoke decode request failed"
[ -n "$TEXT" ] || die "smoke decode returned empty text"
say "smoke decode: $TEXT"

say "done in $(( $(date +%s) - START )) s: $SVC on 127.0.0.1:$PORT"
