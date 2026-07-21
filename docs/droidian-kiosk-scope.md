# taOS phone on Droidian — scope (recommended)

Jay's question — "do we really need a Mir shell, why not cage + Chromium
kiosk?" — turns out to be the right one. The answer is yes to cage, but the
base has to change: Droidian, not Ubuntu Touch.

**Status:** proposed 2026-07-21. Supersedes `custom-image-scope.md` (build our
own UT image) and `android-kiosk-scope.md` (Android + Device Owner).

## The correction

I concluded from testing that wlroots/cage "cannot work on Halium" because
cage powered the phone off three times. That was wrong in an important way.

**Ubuntu's `cage` is built against vanilla wlroots, which only has DRM/KMS
backends.** On a Halium device the display pipeline belongs to the Android
hwcomposer HAL, so vanilla wlroots fights it for DRM master and takes the
device down.

Droidian maintains a [wlroots fork with an **hwcomposer backend**](https://github.com/droidian/wlroots)
that talks to the same HAL Mir uses via `graphics-android2.so`. On Droidian,
wlroots compositors are the normal way things run — its shell (`phoc`) is a
wlroots compositor. So `cage` is not exotic there; it is the same stack the
whole OS already uses.

## Why this beats both previously scoped routes

| | Ubuntu Touch (current) | Android + Device Owner | **Droidian** |
|---|---|---|---|
| Compositor | Mir 1; no web-capable shell replacement exists | SurfaceFlinger | **wlroots (+hwcomposer)** — `cage` works |
| Browser | QtWebEngine 5.15 = Chromium 87 | System WebView (current) | **Debian Chromium** (current) |
| Controller | systemd-native ✓ (working today) | Termux/proot — no systemd | **systemd-native ✓** (real Debian) |
| Sovereignty story | intact | undermined (runs on Google's OS) | **intact** |
| Effort | blocked | days | **days** |

Droidian is the only option that satisfies every requirement without
compromise: exclusive kiosk, modern rendering, and the controller as a
first-class systemd service on real Linux.

## Port status (verify before flashing)

**The `spacewar` port is a community build, not officially supported.**
Verified 2026-07-21:

- Droidian's official images are **GSI-based** — the only image repos under
  `droidian-images` are `droidian`, `rootfs-api28gsi-all`, `rootfs-api29gsi-all`
  and `rootfs-api30gsi-all`. There is no per-device official image pipeline.
- No `spacewar`/`nothing` repository exists in either the `droidian` or
  `droidian-images` GitHub orgs.
- The port is distributed via an XDA forum thread, not a maintained channel.

Consequences: no OTA updates, no guarantee of maintenance, and the image's
provenance is a forum post. That is acceptable for a concept device, but it
argues strongly for the dual-boot route below — keep the working Ubuntu Touch
install rather than trading it for an unmaintained image.

Reported working/broken as of the XDA thread:

- **Broken:** fingerprint, auto-brightness, Glyph LEDs.
- **Flaky:** WiFi disconnects on some desktop environments; "UI lags on all
  DE"; camera quality poor.
- Default user password is `1234`.

The UI-lag report is worth noting but may not apply to us: it describes full
desktop environments (Phosh/GNOME). A `cage` + Chromium kiosk is a much
lighter load than Phosh, so this needs measuring rather than assuming.

**Open questions to settle before committing:**

1. Do calls/SMS work (oFono or ModemManager)? This decides whether the
   original bridge spec (`docs/superpowers/specs/2026-07-20-*`) is revivable.
2. Is GPU acceleration available to Chromium, or is it software rendering?
   This is probably what the "lag" reports are about.
3. Which Droidian base (Debian version) and how current is the image?

## Install path

Standard fastboot, no repartitioning:

```
fastboot -w                                          # erase userdata
fastboot flash boot   boot-nothing-spacewar.img
fastboot flash userdata rootfs-nothing-spacewar.img  # rootfs lives in userdata
fastboot erase vendor_boot
fastboot erase dtbo
fastboot reboot
```

Note this writes to `boot` and `userdata` — ordinary partition *writes* with
matching images, not GPT/`super` edits. That is the supported install route and
is exactly how Ubuntu Touch got there; UT can be reflashed the same way at any
time.

**A dual-boot option exists** ([XDA guide](https://xdaforums.com/t/guide-dual-boot-ubuntu-touch-or-droidian-android-13-on-nothing-phone-1.4766027/))
— worth evaluating first, since it would preserve the working Ubuntu Touch
install (and its proven taOS controller) alongside Droidian.

## Brick avoidance

Unchanged from prior research, plus one Droidian-specific rule:

- **Never wipe data from Recovery** on Droidian — the rootfs lives in
  `userdata`, so a recovery wipe deletes the OS.
- Keep the bootloader **unlocked**.
- Never resize `super` or edit GPT — the classic `spacewar` soft-brick.
- Never touch `xbl`/`abl` — the EDL 9008 class, unrecoverable without a signed
  firehose loader Nothing does not publish.
- Download the full stock fastboot ROM **and** the Ubuntu Touch installer
  before flashing, so rollback never depends on the network.

## Build on Droidian

1. Flash Droidian; verify hardware over SSH before touching the display.
2. **Controller** — port the venv and units from this repo. Debian has a
   compiler and full apt, so the aarch64-wheel constraints we worked around on
   UT no longer bind. `taos-controller.service` transfers nearly unchanged;
   `loginctl enable-linger` is not needed if it runs as a system service.
3. **Kiosk** — `cage -- chromium --kiosk --app=http://localhost:6969/` as a
   systemd unit replacing the Phosh session. Chromium flags for touch, and
   `--ozone-platform=wayland`.
4. **Boot splash** — Plymouth theme in `kiosk/plymouth-taos/` transfers
   directly (Droidian is Debian; Plymouth is standard).
5. **Telephony** — revisit the SMS/dial bridge; on Debian this is ModemManager
   or oFono over D-Bus, which is what the original spec assumed.

## What carries over from the Ubuntu Touch work

- `taos-controller.service`, the venv layout, and the whole install approach.
- `kiosk/plymouth-taos/` — Plymouth theme, unchanged.
- `kiosk/osk.js` — only needed if the browser lacks a keyboard; Droidian has
  `squeekboard`, so likely retired.
- `kiosk/polyfills.js` — retired; Debian Chromium needs none of it.
- The three upstream taOS bugs (#2080, #2081, #2082) stand regardless.
- Tailscale-as-system-service pattern, which saved the device repeatedly.
