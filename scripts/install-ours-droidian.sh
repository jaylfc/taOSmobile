#!/bin/bash
# Install OUR Droidian build onto spacewar. Run from the Pi with the phone in
# stock recovery (slot B) and adb AUTHORISED. userdata is not slotted, so the
# /data work is slot-independent; slot A is restored at the end.
set -euo pipefail
S=~/taosflash
log(){ printf "\n=== %s ===\n" "$*"; }

log "adb state"
adb devices
adb wait-for-device
[ "$(adb get-state)" = "recovery" ] || { echo "NOT in recovery, aborting"; exit 1; }

log "mount /data"
adb shell "mount /data 2>/dev/null; ls -la /data"

log "remove old porter install"
adb shell "rm -f /data/rootfs.img /data/android-rootfs.img; rm -rf /data/android-data; ls -la /data"

log "push tools"
adb push "$S/e2fsck" /data/
adb push "$S/resize2fs" /data/
adb shell "chmod 755 /data/e2fsck /data/resize2fs"

log "push rootfs (4GiB, several minutes)"
time adb push "$S/data/rootfs.img" /data/
adb shell "ls -l /data/rootfs.img"

log "fsck before resize"
adb shell "/data/e2fsck -fy /data/rootfs.img" || true

log "resize 4G -> 32G"
adb shell "/data/resize2fs -f /data/rootfs.img 32G"

log "fsck after resize"
adb shell "/data/e2fsck -fy /data/rootfs.img" || true

log "android-rootfs symlink"
adb shell "ln -s /halium-system/var/lib/lxc/android/android-rootfs.img /data/android-rootfs.img"
adb shell "ls -la /data"

log "back to bootloader, restore slot A"
adb reboot bootloader
sleep 12
sudo fastboot getvar current-slot 2>&1 | head -1
sudo fastboot set_active a
sudo fastboot getvar current-slot 2>&1 | head -1
sudo fastboot getvar slot-unbootable:a 2>&1 | head -1

echo
echo "READY. Next: sudo fastboot reboot   (then watch the Pi for RNDIS / 172.16.42.1)"
