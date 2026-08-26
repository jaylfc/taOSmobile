# Flashing Droidian to the Nothing Phone (1)

**Corrected 2026-08-26** after reading the porter's own installer and researching
device-specific brick modes. The previous version of this file was **wrong** — it
described a hand-rolled `fastboot flash userdata rootfs.img` sequence. That is not how
Droidian installs on this device.

## Device facts (verified on-device, not assumed)

- **A/B device**, currently on slot `_a` (`ro.boot.slot_suffix=_a`, `ro.build.ab_update=true`).
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

**Result: still no boot** — no display, no SSH, no RNDIS after 4 minutes. **But the
failure changed shape.** After the first attempt the device exposed *no USB whatsoever*.
After the resize it enumerates as an Android adb gadget (`A063`, vendor `0x18D1`), so
userspace now gets far enough to bring a USB gadget up. That is progress, not a fix.

Next diagnostic (not yet completed): boot to recovery, loop-mount `/data/rootfs.img` and
read the failed boot's journal. Blocked at the time of writing because recovery lands on
the AOSP "No command" screen with `adb` in the `unauthorized` state, and the
authorisation prompt cannot be accepted on a device whose display never comes up.

> **Gotcha for next time:** do not trust a USB-presence check that greps for the word
> `gadget`. On this flash host a "UVC+UAC_MICROPHONE Composite Gadget" matches and reads
> as a false positive. Match on `A063`, on the serial, or on vendor `0x18D1`.

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
