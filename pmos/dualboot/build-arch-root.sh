#!/bin/sh
# Build the Omarchy guest root as a sparse loop image on taOS's root (runs on the handset as root).
# Stage A: Arch Linux ARM base that boots, joins wifi + tailnet, and leaves a marker. Desktop later.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
D=/var/lib/dualboot
IMG=$D/arch-root.img
MNT=/mnt/arch
LOG=$D/build.log
mkdir -p "$D" "$MNT"
exec >>"$LOG" 2>&1
step() { echo "=== $(date -u +%H:%M:%S) $*"; }

step start
apk add --no-progress libarchive-tools arch-install-scripts 2>/dev/null || apk add --no-progress libarchive-tools
apk add --no-progress gnupg gnupg-dirmngr ca-certificates

if [ ! -e "$IMG" ]; then
  step image
  truncate -s 64G "$IMG"
  mkfs.ext4 -q -L omarchy-root "$IMG"
fi
mountpoint -q "$MNT" || mount -o loop "$IMG" "$MNT"

if [ ! -x "$MNT/usr/bin/pacman" ]; then
  step fetch
  cd "$D"
  # The tarball is checked against the detached GPG signature of the Arch Linux ARM build system key,
  # whose fingerprint is pinned here. Source: the "Package signing" page, archlinuxarm.org/about/package-signing
  # (entry "Arch Linux ARM Build System"). os.archlinuxarm.org serves a certificate for another name,
  # so the download comes from a mirror over HTTPS; the signature, not the transport, is the trust.
  ALARM_KEY_FPR=68B3537F39A313B3E574D06777193F152BDBE6A6
  ALARM_URL=https://de3.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz
  rm -f alarm.tar.gz alarm.tar.gz.sig
  wget -q -O alarm.tar.gz "$ALARM_URL"
  wget -q -O alarm.tar.gz.sig "$ALARM_URL.sig"
  GH=$D/alarm-gnupg
  rm -rf "$GH"; mkdir -m 700 "$GH"
  if [ -n "${ALARM_KEYFILE:-}" ]; then
    gpg --homedir "$GH" --batch --import "$ALARM_KEYFILE"
  else
    gpg --homedir "$GH" --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys "$ALARM_KEY_FPR"
  fi
  [ "$(gpg --homedir "$GH" --batch --with-colons --fingerprint "$ALARM_KEY_FPR" | awk -F: '$1=="fpr"{print $10; exit}')" = "$ALARM_KEY_FPR" ] \
    || { echo "SIGNING KEY FINGERPRINT MISMATCH"; rm -f alarm.tar.gz alarm.tar.gz.sig; exit 1; }
  VST=$(gpg --homedir "$GH" --batch --status-fd 1 --verify alarm.tar.gz.sig alarm.tar.gz 2>/dev/null) || VST=
  # GOODSIG (not EXPKEYSIG or REVKEYSIG) and a VALIDSIG whose primary key is the pinned one
  echo "$VST" | grep -q '^\[GNUPG:\] GOODSIG ' \
    && [ "$(echo "$VST" | awk '$1=="[GNUPG:]" && $2=="VALIDSIG"{print $NF}')" = "$ALARM_KEY_FPR" ] \
    || { echo "SIGNATURE CHECK FAILED"; rm -f alarm.tar.gz alarm.tar.gz.sig; exit 1; }
  step extract
  bsdtar -xpf alarm.tar.gz -C "$MNT"
  rm -f alarm.tar.gz alarm.tar.gz.sig
fi

step chroot-mounts
for m in proc sys dev dev/pts run; do mkdir -p "$MNT/$m"; mountpoint -q "$MNT/$m" || mount --bind "/$m" "$MNT/$m"; done
rm -f "$MNT/etc/resolv.conf"; cp /etc/resolv.conf "$MNT/etc/resolv.conf"

step pacman
cat > "$MNT/root/stage-a.sh" <<'EOF'
set -eu
# The pmOS kernel (here and when the guest boots) has no Landlock, which pacman's download sandbox needs
grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
pacman-key --init
pacman-key --populate archlinuxarm
# The ALARM tarball's own kernel never runs here (the pmOS kernel does)
pacman -Rdd --noconfirm linux-aarch64 linux-firmware 2>/dev/null || true
pacman -Syu --noconfirm --needed base sudo openssh networkmanager e2fsprogs kmod python polkit \
  less usbutils wireless-regdb wpa_supplicant tailscale
