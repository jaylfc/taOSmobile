#!/bin/sh
# Install the handset's local text-to-speech engine. Run as root ON THE DEVICE,
# AFTER install-stt.sh:
#
#     sudo sh pmos/voice/install-stt.sh     # once: builds the shared sherpa-onnx library
#     sudo sh pmos/voice/install-tts.sh
#
# Downloads a Piper voice (public domain, LibriVox) as packaged by sherpa-onnx,
# REFUSES it unless its MODEL_CARD names an allowed licence (public domain, CC0
# or CC BY), and VERIFIES it against pinned sha256s, installs the daemon and its
# systemd unit, starts it, and proves it with a real synthesis.
#
# Voice choice: TAOS_TTS_VOICE=medium|high (default medium). medium is
# en_GB-cori-medium, the default since Jay's 2026-10-02 decision because first
# audio is faster; high is en_GB-cori-high (larger, 115 MB), installed with
# TAOS_TTS_VOICE=high. Any other value is refused. Both are 22050 Hz, one
# speaker. Switching voice installs into a NEW model dir and leaves the other
# voice's dir (about 115 MB for high) in the model store: harmless, delete it by
# hand if wanted. There is no per-request voice parameter. A FILE in this repo, not a hand-edit: a reflash wipes
# anything typed in.
#
# ONE sherpa-onnx library, not two. install-stt.sh already builds
# libsherpa-onnx-c-api.so from source at a pinned commit (about the longest step
# of either install), with TTS enabled for this daemon. This script does NOT
# build a second copy, and does not factor the build into a shared helper
# either: that would move the STT library's path and its skip-the-rebuild stamp
# for no gain while there is one library and one builder. Instead it REQUIRES
# the STT install's library, and refuses it with a clear message unless its
# recorded commit equals SHERPA_COMMIT below (the same pin as install-stt.sh;
# test_taos_ttsd.py asserts the two match) and it was built with TTS. The
# consequence is an ordering rule: run install-stt.sh first, and re-run this
# after any install-stt.sh that moves the pin.
#
# Idempotent: a re-run with everything present skips the download, but
# re-verifies every hash and only restarts the daemon if something it runs from
# changed.
#
# Fails loudly: set -eu, and no `|| true` on anything that matters.
#
# Layout (every root is overridable by an environment variable):
#   $TAOS_TTS_PREFIX  (default /opt/taos-voice/tts)
#       bin/ NOTICE                       the daemon and its notice
#   $TAOS_STT_PREFIX  (default /opt/taos-voice/stt; must equal the
#                     TAOS_VOICE_PREFIX install-stt.sh used)
#       lib/libsherpa-onnx-c-api.so       the ONE engine library, READ here
#   $TAOS_MODELS_ROOT/sherpa-onnx/piper/<MODEL_NAME>/
#       <VOICE_FILE>.onnx <VOICE_FILE>.onnx.json tokens.txt MODEL_CARD
#       espeak-ng-data/
#                                         the controller's UNIFIED MODEL STORE
#                                         (tinyagentos installers/model_paths.py:
#                                         <root>/<backend>/<family>/<id>/<file>)
#   $TAOS_DATA_DIR/voice/tts/manifest.json   for the controller to read
#   /var/cache/taos-voice/dl-tts/        download scratch. NOT /tmp: /tmp is a
#                                        RAM tmpfs on this phone
#
# TAOS_DATA_DIR and TAOS_MODELS_ROOT are resolved from the RUNNING controller
# (its environment, else its working directory + /data and /models, which is
# what tinyagentos itself does) unless you set them; the install fails if it
# cannot resolve them.
set -eu

# ---- pins ----------------------------------------------------------------
# must equal install-stt.sh's pin: this is the commit the shared library is
# required to have been built at
SHERPA_COMMIT=11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf
SHERPA_FEATURES=tts
PORT=6976

