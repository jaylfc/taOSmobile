#!/bin/sh
# Install the taOS mobile Plymouth boot splash on the handset.
#
# WHY THIS IS A FILE IN THIS REPO, not a hand-edit on the device: a reflash
# wipes /usr/share/plymouth, and a splash that only exists on the running
# rootfs is one flash away from being gone (boundary (c), bus 4375).
#
# THE THING THAT IS EASY TO GET WRONG: Plymouth runs from the INITRAMFS, which
# embeds its OWN copy of /usr/share/plymouth. Installing the theme into the
# rootfs and setting it default changes nothing about what you see at boot --
# the initramfs must be rebuilt, and the resulting boot.img flashed. This
# script does the first two and then tells you the flash is still owed; it
# deliberately does NOT flash, because on this device there is no software path
# into fastboot and a flash needs hands on the hardware.
#
# Usage:  sudo sh install-taos-theme.sh [--verify]
#         --verify  check an existing install and change nothing

set -eu

THEME_NAME="taos"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/taos"
DEST_DIR="/usr/share/plymouth/themes/${THEME_NAME}"
FILES="taos.plymouth taos.script wordmark.png wordmark-glow.png"

VERIFY_ONLY=0
[ "${1:-}" = "--verify" ] && VERIFY_ONLY=1

fail() { echo "FAIL: $*" >&2; exit 1; }

# The theme is only real if the initramfs carries it AND is configured to use
# it. Both are checked, because either alone boots the old splash.
verify() {
    rc=0

    for f in $FILES; do
        [ -f "${DEST_DIR}/${f}" ] || { echo "MISSING in rootfs: ${DEST_DIR}/${f}"; rc=1; }
    done

    theme="$(plymouth-set-default-theme 2>/dev/null || echo "?")"
    echo "rootfs default theme: ${theme}"
    [ "$theme" = "$THEME_NAME" ] || { echo "rootfs theme is not ${THEME_NAME}"; rc=1; }

    if [ -f /boot/initramfs ]; then
        n="$(zcat /boot/initramfs | cpio -t 2>/dev/null | grep -c "themes/${THEME_NAME}/" || true)"
        # Control: a term known to be present, so "0 taos entries" cannot be
        # confused with "the listing produced nothing at all".
        total="$(zcat /boot/initramfs | cpio -t 2>/dev/null | wc -l)"
        echo "initramfs: ${n} ${THEME_NAME} entries (of ${total} total)"
        [ "$total" -gt 0 ] || fail "could not read /boot/initramfs -- the count above measured nothing"
        [ "$n" -ge 4 ] || { echo "initramfs does not carry the theme"; rc=1; }
    else
        echo "/boot/initramfs absent"; rc=1
    fi

    return $rc
}

if [ "$VERIFY_ONLY" = "1" ]; then
    verify || fail "verification failed"
    echo
    echo "PASS: rootfs and initramfs both carry ${THEME_NAME}."
    echo "NOTE: this says nothing about what is FLASHED. Compare /boot/boot.img"
    echo "      against the boot_b partition to know what will boot."
    exit 0
fi

[ -d "$SRC_DIR" ] || fail "theme source not found at ${SRC_DIR}"
for f in $FILES; do
    [ -f "${SRC_DIR}/${f}" ] || fail "theme source incomplete: missing ${f}"
done

echo "==> installing ${THEME_NAME} to ${DEST_DIR}"
mkdir -p "$DEST_DIR"
for f in $FILES; do
    cp "${SRC_DIR}/${f}" "${DEST_DIR}/${f}"
done
chown -R root:root "$DEST_DIR"
chmod 644 "${DEST_DIR}"/*

echo "==> setting default theme"
plymouth-set-default-theme "$THEME_NAME"

echo "==> rebuilding initramfs (this restages /boot/boot.img)"
before="$(sha256sum /boot/boot.img 2>/dev/null | cut -d' ' -f1 || echo none)"
mkinitfs
after="$(sha256sum /boot/boot.img | cut -d' ' -f1)"
echo "    boot.img $(echo "$before" | cut -c1-16) -> $(echo "$after" | cut -c1-16)"
[ "$before" != "$after" ] || echo "    WARNING: boot.img is unchanged -- expected a rebuild"

echo
verify || fail "post-install verification failed"

echo
echo "================================================================"
echo "INSTALLED AND STAGED -- BUT NOT YET WHAT BOOTS."
echo
echo "/boot/boot.img now contains the ${THEME_NAME} splash. The boot_b"
echo "partition does NOT until it is flashed, and this script does not"
echo "flash: there is no software path into fastboot on this device."
echo
echo "To finish, with the phone attached to the build host:"
echo "  1. power off, then hold Volume Down + Power to reach fastboot"
echo "     (NEVER both volume keys -- that is EDL)"
echo "  2. fastboot flash boot_b /path/to/boot.img"
echo "================================================================"