# pd-mapper and the qrtr nameserver are in-kernel on 7.2; tqftpserv is not packaged by ALARM (built later, wifi)
for p in qrtr rmtfs; do pacman -S --noconfirm --needed "$p" || echo "NOPKG $p"; done
# tqftpserv serves the wifi (WPSS) firmware files and ALARM does not package it: build taOS's release
if [ ! -x /usr/bin/tqftpserv ]; then
  pacman -S --noconfirm --needed base-devel meson git zstd
  S=/root/src/tqftpserv
  rm -rf "$S"
  git clone -q --branch v1.2 https://github.com/linux-msm/tqftpserv "$S"
  [ "$(git -C "$S" rev-parse HEAD)" = b6bb92d40cfffe28621abcf7bfaa6d99beea46cb ] || { echo "tqftpserv v1.2 moved"; exit 1; }
  meson setup --prefix=/usr "$S/build" "$S"
  ninja -C "$S/build" install
fi
EOF
chroot "$MNT" /bin/bash /root/stage-a.sh

step device-bits
# Kernel modules + firmware come from taOS, so they match the pmOS kernel exactly
mkdir -p "$MNT/usr/lib/modules" "$MNT/usr/lib/firmware"
rm -rf "$MNT/usr/lib/modules/$(uname -r)"
cp -a "/lib/modules/$(uname -r)" "$MNT/usr/lib/modules/"
cp -a /lib/firmware/. "$MNT/usr/lib/firmware/"
cp -a /etc/NetworkManager/system-connections/. "$MNT/etc/NetworkManager/system-connections/"
chmod 600 "$MNT"/etc/NetworkManager/system-connections/* 2>/dev/null || true
mkdir -p "$MNT/var/lib/tailscale"; cp -a /var/lib/tailscale/. "$MNT/var/lib/tailscale/"

step config
echo omarchy-mobile > "$MNT/etc/hostname"
cat > "$MNT/root/stage-a-config.sh" <<'EOF'
set -eu
id jay >/dev/null 2>&1 || useradd -m -G wheel -s /bin/bash jay
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/10-wheel
# The default alarm user has a well-known password: remove it
userdel -r alarm 2>/dev/null || true
passwd -l root
systemctl enable sshd NetworkManager tailscaled dualboot-marker.service dualboot-heartbeat.service dualboot-return.service dualboot-usbnet.service
# ALARM enables systemd-networkd, which would fight NetworkManager for wlan0
systemctl disable systemd-networkd.service systemd-networkd.socket 2>/dev/null || true
for u in rmtfs tqftpserv; do systemctl enable "$u" 2>/dev/null || echo "NOUNIT $u"; done
EOF
. "$HERE/guest-units.sh"
dualboot_guest_units "$MNT" /usr/local/bin Omarchy arch omarchy-mobile
mkdir -p "$MNT/mnt/taos"
chroot "$MNT" /bin/bash /root/stage-a-config.sh
# useradd -m creates the home as jay; it is only seeded here, after the user exists. The chown
# also repairs a home that an earlier run of this script left owned by root.
chroot "$MNT" chown jay:jay /home/jay
mkdir -p "$MNT/home/jay/.ssh"
cp /home/jay/.ssh/authorized_keys "$MNT/home/jay/.ssh/authorized_keys"
chmod 700 "$MNT/home/jay/.ssh"; chmod 600 "$MNT/home/jay/.ssh/authorized_keys"
chroot "$MNT" chown -R jay:jay /home/jay/.ssh
# jay's password = taOS's hash, so sudo on the guest uses the same password
H=$(awk -F: '$1=="jay"{print $2}' /etc/shadow); chroot "$MNT" usermod -p "$H" jay
. "$HERE/guest-agent.sh"
dualboot_agent_account "$MNT" "$MNT/home/taos-agent" /home/taos-agent /home/jay/.ssh/authorized_keys

step umount
sync
# pacman-key leaves a gpg-agent inside the chroot holding /dev (umount: target is busy, measured)
for p in /proc/[0-9]*; do [ "$(readlink "$p/root")" = "$MNT" ] && kill "${p#/proc/}" 2>/dev/null; done
sleep 1
for m in run dev/pts dev sys proc; do umount "$MNT/$m" 2>/dev/null || umount -l "$MNT/$m" 2>/dev/null || true; done
umount "$MNT"
step DONE
