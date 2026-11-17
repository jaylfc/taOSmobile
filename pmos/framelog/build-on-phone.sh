#!/bin/sh
# Build taos-framelog for the handset (aarch64 musl) inside an alpine:edge
# container run on the handset itself, so the binary matches the host libc and
# libwayland. Nothing is installed on the host.
#
# usage: build-on-phone.sh USER@HOST
# The host must let that user run docker. The result is left at
# /tmp/framelog-build/taos-framelog on the host.
set -eu

HOST="${1:-${FRAMELOG_HOST:-}}"
[ -n "$HOST" ] || { echo "usage: $0 USER@HOST" >&2; exit 2; }

HERE=$(cd "$(dirname "$0")" && pwd)
DEST=/tmp/framelog-build
SRC="framelog.c stats.c stats.h test_stats.c Makefile"

ssh "$HOST" "mkdir -p $DEST/src"
for f in $SRC; do scp -q "$HERE/$f" "$HOST:$DEST/src/$f"; done

# Build and run the unit test in the container; hand ownership back to the
# invoking user so the next build can overwrite the files.
ssh "$HOST" "docker run --rm -v $DEST:/src alpine:edge sh -ec '
    apk add -q build-base pkgconf wayland-dev wayland-protocols wlr-protocols
    cd /src/src
    make clean
    make test
    make
    cp taos-framelog /src/taos-framelog
    chown -R \$(stat -c %u:%g /src) /src
'"

echo "--- needed libraries (readelf) ---"
ssh "$HOST" "readelf -d $DEST/taos-framelog | grep NEEDED"
echo "--- resolved on the host (ldd) ---"
ssh "$HOST" "ldd $DEST/taos-framelog"
# every NEEDED library must resolve on the host, not just in the container
if ssh "$HOST" "ldd $DEST/taos-framelog | grep -q 'not found'"; then
	echo "FAIL: a needed library is missing on the host" >&2
	exit 1
fi
echo "built $DEST/taos-framelog on $HOST"
