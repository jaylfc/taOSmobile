# Flashing Droidian to the Nothing Phone (1)

**Corrected 2026-08-26** after reading the porter's own installer and researching
device-specific brick modes. The previous version of this file was **wrong** — it
described a hand-rolled `fastboot flash userdata rootfs.img` sequence. That is not how
Droidian installs on this device.

> **Do not confuse that with the prebuilt-`/data` route added below (2026-08-26).**
> The mistake above was flashing `rootfs.img` *as* the userdata partition — the rootfs
> is a **file that lives inside** the `/data` filesystem, not the filesystem itself.
> The prebuilt route flashes a real `/data` **ext4 filesystem that contains**
> `rootfs.img` plus the `android-rootfs` symlink, which is byte-for-byte the state the
> porter's `adb push` sequence leaves behind. Same destination, different vehicle.

- **A/B device.** Slot suffix is *not* fixed — see the slots section below; the `/data`
  work is done from slot B and the system is booted from slot A.

## Device facts (verified on-device, not assumed)

- **A/B device** (`ro.build.ab_update=true`).
- **Vendor is Android 11** (`ro.vendor.build.version.release=11`, `RKQ1.230824.001`).
  This matches our Halium 11 / api30 build. Note the XDA Droidian image expects an
  Android **13** vendor and is therefore the wrong image for this device state.

## Brick modes to avoid (researched)

- **Never run `qbootctl`.** On this device it causes a permanent reboot-to-fastboot loop.
- **Never relock the bootloader.** Especially with a non-stock OS installed.
- **Never take an OTA.**
- **Never wipe data from recovery once Droidian is installed** — the rootfs is a *file*
  on `/data`, so wiping data deletes the OS.
- Never resize `super` or edit GPT; never touch `xbl`/`abl` (EDL 9008, unrecoverable).

## The actual procedure

Taken from `Flash_on_Linux.sh` in Nonta72's `Droidian-beta` release — the porter's own
installer for this exact device. Note the rootfs is **pushed as a file to `/data`**, not
flashed to a partition, and **vbmeta and dtbo are not touched at all**.

```
fastboot flash vendor_boot vendor_boot.img   # vendor_boot FIRST
fastboot flash boot        boot.img
fastboot format:ext4       userdata          # format, do NOT flash an image here
fastboot reboot recovery
# wait ~45s for recovery
adb shell "mount /data"
adb push rootfs.img /data/
adb push e2fsck /data/ && adb shell "chmod 755 /data/e2fsck"
adb shell "/data/e2fsck -fy /data/rootfs.img"
adb push resize2fs /data/ && adb shell "chmod 755 /data/resize2fs"
adb shell "/data/resize2fs -f /data/rootfs.img 32G"
adb shell "/data/e2fsck -fy /data/rootfs.img"
adb shell "ln -s /halium-system/var/lib/lxc/android/android-rootfs.img /data/android-rootfs.img"
adb reboot
```

The `/data` half of that sequence — for **our** build, driven from the Linux Pi — is
scripted as `scripts/install-ours-droidian.sh`. It asserts it is talking to a device in
`recovery`, removes the old porter install, pushes the api30 rootfs, runs the fsck /
resize / fsck, makes the symlink, and finally returns to the bootloader and restores
`set_active a`. It deliberately stops before the reboot to system so the slot can be
checked first.

**A real bug in the upstream release, now worked around:** `Flash_on_Linux.sh` runs
`adb push resize2fs /data/`, but **`resize2fs` is not in the tarball**. Running the script
unmodified fails at that step.

The first flash (2026-08-26) skipped the resize for that reason and did not boot. That
skip is the leading hypothesis for the failure — Droidian appears to expect the rootfs
grown to 32G rather than left at the raw 7.5GB image size — so the resize must not be
skipped again.

A **statically linked aarch64 `resize2fs`** is now staged next to the images at
`jays-mac-mini:~/taosphone-images/droidian/resize2fs`, together with a matching static
`e2fsck` so the check either side of the resize runs the same e2fsprogs version rather
than whatever recovery happens to ship.

