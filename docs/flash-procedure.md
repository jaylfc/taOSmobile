# Flashing the taOS Droidian image to the Nothing Phone (1)

**Status:** image assembled 2026-07-22. NOT yet flashed.

## What exists

`droidian-spacewar-api30-taos.zip` (~4.1GB, on the Fedora build host at
`~/taos-build/rootfs/`): the generic Droidian api30 Phosh rootfs with our
device boot artifacts injected at `/boot/` (`boot.img`, `vendor_boot.img`,
`vbmeta.img`, `recovery.img`) and the kernel debs staged under
`/var/lib/taosmobile/` for first-boot install. Recovery-flashable
(META-INF/updater-script); `setup.sh` flashes the embedded `boot.img` to the
boot partition during install.

## This flash WIPES the phone

It replaces the current Ubuntu Touch + working taOS controller install. That
install is recoverable: the verified UT image is on the Mac Mini
(`~/taosphone-images/UT-24.02_v3.tar.xz`, integrity-checked), and the phone
is fastboot-reachable there.

## Risk

First boot of a never-booted kernel. It may bootloop or hang. Mitigations:
- Bootloader stays unlocked → fastboot recovery always available.
- No `super`/GPT edits — only `boot`, `vendor_boot`, `vbmeta`, `userdata`
  writes with matching images (never the EDL-class partitions).
- Watch the screen during first boot (we cannot see boot state remotely; the
  earlier UT display work proved process state ≠ screen state).

## Procedure (from the Mac Mini, phone in fastboot)

1. Transfer the image Fedora → Mac Mini.
2. `adb reboot bootloader`
3. Flash the boot set:
   ```
   fastboot flash boot        boot.img
   fastboot flash vendor_boot vendor_boot.img
   fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img
   ```
4. Install the rootfs — either:
   - boot our `recovery.img` and `adb sideload droidian-spacewar-api30-taos.zip`, or
   - extract `data/rootfs.img` and `fastboot flash userdata rootfs.img`.
5. `fastboot reboot`, watch the screen.
6. If it boots: SSH in, `dpkg -i /var/lib/taosmobile/*.deb` and the adaptation
   package, then bring up `cage` + Chromium + the taOS controller.

## Rollback if it fails

```
fastboot flash boot <UT boot.img>
fastboot flash vendor_boot <UT vendor_boot.img>
# reflash UT via the UBports installer / the staged UT image
```
