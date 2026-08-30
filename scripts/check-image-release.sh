#!/bin/bash
# Acceptance check for image-mount-lib.sh -- the code that has to guarantee the
# /data image is not mounted anywhere before build-userdata-image.sh fscks it.
#
# WHY THIS FILE EXISTS. d1389b1: a desktop automounter grabbed the freshly built
# image, e2fsck ran against a live filesystem and rewrote it underneath. bef6352
# fixed the unmount and added a post-condition that warns if the image is still
# held. Audited on 2026-08-30 (tsk-rej3yq) and MEASURED: that post-condition
# asked `findmnt --source <the image file>`, which matches nothing at all,
# because a loop mount appears in the mount table under its /dev/loopN name.
# The warning could never print. The guard read as covering the corruption and
# covered the empty set -- and nothing could tell, because the logic was inline
# in a script that needs sudo, loop devices and a 40 GiB build to reach.
#
# So the point of this file is narrow and specific: prove the "is it still
# mounted" predicate can say YES. A post-condition that cannot fail is not a
# post-condition, it is a comment.
#
# Runs anywhere with loop devices and sudo; SKIPs loudly without them, because
# an LXC container has no /dev/loop-control and a skip that reads as a pass is
# the same defect one level up. Uses a 32 MiB scratch image, never the real one.
set -uo pipefail
cd "$(dirname "$0")"
LIB="$PWD/image-mount-lib.sh"

PASS=0; FAIL=0; SKIP=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP  $1"; SKIP=$((SKIP+1)); }
give_up() { echo "GIVING UP: $1"; exit 3; }

# NOT `findmnt --target DIR`: --target resolves UPWARD to the containing
# filesystem, so it succeeds for any path that exists and an assertion built on
# it can never pass. --mountpoint matches the exact mountpoint and nothing else.
# This file is a fix for that family of bug; it does not get to contain one,
# which is why the first use below is preceded by a control proving it says YES
# to a mountpoint that really is mounted.
still_mounted() { [ -n "$(findmnt -n -o TARGET --mountpoint "$1" 2>/dev/null)" ]; }

[ -f "$LIB" ] || give_up "image-mount-lib.sh not found next to this script"
# shellcheck source=/dev/null
. "$LIB"
for fn in image_mounts release_image; do
    declare -F "$fn" >/dev/null || give_up "$LIB does not define $fn()"
done

if [ ! -e /dev/loop-control ]; then
    skip "no /dev/loop-control (container?): cannot make a loop mount to measure"
    echo; echo "checks: $PASS passed, $FAIL failed, $SKIP skipped"
    echo "VERDICT: INCOMPLETE -- nothing was measured on this host. Run it where"
    echo "the image is actually built (the Pi), not here."
    exit 2