PREFIX="${TAOS_TTS_PREFIX:-/opt/taos-voice/tts}"
STT_PREFIX="${TAOS_STT_PREFIX:-/opt/taos-voice/stt}"
CACHE=/var/cache/taos-voice
DL="$CACHE/dl-tts"
SVC=taos-ttsd
HERE="$(cd "$(dirname "$0")" && pwd)"

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

# tree_sha256 DIR -> prints one sha256 over every regular file under DIR:
# sha256 of the byte-sorted lines "<relative path>\0<file sha256 hex>\n". Any
# added, removed, renamed or changed file changes it. Refuses symlinks and
# anything that is not a regular file or a directory. Python, not find|sort|
# sha256sum, so a filename with a space (espeak-ng-data has one) or the locale
# cannot change the answer.
tree_sha256() {
  python3 - "$1" <<'PYEOF'
import hashlib, os, stat, sys
root = sys.argv[1]
if not os.path.isdir(root) or os.path.islink(root):
    sys.exit("tree_sha256: %s is not a directory" % root)
files = []
for d, dirs, names in os.walk(root):
    for n in dirs + names:
        p = os.path.join(d, n)
        m = os.lstat(p).st_mode
        if stat.S_ISLNK(m):
            sys.exit("tree_sha256: refusing symlink %s" % p)
        if stat.S_ISREG(m):
            files.append(os.path.relpath(p, root).encode("utf-8", "surrogateescape"))
        elif not stat.S_ISDIR(m):
            sys.exit("tree_sha256: refusing non-regular file %s" % p)
h = hashlib.sha256()
for rel in sorted(files):
    fh = hashlib.sha256()
    with open(os.path.join(root.encode("utf-8", "surrogateescape"), rel), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            fh.update(b)
    h.update(rel + b"\0" + fh.hexdigest().encode() + b"\n")
print(h.hexdigest())
PYEOF
}

# verify_tree DIR WANT_HEX64 -> 0 if the tree digest matches, else 1 (loudly).
verify_tree() {
  _got=$(tree_sha256 "$1") || return 1
  if [ "$_got" != "$2" ]; then
    echo "verify_tree: MISMATCH $1: want $2 got $_got" >&2
    return 1
  fi
  return 0
}

# check_voice_licence MODEL_CARD -> 0 only if the card's "License:" field(s) are
# ALL public domain, CC0 or CC BY (any version, no NC / ND / SA suffix), and
# the card states no other restriction (non-commercial, research). Anything
# else, an absent or empty card, or no License field at all, is REFUSED: the
# allowlist is the rule, not a blocklist of known-bad voices. Refuses, among
# others, hfc_female/hfc_male (CC BY-NC-SA 4.0) and en_US-lessac (Blizzard 2013,
# research only).
check_voice_licence() {
  _card=$1
  [ -s "$_card" ] || { echo "voice licence REFUSED: $_card is missing or empty" >&2; return 1; }
  python3 - "$_card" <<'PYEOF' || return 1
import re, sys
try:
    text = open(sys.argv[1], encoding="utf-8").read()
except (OSError, UnicodeDecodeError) as e:
    sys.exit("voice licence REFUSED: cannot read the model card: %s" % e)
ALLOWED = re.compile(r"(public[ -]domain|cc0(?:[ -]1\.0)?|cc[ -]by(?:[ -][0-9]+(?:\.[0-9]+)?)?)", re.I)
RESTRICT = re.compile(r"non[ -]?commercial|no[ -]?derivative|research|\bNC\b|\bND\b", re.I)
vals = [m.group(1).strip() for m in re.finditer(r"^[ \t]*[*-]?[ \t]*License[ \t]*:(.*)$", text, re.I | re.M)]
if not vals:
    sys.exit("voice licence REFUSED: the model card has no 'License:' field")
for v in vals:
    if not ALLOWED.fullmatch(v):
        sys.exit("voice licence REFUSED: '%s' is not public domain, CC0 or CC BY" % v)
m = RESTRICT.search(text)
if m:
    sys.exit("voice licence REFUSED: the model card states a restriction ('%s')" % m.group(0))
print("voice licence ok: %s" % "; ".join(vals))
PYEOF
}

# ---- the voice choice (before the source-only return, so tests see it too) ----
die() { echo "install-tts: FAIL: $*" >&2; exit 1; }
say() { echo "install-tts: $*"; }

VOICE="${TAOS_TTS_VOICE:-medium}"
case "$VOICE" in
  medium)
    MODEL_NAME=vits-piper-en_GB-cori-medium
    MODEL_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_GB-cori-medium.tar.bz2
    # Computed 2026-10-02 from the real archive (67257412 bytes); the per-file
    # hashes are of the files in it. tokens.txt and espeak-ng-data are identical
    # to the high voice's.
    MODEL_ARCHIVE_SHA256=49c9a5361bbdd95d7ca9687c4de11e5908481f65e7c7c368960df79949fdac2b
    SHA_voice_onnx=8b0d3cdd77f2878e0aa2048103eabb4d01b334783f99629f042bcf703aeba487
    SHA_voice_onnx_json=e262c16d7f192f69d4edd6b4ef8a5915379e67495fcc402f1ab15eeb33da3d36
    SHA_tokens_txt=ef3a7e4a8d1af0c9d4dc45aaae1a6242ebe24a7ed6f3d025a49eb29682784c6d
    SHA_model_card=38d76dfde845837b184668f8bab1c6426ad740b1af0fcebc349037822739e536
    # espeak-ng-data/ is 355 files (one has a space in its name): pinned as ONE
    # digest over the whole tree, computed by tree_sha256 below
    TREE_espeak_ng_data=1c2ec0747e40d30f8f123b65c93dcba64f2404195b3344ed614df41237df8332
    VOICE_FILE=en_GB-cori-medium
    ;;
  high)
    MODEL_NAME=vits-piper-en_GB-cori-high
    MODEL_URL=https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_GB-cori-high.tar.bz2
    # Computed 2026-10-01 by downloading MODEL_URL (115574061 bytes) and hashing it;
    # equal to the sha256 digest GitHub publishes for that release asset. The
    # per-file hashes below are of the files in that archive.
    MODEL_ARCHIVE_SHA256=42922f07738fcde2e49eed4e959635692f73b933de35a6b7c1010162ff566292
    SHA_voice_onnx=006bb4db48e066f7f1be91d218db3b76617a707196271694ca6455d7bbd13842
    SHA_voice_onnx_json=9e7fb5b5671612c22f3c81cbe46c1ae87b031a4632bcb509e499dad6f1e2adec
    SHA_tokens_txt=ef3a7e4a8d1af0c9d4dc45aaae1a6242ebe24a7ed6f3d025a49eb29682784c6d
    SHA_model_card=136e7bd168b6c35b4a5df01a0253297e5773b5775ceae0af5160f264aa58208f
    TREE_espeak_ng_data=1c2ec0747e40d30f8f123b65c93dcba64f2404195b3344ed614df41237df8332
    VOICE_FILE=en_GB-cori-high
    ;;
  *) die "TAOS_TTS_VOICE='$VOICE' is not a voice; use medium (default) or high" ;;
