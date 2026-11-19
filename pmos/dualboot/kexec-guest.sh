#!/bin/sh
# Stage a guest OS for a RAM-only kexec from taOS (runs on the handset as root).
# Usage: kexec-guest.sh [--guest omarchy|luma] [--kernel <linux-*.apk>] [--return-after <seconds>] [--go]
#        (omarchy by default)
# --return-after is for tests: the guest reboots back to taOS by itself after that many seconds.
# Without it a launch clears the timer, so a real switch stays in the other system until it is rebooted.
# Writes no partition and no slot attribute. Without --go it only loads; with --go it hands over
# via systemctl kexec, so taOS stops cleanly and its root (which the guest mounts rw) is unmounted.
# Inputs in $D/guest: kernel + ramdisk unpacked from the ACTIVE slot's boot image (refresh-guest-boot.sh),
# so modules on taOS's root match. guest-init sits next to this script.
# --kernel boots an UNFLASHED kernel package instead: its Image and its own modules (bound over the
# guest's by guest-init) come from the apk, so a new kernel is tried from RAM before any flash.
set -eu
D=/var/lib/dualboot
G=$D/guest
HERE=$(dirname "$0")
. "$HERE/build-identity.sh"
GUEST=omarchy GO= KAPK= RA=
while [ $# -gt 0 ]; do
  case "$1" in
    --guest) GUEST=${2:?--guest needs a name}; shift ;;
    --kernel) KAPK=${2:?--kernel needs a package}; shift ;;
    --return-after) RA=${2:?--return-after needs seconds}; shift ;;
    --go) GO=1 ;;
    *) echo "unknown argument $1"; exit 1 ;;
  esac
  shift
done
case "$RA" in
  '') ;;
  *[!0-9]*) echo "--return-after takes whole seconds"; exit 1 ;;
  *) [ "${#RA}" -le 5 ] && [ "$RA" -ge 10 ] || { echo "--return-after must be 10..99999"; exit 1; } ;;
esac
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
  # The apk's modules.dep lists no dependencies (apk's install trigger runs depmod); without this
  # usb_f_ncm loads before u_ether and the guest's USB network never comes up
  depmod -b "$K/usr" "$(uname -r)"
  grep -q '^kernel/drivers/usb/gadget/function/usb_f_ncm.ko[^:]*: .*u_ether' "$K/usr/lib/modules/$(uname -r)/modules.dep" || { echo "depmod left usb_f_ncm without u_ether"; exit 1; }
  zcat "$K/boot/vmlinuz" > "$G/Image"
  strings "$G/Image" | grep -m1 '^Linux version'
else
  zcat "$G/kernel" > "$G/Image"
  # Same build as the running kernel, or the modules on taOS's root will not load
  dualboot_stream_matches_running < "$G/Image" || { echo "kernel build != running"; exit 1; }
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
if [ -z "$GO" ]; then
  # a dry run must not leave an image armed for the next systemctl kexec
  kexec -u || { echo "kexec -u failed: kexec image may still be loaded"; exit 1; }
  [ "$(cat /sys/kernel/kexec_loaded 2>/dev/null)" = 0 ] || { echo "kexec_loaded is not 0: kexec image may still be loaded"; exit 1; }
  echo "unloaded (dry run)"
  exit 0
fi
if [ -n "$GO" ]; then
  # The guest's dualboot-return unit reads this file from taOS's root. Written or removed HERE, at
  # the moment of going, so a timer armed for one test never follows a later real switch.
  if [ -n "$RA" ]; then echo "$RA" > "$D/return-after"; else rm -f "$D/return-after"; fi
  # The q6v5 remoteprocs have no shutdown hook, so kexec leaves them running and the guest kernel's
  # attempt to boot them again hard-reset the phone (guest died at 5.6s as wpss/cdsp/adsp powered up;
  # with their driver blacklisted the guest lived). Stop them first; wifi and ssh drop here.
  # From here a failure would leave taOS up without wifi, modem or touch: on any error unload the image
  # and reboot normally, which brings every device back. The handler is cleared just before the handover.
  go_failed() {
    trap - EXIT
    echo "$(date -u +%H:%M:%S) --go failed with status $1 after teardown began; unloading and rebooting" >> "$D/go.log"
    if [ "$(cat /sys/kernel/kexec_loaded 2>/dev/null)" = 1 ]; then kexec -u >> "$D/go.log" 2>&1 || true; fi
    systemctl reboot >> "$D/go.log" 2>&1 || reboot -f
    exit "$1"
  }
  trap 'go_failed $?' EXIT
  trap 'exit 1' HUP INT TERM
  for r in /sys/class/remoteproc/remoteproc*; do
    [ "$(cat "$r/state")" = running ] && echo stop > "$r/state"
    echo "$(cat "$r/name") $(cat "$r/state")" >> "$D/rproc-stop.log"
  done
  # Same for GPI DMA: a bound geni_spi keeps its channels, and the guest's touch SPI then fails
  # "EV ALLOCATE ret:-5" and times out. Unbinding releases them (touch on taOS stops here).
  for d in /sys/bus/platform/drivers/geni_spi/*.spi; do
    [ -e "$d" ] && echo "${d##*/}" > /sys/bus/platform/drivers/geni_spi/unbind
  done
  trap - EXIT HUP INT TERM
  systemctl kexec || go_failed $?
fi
exit 0
