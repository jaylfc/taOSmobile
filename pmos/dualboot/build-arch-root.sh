#!/bin/sh
# Build the Omarchy guest root as a sparse loop image on taOS's root (runs on the handset as root).
# Stage A: Arch Linux ARM base that boots, joins wifi + tailnet, and leaves a marker. Desktop later.
set -eu
D=/var/lib/dualboot
IMG=$D/arch-root.img
MNT=/mnt/arch
LOG=$D/build.log
mkdir -p "$D" "$MNT"
exec >>"$LOG" 2>&1
step() { echo "=== $(date -u +%H:%M:%S) $*"; }

step start
apk add --no-progress libarchive-tools arch-install-scripts 2>/dev/null || apk add --no-progress libarchive-tools

if [ ! -e "$IMG" ]; then
  step image
  truncate -s 64G "$IMG"
  mkfs.ext4 -q -L omarchy-root "$IMG"
fi
mountpoint -q "$MNT" || mount -o loop "$IMG" "$MNT"

if [ ! -x "$MNT/usr/bin/pacman" ]; then
  step fetch
  cd "$D"
  wget -q -O alarm.tar.gz http://os.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz
  wget -q -O alarm.tar.gz.md5 http://os.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz.md5
  [ "$(md5sum alarm.tar.gz | cut -d' ' -f1)" = "$(cut -d' ' -f1 alarm.tar.gz.md5)" ] || { echo "MD5 MISMATCH"; exit 1; }
  step extract
  bsdtar -xpf alarm.tar.gz -C "$MNT"
  rm -f alarm.tar.gz
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
mkdir -p "$MNT/home/jay/.ssh"
cp /home/jay/.ssh/authorized_keys "$MNT/home/jay/.ssh/authorized_keys"
. "$(dirname "$0")/guest-units.sh"
dualboot_guest_units "$MNT" /usr/local/bin Omarchy arch omarchy-mobile
mkdir -p "$MNT/mnt/taos"
chroot "$MNT" /bin/bash /root/stage-a-config.sh
chroot "$MNT" chown -R jay:jay /home/jay/.ssh
# jay's password = taOS's hash, so sudo on the guest uses the same password
H=$(awk -F: '$1=="jay"{print $2}' /etc/shadow); chroot "$MNT" usermod -p "$H" jay

step umount
sync
# pacman-key leaves a gpg-agent inside the chroot holding /dev (umount: target is busy, measured)
for p in /proc/[0-9]*; do [ "$(readlink "$p/root")" = "$MNT" ] && kill "${p#/proc/}" 2>/dev/null; done
sleep 1
for m in run dev/pts dev sys proc; do umount "$MNT/$m" 2>/dev/null || umount -l "$MNT/$m" 2>/dev/null || true; done
umount "$MNT"
step DONE