esac

# Sourced by the tests to reach verify_sha256 and tree_sha256 alone.
if [ "${INSTALL_TTS_SOURCE_ONLY:-0}" = 1 ]; then return 0; fi

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
MODEL_DIR="$TAOS_MODELS_ROOT/sherpa-onnx/piper/$MODEL_NAME"
MANIFEST_DIR="$TAOS_DATA_DIR/voice/tts"
say "controller data dir:  $TAOS_DATA_DIR ($DATA_OWNER)"
say "controller model store: $TAOS_MODELS_ROOT ($MODELS_OWNER) -> $MODEL_DIR"

# ---- 1. the shared sherpa-onnx library: required, never built here -----------
LIB="$STT_PREFIX/lib/libsherpa-onnx-c-api.so"
RUN_STT="run 'sudo sh pmos/voice/install-stt.sh' first: it builds the one sherpa-onnx library (with TTS, at $SHERPA_COMMIT) that this daemon loads"
[ -f "$LIB" ] || die "$LIB is missing; $RUN_STT"
got=$(cat "$STT_PREFIX/lib/.commit" 2>/dev/null || true)
[ "$got" = "$SHERPA_COMMIT" ] ||
  die "$LIB was built at '${got:-unknown}', this script needs $SHERPA_COMMIT; $RUN_STT"
