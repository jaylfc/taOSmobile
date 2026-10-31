#!/bin/bash
# Build host, as root: turn Luma Mobile's generic porting image into luma-root.img for the handset.
# Luma ships a userdata image (Android sparse, zstd) holding a 4096-byte-sector GPT: p1 an empty
# vfat "pmOS_boot", p2 the ext4 "pmOS_root" carrying an OSTree sysroot. Only p2 is kept, grown to a
# sparse 32G and relabelled, since taOS's own root is also labelled pmOS_root. The UUID stays: the
# deployment's fstab mounts /sysroot by it. Output: $OUT/luma-root.img.zst (sparse when unpacked
# with zstd -d --sparse). The download is checked against Luma's published SHA256SUMS.
set -euo pipefail
W=${1:?usage: build-luma-image.sh <workdir holding luma-mobile-alpha1-fp6-userdata.sparse.zst and sums>}
SIZE=32G
cd "$W"
say() { echo "=== $(date -u +%H:%M:%S) $*"; }

say verify
sha256sum -c sums --ignore-missing
say unpack
zstd -dqf luma-mobile-alpha1-fp6-userdata.sparse.zst -o userdata.sparse
simg2img userdata.sparse userdata.raw
L=$(losetup --show -rPf -b 4096 userdata.raw)
trap 'losetup -d "$L"' EXIT
udevadm settle
[ "$(blkid -o value -s TYPE "${L}p2")" = ext4 ] || { echo "${L}p2 is not ext4"; exit 1; }
say "extract p2"
rm -f luma-root.img
dd if="${L}p2" of=luma-root.img bs=4M conv=sparse status=none
say "grow to $SIZE"
e2fsck -fy luma-root.img
truncate -s "$SIZE" luma-root.img
resize2fs luma-root.img
e2label luma-root.img luma-root
blkid luma-root.img
say compress
zstd -qf -T0 luma-root.img -o luma-root.img.zst
sha256sum luma-root.img.zst | tee luma-root.img.zst.sha256
say DONE
