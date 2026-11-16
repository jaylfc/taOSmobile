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
echo "install-display: SCREEN_OFF_SECONDS=$V"
echo "Apply now (as the session user, in the session): swaymsg exec /usr/local/bin/taos-kiosk-idle"
echo "NOT PROVEN by this script: the time to panel off. Leave the phone alone and time it."
