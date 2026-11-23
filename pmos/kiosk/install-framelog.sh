#!/bin/sh
# Build and install taos-framelog, the frame logger taos-kiosk-screen uses to
# wait for the panel to go black.
#
# Run as root ON the device, with docker available. Idempotent: re-running
# rebuilds only when the source changed, and only replaces the installed
# binary when the new one differs. Nothing else in the kiosk install is
# touched, so a failed build leaves the rest of the install in place.
#
# taos-kiosk-screen looks for the logger at /usr/local/bin/taos-framelog (or
# $TAOS_FRAMELOG) and falls back to its grim loop when it is absent, so this
# is what makes that path usable on a fresh install: before it, the binary
# was installed by hand and every reflash lost it.
set -eu

HERE="$(dirname "$0")"
SRC_DIR="$HERE/../framelog"
DEST="${TAOS_FRAMELOG_DEST:-/usr/local/bin/taos-framelog}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "FAIL: docker is not on PATH"; exit 1; }

# Build in an alpine:edge container on the handset, the same way
# pmos/framelog/build-on-phone.sh does, so the binary matches the host libc and
# libwayland. The container is the build environment; nothing it installs is
# persisted on the host.
mkdir -p "$STAGE/src"

# The same sources build-on-phone.sh copies, including the unit tests: `make
# test` in the container builds test_stats and test_out from these.
for f in framelog.c stats.c stats.h out.c out.h test_out.c test_stats.c Makefile; do
    [ -r "$SRC_DIR/$f" ] || { echo "FAIL: $SRC_DIR/$f not readable"; exit 1; }
    cp "$SRC_DIR/$f" "$STAGE/src/"
done

if ! docker run --rm -v "$STAGE:/src" alpine:edge sh -ec '
    apk add -q build-base pkgconf wayland-dev wayland-protocols wlr-protocols
    cd /src/src
    make clean
    make test
    make
    cp taos-framelog /src/taos-framelog
'; then
    echo "FAIL: taos-framelog did not build in the alpine container" >&2
    exit 1
fi

[ -x "$STAGE/taos-framelog" ] || { echo "FAIL: the container produced no taos-framelog binary"; exit 1; }

# Built in alpine:edge, run on the host: a library the host lacks would make the
# new binary unrunnable, and it must never replace a working one. Same check as
# build-on-phone.sh (ldd), plus actually running it.
if ! command -v ldd >/dev/null 2>&1; then
    echo "FAIL: ldd is not available to check the new binary" >&2; exit 1
fi
if ldd "$STAGE/taos-framelog" 2>&1 | grep -q "not found"; then
    echo "FAIL: the new taos-framelog needs libraries this host does not have:" >&2
    ldd "$STAGE/taos-framelog" 2>&1 | grep "not found" >&2
    exit 1
fi
"$STAGE/taos-framelog" --help >/dev/null 2>&1 || { echo "FAIL: the new taos-framelog does not run here" >&2; exit 1; }

# Idempotency: only replace the installed binary when the new one changed, so a
# re-run that rebuilt nothing is a no-op on the device.
if [ -x "$DEST" ] && cmp -s "$STAGE/taos-framelog" "$DEST"; then
    echo "taos-framelog already installed and unchanged"
else
    install -m 0755 "$STAGE/taos-framelog" "$DEST"
    echo "installed $DEST"
fi

# The logger must answer on the kiosk PATH, or taos-kiosk-screen hands over to
# grim. A build that left it somewhere else is not an install.
[ -x "$DEST" ] || { echo "FAIL: $DEST is not executable after install"; exit 1; }
echo "PASS: taos-framelog is at $DEST"
