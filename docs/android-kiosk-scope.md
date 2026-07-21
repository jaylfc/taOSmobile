# taOS phone via Android kiosk — scope

An alternative to the custom Ubuntu Touch image, proposed by Jay after the
stock UT image was exhausted. For a concept phone whose value is showing what
taOS feels like, this is the recommended route.

**Status:** proposed 2026-07-21.
**Device:** Nothing Phone (1) (`spacewar`, SM7325) — ships Android 13.

## Why: every wall we hit was a Halium wall

Ubuntu Touch runs the Android display stack through libhybris. `Mir 1`
(`lomiri-system-compositor`) owns the VT and the hwcomposer HAL, and nothing
else can have it. Exhaustively tested on the device:

| Attempt | Outcome |
|---|---|
| webapp-container as Mir client, under Lomiri | **Renders taOS** — but Lomiri must run, so its top bar stays. Not exclusive. |
| webapp-container as nested Mir server (`mir1server`) | Acquires the display, then `No suitable graphics backend found`; segfaults when forced to software rendering. |
| cage / wlroots on KMS | **Powers the phone off.** Three times. wlroots drives KMS directly; the Android HAL owns the pipeline. |
| Qt `eglfs` | Runs, but one process owns the framebuffer — no keyboard, never becomes the session. |
| Electron on Mir 1's Wayland | Mir lacks `wp_viewporter`/`text-input-v3`, errors on every `Surface::commit()`. |
| Electron, any mode | Hangs before `app ready` on Wayland; **segfaults** creating a window even headless. |
| miral-kiosk (Mir 2) nested via `mir:wayland` | Runs **only while Lomiri is up**; segfaults the moment Lomiri stops — i.e. exactly when it would be useful. |

Conclusion: **exclusive fullscreen with a working renderer is not achievable on
the stock UT image.** The only routes are a custom UT image (see
`custom-image-scope.md`) or Android.

## Why Android is the better bet here

- **Rendering.** Android System WebView is current Chromium (~130+). The
  Chromium 87 problems — missing `Object.hasOwn`/`structuredClone`, the
  viewport/scale defect visible in Jay's photo — simply do not exist.
- **Exclusivity is a supported API, not a fight.** Device Owner + Lock Task
  Mode: no status bar, no nav bar, no recents, no escape, starts at boot. This
  is requirement (1) as documented Android behaviour.
- **Input.** Keyboard, gestures, rotation, sleep/wake all native.
- **No image build required** (see below).

The cost is the controller: Android has no systemd and no glibc, so taOS runs
under Termux or a proot/chroot Debian rather than the clean systemd service
that works today on UT. That is not novel — it is taOS issue #39 (Termux
workers) — and the aarch64 wheel set proven today still applies.

## No custom ROM needed

Device Owner can be set on a stock or LineageOS install over adb, on a freshly
provisioned device with no accounts:

```
adb shell dpm set-device-owner com.taos.kiosk/.AdminReceiver
```

So the build is:

1. **Kiosk APK** — single activity, `WebView` pointed at `http://localhost:6969/`,
   `startLockTask()`, immersive mode, `HOME` category intent filter so it *is*
   the launcher, `BOOT_COMPLETED` receiver.
   - `setJavaScriptEnabled`, `setDomStorageEnabled`, and a `WebViewClient` that
     keeps navigation inside the controller origin.
2. **Controller** — Termux + `proot-distro` Debian, taOS venv (same wheels),
   started from Termux:Boot. Battery optimisation exemption + a foreground
   service so Android does not kill it.
3. **Splash** — Android boot animation is `/system/media/bootanimation.zip`, a
   documented format (a ZIP of PNG frames + `desc.txt`). Replaceable on a
   rooted/custom ROM without touching the bootloader logo.

## Reflashability and brick avoidance

Same rules as the UT scope, and they matter more here because Android work
tempts partition writes:

- Keep the bootloader **unlocked**; flashing while locked strands the device in
  fastboot ("Flashing not allowed in Lock State").
- **Never resize `super` or edit GPT** — the classic spacewar soft-brick, after
  which stock images no longer fit.
- **Never touch `xbl`/`abl`/bootloader partitions** — that is the EDL 9008
  class, which needs a signed firehose loader Nothing does not publish. This is
  the only unrecoverable failure mode.
- Download the full stock fastboot ROM **before** flashing anything.
- The **Nothing boot logo** lives in a splash partition. Leave it alone: purely
  cosmetic, and the only remaining item that requires a partition write.

Device Owner setup requires a factory reset (no existing accounts), which is
fine for a concept device and is fully reversible.

## Sequencing

1. Build the kiosk APK and test it on the phone's **current** state? No — this
   requires Android, so first decide and reflash to stock/Lineage.
2. Flash stock Android (or LineageOS for `spacewar`) via the standard fastboot
   route. Ubuntu Touch remains reflashable at any time by the same mechanism.
3. Termux + proot Debian + taOS controller; verify `http://localhost:6969/` in
   ordinary Chrome first.
4. Install the kiosk APK, set Device Owner, verify lock task survives reboot.
5. Boot animation and branding last.

## What we keep from the Ubuntu Touch work

Not wasted — several pieces are portable or independently valuable:

- taOS controller proven on aarch64 with **no compiler** (every dependency has
  a wheel; `http-ece` needs `--prefer-binary`). Same venv works under proot.
- Three upstream bugs filed with reproductions: taOS #2080 (static assets not
  shipped as package data), #2081 (CSRF lockout on the server-rendered login
  form), #2082 (SPA needs Chromium 93+ APIs).
- `kiosk/polyfills.js` and `kiosk/osk.js` remain useful for any old webview.
- This device stays a working "taOS on real Linux, systemd-native" demo until
  it is reflashed.
