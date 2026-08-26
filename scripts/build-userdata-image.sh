#!/bin/bash
# Build a complete /data ext4 image, ready for `fastboot flash userdata`.
#
# This exists because stock recovery on spacewar shows no adb authorisation
# prompt, so the push-to-/data route the porter's installer uses cannot be
# driven remotely. Everything that used to need an on-device shell -- the
# rootfs resize in particular -- happens here instead, and the result goes on
# in one fastboot write.
#
# Feature flags matter: the Halium kernel is 5.4, which cannot mount ext4 with
# orphan_file (6.x) or fast_commit (5.10+). e2fsprogs 1.47 does not enable
# either by default, but disable them explicitly so a toolchain bump cannot
# silently produce a filesystem the phone refuses to mount.
#
# Run on the Linux USB host. Needs mkfs.ext4, resize2fs, e2fsck and sudo.
set -euo pipefail
export PATH="$PATH:/sbin:/usr/sbin"

S="${TAOSFLASH:-$HOME/taosflash}"
W="$S/work"
FS_SIZE="${FS_SIZE:-40G}"          # /data filesystem; partition is 226GiB.
ROOTFS_SIZE="${ROOTFS_SIZE:-32G}"  # what the porter's procedure resizes to.

log() { printf '\n=== %s ===\n' "$*"; }

[ -f "$S/data/rootfs.img" ] || { echo "missing $S/data/rootfs.img"; exit 1; }
mkdir -p "$W"

log "sparse copy of the api30 rootfs"
rm -f "$W/rootfs32.img"
cp --sparse=always "$S/data/rootfs.img" "$W/rootfs32.img"
ls -ls "$W/rootfs32.img"

log "grow the file, then the filesystem, to $ROOTFS_SIZE"
truncate -s "$ROOTFS_SIZE" "$W/rootfs32.img"
e2fsck -fy "$W/rootfs32.img" || true
resize2fs -f "$W/rootfs32.img" "$ROOTFS_SIZE"
e2fsck -fy "$W/rootfs32.img" || true
ls -ls "$W/rootfs32.img"

log "make the /data filesystem ($FS_SIZE)"
rm -f "$W/userdata.img"
truncate -s "$FS_SIZE" "$W/userdata.img"
mkfs.ext4 -F -L data -m 0 \
  -O '^orphan_file,^fast_commit' \
  -E lazy_itable_init=1,lazy_journal_init=1 \
  "$W/userdata.img"

log "populate it exactly as the porter procedure leaves /data"
mkdir -p "$W/mnt"
sudo mount -o loop "$W/userdata.img" "$W/mnt"
sudo cp --sparse=always "$W/rootfs32.img" "$W/mnt/rootfs.img"
sudo ln -s /halium-system/var/lib/lxc/android/android-rootfs.img "$W/mnt/android-rootfs.img"
sudo ls -la "$W/mnt"
sudo umount "$W/mnt"

log "verify"
e2fsck -fy "$W/userdata.img" || true
ls -ls "$W/userdata.img"
df -h "$S" | tail -1
echo "BUILD-OK"
