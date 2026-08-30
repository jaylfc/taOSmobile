#!/bin/bash
# Mount bookkeeping for the loop-mounted /data image, shared by
# build-userdata-image.sh and check-image-release.sh.
#
# It lives in its own file so the post-condition below can be exercised against
# a real loop mount by check-image-release.sh. Inline in a script that needs
# sudo, loop devices and a 40 GiB build to reach, it was unreachable by any
# check and was wrong without anyone being able to tell.
#
# Source this; do not run it. PATH needs /sbin for losetup on Debian.
export PATH="$PATH:/sbin:/usr/sbin"

# Every mountpoint currently holding this image, one per line, empty if none.
#
# THE SPELLING MATTERS AND IT IS NOT THE OBVIOUS ONE. A loop mount appears in
# the mount table under its /dev/loopN name, NOT under the backing file:
#
#     mount -o loop img.raw /mnt      ->  findmnt SOURCE = /dev/loop0
#     losetup, then mount /dev/loop0  ->  findmnt SOURCE = /dev/loop1
#
# so `findmnt --source <the image file>` matches NOTHING, in either case.
# Measured on the Pi this runs on, util-linux 2.38.1, both spellings.
#
# bef6352 recorded the opposite -- "util-linux resolves a loop mount back to
# its backing file, so a match on the /dev/loopN name never fires" -- and the
# post-condition derived from that reading queried the backing file and could
# therefore never fire at all. The unmount loop in the same commit survived the
# inverted rationale only because it happened to iterate BOTH spellings.
# Ask losetup which devices back the image and query by those, and keep the
# backing file in the list so a future util-linux that does report it that way
# stays covered.
image_mounts() {
    local img="$1" src
    for src in "$img" $(losetup -j "$img" -O NAME --noheadings 2>/dev/null); do
        findmnt -n -o TARGET --source "$src" 2>/dev/null
    done | sort -u
}

# Loop devices currently attached to this image, space separated, empty if none.
image_loop_devices() {
    losetup -j "$1" -O NAME --noheadings 2>/dev/null | tr '\n' ' '
}

# Release every mount and loop device backed by this image.
#
# A desktop automounter will grab a freshly created loop device and mount it
# somewhere of its own choosing (seen at /tmp/loop0), so unmounting only OUR
# mountpoint is not enough -- the image stays mounted elsewhere and an e2fsck
# then runs against a live filesystem and rewrites it. That happened once
# (d1389b1); do not let it happen again.
#
# Unmount by MOUNTPOINT, never by device name: umount accepts either, but the
# mountpoint is the unambiguous one when several loop devices back one file.
#
# RETURNS 0 ONLY IF NO MOUNT REMAINS. That is the safety property: an fsck is
# corrupted by a live MOUNT writing underneath it, not by a block device sitting
# open. A still-attached loop device is reported by image_loop_devices() and is
# deliberately not a failure.
#
# It retries the WHOLE release, not just the unmount, because the automounter
# races us: measured on the Pi 2026-08-30, our umount succeeded and udev then
# re-mounted the same image at /tmp/loop6 while we were detaching, so a single
# unmount pass returned with the image mounted again. That is d1389b1's exact
# scenario, still live on this host. `losetup -d` is no help there and no
# evidence either -- on an AUTOCLEAR device it returns 0 while a udev worker
# still holds the device open.
#
# The retry is bounded and the verdict is honest: if udev keeps winning, this
# returns non-zero with the image still mounted, and the CALLER MUST REFUSE TO
# FSCK. Do not paper over a non-zero return here.
release_image() {
    local img="$1" dev mp pass settle

    for pass in 1 2 3 4 5; do
        local inner=0
        while mp=$(image_mounts "$img" | head -1); [ -n "$mp" ]; do
            inner=$((inner + 1))
            # Bounded. A mountpoint that will not go away has to END this loop
            # with the image still held, so the post-condition can refuse. An
            # unbounded loop would hang, and a check that hangs reports nothing
            # at all rather than reporting a red.
            [ "$inner" -gt 20 ] && break
            sudo umount "$mp" || break
        done

        for dev in $(image_loop_devices "$img"); do
            sudo losetup -d "$dev" 2>/dev/null || true
        done
        sync

        # Let udev act -- either finishing a deferred detach or making its grab
        # visible -- before deciding. Deciding immediately would read a race as
        # a clean release.
        for settle in 1 2 3; do
            [ -z "$(image_loop_devices "$img")" ] && break
            sleep 0.3
        done

        [ -z "$(image_mounts "$img")" ] && return 0
    done

    return 1
}
