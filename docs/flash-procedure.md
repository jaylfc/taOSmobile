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
adb shell "e2fsck -fy /data/rootfs.img"
adb shell "resize2fs -f /data/rootfs.img 32G"     # see caveat below
adb shell "e2fsck -fy /data/rootfs.img"
adb shell "ln -s /halium-system/var/lib/lxc/android/android-rootfs.img /data/android-rootfs.img"
adb reboot
```

**Caveat — a real bug in the upstream release:** `Flash_on_Linux.sh` runs
`adb push resize2fs /data/`, but **`resize2fs` is not in the tarball**. Running the
script unmodified fails at that step. The resize only grows the 7.5GB rootfs to 32GB, so
it can be skipped for a first boot test, or satisfied with recovery's own `resize2fs` if
present (recovery clearly has `e2fsck`, which the script calls without pushing).

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
