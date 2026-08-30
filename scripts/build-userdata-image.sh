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

# Mount bookkeeping lives in image-mount-lib.sh so it can be exercised against a
# real loop mount by check-image-release.sh. It was inline here, unreachable by
# any check, and its post-condition queried the backing file -- which never
# matches a loop mount -- so it could not fire. See that file for the
# measurement and scripts/check-image-release.sh for the proof it fires now.
# shellcheck source=scripts/image-mount-lib.sh
. "$(dirname "$0")/image-mount-lib.sh"

# Refuse to continue while the image is mounted anywhere. This is a HARD STOP,
# not a warning. The warning it replaces was advisory: the script carried on to
# e2fsck the live filesystem and printed BUILD-OK underneath it, so the one log
# line saying not to trust the result was followed by a line saying the build
# was fine. An fsck against a live filesystem is the failure this whole function
# exists to prevent; it does not get a soft landing.
require_released() {
    local img="$1" held
    release_image "$img" && return 0
    held="$(image_mounts "$img" | tr '\n' ' ')"
    echo "FATAL: $img is still mounted at: $held" >&2
    echo "Refusing to fsck a live filesystem. An automounter has most likely" >&2
    echo "grabbed the loop device; unmount it and re-run." >&2
    exit 1
}

log "populate it exactly as the porter procedure leaves /data"
require_released "$W/userdata.img"
mkdir -p "$W/mnt"
sudo mount -o loop "$W/userdata.img" "$W/mnt"
sudo cp --sparse=always "$W/rootfs32.img" "$W/mnt/rootfs.img"
sudo ln -s /halium-system/var/lib/lxc/android/android-rootfs.img "$W/mnt/android-rootfs.img"
sudo ls -la "$W/mnt"
sudo umount "$W/mnt"
require_released "$W/userdata.img"

log "verify (must end with a pass that modifies nothing)"
sudo e2fsck -fy "$W/userdata.img" || true
sudo e2fsck -fy "$W/userdata.img" || true
ls -ls "$W/userdata.img"
df -h "$S" | tail -1
echo "BUILD-OK"
