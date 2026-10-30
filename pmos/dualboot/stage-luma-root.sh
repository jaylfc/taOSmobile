#!/bin/sh
# On the handset in taOS, as root: make luma-root.img (from build-luma-image.sh) bootable by
# kexec-guest.sh --guest luma. Unpacks $D/luma-root.img.zst sparse, then edits only the OSTree
# deployment's /etc and the stateroot's /var: the dualboot units (guest-units.sh), taOS's wifi
# connections, jay's ssh key for the luma user, and luma's password set to taOS's. Never touches
# taOS's own root otherwise.
set -eu
D=/var/lib/dualboot
HERE=$(dirname "$0")
IMG=$D/luma-root.img
MNT=/mnt/luma-stage
say() { echo "=== $(date -u +%H:%M:%S) $*"; }

if [ ! -f "$IMG" ]; then
  [ -f "$IMG.zst" ] || { echo "no $IMG.zst"; exit 1; }
  say unpack
  zstd -dq --sparse "$IMG.zst" -o "$IMG"
fi
mkdir -p "$MNT"
L=$(losetup --show -f "$IMG")
trap 'umount "$MNT" 2>/dev/null; losetup -d "$L"' EXIT
mount -t ext4 "$L" "$MNT"

# boot/loader is a symlink to the live loader.N; its newest entry names the deployment (the same pick
# kexec-guest.sh makes at every launch)
OST=$(for e in "$MNT"/boot/loader/entries/*.conf; do
        printf '%s %s\n' "$(sed -n 's/^version //p' "$e")" "$(tr ' ' '\n' < "$e" | sed -n 's/^ostree=//p')"
      done | sort -n | tail -n1 | cut -d' ' -f2)
[ -n "$OST" ] || { echo "no ostree= boot entry"; exit 1; }
DEP=$(readlink -f "$MNT$OST")
SR=${DEP%/deploy/*}
[ -d "$DEP/usr" ] && [ -d "$SR/var" ] || { echo "bad deployment $DEP"; exit 1; }
say "deployment $DEP"

say units
. "$HERE/guest-units.sh"
dualboot_guest_units "$DEP" /etc/dualboot Luma luma luma-mobile
mkdir -p "$DEP/mnt/taos"
for u in dualboot-marker dualboot-heartbeat dualboot-return dualboot-usbnet; do
  t=$(sed -n 's/^WantedBy=//p' "$DEP/etc/systemd/system/$u.service")
  mkdir -p "$DEP/etc/systemd/system/$t.wants"
  ln -sf "../$u.service" "$DEP/etc/systemd/system/$t.wants/$u.service"
done
for u in sshd NetworkManager; do
  if [ -f "$DEP/usr/lib/systemd/system/$u.service" ]; then
    mkdir -p "$DEP/etc/systemd/system/multi-user.target.wants"
    ln -sf "/usr/lib/systemd/system/$u.service" "$DEP/etc/systemd/system/multi-user.target.wants/$u.service"
    echo "enabled $u"
  else
    echo "NOUNIT $u"
  fi
done

say access
mkdir -p "$DEP/etc/NetworkManager/system-connections"
cp -a /etc/NetworkManager/system-connections/. "$DEP/etc/NetworkManager/system-connections/"
chmod 600 "$DEP"/etc/NetworkManager/system-connections/* 2>/dev/null || true
# /home is var/home on OSTree; uid 1000 is luma in the deployment's passwd
grep -q '^luma:x:1000:1000:' "$DEP/etc/passwd" || { echo "no luma uid 1000 in the deployment"; exit 1; }
mkdir -p "$SR/var/home/luma/.ssh"
cp /home/jay/.ssh/authorized_keys "$SR/var/home/luma/.ssh/authorized_keys"
chmod 700 "$SR/var/home/luma/.ssh"; chmod 600 "$SR/var/home/luma/.ssh/authorized_keys"
chown -R 1000:1000 "$SR/var/home/luma"
H=$(awk -F: '$1=="jay"{print $2}' /etc/shadow)
if grep -q '^luma:' "$DEP/etc/shadow"; then
  awk -F: -v OFS=: -v h="$H" '$1=="luma"{$2=h} {print}' "$DEP/etc/shadow" > "$DEP/etc/shadow.new"
  chmod "$(stat -c %a "$DEP/etc/shadow")" "$DEP/etc/shadow.new"; mv "$DEP/etc/shadow.new" "$DEP/etc/shadow"
  echo "luma password = taOS's"
else
  echo "NOSHADOW luma"
fi
echo luma-mobile > "$DEP/etc/hostname"

sync
say DONE