| File | sha256 |
|---|---|
| `resize2fs` | `aeb05b86b5cd2240d4ffd4d8db4f58c7892c78a2488fbd112abc5616e2d61120` |
| `e2fsck` | `de01aab4e6ab08827fa2a2977eb62f28686a928cbb874dfa2546b6ec7eaea7d4` |

Static matters: the resize runs under Android **recovery**, which has no glibc, so a
Debian arm64 `.deb` binary would not run there. Both were verified `ELF64 / AArch64` with
zero `NEEDED` entries, and proven on a real ext4 image (create at 64M, grow to 256M,
`e2fsck -fy` clean afterwards).

To rebuild them, on any aarch64 Linux host with `gcc`, `make` and `libc6-dev`:

```
curl -fsSLO https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v1.47.2/e2fsprogs-1.47.2.tar.gz
tar xf e2fsprogs-1.47.2.tar.gz && mkdir -p e2fsprogs-1.47.2/build && cd e2fsprogs-1.47.2/build
../configure --disable-nls --disable-uuidd --disable-fsck --disable-defrag \
  --disable-debugfs --disable-imager LDFLAGS="-static" CFLAGS="-O2"
make -j4 libs && make -j4 -C resize resize2fs && make -j4 -C e2fsck e2fsck
```

If recovery turns out to ship its own working `resize2fs`, the pushed copy is harmless —
the procedure above calls `/data/resize2fs` by absolute path, so it never depends on
which one `PATH` resolves to.

## Attempt log

### 2026-08-26 — Nonta72 `Droidian-beta`, did NOT boot

Ran the sequence above **with the resize step skipped**, because the upstream tarball is
missing the `resize2fs` binary its own script pushes.

What went right:

- `fastboot flash vendor_boot` OK; `fastboot flash boot` OK (auto-resolved to `boot_a`).
- `fastboot format:ext4 userdata` OK.
- `rootfs.img` (7.5GB) pushed to `/data/`; `e2fsck` clean; android-rootfs symlink made.
- `adb reboot` at 19:04:48Z. Jay saw the **Droidian logo**, so the flashed `boot.img`
  loaded and the kernel started.

What went wrong: black screen after the logo, then the device **disappeared from USB
entirely** — checked by USB product name across the whole bus on the flash host, not just
for a "Nothing Phone" entry — and RNDIS `172.16.42.1` never answered. Still absent as of
2026-08-26 20:40Z. In that state there is no remote route to the device at all: it cannot
be read, rebooted or rolled back until it enumerates again.

**Leading hypothesis: the skipped resize.** That is the one documented step that was not
performed, and it is now fixed rather than worked around — see the static binaries above.
Retry the full sequence *including* the resize before concluding the build is bad.

**To get the device back:** hold Power ~10s to force it off, then Power + Volume Down for
fastboot. The bootloader is unlocked and no partition layout was changed, so fastboot is
always reachable by key combo.

### 2026-08-26 (second attempt) — resize performed, still no boot, but the failure MOVED

Retried without re-flashing anything and without re-pushing the rootfs, so the **only
variable changed was the resize**. Findings, all verified on-device rather than assumed:

- **Recovery has `e2fsck` but no `resize2fs`** (`which` finds only `/system/bin/e2fsck`).
  Combined with the binary being absent from the tarball, this means *nobody* following
  `Flash_on_Linux.sh` unmodified has ever had the resize succeed. The tarball contains
  exactly five files: the two flash scripts, `boot.img`, `vendor_boot.img`, `rootfs.img`.
- **The static aarch64 binaries run natively in Android recovery** — both report
  `1.47.2` when executed there. This is what the static linking was for.
- **The rootfs had been mounted and written to, then cut off uncleanly**: the pre-resize
  `e2fsck` found and fixed an inode on the corrupted orphan list. Together with
  `/data/android-data` holding 46 entries, this proves the first boot got as far as
  mounting the rootfs and starting the Halium Android container. The kernel is not the
  problem.
