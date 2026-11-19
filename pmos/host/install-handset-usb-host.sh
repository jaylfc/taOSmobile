#!/bin/sh
# Install the handset's USB fallback link on the Linux flash host (omarchy),
# then PROVE it when the phone is plugged in.
#
# Run as root ON the USB host. Idempotent. Needs NetworkManager.
#
# Exit 0 only when taosphone0 holds 172.16.42.2 AND 172.16.42.1 answers.
# Exit 2 means INSTALLED BUT NOT PROVEN (no phone link present): that is not
# a pass, it is "nothing was measured". Exit 1 is a failure.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
RULE=/etc/udev/rules.d/70-taos-handset-usb.rules
PROFILE=/etc/NetworkManager/system-connections/taos-handset-usb.nmconnection
IFACE=taosphone0
PHONE_IP=172.16.42.1

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) not found" >&2; exit 1; }

install -m 0644 "$HERE/70-taos-handset-usb.rules" "$RULE"
# NM ignores a keyfile that is readable by anyone but root.
install -m 0600 "$HERE/taos-handset-usb.nmconnection" "$PROFILE"
udevadm control --reload
nmcli connection reload
echo "installed: $RULE $PROFILE"

# A link that enumerated before the rule existed keeps its old name: re-run
# the add event for the phone's net device so the rule renames it now.
# NM is told first: a plain link-down does not stop it reactivating the
# link mid-rename (systemd.link(5) names this case).
seen=""
for dev in /sys/class/net/*; do
    name=${dev##*/}
    [ "$name" = "$IFACE" ] && continue
    if udevadm info -a -p "$dev" 2>/dev/null | grep -q 'ATTRS{serial}=="nothing,spacewar"'; then
        echo "renaming $name -> $IFACE"
        seen=$name
        nmcli device disconnect "$name" >/dev/null 2>&1 || true
        ip link set "$name" down
        udevadm trigger --action=add "$dev"
        udevadm settle
    fi
done

if [ ! -e "/sys/class/net/$IFACE" ] && [ -n "$seen" ]; then
    # The phone IS here; the rename failed. Not the same as "no phone".
    echo "FAIL: the phone's link $seen was not renamed to $IFACE"
    exit 1
fi
if [ ! -e "/sys/class/net/$IFACE" ]; then
    echo "NOT PROVEN: no $IFACE (phone not on this host's USB, or not in NCM mode)"
    exit 2
fi
nmcli connection up taos-handset-usb >/dev/null || true
ip -4 -o addr show dev "$IFACE" | grep -q " 172\.16\.42\.2/24 " \
    || { echo "FAIL: $IFACE has no 172.16.42.2/24"; ip -4 -o addr show dev "$IFACE"; exit 1; }
ping -c 2 -W 2 -I "$IFACE" "$PHONE_IP" >/dev/null \
    || { echo "FAIL: $IFACE holds 172.16.42.2 but $PHONE_IP does not answer"; exit 1; }
echo "PROVEN: $IFACE 172.16.42.2/24 and $PHONE_IP answers"
