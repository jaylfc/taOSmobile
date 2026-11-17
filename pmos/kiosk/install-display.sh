#!/bin/sh
# Install the screen-timeout setting on the handset. Run as root ON the device.
#
# /etc/taos/display.conf is the one place the timeout lives. An existing file
# is KEPT, so a value the owner chose survives an update; pass --reset to put
# this repo's default (60 s) back.
set -eu
HERE="$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
RESET=0
case "${1:-}" in
    '') ;;
    --reset) RESET=1 ;;
    *) echo "usage: $0 [--reset]" >&2; exit 2 ;;
esac
install -m 755 "$HERE/bin/taos-kiosk-idle" /usr/local/bin/taos-kiosk-idle
sh -n /usr/local/bin/taos-kiosk-idle || { echo "FAIL: installed taos-kiosk-idle is not valid sh"; exit 1; }
install -d -m 755 /etc/taos
if [ "$RESET" = 1 ] || [ ! -e /etc/taos/display.conf ]; then
    install -m 644 "$HERE/etc/display.conf" /etc/taos/display.conf
    echo "installed /etc/taos/display.conf"
else
    echo "kept the existing /etc/taos/display.conf"
fi
V="$(sed -n 's/^SCREEN_OFF_SECONDS=\([0-9][0-9]*\)[[:space:]]*$/\1/p' /etc/taos/display.conf | tail -n 1)"
[ -n "$V" ] || { echo "FAIL: /etc/taos/display.conf has no valid SCREEN_OFF_SECONDS"; exit 1; }
# The same range taos-kiosk-idle accepts: reporting a value it would reject
# (and replace with 30) would be a false "ok". The file is left as it is.
# Length first: a number too large for the shell makes `[ -ne ]` fail with an
# error, which would skip the range test.
case "$V" in ?????*) TOO_LONG=1 ;; *) TOO_LONG=0 ;; esac
if [ "$TOO_LONG" = 1 ] || { [ "$V" -ne 0 ] && { [ "$V" -lt 5 ] || [ "$V" -gt 3600 ]; }; }; then
    echo "FAIL: SCREEN_OFF_SECONDS=$V in /etc/taos/display.conf is outside 5..3600 (0 = never); the phone will use 30 until it is corrected, or run $0 --reset"
    exit 1
fi
echo "install-display: SCREEN_OFF_SECONDS=$V"
echo "Apply now (as the session user, in the session): swaymsg exec /usr/local/bin/taos-kiosk-idle"
echo "NOT PROVEN by this script: the time to panel off. Leave the phone alone and time it."