got=$(cat "$STT_PREFIX/lib/.features" 2>/dev/null || true)
[ "$got" = "$SHERPA_FEATURES" ] ||
  die "$LIB was built without TTS (features '${got:-none}'); $RUN_STT"
# belt and braces: the TTS-OFF build's stubs carry this message, the real code never does
! grep -q "TTS is not enabled" "$LIB" || die "$LIB holds the TTS-disabled stubs; $RUN_STT"
if ldd "$LIB" 2>&1 | grep -q 'not found'; then
  ldd "$LIB" >&2
  die "$LIB has unresolved dependencies"
fi
ORT=$(ldd "$LIB" | awk '/libonnxruntime/ {print $3}')
[ -n "$ORT" ] || die "$LIB does not link libonnxruntime"
say "using $LIB (sherpa-onnx $SHERPA_COMMIT, $SHERPA_FEATURES)"

# ---- 2. runtime tools (nothing to build) ------------------------------------
need=""
for p in python3 curl bzip2 tar; do
  apk info -e "$p" >/dev/null 2>&1 || need="$need $p"
done
if [ -n "$need" ]; then
  say "apk add$need"
  # shellcheck disable=SC2086
  apk add --no-progress $need
fi

# ---- 3. the service user ---------------------------------------------------
if ! getent passwd taos-tts >/dev/null; then
  say "creating system user taos-tts"
  adduser -S -D -H -h /var/empty -s /sbin/nologin taos-tts
fi
id taos-tts >/dev/null || die "user taos-tts does not exist"
[ "$(id -u taos-tts)" -ne 0 ] || die "taos-tts resolved to uid 0"

mkdir -p "$PREFIX/bin" "$MODEL_DIR" "$MANIFEST_DIR" "$DL"
chmod 0755 "$PREFIX" "$PREFIX/bin"
# the model store is the controller's: its files are owned by the controller user
chown "$MODELS_OWNER" "$TAOS_MODELS_ROOT/sherpa-onnx" "$TAOS_MODELS_ROOT/sherpa-onnx/piper" "$MODEL_DIR"
chmod 0755 "$MODEL_DIR"
chown "$DATA_OWNER" "$TAOS_DATA_DIR/voice" "$MANIFEST_DIR"
chmod 0755 "$TAOS_DATA_DIR/voice" "$MANIFEST_DIR"

# ---- 4. the model: verify, fetch only if needed, verify again -----------------
verify_all() { # verify_all [quiet]
  for f in ${VOICE_FILE}.onnx:$SHA_voice_onnx ${VOICE_FILE}.onnx.json:$SHA_voice_onnx_json tokens.txt:$SHA_tokens_txt MODEL_CARD:$SHA_model_card; do
    if [ "${1:-}" = quiet ]; then verify_sha256 "$MODEL_DIR/${f%:*}" "${f##*:}" 2>/dev/null || return 1
    else verify_sha256 "$MODEL_DIR/${f%:*}" "${f##*:}" || return 1; fi
  done
  if [ "${1:-}" = quiet ]; then verify_tree "$MODEL_DIR/espeak-ng-data" "$TREE_espeak_ng_data" 2>/dev/null
  else verify_tree "$MODEL_DIR/espeak-ng-data" "$TREE_espeak_ng_data"; fi
}
if verify_all quiet; then
  say "model files present with the pinned hashes, skipping the download"
