#!/bin/sh
# Install the system on-screen keyboard on the handset. Run as root ON the
# device, with the kiosk session running.
#
# The keyboard itself (wrapper, `taos-session`, theme packages) lives
# in taOS's shared session/ layer and is taken from the taOS checkout on the
# device, never copied into this repo. The handset still installs its OWN
# sway-kiosk.conf and launcher from here (the power key, volume keys and
# sensor blocks are not in the generic config), so taOS's install-session.sh
# must not be run on it: that would replace the config and drop those
# bindings. That duplication is the debt taOSmobile #20 removes.
set -eu
HERE="$(dirname "$0")"
SRC="${TAOS_SESSION_SRC:-/root/tinyagentos/session}"
[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
for f in bin/taos-session bin/taos-kiosk-keyboard themes/taos-default/theme.json; do
    [ -r "$SRC/$f" ] || { echo "FAIL: $SRC/$f missing (taOS checkout too old for the system keyboard?)"; exit 1; }
done
# Control for the grep below: an unreadable config would also "lack" the line.
[ -r "$HERE/etc/sway-kiosk.conf" ] || { echo "FAIL: $HERE/etc/sway-kiosk.conf not readable"; exit 1; }
grep -q '^include /etc/taos/sway.d/\*\.conf$' "$HERE/etc/sway-kiosk.conf" || {
    echo "FAIL: this checkout's sway-kiosk.conf has no sway.d include, the keyboard drop-in would never load"; exit 1; }

apk info -e squeekboard >/dev/null 2>&1 || apk add squeekboard
[ -x /usr/bin/squeekboard ] || { echo "FAIL: /usr/bin/squeekboard missing after install"; exit 1; }

install -m 755 "$SRC/bin/taos-session" /usr/local/bin/taos-session
install -m 755 "$SRC/bin/taos-kiosk-keyboard" /usr/local/bin/taos-kiosk-keyboard
for s in taos-session taos-kiosk-keyboard; do
    sh -n "/usr/local/bin/$s" || { echo "FAIL: installed $s is not valid sh"; exit 1; }
done
install -d -m 755 /usr/share/taos/themes /etc/taos/sway.d
cp -R "$SRC/themes/." /usr/share/taos/themes/
chmod -R a+rX /usr/share/taos/themes
[ -d /var/lib/taos ] || install -d -m 755 /var/lib/taos
install -d -m 755 -o taos /var/lib/taos/session

# The handset's own launcher (IME flags) and config (sway.d include).
install -m 755 "$HERE/bin/taos-kiosk-launch" /usr/local/bin/taos-kiosk-launch
grep -q -- '--enable-wayland-ime' /usr/local/bin/taos-kiosk-launch || {
    echo "FAIL: installed launcher has no IME flags, the keyboard would never auto-show"; exit 1; }
install -m 644 "$HERE/etc/sway-kiosk.conf" /etc/taos/sway-kiosk.conf

# Writes the drop-in and the screen-keyboard-enabled gsetting, reads it back.
/usr/local/bin/taos-session keyboard on || { echo "FAIL: taos-session keyboard on failed"; exit 1; }
[ -s /etc/taos/sway.d/50-keyboard.conf ] || { echo "FAIL: keyboard drop-in missing"; exit 1; }
/usr/local/bin/taos-session resolve-theme >/dev/null || { echo "FAIL: no usable keyboard theme installed"; exit 1; }
echo "install-keyboard: ok. Restart the kiosk to load it: systemctl restart taos-kiosk"
echo "NOT PROVEN by this script: that the keyboard shows on the glass. Focus a text field and look."
