#!/usr/bin/env bash
# Take a reflashable checkpoint image of the phone (postmarketOS on spacewar).
#
# Runs on the BUILD HOST (the machine with fastboot and the USB cable), never
# on the phone. Pulls the raw block devices over ssh, compresses them on the
# phone with lz4 so a 226 GB mostly-empty partition costs minutes rather than
# hours on a 44 MB/s link, and verifies the RAW bytes on this side by
# decompressing the stream as it lands and hashing it. Nothing is written on
# the phone: the phone's /tmp is tmpfs (RAM), and a partition dump does not
# fit there.
#
# WHAT IS CAPTURED, and why these two:
#   userdata   the whole pmOS install. `/` is /dev/loop0p2 and loop0 is
#              userdata at offset 0 (checked in /sys/block/loop0/loop/), so
#              one `fastboot flash userdata` restores boot fs + rootfs.
#   boot_<s>   the active slot's kernel+initramfs+dtb. This is the partition
#              every audio experiment rewrites; it is what a "rollback" needs.
# Everything else (xbl, abl, super, vbmeta, dtbo...) is stock Android and is
# NOT captured on purpose. xbl/abl must never be flashed: EDL 9008 is the one
# unrecoverable failure on this device.
#
# CONSISTENCY: the dump is taken while the root fs is mounted rw, so it is
# crash-consistent, not clean. `fsfreeze` on / would also freeze journald and
# therefore sshd's own session, so it is deliberately not used. Run this with
# the phone idle; the script `sync`s first and the restore doc says to fsck.
#
# SUDO ON THE PHONE needs the PIN piped in (`sudo -S`); a plain `sudo` yields
# EMPTY output that reads exactly like success. The PIN is taken from
# $PHONE_SUDO_PASS or prompted (silently), passed on ssh stdin only (never on
# a command line, never into a file), and the script proves sudo actually
# worked by demanding `uid=0` back BEFORE any dd runs.
#
# USAGE
#   PHONE=jay@172.16.42.1 scripts/phone-checkpoint.sh [label]
#   env: PHONE      ssh target (default jay@172.16.42.1, the USB link)
#        DEST_ROOT  where checkpoints go (default ~/phone-checkpoints)
#        PARTS      space-separated partlabels (default "boot_<active> userdata");
#                   an entry "file:/abs/path" dumps a regular file without sudo
#                   and exists so the pipeline can be tested without the PIN.
#        PHONE_SUDO_PASS  the phone PIN; prompted if unset and PARTS needs sudo
# OUTPUT  $DEST_ROOT/<UTC stamp>-<label>/{<part>.img.lz4, SHA256SUMS, MANIFEST.txt, RESTORE.md}
# EXIT    0 all parts dumped and every raw byte count matched the device size
#         1 usage / preflight failure (nothing dumped)
#         3 a dump ran but its size or hash check failed (the file is kept,
#           renamed *.BAD, so a partial image is never mistaken for a good one)
set -euo pipefail

PHONE="${PHONE:-jay@172.16.42.1}"
DEST_ROOT="${DEST_ROOT:-$HOME/phone-checkpoints}"
LABEL="${1:-checkpoint}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$PHONE")

die() { echo "phone-checkpoint: $*" >&2; exit 1; }
for t in lz4 sha256sum; do command -v "$t" >/dev/null || die "need $t on this host"; done

# --- preflight: reach the phone, learn the slot, map partlabels to devices ---
"${SSH[@]}" true 2>/dev/null || die "cannot ssh to $PHONE (is the USB link up? try the tailnet address)"
"${SSH[@]}" 'command -v lz4 >/dev/null' || die "phone has no lz4 (apk add lz4)"
SLOT=$("${SSH[@]}" "sed -n 's/.*androidboot.slot_suffix=_\([ab]\).*/\1/p' /proc/cmdline")
[[ "$SLOT" == a || "$SLOT" == b ]] || die "could not read active slot from /proc/cmdline (got '$SLOT')"
PARTS="${PARTS:-boot_$SLOT userdata}"