else
  say "downloading $MODEL_URL"
  rm -rf "$DL/x"; mkdir -p "$DL/x"
  curl -fsSL --retry 3 --retry-delay 5 -o "$DL/model.tar.bz2" "$MODEL_URL"
  verify_sha256 "$DL/model.tar.bz2" "$MODEL_ARCHIVE_SHA256" || die "model archive hash mismatch"
  tar -xjf "$DL/model.tar.bz2" -C "$DL/x"
  src="$DL/x/$MODEL_NAME"
  [ -d "$src" ] || die "archive did not contain the expected directory"
  # the licence gate: BEFORE anything is installed from the archive
  check_voice_licence "$src/MODEL_CARD" >&2 || die "refusing this voice: its licence is not public domain, CC0 or CC BY"
  for f in ${VOICE_FILE}.onnx ${VOICE_FILE}.onnx.json tokens.txt MODEL_CARD; do
    install -m 0644 -o "${MODELS_OWNER%:*}" -g "${MODELS_OWNER#*:}" "$src/$f" "$MODEL_DIR/$f.new"
    mv -f "$MODEL_DIR/$f.new" "$MODEL_DIR/$f"
  done
  # the tree is swapped in whole: never a half-copied espeak-ng-data in place
  rm -rf "$MODEL_DIR/espeak-ng-data.new" "$MODEL_DIR/espeak-ng-data.old"
  cp -R "$src/espeak-ng-data" "$MODEL_DIR/espeak-ng-data.new"
  chown -R "$MODELS_OWNER" "$MODEL_DIR/espeak-ng-data.new"
  find "$MODEL_DIR/espeak-ng-data.new" -type d -exec chmod 0755 {} \;
  find "$MODEL_DIR/espeak-ng-data.new" -type f -exec chmod 0644 {} \;
  verify_tree "$MODEL_DIR/espeak-ng-data.new" "$TREE_espeak_ng_data" || die "espeak-ng-data failed verification"
  if [ -e "$MODEL_DIR/espeak-ng-data" ]; then mv "$MODEL_DIR/espeak-ng-data" "$MODEL_DIR/espeak-ng-data.old"; fi
  mv "$MODEL_DIR/espeak-ng-data.new" "$MODEL_DIR/espeak-ng-data"
  rm -rf "$MODEL_DIR/espeak-ng-data.old" "$DL/x" "$DL/model.tar.bz2"
  CHANGED=1
fi
# the loud pass, always: a re-run re-verifies even when it skipped the download
verify_all || die "a model file failed verification"
say "model hashes verified"
# and the allowlist on the card actually installed (it is pinned, so this is the same card)
check_voice_licence "$MODEL_DIR/MODEL_CARD" || die "refusing this voice: its licence is not public domain, CC0 or CC BY"

# ---- 5. daemon, unit, notice -------------------------------------------------
put() { # put SRC DST MODE : install if different, flag a change
  if ! cmp -s "$1" "$2" 2>/dev/null; then install -D -m "$3" "$1" "$2"; CHANGED=1; fi
}
put "$HERE/taos-ttsd" "$PREFIX/bin/taos-ttsd" 0755
put "$HERE/NOTICE-TTS.md" "$PREFIX/NOTICE" 0644
# the library is rebuilt in place by install-stt.sh; restart if it changed under us
LIB_ID=$(sha256sum "$LIB"); LIB_ID=${LIB_ID%% *}
[ "$(cat "$PREFIX/.lib-sha256" 2>/dev/null || true)" = "$LIB_ID" ] || {
  printf '%s\n' "$LIB_ID" > "$PREFIX/.lib-sha256"; chmod 0644 "$PREFIX/.lib-sha256"; CHANGED=1; }
# the unit is a template: prefix, library, model dir and port are filled in here
sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@LIB@|$LIB|g" -e "s|@MODEL_DIR@|$MODEL_DIR|g" -e "s|@MODEL_NAME@|$MODEL_NAME|g" -e "s|@PORT@|$PORT|g" \
  "$HERE/../systemd/taos-ttsd.service" > "$CACHE/taos-ttsd.service.rendered"
