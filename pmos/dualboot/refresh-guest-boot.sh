#!/bin/sh
# On the handset in taOS, as root: refresh $D/guest/kernel and $D/guest/ramdisk from the ACTIVE
# slot's boot image. kexec-guest.sh refuses a kernel whose build differs from the running one, so
# this runs once after every kernel flash (the unpack is otherwise a one-time hand step and went
# stale at r8 -> r9). The previous pair is kept as kernel.prev / ramdisk.prev.
set -eu
D=/var/lib/dualboot
G=$D/guest
HERE=$(dirname "$0")
. "$HERE/build-identity.sh"
slot=$(sed -n 's/.*androidboot\.slot_suffix=\(_[ab]\).*/\1/p' /proc/cmdline)
[ -n "$slot" ] || { echo "no slot_suffix on the command line"; exit 1; }
PART=/dev/disk/by-partlabel/boot$slot
[ -b "$PART" ] || { echo "no $PART"; exit 1; }
mkdir -p "$G"
rm -f "$G/kernel.new" "$G/ramdisk.new"
# Android boot image header (v0..v2): kernel_size, kernel_addr, ramdisk_size, ramdisk_addr at
# byte 8; page_size at 36; kernel starts at page 1, ramdisk on the next page boundary
python3 - "$PART" "$G" <<'EOF'
import struct, sys
part, g = sys.argv[1], sys.argv[2]
with open(part, "rb") as f:
    h = f.read(4096)
    if h[:8] != b"ANDROID!":
        sys.exit("not an Android boot image: %r" % h[:8])
    ks, _, rs, _ = struct.unpack("<IIII", h[8:24])
    ps = struct.unpack("<I", h[36:40])[0]
    pages = lambda n: (n + ps - 1) // ps * ps
    f.seek(ps); kernel = f.read(ks)
    f.seek(ps + pages(ks)); ramdisk = f.read(rs)
if len(kernel) != ks or len(ramdisk) != rs:
    sys.exit("short read from %s" % part)
if kernel[:2] != b"\x1f\x8b":
    sys.exit("kernel is not gzip; kexec-guest.sh expects zcat to work on it")
open(g + "/kernel.new", "wb").write(kernel)
open(g + "/ramdisk.new", "wb").write(ramdisk)
print("kernel %d ramdisk %d page %d" % (ks, rs, ps))
EOF
# Validate the candidate before it touches the published pair: a rejected one must not become
# kernel/ramdisk, nor push the good pair out to .prev on the next try
zcat "$G/kernel.new" | dualboot_stream_matches_running \
  || { rm -f "$G/kernel.new" "$G/ramdisk.new"; echo "unpacked kernel build != running kernel; is $PART the slot you booted?"; exit 1; }
for f in kernel ramdisk; do [ -f "$G/$f" ] && cp -a "$G/$f" "$G/$f.prev"; done
mv -f "$G/kernel.new" "$G/kernel"
mv -f "$G/ramdisk.new" "$G/ramdisk"
echo "guest boot refreshed from $PART"