NEED_SUDO=0
declare -A DEV SIZE
for p in $PARTS; do
  if [[ "$p" == file:* ]]; then
    f="${p#file:}"
    DEV[$p]="$f"
    SIZE[$p]=$("${SSH[@]}" "stat -c %s '$f'") || die "no such file on phone: $f"
  else
    NEED_SUDO=1
    d=$("${SSH[@]}" "readlink -f /dev/disk/by-partlabel/$p") || die "no partlabel $p"
    [[ "$d" == /dev/* ]] || die "partlabel $p resolved to '$d', not a device"
    DEV[$p]="$d"
    SIZE[$p]=$(( $("${SSH[@]}" "cat /sys/class/block/$(basename "$d")/size") * 512 ))
  fi
  [[ "${SIZE[$p]}" -gt 0 ]] || die "$p has size 0; refusing"
done

# --- sudo: obtain the PIN and PROVE it works before touching any device -----
if (( NEED_SUDO )); then
  if [[ -z "${PHONE_SUDO_PASS:-}" ]]; then
    read -r -s -p "phone sudo PIN for $PHONE: " PHONE_SUDO_PASS; echo
  fi
  got=$(printf '%s\n' "$PHONE_SUDO_PASS" | "${SSH[@]}" "sudo -S -p '' id -u 2>/dev/null" || true)
  [[ "$got" == 0 ]] || die "sudo on the phone did not yield uid 0 (got '${got:-<empty>}'); wrong PIN? An empty result is NOT success."
fi

# --- destination ---------------------------------------------------------------
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="$DEST_ROOT/$STAMP-$LABEL"
mkdir -p "$DEST"
echo "checkpoint -> $DEST"
echo "slot=$SLOT parts=[$PARTS]"

# --- manifest: what the phone was when this was taken ---------------------------
{
  echo "taken_utc: $STAMP"
  echo "label: $LABEL"
  echo "phone: $PHONE"
  echo "active_slot: $SLOT"
  for p in $PARTS; do echo "part: $p device=${DEV[$p]} bytes=${SIZE[$p]}"; done
  echo "--- uname"; "${SSH[@]}" uname -a
  echo "--- os-release"; "${SSH[@]}" 'cat /etc/os-release | head -3'
  echo "--- uptime"; "${SSH[@]}" uptime
  echo "--- cmdline"; "${SSH[@]}" cat /proc/cmdline
  echo "--- root mount"; "${SSH[@]}" 'findmnt / -o SOURCE,FSTYPE,OPTIONS -n; cat /sys/block/loop0/loop/backing_file 2>/dev/null'
  echo "--- df /"; "${SSH[@]}" df -h /
  echo "--- motd"; "${SSH[@]}" 'cat /etc/motd 2>/dev/null'
  echo "--- kernel pkg"; "${SSH[@]}" 'apk info -v 2>/dev/null | grep -E "^linux-postmarketos|^alsa-ucm|^device-nothing" | sort'
} > "$DEST/MANIFEST.txt"

# --- dump ----------------------------------------------------------------------
rc=0
: > "$DEST/SHA256SUMS"
for p in $PARTS; do
  name="${p#file:}"; name="${name##*/}"
  out="$DEST/$name.img.lz4"
  dev="${DEV[$p]}"
  echo "== $p  ($dev, ${SIZE[$p]} bytes)"
  "${SSH[@]}" sync
  if [[ "$p" == file:* ]]; then
    remote="lz4 -1 -c '$dev'"
    feed=(true)
  else
    remote="sudo -S -p '' dd if=$dev bs=4M status=progress | lz4 -1 -c"
    feed=(printf '%s\n' "$PHONE_SUDO_PASS")
  fi
  # The lz4 stream is written to disk AND decompressed here to hash the raw
  # bytes and count them; the count is the check, the hash is the record.
  bytes=-1; rawsha=none
  if "${feed[@]}" | "${SSH[@]}" "$remote" \
      | tee "$out" \
      | lz4 -d -c \
      | tee >(wc -c > "$DEST/.$name.bytes") \
      | sha256sum | awk '{print $1}' > "$DEST/.$name.rawsha"; then
    bytes=$(cat "$DEST/.$name.bytes"); rawsha=$(cat "$DEST/.$name.rawsha")
  else
    echo "!! $p: the dump pipeline failed (ssh/dd/lz4 exit non-zero)" >&2
  fi
  rm -f "$DEST/.$name.bytes" "$DEST/.$name.rawsha"
  if [[ "$bytes" != "${SIZE[$p]}" ]]; then
    echo "!! $p: got $bytes raw bytes, device is ${SIZE[$p]} -- dump is INCOMPLETE" >&2
    mv -f "$out" "$out.BAD"; rc=3; continue
  fi
  # Plain "hash  file" lines so `sha256sum -c SHA256SUMS` works after lz4 -d.
  echo "$rawsha  $name.img" >> "$DEST/SHA256SUMS"
  (cd "$DEST" && sha256sum "$name.img.lz4") >> "$DEST/SHA256SUMS"
  echo "raw_sha256: $name.img $rawsha bytes=$bytes" >> "$DEST/MANIFEST.txt"
  echo "   ok: $bytes bytes, raw sha256 $rawsha, $(du -h "$out" | cut -f1) on disk"
done

# --- restore instructions, written next to the images ----------------------------
TOTAL=0; for p in $PARTS; do TOTAL=$(( TOTAL + SIZE[$p] )); done
cat > "$DEST/RESTORE.md" <<EOF
# Restore checkpoint $STAMP-$LABEL

Taken from slot **$SLOT**. Restoring is a write to the phone: get a fresh go-ahead
from Jay before running any \`fastboot flash\` below. Never flash xbl/abl, never
touch the GPT; those are not in this checkpoint and must not be.

1. Verify the files first:

       cd "$DEST" && sha256sum -c SHA256SUMS 2>&1 | grep -v 'img: No such'

   (the raw lines can only be checked after step 2; the .lz4 lines check now.)

2. Decompress (needs ~$(( TOTAL / 1024 / 1024 / 1024 + 1 )) GiB free):

EOF
for p in $PARTS; do
  n="${p#file:}"; n="${n##*/}"
  echo "       lz4 -d $n.img.lz4 $n.img" >> "$DEST/RESTORE.md"
done
cat >> "$DEST/RESTORE.md" <<EOF

       sha256sum -c SHA256SUMS      # now every line must say OK

3. Phone into fastboot (\`sudo reboot bootloader\` over ssh, or Vol-down + Power).
   Confirm it is THIS phone and THIS slot before flashing:

       fastboot devices                       # expect c2a59521
       fastboot getvar current-slot           # expect $SLOT
       fastboot getvar unlocked               # expect yes

4. Flash. Order matters only in that boot goes last so a failed userdata flash
   leaves the current kernel in place:

EOF
for p in $PARTS; do
  [[ "$p" == file:* || "$p" == userdata ]] && continue
  echo "       fastboot flash $p $p.img" >> "$DEST/RESTORE.md"
done
for p in $PARTS; do
  [[ "$p" == userdata ]] || continue
  cat >> "$DEST/RESTORE.md" <<'EOF'
       fastboot flash userdata userdata.img
EOF
done
cat >> "$DEST/RESTORE.md" <<EOF

   \`userdata.img\` is a raw ext4 image the size of the partition; fastboot
   converts a raw image to sparse chunks itself and skips all-zero blocks, so
   the transfer is roughly the used space, not the partition size. If fastboot
   refuses on size, make a sparse image first: \`img2simg userdata.img
   userdata.simg\` and flash that.

5. \`fastboot reboot\`. The dump was crash-consistent (taken from a live rw
   root), so on first boot run \`sudo -S fsck.ext4 -fn /dev/loop0p2\` and read
   the output; a clean journal replay is expected, anything more is not.

Taken with \`scripts/phone-checkpoint.sh\` from \`jaylfc/taOSmobile\`; see
\`MANIFEST.txt\` for what the phone was running.
EOF

echo
cat "$DEST/SHA256SUMS"
(( rc == 0 )) && echo "checkpoint complete: $DEST" || echo "checkpoint INCOMPLETE (rc=$rc): $DEST" >&2
exit $rc
