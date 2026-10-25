# iOS as an app on the handset: findings (parked)

Jay asked on 2026-10-25 whether iOS could run as an app on the handset through an emulator, as people
are now doing on Linux. This records what was found that day. **Jay parked it the same day**, behind
taOS Mirror and the handset gate. Nothing has been built or run.

## The candidates (GitHub, read 2026-10-25)

| Project | What it runs | State |
|---|---|---|
| ChefKissInc/Inferno (about 4,000 stars, pushed 2026-09) | Real iOS on an emulated iPhone 11 (A13, `t8030`), as a QEMU fork | Boots to SpringBoard. iOS 14.0b5 is the version its wiki supports; issue #241 (closed) reports iOS 18.5 booting, but the wiki still lists 15 to 26 as upcoming. Display and multitouch work; audio and baseband are WIP; App Store, iCloud, Face ID and passcode do not work. |
| touchHLE/touchHLE (about 4,000 stars, pushed 2026-10) | Early 32-bit iOS apps (iPhone OS 2.x to iOS 4.0), high-level emulation, no iOS inside | AArch64 Linux "probably works, build it yourself"; AArch64 Android ships as a release. Mostly games. 64-bit iOS is ruled out by the project. |
| devos50/qemu-ios | iPod touch 1G and 2G | Hardware preservation, not a usable phone OS. |
| TrungNguyen1909/qemu-t8030 | Inferno's predecessor | Archived 2022. |
| MakrSas/Inferno-iOS | Inferno as an app on a real iPhone | Shows Inferno running on an ARM phone without KVM: boot takes minutes; 8 to 25 fps depending on the translation buffer (64 MB to 256 MB). |

## What Inferno would need here

- **No KVM.** Everything runs under QEMU's software translation (TCG). The phone is arm64 like the
  guest, but Apple-specific registers and PAC rule out hardware virtualisation. Expect the
  Inferno-iOS numbers or worse on the sc7280: boot in minutes, around 10 to 25 fps, software rendering.
- **Memory.** The wiki runs the guest with `-smp 7 -m 4G`, alongside the kiosk.
- **A one-time restore off the phone.** Restoring the IPSW needs a companion VM over emulated USB,
  and patching the filesystem needs a macOS host (APFS read-write). After that the disk images could
  be copied to the phone.
- **Display and input.** QEMU's framebuffer would reach the kiosk the same way the taOS Mirror plan
  streams the phone (taOSmobile PR #21), and touch would map to Inferno's multitouch device.

## Why it was parked: the firmware

- The IPSW is a public download from Apple's CDN. Apple's licence for iOS covers Apple-branded hardware
  only.
- The **SEP ROM** (Secure Enclave boot ROM) is not distributed by Apple at all. The wiki's only
  pointer is to search for an unofficial "Apple ROM Collection" of dumps. The A13 is not vulnerable to
  checkm8, so there is no way to dump one from a device you own.
- The project itself says: "Do not automate the download, setup, patching, or anything related or
  similar of images, firmware, and any other files."

So taOS could at most ship the emulator build and a launcher; any firmware would be Jay's own,
placed by hand. It would never be fetched by a script and never committed to a repository.

## If it is picked up again

- **The quick, clean win** is touchHLE as a taOS app. It runs natively on arm64, needs only IPAs Jay
  owns, takes real touch and the accelerometer, and is limited to classic iOS games.
- **The research run** is Inferno on omarchy (x86_64, faster) first, with firmware Jay places there.
  The phone would come second, once omarchy shows SpringBoard and gives a frame-rate number.
