#!/bin/sh
# On the handset in taOS, as root: make luma-root.img (from build-luma-image.sh) bootable by
# kexec-guest.sh --guest luma. Unpacks $D/luma-root.img.zst sparse, then edits only the OSTree
# deployment's /etc and the stateroot's /var: the dualboot units (guest-units.sh), taOS's wifi
# connections, jay's ssh key for the luma user and the taOS agent's own sudo account
# (guest-agent.sh). luma keeps Luma's factory password unless LUMA_PIN is set in the environment
# (never written to the repo), and its first-user state stays empty, so Luma's own owner setup still
# runs on the first launch by hand.
# Never touches taOS's own root otherwise.
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
. "$HERE/guest-agent.sh"
dualboot_agent_account "$DEP" "$SR/var/home/taos-agent" /home/taos-agent /home/jay/.ssh/authorized_keys
echo luma-mobile > "$DEP/etc/hostname"

# luma's PIN (the lock screen's password). Written through the existing file so its inode, mode and
# label stay as they are
if [ -n "${LUMA_PIN:-}" ]; then
  h=$(printf '%s\n' "$LUMA_PIN" | openssl passwd -6 -stdin)
  awk -F: -v OFS=: -v h="$h" '$1 == "luma" { $2 = h; n++ } { print } END { exit n != 1 }' \
    "$DEP/etc/shadow" > "$MNT/.shadow.new" || { rm -f "$MNT/.shadow.new"; echo "no single luma line in shadow"; exit 1; }
  cat "$MNT/.shadow.new" > "$DEP/etc/shadow"
  rm -f "$MNT/.shadow.new"
  echo "luma PIN set"
fi

# /etc/shadow keeps the mode and SELinux label OSTree ships in usr/etc (an earlier hand edit of this
# image left it mode 000 with no label); fail loud if they still differ
chmod --reference="$DEP/usr/etc/shadow" "$DEP/etc/shadow"
lbl=$(getfattr --only-values -n security.selinux "$DEP/usr/etc/shadow" 2>/dev/null) \
  && setfattr -n security.selinux -v "$lbl" "$DEP/etc/shadow"
[ "$(stat -c %a:%u:%g "$DEP/etc/shadow")" = "$(stat -c %a:%u:%g "$DEP/usr/etc/shadow")" ] \
  || { echo "BADSHADOW $(stat -c %a:%u:%g "$DEP/etc/shadow")"; exit 1; }

sync
say DONE
