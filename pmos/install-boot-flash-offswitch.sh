#!/bin/sh
# Install the boot-slot flash off-switch on the handset, then PROVE it works.
#
# Run as root ON the device. Idempotent.
#
# A pass here must not be confusable with "nothing was measured", so the check
# installs a real leaf package and requires TWO independent observations plus a
# positive control:
#   1. boot_b is byte-identical before and after
#   2. /boot/boot.img mtime is unchanged (boot-deploy did not even rebuild)
#   3. positive control: the package really did install, so the transaction ran
#
# What this canNOT prove, and must not claim to: that a DELIBERATE kernel change
# still reaches boot_b. That path is `pmbootstrap flasher flash_kernel` from the
# build host, which this switch does not touch. Measuring it here would test the
# very flash the switch disables by design, and a correct guard would read as a
# broken install.
set -eu

SRC="$(dirname "$0")/etc-deviceinfo"
[ -r "$SRC" ] || { echo "FAIL: $SRC not readable"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }

install -m 644 "$SRC" /etc/deviceinfo
echo "installed /etc/deviceinfo"

# Read the value back through the REAL sourcing chain, not by grepping the file.
# deviceinfo_arch is the control: if it is empty the source did not happen and a
# "false" reading would mean nothing.
eval "$(
  sh -c '. /usr/share/misc/source_deviceinfo 2>/dev/null
         printf "RESOLVED=%s\nCONTROL_ARCH=%s\n" \
           "$deviceinfo_flash_kernel_on_update" "$deviceinfo_arch"'
)"
[ -n "${CONTROL_ARCH:-}" ] || { echo "FAIL: control empty - deviceinfo never sourced"; exit 1; }
[ "${RESOLVED:-}" = "false" ] || { echo "FAIL: resolved to '${RESOLVED:-}', not false"; exit 1; }
echo "PASS: resolves to false (control arch=$CONTROL_ARCH)"

[ "${1:-}" = "--verify-flash" ] || {
  echo "installed; re-run with --verify-flash to run the apk acceptance test"
  exit 0
}

PKG="${2:-tree}"
apk info -e "$PKG" >/dev/null 2>&1 && { echo "SKIP: $PKG already installed, pick another"; exit 1; }
apk add --simulate "$PKG" 2>&1 | grep -q "^(1/1)" || {
  echo "FAIL: $PKG is not a single dependency-free leaf; pick another"; exit 1; }

PRE="$(sha256sum /dev/disk/by-partlabel/boot_b | cut -d' ' -f1)"
PRE_IMG="$(stat -c %Y /boot/boot.img)"
apk add "$PKG" >/dev/null 2>&1
POST="$(sha256sum /dev/disk/by-partlabel/boot_b | cut -d' ' -f1)"
POST_IMG="$(stat -c %Y /boot/boot.img)"

apk info -e "$PKG" >/dev/null 2>&1 || { echo "FAIL: control - $PKG did not install, nothing was tested"; exit 1; }
[ "$PRE" = "$POST" ] || { echo "FAIL: boot_b CHANGED $PRE -> $POST"; exit 1; }
[ "$PRE_IMG" = "$POST_IMG" ] || { echo "FAIL: boot.img was rebuilt (mtime moved)"; exit 1; }
echo "PASS: boot_b identical ($PRE), boot.img not rebuilt, and $PKG really installed"
