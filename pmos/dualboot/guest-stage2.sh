#!/bin/bash
# Stage 2a, run INSIDE the booted Omarchy guest as root: Omarchy's desktop from its own [omarchy] repo,
# the way omarchy-mobile's rootfs/build-inside picks it (packages.base, then Omarchy's
# omarchy-base.packages filtered to what exists for aarch64, minus packages.omarchy-skip).
# Their [omamobile] packages need building from PKGBUILDs first (no public binary repo): later.
# Graphics: Arch Linux ARM's mesa (freedreno for this Adreno), not their vulkan-panfrost (Pixel Mali).
set -euo pipefail
LOG=/var/log/dualboot-stage2.log
exec >>"$LOG" 2>&1
say() { echo "=== $(date -u +%H:%M:%S) $*"; }
list() { grep -v -E '^\s*(#|$)' "$1" | awk '{print $1}'; }
available() { pacman -Sp --print-format %n "$1" >/dev/null 2>&1; }

say start
if ! grep -q '^\[omarchy\]' /etc/pacman.conf; then
  # [omarchy] ahead of [core], as in their pacman.conf
  sed -i '0,/^\[core\]/s//[omarchy]\nServer = https:\/\/pkgs.omarchy.org\/stable\/$arch\n\n[core]/' /etc/pacman.conf
fi
pacman-key --init 2>/dev/null || true
pacman-key --populate archlinuxarm
if ! pacman-key --list-keys 2>/dev/null | grep -qi omarchy; then
  # The key ships inside omarchy-keyring, so its own signature cannot be checked first: populate from
  # the unpacked package, then install it normally, signature checked
  say omarchy-keyring
  pacman -Sy
  pacman -Sw --noconfirm --config <(sed 's/^SigLevel.*/SigLevel = Never/' /etc/pacman.conf) omarchy-keyring
  rm -rf /tmp/okr; mkdir /tmp/okr
  bsdtar -xf "$(ls /var/cache/pacman/pkg/omarchy-keyring-*.pkg.tar.* | grep -v '\.sig$' | tail -1)" -C /tmp/okr
  cp /tmp/okr/usr/share/pacman/keyrings/omarchy* /usr/share/pacman/keyrings/
  pacman-key --populate omarchy
  rm /usr/share/pacman/keyrings/omarchy*
  pacman -S --noconfirm omarchy-keyring
fi

say sync
pacman -Syu --noconfirm

say base
# linux-firmware-other would collide with the pmOS firmware copied into /lib/firmware
mapfile -t base < <(list /root/omarchy-src/packages.base | grep -v -x -E 'vulkan-panfrost|linux-firmware-other')
pacman -S --noconfirm --needed "${base[@]}" hyprland mesa vulkan-freedreno

say "omarchy-base.packages"
mapfile -t skip < <(list /root/omarchy-src/packages.omarchy-skip)
want=() unavailable=()
while read -r p; do
  [[ " ${skip[*]} " == *" $p "* ]] && continue
  if available "$p"; then want+=("$p"); else unavailable+=("$p"); fi
done < <(list /usr/share/omarchy/install/omarchy-base.packages)
echo "want ${#want[@]}, not built for aarch64: ${unavailable[*]}"
pacman -S --noconfirm --needed "${want[@]}"

say "desktop session"
# jay predates the omarchy package, so useradd never copied its /etc/skel: seed it the way
# omarchy-reinstall-configs does, minus that script's limine/plymouth refresh (abl boots this phone)
runuser -u jay -- cp -af /etc/skel/. /home/jay/
runuser -u jay -- env OMARCHY_SETUP_CONTEXT=provision-owner omarchy-provision-user --first-install
# The panel is 1080x2400 at 68 mm wide: scale 2.5 gives a 432x960 logical desktop
grep -q '"DSI-1"' /home/jay/.config/hypr/monitors.lua ||
  echo 'hl.monitor({ output = "DSI-1", mode = "1080x2400@120", position = "0x0", scale = 2.5 })' \
    >> /home/jay/.config/hypr/monitors.lua
printf '[Autologin]\nUser=jay\nSession=hyprland-uwsm\n' > /etc/sddm.conf.d/20-autologin.conf
systemctl enable sddm
say DONE
