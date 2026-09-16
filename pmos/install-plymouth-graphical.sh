#!/bin/sh
# Install the plymouth.graphical kernel-cmdline drop-in, then PROVE the real
# cmdline generator picks it up.
#
# Run as root, either ON the handset or INSIDE the pmbootstrap rootfs chroot on
# the build host. The chroot copy is the one that matters for a flash: the
# cmdline is baked into the boot.img header when boot-deploy repacks it, so a
# drop-in that exists only on the phone will NOT be in the image you flash.
# Idempotent.
#
# Why /etc and not /usr/lib: boot-deploy-functions.sh itself says
# "Please migrate to /etc/kernel-cmdline.d/ configuration files". /usr/lib is
# the distro's, and apk owns it.
#
# The check must not be confusable with "nothing was measured", so it reads the
# value back through `generate-kernel-cmdline` -- the real chain boot-deploy
# calls -- and requires a positive control from a DIFFERENT file in the same
# directory. If the generator never ran, the control is missing and the check
# fails rather than passing quietly.
set -eu

SRC="$(dirname "$0")/kernel-cmdline.d/30-taos-plymouth-graphical.conf"
DEST=/etc/kernel-cmdline.d/30-taos-plymouth-graphical.conf

[ -r "$SRC" ] || { echo "FAIL: $SRC not readable"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "FAIL: must run as root"; exit 1; }
command -v generate-kernel-cmdline >/dev/null 2>&1 || {
  echo "FAIL: generate-kernel-cmdline absent - wrong root, or boot-deploy not installed"
  exit 1
}

install -d -m 755 /etc/kernel-cmdline.d
install -m 644 "$SRC" "$DEST"
echo "installed $DEST"

CMDLINE="$(generate-kernel-cmdline)"
[ -n "$CMDLINE" ] || { echo "FAIL: generator produced nothing - measured NOTHING"; exit 1; }

# Control: 'splash' comes from /usr/lib/kernel-cmdline.d/20-plymouth.conf, a
# file this script does not touch. Its presence proves the generator really
# assembled the drop-in directory, so the absence of our argument would mean
# our argument, not a dead generator.
case " $CMDLINE " in
  *" splash "*) ;;
  *) echo "FAIL: control 'splash' missing - generator did not read the drop-ins"; exit 1 ;;
esac

case " $CMDLINE " in
  *" plymouth.graphical "*) ;;
  *) echo "FAIL: plymouth.graphical absent from generated cmdline"; exit 1 ;;
esac

echo "PASS: generate-kernel-cmdline emits plymouth.graphical (control 'splash' present)"
echo
echo "cmdline now: $CMDLINE"
echo
echo "NOT YET PROVEN by this script, and it must not claim to be:"
echo "  - that the repacked boot.img header carries it. Verify after boot-deploy with:"
echo "      dd if=<boot.img> bs=1 skip=64 count=512 2>/dev/null | tr -d '\\0'"
echo "  - that plymouthd survives. Verify after the NEXT boot with:"
echo "      coredumpctl list | grep plymouthd   # expect no new entries"
