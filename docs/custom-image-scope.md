# taOS phone image — scope

Why a custom image is required, what to change, and how to build and flash it
without bricking the device.

**Status:** proposed 2026-07-21, after exhausting the stock-image options.
**Device:** Nothing Phone (1) (`spacewar`, SM7325), Ubuntu Touch 24.04 (noble).

## Why the stock image cannot do this

Ubuntu Touch's session shell is not an app — it is a **nested Mir server**.
`lomiri-system-compositor` owns the VT and the Android hwcomposer HAL and
serves `/run/mir_socket`; Lomiri hosts on that socket and in turn provides
`/run/user/<uid>/mir_socket` to apps.

That leaves exactly two options for a web kiosk, both dead ends:

| Approach | Result |
|---|---|
| Kiosk as a Mir **client** (`ubuntumirclient`) | Renders correctly, but Lomiri must run to host it → its top bar stays, and the compositor keeps showing the boot spinner. |
| Kiosk as a nested Mir **server** (`mir1server`) | Becomes the session and acquires the real display (`PlatformScreen id: 1, 1080x2400`), but QtWebEngine cannot get a GL context: `No suitable graphics backend found`, and segfaults when forced to software rendering. |

Other paths tested and ruled out on hardware:

- **cage / wlroots** — powers the device off. wlroots drives KMS directly; on
  Halium the pipeline belongs to the Android hwcomposer HAL. Mir works only
  because it loads `mir1/server-platform/graphics-android2.so` via libhybris.
  The presence of `/dev/dri/card0` and Mesa freedreno is misleading.
- **Qt `eglfs`** — runs, but one process owns the framebuffer, so there is no
  on-screen keyboard and no compositor.
- **Electron on Mir's Wayland** (`/run/wayland-syscomp`) — connects, then dies:
  Mir's Wayland lacks `wp_viewporter`, `text-input-v3` (the keyboard protocol)
  and errors on every `Surface::commit()`.
- **Electron on DRM** — this build has no `drm` ozone backend.
- **Masking `lomiri-full-greeter`** — the session loses its main process, so
  lightdm falls back to its own greeter and the shell returns.
- **Overriding that unit's `ExecStart`** — shell stays down and the kiosk runs,
  but as a plain client it never becomes the active session, so the spinner
  never clears.

Conclusion: **modern web rendering and an exclusive session cannot coexist on
the stock image.** The shell must be chosen at build time.

## What the image changes

Minimal delta against the UBports `nothing-spacewar` rootfs. The kernel, HAL,
modem and drivers are untouched — that is what keeps the risk low.

1. **Replace the shell.** Drop `lomiri` (and its greeter/indicators) from the
   session. Provide a MirAL-based kiosk shell that hosts a single fullscreen
   web surface. MirAL is the supported way to write a Mir shell and inherits
   the working `graphics-android2` platform, which is why this succeeds where
   wlroots cannot.
2. **Choose a browser engine that works in-shell.** Two candidates, decided by
   experiment during the build:
   - QtWebEngine embedded *in the shell process* (one Qt app that is both Mir
     server and web view) — avoids the cross-process GL failure seen with
     `webapp-container`.
   - A newer Chromium/Electron on a Mir version whose Wayland implements
     `wp_viewporter` and `text-input-v3`. Electron 43 already runs on the
     device, so this becomes viable the moment the compositor is adequate.
3. **taOS preinstalled** — controller venv under `/home` (or `/opt`), systemd
   units enabled, lingering on so it starts with no login. Already proven on
   device; only the packaging moves.
4. **Boot splash** — replace `lomiri-system-compositor-spinner` (the animation
   still showing today) and ship the Plymouth theme in
   `kiosk/plymouth-taos/`. Both are ordinary rootfs files.
5. **Keyboard and gestures** — with a real compositor, `maliit` attaches as it
   does for Lomiri. The web OSK in `kiosk/osk.js` stays as the fallback for
   engines/sessions where it cannot.

## Sources

- Device port: https://gitlab.com/ubports/porting/community-ports/android11/nothing-phone-1/nothing-spacewar
- Kernel: https://gitlab.com/ubports/porting/community-ports/android11/nothing-phone-1/kernel-nothing-sm7325

The kernel repo matters only if we need HAL/driver changes — we do not. Work
happens in the rootfs, not the kernel.

## Not bricking it

Researched failure modes for this device, worst first:

- **EDL 9008.** Unrecoverable without a signed firehose loader, which Nothing
  does not publish. Reached by corrupting bootloader-adjacent partitions.
  **Never flash `xbl`, `abl`, `boot`-adjacent firmware, or GPT.**
- **Super partition resize.** The common soft-brick: custom recoveries that
  repartition, after which stock images no longer fit and fastboot restore
  fails. **Never modify the `super` layout.**
- **Flashing with the bootloader locked** — leaves the device stuck in
  fastboot ("Flashing not allowed in Lock State"). **Keep it unlocked.**

Rules for this project:

1. Rootfs images only, via the UBports installer's supported path. No manual
   partition writes, no GPT edits, no `super` changes.
2. Keep the bootloader unlocked for the project's duration.
3. Download a full stock fastboot ROM **before** the first flash, so recovery
   never depends on network access at a bad moment.
4. Leave the **Nothing bootloader logo alone.** It lives in a splash partition;
   changing it is the only remaining item that requires a partition write, and
   it is purely cosmetic. Not worth the only unrecoverable risk class.
5. Every image keeps SSH enabled and `taos-tailscaled.service` at
   `multi-user.target`, so a black screen is always recoverable remotely — this
   already rescued the device three times.

## Build and flash loop

1. Fork the `nothing-spacewar` rootfs recipe; add the taOS shell package,
   controller, units and splash.
2. Build the rootfs; keep the stock boot image.
3. Flash with the UBports installer over fastboot (system/rootfs only).
4. Verify over SSH before touching the screen: controller up, shell process up,
   compositor up, then look at the display.
5. Keep the last known-good rootfs so a bad build is one reflash from working.

## Sequencing

1. Prove the shell **on the current phone first** — a MirAL kiosk hosting a web
   view, run in place of Lomiri via the `ExecStart` override already working in
   `kiosk/`. If it renders and clears the spinner, the image is packaging.
2. Only then build the image.

This keeps the expensive step (image build + flash) until after the risky
unknown (can a MirAL shell host taOS?) is settled on hardware that is one
`systemctl` away from working.