- **The rootfs was 90.6% full** before the resize (1662665/1835008 blocks). That is a
  second, independent reason an unresized rootfs would fail to boot cleanly.
- Resize succeeded: 8388608 4k blocks = 32GiB exactly, post-resize `e2fsck` clean with no
  modifications, usage down to 21%.

**Result: it is a REBOOT LOOP** (observed directly on the device). During the loop the
phone brings up **no USB whatsoever** — 90 seconds of cleared `dmesg` on a Linux USB host
recorded zero USB events — so there is no channel to read the journal through while it
loops. Earlier readings of "USB appears then dies" were the loop cycling, not a gadget
failing.

Do not leave it looping: `spacewar` is A/B, and a slot that keeps failing eventually gets
marked unbootable by the bootloader. That is not unrecoverable — fastboot is always
reachable by key combo — but there is nothing to gain. Force it off with a ~10s Power
hold, then Power + Volume Down.

**The failure did change shape.** After the first attempt the device exposed *no USB whatsoever*.
After the resize it enumerates as an Android adb gadget (`A063`, vendor `0x18D1`), so
userspace now gets far enough to bring a USB gadget up. That is progress, not a fix.

Next diagnostic (not yet completed): boot to recovery, loop-mount `/data/rootfs.img` and
read the failed boot's journal. Blocked at the time of writing because recovery lands on
the AOSP "No command" screen with `adb` in the `unauthorized` state, and the
authorisation prompt cannot be accepted on a device whose display never comes up.

> **Gotcha for next time:** do not trust a USB-presence check that greps for the word
> `gadget`. On this flash host a "UVC+UAC_MICROPHONE Composite Gadget" matches and reads
> as a false positive. Match on `A063`, on the serial, or on vendor `0x18D1`.

## Diagnostic hosts — read before trusting any "no RNDIS" result

**macOS cannot speak RNDIS.** It ships no RNDIS driver at all (`kextstat | grep -c rndis`
returns 0 on the flash host). Droidian's primary debug channel is SSH over USB at
`172.16.42.1`, carried by an RNDIS gadget — so **every "RNDIS does not answer" check run
from the Mac Mini was meaningless**, including the ones that were recorded as evidence
that the device was dead. A Droidian that booted perfectly would look identical from macOS.

Use a **Linux** USB host for any RNDIS or SSH-over-USB diagnosis. macOS is fine for
`fastboot` and `adb` and nothing else.

Also note, from the same session:

- Our built `recovery.img` is **not a recovery image**. `fastboot boot recovery.img` runs
  the ordinary Droidian boot flow (splash, then black), so it is useless as a rescue
  route. Do not reach for it expecting a shell.
- Stock recovery lands on the AOSP "No command" screen. Press and hold Power, then tap
  Volume Up, to reach the menu. Its `adb` is in the `unauthorized` state, and neither the
  Mac's March key nor a freshly generated Pi key changes that — both were tried and both
  stay `unauthorized`, so this is not a stale-key problem.
  **Correction (2026-08-26 late):** the earlier claim that "the authorisation prompt
  cannot be accepted on a device whose display never comes up" was wrong, and it was
  self-refuting — the "No command" screen was itself *read off that display*. The dead
  display is a **Droidian boot** symptom; stock recovery renders its own UI perfectly.
  The prompt therefore can be accepted, but it needs someone at the device.

## Recovery lives in the SLOT, not in a partition — this is why recovery "broke"

`spacewar` has **no `recovery` partition at all**. The full partition list from
`fastboot getvar all` contains `boot_a/_b`, `vendor_boot_a/_b`, `dtbo_a/_b`,
`vbmeta_a/_b`, `vbmeta_system_a/_b` and a single un-slotted `userdata` — and no
`recovery`. This is a GKI Android 11 layout: recovery is the generic ramdisk in
`boot` plus a recovery fragment in `vendor_boot`, so **`fastboot reboot recovery`
boots whatever is in the ACTIVE slot.**

