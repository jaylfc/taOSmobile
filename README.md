# taOSmobile

Turning a Nothing Phone (1) into a dedicated [taOS](https://github.com/jaylfc/taOS)
device: the taOS controller running natively on the phone, with the taOS PWA as
the full-screen surface.

## Where things stand

**Working today** (Ubuntu Touch, on-device):

- taOS controller running natively as a systemd service on `:6969`, surviving
  reboots. Every dependency resolved as an aarch64 wheel — the phone has no
  compiler.
- taOS rendering on the device through the platform webview.
- Resilient remote access (Tailscale as a *system* service, so it survives the
  graphical session dying).

**Not working:** full-screen exclusive boot. Ubuntu Touch's session shell is a
nested Mir server, and QtWebEngine cannot obtain a graphics backend in that
role — so a web-rendering shell cannot replace Lomiri. Eight approaches were
tested on hardware; see `docs/android-kiosk-scope.md` for the full table of
what was tried and how each failed.

**In progress:** porting [Droidian](https://droidian.org) to the device. Droidian
is Debian (glibc + systemd, so the controller is a straight lift) on Halium (so
the Android vendor blobs keep camera, RIL and VoLTE working), with wlroots — so
`cage` + Chromium gives an exclusive kiosk without fighting the display stack.

## Layout

```
bridge/     Rust hardware bridge (SMS/dial/battery over D-Bus) — scaffold
kiosk/      Kiosk surface: launchers, systemd units, Plymouth theme, polyfills
droidian/   Droidian port: kernel packaging (debian/, config fragments, CI)
scripts/    Device introspection and deployment helpers
docs/       Specs, scopes, and the record of what was tried
```

## Upstream issues filed

Found while bringing taOS up on the device:

- [taOS#2080](https://github.com/jaylfc/taOS/issues/2080) — installed package
  cannot serve the SPA; `static/` is not shipped as package data.
- [taOS#2081](https://github.com/jaylfc/taOS/issues/2081) — login is impossible
  once a session cookie exists: a server-rendered form cannot send
  `X-CSRF-Token`.
- [taOS#2082](https://github.com/jaylfc/taOS/issues/2082) — SPA renders blank in
  system webviews; the bundle calls `Object.hasOwn`/`structuredClone`
  (Chromium 93/98).

## Hardware notes

Findings from the device are in `docs/device-notes.md` — including that
`/home/phablet` ships world-writable and root-owned, which makes sshd's
`StrictModes` silently reject every key.