fi
command -v losetup >/dev/null || give_up "losetup not on PATH"
command -v mkfs.ext4 >/dev/null || give_up "mkfs.ext4 not on PATH"
sudo -n true 2>/dev/null || give_up "no non-interactive sudo; mount/umount need it"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/check-image-release.XXXXXX")
IMG="$WORK/scratch.img"
MNT="$WORK/mnt"
cleanup() {
    # Never leave a loop device behind, whatever happened above.
    local mp dev
    for mp in $(findmnt -n -o TARGET --source "$IMG" 2>/dev/null) \
              $(for dev in $(losetup -j "$IMG" -O NAME --noheadings 2>/dev/null); do
                    findmnt -n -o TARGET --source "$dev" 2>/dev/null; done); do
        sudo umount "$mp" 2>/dev/null
    done
    for dev in $(losetup -j "$IMG" -O NAME --noheadings 2>/dev/null); do
        sudo losetup -d "$dev" 2>/dev/null
    done
    sudo rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$MNT"
truncate -s 32M "$IMG"
mkfs.ext4 -Fq "$IMG" || give_up "mkfs.ext4 failed on the scratch image"

echo "== control: an unmounted image reports no mounts =="
# If this fails the predicate is stuck saying YES and the checks below would
# pass for the wrong reason.
[ -z "$(image_mounts "$IMG")" ] \
    && ok "a fresh, unmounted image reports nothing" \
    || bad "an unmounted image already reports a mountpoint: $(image_mounts "$IMG")"

echo "== the predicate sees a mount made with 'mount -o loop <file>' =="
# This is exactly what build-userdata-image.sh does to populate the image.
sudo mount -o loop "$IMG" "$MNT" || give_up "could not loop-mount the scratch image"
TABLE_SRC=$(findmnt -n -o SOURCE --target "$MNT")
echo "        (mount table reports SOURCE=$TABLE_SRC)"
still_mounted "$MNT" \
    && ok "control: still_mounted() says YES to a mountpoint that IS mounted" \
    || bad "control: still_mounted() cannot see a live mount, so every later use of it is vacuous"
SEEN=$(image_mounts "$IMG")
if [ -n "$SEEN" ]; then
    ok "image_mounts reports it as mounted"
    [ "$SEEN" = "$MNT" ] \
        && ok "and names the right mountpoint" \
        || bad "names '$SEEN', expected '$MNT'"
else
    bad "image_mounts says NOTHING is mounted while the image IS mounted at $MNT"
    bad "  -> the post-condition in build-userdata-image.sh cannot fire; an fsck"
    bad "     against a live filesystem would be reported as a clean build"
fi

echo "== release_image actually releases that mount =="
if release_image "$IMG"; then
    ok "release_image returned success"
else
    bad "release_image returned failure"
fi
still_mounted "$MNT" \
    && bad "the mountpoint is STILL mounted after release_image" \
    || ok "the mountpoint is gone"
# A lingering loop device is reported but is NOT a failure: it is not the
# hazard. `losetup -d` on an AUTOCLEAR device returns 0 while a udev worker
# still holds it open (measured on the Pi: rc=0, device still listed 1.8s
# later), so demanding detachment here would make this check flaky about
# something that cannot corrupt an fsck. Live MOUNTS are the hazard, and the
# assertions above and below cover those.
LEFT="$(image_loop_devices "$IMG")"
[ -z "$LEFT" ] \
    && echo "        (no loop device left attached)" \
    || echo "        (loop device still attached, deferred detach: $LEFT -- not a failure)"

echo "== the predicate also sees a mount made by 'losetup' then 'mount <dev>' =="
# An automounter attaches the loop device first and mounts the DEVICE. That is
# the case d1389b1 was actually bitten by, and it is a different table entry
# from the one above, so it is worth measuring separately rather than assuming.
DEV=$(sudo losetup --find --show "$IMG") || give_up "losetup --find failed"
sudo mount "$DEV" "$MNT" || give_up "could not mount $DEV"
SEEN=$(image_mounts "$IMG")
if [ -n "$SEEN" ]; then
    ok "image_mounts reports the device-spelled mount too"
else
    bad "image_mounts misses a mount made as 'mount $DEV': an automounter's grab"
    bad "  -> is invisible to the guard, which is the exact case d1389b1 hit"
fi
release_image "$IMG" >/dev/null 2>&1
still_mounted "$MNT" \
    && bad "release_image left the device-spelled mount in place" \
    || ok "release_image cleared the device-spelled mount"

echo "== release_image's RETURN VALUE tracks whether a mount remains =="
# The post-condition is only worth anything if the value it returns is the same
# question the caller asks. Prove both directions against a real mount.
sudo mount -o loop "$IMG" "$MNT" || give_up "could not re-mount for the return-value test"
if [ -n "$(image_mounts "$IMG")" ]; then
    ok "with a mount present, image_mounts reports it"
else
    bad "with a mount present, image_mounts reports nothing"
fi
sudo umount "$MNT"
[ -z "$(image_mounts "$IMG")" ] \
    && ok "with the mount gone, image_mounts reports nothing" \
    || bad "with the mount gone, image_mounts still reports: $(image_mounts "$IMG")"
release_image "$IMG" >/dev/null 2>&1

echo "== release_image REFUSES when it cannot release =="
# The whole point of the return value is the failing branch, so prove it exists
# rather than assuming symmetry. Hold a file open inside the mount: umount then
# fails with EBUSY, release_image's bounded loop ends with the image still
# mounted, and it has to say so. Without this the refusal path is never
# executed by anything and could rot into "always returns 0" unnoticed.
# The "target is busy" lines below are the POINT of this section, not a
# malfunction: they are umount refusing, which is what makes the branch fire.
sudo mount -o loop "$IMG" "$MNT" || give_up "could not re-mount for the refusal test"
sudo touch "$MNT/held"
sudo chmod 0644 "$MNT/held"
# Hold the fd in THIS shell rather than a background sudo child: a root-owned
# holder cannot be killed from here, and the release half of this test would
# then fail for a reason that has nothing to do with release_image.
exec 9<"$MNT/held" || give_up "could not open a file inside the mount to hold it"
if release_image "$IMG"; then
    bad "release_image returned SUCCESS while a busy mount still holds the image"
    bad "  -> the post-condition cannot refuse, so a build would fsck a live filesystem"
else
    ok "release_image returns failure while the image is still held"
    [ -n "$(image_mounts "$IMG")" ] \
        && ok "and the image really is still mounted, so the refusal is not a false alarm" \
        || bad "it refused, but nothing is mounted -- the refusal is spurious"
fi
exec 9<&-
release_image "$IMG" >/dev/null 2>&1 \
    && ok "and it succeeds again once the holder is gone" \
    || bad "still refusing after the holder was released"

echo
echo "checks: $PASS passed, $FAIL failed, $SKIP skipped"
if [ "$FAIL" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
if [ "$SKIP" -gt 0 ]; then echo "RESULT: PASS (with $SKIP skipped)"; exit 0; fi
echo "RESULT: PASS"