Consequences, verified on-device 2026-08-26:

- Slot **B is still stock** (`slot-successful:b: yes`, never flashed). `set_active b`
  then `fastboot reboot recovery` reaches **stock Android recovery** — confirmed, the
  device came up as `adb ... recovery` within 20s.
- Slot **A holds our Droidian images**, whose ramdisk has no recovery mode. On slot A,
  `fastboot reboot recovery` simply returns to the bootloader — confirmed, reproduced.

**So `fastboot reboot recovery` failing after our flash was caused by `set_active a`,
not by our `vbmeta.img`.** The checkpoint's suspicion that the 4096-byte vbmeta was
failing verification for the recovery path is **disproven**: with vbmeta untouched,
merely moving the active slot back to B restored recovery. Do not spend time re-flashing
or skipping vbmeta on account of recovery.

Practical rule: **do the `/data` work from slot B, boot the system from slot A.**
`userdata` is not slotted, so the rootfs push, resize and symlink are slot-independent.
Always `fastboot set_active a` before rebooting to system, or the phone boots stock
Android and will treat the Droidian `/data` as a corrupt Android `/data`.

### And it is not the boot path either — our vbmeta already disables verification

The one variable the recovery finding left untested was whether our `vbmeta.img` could
still be breaking the **boot** path. It cannot. Decoding the AVB header of the staged
image (`pitop:~/taosflash/vbmeta.img`, 4096 bytes, only 19 non-zero bytes in the whole
file) gives:

```
magic                AVB0        release_string      avbtool 1.3.0
algorithm_type       0           (unsigned)
auth block           0 bytes     aux block           0 bytes
descriptors          0 bytes     rollback_index      0
flags                0x00000003  ->  HASHTREE_DISABLED | VERIFICATION_DISABLED
```

It is an empty, unsigned vbmeta with **both** AVB flags set, i.e. the image `fastboot
--disable-verity --disable-verification flash vbmeta` produces. It switches verification
**off**; it cannot impose a check that a stock vbmeta would have let pass.

Two things follow, and they close the question:

- **Re-flashing with `--disable-verity --disable-verification` is not an experiment.**
  Those flags set exactly bits 0 and 1 of this field, which are already set. The result
  would be byte-identical.
- **"Skip vbmeta like the porter does" is not available and would not help.** The
  porter's bundle ships **no vbmeta at all** — `beta/Flash_on_Linux.sh` flashes only
  `vendor_boot.img` and `boot.img` — and no stock vbmeta dump exists on any host here,
  so there is nothing to restore. The porter's procedure works *because* the bootloader
  is unlocked, which is the same permissive state our flags give us.

So the RESUME checkpoint's "one remaining untried variable" is spent. If the prebuilt
`/data` flash still reboot-loops, vbmeta is not the reason to keep going, and
`tsk-q3rkpx` (LineageOS / Android Device Owner) is the answer Jay pre-authorised.

## Slots — check them before and after every flash

`spacewar` is A/B and the bootloader will quietly move the goalposts under you.

After the 2026-08-26 reboot loop, the bootloader had **exhausted slot A's retries and
marked it unbootable**, falling back to B:

```
current-slot: b
slot-retry-count:a: 0      slot-successful:a: no     slot-unbootable:a: yes
slot-retry-count:b: 7      slot-successful:b: yes    slot-unbootable:b: no
```

Two consequences:

1. **`fastboot flash boot` writes to the ACTIVE slot.** With the active slot silently
   changed to B, a flash intended for A lands in the wrong place and the failure looks
   like a bad image. Always `fastboot getvar current-slot` first.
2. **Target slot A deliberately.** The Android **11 / API 30** vendor this port depends on
   was verified on slot A, and that dependency is the highest-risk assumption in the whole
   port. Slot B's vendor version is unverified, so flashing there swaps a known quantity
   for an unknown one.

Reactivate A (this also clears the unbootable flag and restores the retry count) before
flashing:

```
fastboot set_active a
fastboot getvar current-slot     # confirm it really moved
```

