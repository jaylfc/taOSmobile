#!/bin/sh
# Stage a guest OS for a RAM-only kexec from taOS (runs on the handset as root).
# Usage: kexec-guest.sh [--guest omarchy|luma] [--kernel <linux-*.apk>] [--go]   (omarchy by default)
# Writes no partition and no slot attribute. Without --go it only loads; with --go it hands over
# via systemctl kexec, so taOS stops cleanly and its root (which the guest mounts rw) is unmounted.
# Inputs in $D/guest: kernel + ramdisk unpacked from the ACTIVE slot's boot image (unpack_bootimg),
# so modules on taOS's root match. guest-init sits next to this script.
# --kernel boots an UNFLASHED kernel package instead: its Image and its own modules (bound over the
# guest's by guest-init) come from the apk, so a new kernel is tried from RAM before any flash.
set -eu
D=/var/lib/dualboot
G=$D/guest
HERE=$(dirname "$0")
GUEST=omarchy GO= KAPK=
while [ $# -gt 0 ]; do
  case "$1" in
    --guest) GUEST=${2:?--guest needs a name}; shift ;;
    --kernel) KAPK=${2:?--kernel needs a package}; shift ;;
    --go) GO=1 ;;
    *) echo "unknown argument $1"; exit 1 ;;
  esac
  shift
done
case "$GUEST" in
  omarchy) IMG=$D/arch-root.img ;;
  luma) IMG=$D/luma-root.img ;;
  *) echo "unknown guest $GUEST"; exit 1 ;;
esac
[ -f "$IMG" ] || { echo "no $IMG"; exit 1; }
[ -f "$G/kernel" ] && [ -f "$G/ramdisk" ] || { echo "need $G/kernel and $G/ramdisk"; exit 1; }
command -v kexec >/dev/null || apk add --no-progress kexec-tools
# guest-init's kernel log writer reruns this after switch_root, where no musl loader exists
[ -x /bin/busybox.static ] || apk add --no-progress busybox-static

if [ -n "$KAPK" ]; then
  [ -f "$KAPK" ] || { echo "no $KAPK"; exit 1; }
  K=$D/test-kernel
  rm -rf "$K"; mkdir -p "$K"
  # an apk is concatenated gzip members (signature, control, data); zcat reads them all
  zcat "$KAPK" | tar -x -C "$K" 2>/dev/null || true
  [ -f "$K/boot/vmlinuz" ] && [ -d "$K/usr/lib/modules/$(uname -r)" ] || { echo "$KAPK has no vmlinuz or $(uname -r) modules"; exit 1; }
  zcat "$K/boot/vmlinuz" > "$G/Image"
  strings "$G/Image" | grep -m1 '^Linux version'
else
  zcat "$G/kernel" > "$G/Image"
  # Same build as the running kernel, or the modules on taOS's root will not load
  want="$(uname -r) (.*) $(uname -v | cut -d' ' -f1)"
  strings "$G/Image" | grep -m1 '^Linux version' | grep -q "^Linux version $want" || { echo "kernel build != running"; exit 1; }
fi

rm -rf "$G/rd"; mkdir -p "$G/rd"
(cd "$G/rd" && zcat "$G/ramdisk" | cpio -id 2>/dev/null)
mv "$G/rd/init" "$G/rd/init.pmos"
install -m755 "$HERE/guest-init" "$G/rd/init"
(cd "$G/rd" && find . | cpio -o -H newc 2>/dev/null | gzip) > "$G/initramfs.gz"

# The live tree carries abl's fixups (memory, reserved-memory) that the packed dtb lacks
cat /sys/firmware/fdt > "$G/live.dtb"
CMD=$(tr ' ' '\n' < /proc/cmdline | grep -vE '^(quiet|splash|ro|plymouth\..*|pmos_.*)$' | tr '\n' ' ')
# net.ifnames=0: the guest renamed wlan0 to wld0, which the NetworkManager connection from taOS does not match
CMD="$CMD panic=10 net.ifnames=0 printk.devkmsg=on systemd.log_level=info systemd.log_target=kmsg dualboot.guest=$GUEST"
[ -n "$KAPK" ] && CMD="$CMD dualboot.modules=${D#/}/test-kernel/usr/lib/modules"
if [ "$GUEST" = luma ]; then
  # Read the deployment at every launch, not once at staging: an update inside Luma adds a newer
  # boot entry (and keeps the old one for rollback), and the newest version is the one to boot
  M=$(mktemp -d)
  mount -o ro,noload "$IMG" "$M"
  OST=$(for e in "$M"/boot/loader/entries/*.conf; do
          printf '%s %s\n' "$(sed -n 's/^version //p' "$e")" "$(tr ' ' '\n' < "$e" | sed -n 's/^ostree=//p')"
        done | sort -n | tail -n1 | cut -d' ' -f2)
  umount "$M"; rmdir "$M"
  [ -n "$OST" ] || { echo "no ostree= boot entry in $IMG"; exit 1; }
  # The taOS kernel (r9 on) builds SELinux in but leaves it out of its default LSM order; Luma names it.
  # enforcing=0 (needs SECURITY_SELINUX_DEVELOP) until its root is relabelled: the staged files carry
  # no labels yet, and Luma's own config says enforcing. selinux must come before bpf.
  CMD="$CMD ostree=$OST lsm=landlock,lockdown,yama,loadpin,safesetid,selinux,smack,tomoyo,ipe,bpf enforcing=0"
fi
echo "cmdline: $CMD"

kexec -l "$G/Image" --initrd="$G/initramfs.gz" --dtb="$G/live.dtb" --command-line="$CMD"
echo "kexec loaded"
if [ -n "$GO" ]; then
  # The q6v5 remoteprocs have no shutdown hook, so kexec leaves them running and the guest kernel's
  # attempt to boot them again hard-reset the phone (guest died at 5.6s as wpss/cdsp/adsp powered up;
  # with their driver blacklisted the guest lived). Stop them first; wifi and ssh drop here.
  for r in /sys/class/remoteproc/remoteproc*; do
    [ "$(cat "$r/state")" = running ] && echo stop > "$r/state"
    echo "$(cat "$r/name") $(cat "$r/state")" >> "$D/rproc-stop.log"
  done
  # Same for GPI DMA: a bound geni_spi keeps its channels, and the guest's touch SPI then fails
  # "EV ALLOCATE ret:-5" and times out. Unbinding releases them (touch on taOS stops here).
  for d in /sys/bus/platform/drivers/geni_spi/*.spi; do
    [ -e "$d" ] && echo "${d##*/}" > /sys/bus/platform/drivers/geni_spi/unbind
  done
  systemctl kexec
fi
exit 0
