# Reflashable checkpoint images of the phone (postmarketOS on `spacewar`)

Jay asked (2026-09-15) for occasional backups of the phone that can be flashed
back with `fastboot`, so that a good state (sound working, motd changed) is
never more than one restore away from any later experiment. This doc records
how the install is laid out, why the checkpoint captures what it captures,
and how to take and restore one. The tool is `scripts/phone-checkpoint.sh`.

## Where the install actually lives (measured, not assumed)

`/proc/cmdline` names the root by UUID (`pmos_root_uuid=180f57c2…`), which
says nothing about the block device. `findmnt /` says the root is
`/dev/loop0p2`, and `/sys/block/loop0/loop/backing_file` is `/dev/sda10` with
offset 0 and no size limit. `/dev/disk/by-partlabel/userdata -> sda10`. So:

| what          | device          | size            | partlabel  |
|---------------|-----------------|-----------------|------------|
| pmOS `/boot`  | `/dev/loop0p1`  | 480 MiB         | (inside userdata) |
| pmOS `/`      | `/dev/loop0p2`  | 225.7 GiB, ext4 | (inside userdata) |
| both          | `/dev/sda10`    | 226.2 GiB       | `userdata` |
| kernel/initramfs/dtb, active slot | `/dev/sde35` | 96 MiB (100663296 B) | `boot_b` |

The kernel loop-mounts the whole `userdata` partition and reads a GPT inside
it. One `fastboot flash userdata` therefore restores both pmOS filesystems.
`super` (`sda6`) still holds the stock Android dynamic partitions and is
untouched by pmOS; it is not part of a checkpoint. **`xbl`/`abl` are never
captured and must never be flashed**: EDL 9008 is the one unrecoverable
failure on this device (see `docs/flash-procedure.md`).

## What a checkpoint contains

`~/phone-checkpoints/<UTC stamp>-<label>/` on the build host (`omarchy`):

- `userdata.img.lz4`, `boot_b.img.lz4`: raw partition dumps, lz4-compressed
  on the phone. `userdata` is 226 GiB raw with about 5 GiB used; lz4 on the
  phone (8 cores, `lz4` is in the base image, `zstd` is not) collapses the
  zero space so the link carries roughly the used bytes.
- `SHA256SUMS`: two lines per image, the raw image and the `.lz4`, in plain
  `sha256sum -c` format.
- `MANIFEST.txt`: slot, device map, byte sizes, kernel package versions,
  uptime, cmdline, motd, and the raw hashes with byte counts.
- `RESTORE.md`: the exact `fastboot flash` commands for this checkpoint.

## Taking one

Runs on the build host, never on the phone (`/tmp` there is tmpfs and the
dump does not fit in RAM). Over the USB link (`172.16.42.1`, 44 MB/s
measured) rather than the tailnet (26 MB/s):

```bash
PHONE=jay@172.16.42.1 scripts/phone-checkpoint.sh sound-working
```

It needs the phone's sudo PIN (`PHONE_SUDO_PASS`, or it prompts silently)
and refuses to run any `dd` until `sudo -S id -u` has come back as `0`: a
plain `sudo` on this phone returns empty output that reads exactly like
success. The PIN goes over ssh stdin only, never on a command line.

The raw byte count is the pass/fail check. The lz4 stream is written to disk
and simultaneously decompressed on the host, counted and hashed; a dump whose
count differs from the partition size is renamed `*.BAD` and the script exits
3. The self-test path `PARTS=file:/home/jay/boot_b.backup` exercises the whole
pipeline without sudo and was run before the first real dump: the host-side
raw hash matched the phone's own `sha256sum` of that file.

The dump is crash-consistent, not clean: `/` stays mounted rw. `fsfreeze /`
would also freeze journald and with it the ssh session doing the dump, so it
is deliberately not used. Take checkpoints with the phone idle.

## Restoring one

Read the checkpoint's own `RESTORE.md`; in short: `lz4 -d`, `sha256sum -c
SHA256SUMS`, phone into fastboot, confirm `fastboot devices` = `c2a59521` and
`getvar current-slot` matches the checkpoint's slot, then `fastboot flash
boot_b boot_b.img` and `fastboot flash userdata userdata.img`. fastboot
converts a raw image to sparse chunks and skips all-zero blocks, so the
transfer is about the used space; `img2simg` is on the build host if it
objects to the size. After first boot, `fsck.ext4 -fn /dev/loop0p2` and read
what it says. **A restore is a write to the phone and needs a fresh go-ahead
from Jay; the backup itself is read-only.**

## Checkpoints taken

| stamp (UTC) | label | notes |
|-------------|-------|-------|
| pre-checkpoint | `boot_b.pre-checkpoint.img` | copy of the on-phone `/home/jay/boot_b.backup` (100663296 B, sha256 `d0a454b9…`), taken before the first full checkpoint |