Check these variables again after any boot failure — a loop that "just fails" may in fact
have moved you to the other slot partway through.

## Prebuilt `/data` route — no on-device shell at all (2026-08-26)

**Why this exists.** Stock recovery on `spacewar` shows **no adb authorisation prompt**.
Confirmed at the device: recovery was reached, `adb` sat at `unauthorized`, both the
Mac's March key and a freshly generated Pi key were tried, and no prompt was ever drawn
on screen to accept. So the porter's `adb push` route cannot be driven at all here. The
answer is to stop needing a shell.

`scripts/build-userdata-image.sh` builds the finished `/data` filesystem on the Linux USB
host and it goes on in one fastboot write:

```
# on the Linux host
scripts/build-userdata-image.sh          # ~4-5GB of real data, mostly zeros
# then, phone in fastboot:
fastboot set_active a                    # boot slot must be A before the reboot
fastboot flash userdata work/userdata.img
fastboot reboot
```

What the script does, and why each part matters:

- **Resizes the rootfs here, not on the phone.** The 4GiB api30 rootfs is grown to 32G
  with `resize2fs`, and `e2fsck` verifies it *before* it ever reaches the device. The
  skipped resize was the leading suspect for the first failed boot; doing it on a machine
  where the result can be checked removes it as a variable.
- **Disables `orphan_file` and `fast_commit` explicitly.** The Halium kernel is 5.4 and
  can mount neither (6.x and 5.10+ respectively). e2fsprogs 1.47 does not enable them by
  default, so this is belt-and-braces against a toolchain bump silently producing a
  filesystem the phone refuses to mount.
- **Refuses to `e2fsck` while the image is mounted anywhere.** A desktop automounter
  grabs the freshly created loop device and mounts the image somewhere of its own
  choosing, and an `e2fsck` then rewrites a live filesystem underneath itself. That
  happened once (`d1389b1`). The mount bookkeeping now lives in
  `scripts/image-mount-lib.sh` and the build calls `require_released`, which is a **hard
  stop**: if the image is still mounted the script exits 1 rather than carrying on. It
  used to print a warning and continue, so a corrupting run still ended in `BUILD-OK`.
  The check that proves the stop fires is `scripts/check-image-release.sh`.
- **Builds a 40G filesystem into a 226GiB partition** (`partition-size:userdata` is
  `0x388D5D3000` = 226.21 GiB). That is deliberate: it keeps the write small and fast.
  `/data` must be grown to fill the partition after first boot — carded, do not forget it.

`max-download-size` is `0x30000000` (768 MiB), so fastboot chunks the image by itself; no
`img2simg` is required.

**Supersedes the recovery route.** `scripts/install-ours-droidian.sh` drove the same
`/data` work over `adb` in recovery. It is kept only for the case where a device *does*
authorise adb; on this phone it cannot run, and it is not the procedure to reach for.

## Rollback to Ubuntu Touch

Verified image staged at `jays-mac-mini:~/taosphone-images/UT-24.02_v3.tar.xz` (721MB,
integrity-checked), containing `boot.img`, `vendor_boot.img`, `ubuntu.img`.

```
fastboot flash boot        <UT boot.img>
fastboot flash vendor_boot <UT vendor_boot.img>
# then reinstall the UT rootfs by the same push-to-/data route
```

## Two builds available

| Build | What | Use |
|---|---|---|
| **Nonta72 `Droidian-beta`** | The porter's own, Oct 2025. `boot.img` + `vendor_boot.img` + 7.5GB ext4 `rootfs.img`. | **Flash first** — proves the device runs Droidian and gives a known-good baseline. |
| **Ours** | Our built kernel (`boot.img`/`vendor_boot`/`vbmeta`/`recovery`, Halium 11) + Droidian generic api30 rootfs. | Swap in on top of a working baseline, so a failure is diagnosable. |

Flashing the reference first is deliberate: if our build fails to boot, we would not
otherwise know whether the fault is our kernel, our adaptation, or the rootfs.
