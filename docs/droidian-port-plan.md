# Porting Droidian to the Nothing Phone (1) — plan

Decision (2026-07-21): target **Droidian**, not postmarketOS. Supersedes the
pmOS direction in `droidian-kiosk-scope.md`.

## Why Droidian over postmarketOS

| | Droidian (Halium) | postmarketOS (mainline) |
|---|---|---|
| Kernel | Halium 11/12 + **Android vendor blobs** | mainline `qcom-sc7280` |
| Camera | works (same blobs the UT port uses) | almost certainly not — Qualcomm camera on mainline is very incomplete |
| Modem / VoLTE | works | partial at best |
| Base | **Debian — glibc + systemd** | Alpine — musl |
| taOS controller | **straight lift of what works today** | every binary wheel must be rebuilt |
| wlroots / `cage` | yes (hwcomposer backend) | yes (native KMS) |

The deciding factors are hardware completeness and the controller. Every taOS
dependency installed on the phone tonight came from a **glibc** `manylinux`
wheel; Debian keeps that working, Alpine does not. And the UT port README shows
the vendor blobs deliver camera (front and back), video recording, RIL, VoLTE,
NFC and GPS — postmarketOS discards all of it for a mainline kernel we do not
need.

## Feasibility

The porting guide's own criterion:

> "If it already has a halium-compliant kernel of halium-9.0 and above chances
> are that Droidian will work without much modification."

This device has a working **Halium 11/12** kernel — Nonta72's Ubuntu Touch port,
which is what the phone runs today. That is the expensive 80% of a port, and it
is already done and public.

**No Droidian device packages for `spacewar` exist anywhere public** (checked
`droidian`, `droidian-images`, `droidian-devices` orgs and a global search). An
XDA build reportedly exists but has no published sources, so it is not
reproducible or maintainable. Porting properly is the better option.

## What a port consists of

Droidian devices follow a two-repo pattern (53 such repos, actively maintained):

1. **`linux-android-nothing-spacewar`** — the kernel packaged as a Debian
   package. Droidian compiles Android kernels with the Android toolchain inside
   Docker and packages the result, so kernels upgrade over APT.
   - Source: `Nonta72/android_kernel_nothing_sm7325`, branch `Halium-11.0`
   - Model on: `droidian-devices/linux-android-volla-vidofnir`
2. **`adaptation-nothing-spacewar`** — the device adaptation package: `debian/`
   packaging plus `etc/`, `usr/`, `src/` trees carrying udev rules, firmware
   quirks, services and device config.
   - Model on: `droidian-devices/adaptation-volla-vidofnir`

Then: flash the Droidian **GSI rootfs** (`droidian-images` publishes
`rootfs-api30gsi-all`) together with our boot image.

## Steps

1. **Kernel package.** Fork the kernel tree, add `droidian/debian` packaging
   per `kernel-compilation.md`, build in Docker (or GitHub Actions, as we did
   for the pmOS attempt — CI worked fine and costs nothing).
   - Verify the Halium defconfig satisfies Droidian's kernel-info options.
2. **Boot image.** Produced by the kernel package build.
3. **Adaptation package.** Start from the vidofnir template; fill in device
   specifics (partitions, firmware paths, udev, audio/modem quirks).
4. **Rootfs.** Fetch the Droidian GSI rootfs matching the Halium level.
5. **Flash and bring up.** Verify over SSH before trusting the display:
   controller, network, then compositor.
6. **taOS layer.** Lift `taos-controller.service` and the venv from this repo —
   Debian means the wheels that work today keep working. Then
   `cage -- chromium --kiosk --app=http://localhost:6969/` as the session.

## Risks

- **Not a supported device.** We would be the porters; no upstream support if
  something in the adaptation is subtly wrong.
- **Halium level.** The UT port is Halium 11 (with a 12 branch). Droidian
  targets specific Halium levels; a mismatch means kernel work.
- **Time.** Steps 1–3 are the real effort. Steps 5–6 are quick because the taOS
  side is already proven.
- Bricking rules are unchanged: rootfs/boot writes only, never `super` or GPT,
  bootloader stays unlocked, and keep the verified UT image (already downloaded
  to the Mac Mini, integrity checked) as rollback.

## Status — FLASHED, not booting (2026-08-26)

The kernel milestone below stands, but it is no longer the latest state. The build has
since been flashed to hardware **twice** and does not boot: it is a **reboot loop**, and
during the loop the device raises no USB, so there is no channel to diagnose through.
The kernel itself is not in question — the rootfs gets mounted and written and the Halium
Android container starts.

**Read `flash-procedure.md` before doing anything with hardware.** It carries the attempt
log, the A/B slot handling (a loop marks the slot unbootable and switches slots under
you), and the correction that macOS cannot speak RNDIS so Droidian's debug channel must
be driven from Linux.

Fallback if our own build also loops: the Android Device Owner kiosk route in
`android-kiosk-scope.md`, pre-authorised by Jay and carded as `tsk-q3rkpx`. Note root is
not required for it.

## Milestone — kernel package BUILDS (2026-07-21)

**Milestone reached: the Droidian kernel package builds end-to-end.** From
`jaylfc/linux-android-nothing-spacewar@droidian-taos`, the Droidian
`build-essential` container produces the full package set:

- `linux-bootimage-5.4.289-nothing-spacewar` (73MB) — the flashable set:
  `boot.img` (header v3, 21MB kernel + 16MB Droidian initramfs),
  `vendor_boot.img`, `vbmeta.img`, `recovery.img`, flash metadata.
- `linux-image-5.4.289-nothing-spacewar` (24MB), `linux-headers-*` (8.8MB),
  and the three "latest" metapackages.

Kernel/config issues fixed to get here (each committed with rationale):
1. CI checkout — `IS_CONTAINER=true` (releng supports Travis/Circle/Drone/Azure, not Actions).
2. `debian/compat` conflicted with the generated `debehelper-compat`.
3. `LLVM_IAS=0` — clang-10's integrated assembler rejects a `head.S` `.ascii` escape.
4. Prebuilt DTB injected — GKI kernel builds no device tree.
5. `droidian/sm7325.config` — the lahaina SoC fragment stack, so QMI/modem
   drivers build (rmnet_core needed prompt-less `QCOM_QMI_HELPERS`).
6. Disabled in-tree qcacld WLAN — Halium uses the vendor prebuilt module.
7. `SECONDIMAGE_OFFSET` set — mkbootimg needs a value for `--second_offset`.

Build host: Fedora (12-core, native amd64) in the Droidian container —
~10-minute cycles vs ~30 on GitHub Actions.

### Old status

### Prior status

- UT rollback image: downloaded to the Mac Mini and verified
  (`boot.img`, `vendor_boot.img`, `ubuntu.img`).
- Phone: connected to the Mac Mini over USB; `fastboot` present there.
- pmOS build: fixed two CI bugs (pmbootstrap/pmaports version skew, wrong
  default branch) and it now builds — but it is no longer the target.
