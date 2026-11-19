#!/bin/sh
# Install the system switcher on the handset (run as root, from this directory).
#   /usr/lib/taos/dualboot/   the kexec launcher and the guest init it packs
#   /usr/local/sbin/taos-switch-os
#   /etc/sudoers.d/50-taos-switch-os   user taos may run EXACTLY the two `go` forms, nothing else
# Also removes a standing return-after file: that timer sends a guest back to taOS on its own and
# is for tests only (kexec-guest.sh --return-after N arms it for one launch).
set -eu
HERE=$(dirname "$0")
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
install -d -m 0755 /usr/lib/taos/dualboot
install -m 0755 "$HERE/kexec-guest.sh" "$HERE/guest-init" /usr/lib/taos/dualboot/
# kexec-guest.sh sources this from its own directory
install -m 0644 "$HERE/build-identity.sh" /usr/lib/taos/dualboot/
# /usr/local/sbin does not exist on a fresh postmarketOS install (measured on the handset).
install -d -m 0755 /usr/local/sbin
install -m 0755 "$HERE/taos-switch-os" /usr/local/sbin/taos-switch-os
T=$(mktemp)
cat > "$T" <<'SUDO'
# taOS shell -> other systems. Exactly these two commands, with exactly these arguments.
# The tool runs as root: sudo gives it a fixed PATH whatever the caller had.
Defaults!/usr/local/sbin/taos-switch-os secure_path=/usr/sbin:/usr/bin:/sbin:/bin
taos ALL=(root) NOPASSWD: /usr/local/sbin/taos-switch-os go omarchy, /usr/local/sbin/taos-switch-os go luma
SUDO
install -m 0440 -o root -g root "$T" /etc/sudoers.d/50-taos-switch-os
rm -f "$T"
rm -f /var/lib/dualboot/return-after
echo "installed. list: $(/usr/local/sbin/taos-switch-os list)"
