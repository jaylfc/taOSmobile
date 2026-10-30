#!/bin/sh
# Stage the Omarchy guest for a RAM-only kexec from taOS (runs on the handset as root).
# Writes no partition and no slot attribute. Without --go it only loads; with --go it hands over
# via systemctl kexec, so taOS stops cleanly and its root (which the guest mounts rw) is unmounted.
# Inputs in $D/guest: kernel + ramdisk unpacked from the ACTIVE slot's boot image (unpack_bootimg),
# so modules on taOS's root match. guest-init sits next to this script.
set -eu
D=/var/lib/dualboot
G=$D/guest
HERE=$(dirname "$0")

[ -f "$D/arch-root.img" ] || { echo "no $D/arch-root.img"; exit 1; }
[ -f "$G/kernel" ] && [ -f "$G/ramdisk" ] || { echo "need $G/kernel and $G/ramdisk"; exit 1; }
command -v kexec >/dev/null || apk add --no-progress kexec-tools
# guest-init's kernel log writer reruns this after switch_root, where no musl loader exists
[ -x /bin/busybox.static ] || apk add --no-progress busybox-static

zcat "$G/kernel" > "$G/Image"
# Same build as the running kernel, or the modules on taOS's root will not load
want="$(uname -r) (.*) $(uname -v | cut -d' ' -f1)"
strings "$G/Image" | grep -m1 '^Linux version' | grep -q "^Linux version $want" || { echo "kernel build != running"; exit 1; }

rm -rf "$G/rd"; mkdir -p "$G/rd"
(cd "$G/rd" && zcat "$G/ramdisk" | cpio -id 2>/dev/null)
mv "$G/rd/init" "$G/rd/init.pmos"
install -m755 "$HERE/guest-init" "$G/rd/init"
(cd "$G/rd" && find . | cpio -o -H newc 2>/dev/null | gzip) > "$G/initramfs.gz"

# The live tree carries abl's fixups (memory, reserved-memory) that the packed dtb lacks
cat /sys/firmware/fdt > "$G/live.dtb"
CMD=$(tr ' ' '\n' < /proc/cmdline | grep -vE '^(quiet|splash|ro|plymouth\..*|pmos_.*)$' | tr '\n' ' ')
CMD="$CMD panic=10 printk.devkmsg=on systemd.log_level=info systemd.log_target=kmsg"
echo "cmdline: $CMD"

kexec -l "$G/Image" --initrd="$G/initramfs.gz" --dtb="$G/live.dtb" --command-line="$CMD"
echo "kexec loaded"
[ "${1:-}" = --go ] && systemctl kexec
exit 0