! grep -q '@[A-Z_]*@' "$CACHE/taos-ttsd.service.rendered" || die "unit has an unfilled placeholder"
put "$CACHE/taos-ttsd.service.rendered" /etc/systemd/system/taos-ttsd.service 0644

# ---- 6. manifest, for the controller (in its data dir, readable by it) ---------
SHA_onnx=$SHA_voice_onnx SHA_onnx_json=$SHA_voice_onnx_json SHA_card=$SHA_model_card SHA_tokens=$SHA_tokens_txt \
TREE_espeak=$TREE_espeak_ng_data SHERPA_COMMIT=$SHERPA_COMMIT PORT=$PORT MODEL_NAME=$MODEL_NAME \
MODEL_URL=$MODEL_URL MODEL_ARCHIVE_SHA256=$MODEL_ARCHIVE_SHA256 ORT=$ORT PREFIX=$PREFIX LIB=$LIB \
VOICE_FILE=$VOICE_FILE MODEL_DIR=$MODEL_DIR MANIFEST_DIR=$MANIFEST_DIR DATA_OWNER=$DATA_OWNER \
python3 - <<'PYEOF'
import json, os, shutil
e = os.environ
m = {
    "engine": "sherpa-onnx",
    "model": e["MODEL_NAME"],
    "voice": "cori",
    "sid": 0,
    "sample_rate": 22050,
    "files": {
        e["VOICE_FILE"] + ".onnx": e["SHA_onnx"],
        e["VOICE_FILE"] + ".onnx.json": e["SHA_onnx_json"],
        "MODEL_CARD": e["SHA_card"],
        "tokens.txt": e["SHA_tokens"],
    },
    # sha256 over sorted "<relpath>\0<sha256 hex>\n" lines (install-tts.sh tree_sha256)
    "espeak_ng_data_tree_sha256": e["TREE_espeak"],
    "port": int(e["PORT"]),
    "sherpa_commit": e["SHERPA_COMMIT"],
    "library": e["LIB"],
    "model_dir": e["MODEL_DIR"],
    "install_prefix": e["PREFIX"],
    "model_url": e["MODEL_URL"],
    "model_archive_sha256": e["MODEL_ARCHIVE_SHA256"],
    "onnxruntime": os.path.realpath(e["ORT"]),
    "license": "public domain (voice dataset, LibriVox); Apache-2.0 (sherpa-onnx); GPL-3.0-or-later (espeak-ng, espeak-ng-data); see NOTICE",
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

# ---- 8. smoke synthesis: real audio through the real engine ---------------------
# (a ctypes layout that drifted from the pinned c-api.h would fail HERE)
PORT=$PORT python3 - <<'PYEOF' || die "smoke synthesis failed"
import array, http.client, json, os, time
c = http.client.HTTPConnection("127.0.0.1", int(os.environ["PORT"]), timeout=60)
t0 = time.monotonic()
c.request("POST", "/tts", json.dumps({"text": "Sure. The handset can speak now."}),
          {"Content-Type": "application/json"})
r = c.getresponse()
assert r.status == 200, "POST /tts -> %d %r" % (r.status, r.read())
assert r.chunked and r.getheader("X-Sample-Rate") == "22050", r.getheaders()
first, parts = None, []
while True:
    d = r.read1(65536)
    if not d:
        break
    first = first if first is not None else time.monotonic() - t0
    parts.append(d)
pcm = b"".join(parts)
assert pcm and len(pcm) % 2 == 0, "odd or empty PCM (%d bytes)" % len(pcm)
a = array.array("h", pcm)
secs, peak = len(a) / 22050.0, max(abs(x) for x in a)
assert secs > 1.0, "only %.2f s of audio" % secs
assert peak > 1000, "audio is near-silent (peak %d)" % peak
print("install-tts: smoke synthesis: %.2f s of audio, first audio after %.0f ms, total %.0f ms, peak %d"
      % (secs, first * 1000, (time.monotonic() - t0) * 1000, peak))
PYEOF

say "done in $(( $(date +%s) - START )) s: $SVC on 127.0.0.1:$PORT"
