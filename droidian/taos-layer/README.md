# taOS layer for Droidian

Step 6 of `docs/droidian-port-plan.md`: bring taOS up on a freshly-flashed
Droidian phone. Authored ahead of the flash so it is ready to drop in.

## Contents

| File | What |
|---|---|
| `install-taos.sh` | One-pass on-device bring-up. Idempotent. Run over SSH. |
| `taos-controller.service` | The controller, as a **system** service |
| `taos-kiosk.service` | `cage` + Chromium, fullscreen exclusive |
| `taos-kiosk-recover.service` | `OnFailure` net — hands the display back to Phosh |

## Design decisions, and why

**The controller is a system service, not a user service.** On Ubuntu Touch it
had to be a user service plus `loginctl enable-linger`, because stopping the
display manager tore down the user session and killed the controller with it.
Droidian is real Debian with real systemd; that whole class of problem is gone.

**`install-taos.sh` verifies the controller over SSH before touching the
display.** It brings the controller up, waits for `:6969` to actually accept a
connection, and then *stops* — printing the command to take the screen rather
than taking it. On this device a broken display costs a reboot to diagnose, and
process state is not screen state.

**The kiosk `Conflicts=phosh.service`** rather than racing it. Only one process
can hold the display.

**Recovery is a separate system unit.** On Ubuntu Touch the watchdog died with
the session it was meant to rescue. This one cannot, because it is not in that
session.

**Editable install (`pip install -e`), deliberately.** taOS#2080 means a
non-editable install ships no `static/`, so the controller onboards fine and
then 404s its own SPA. Editable keeps `SPA_DIR` on the source tree. The script
asserts `index.html` is actually resolvable rather than assuming.

**`--prefer-binary`, not `--only-binary`.** `http-ece` is source-only but pure
Python; `--only-binary` refuses the entire resolve because of it.

## Not needed here (unlike the Ubuntu Touch attempt)

- `kiosk/polyfills.js` — Debian Chromium is current; the `Object.hasOwn` /
  `structuredClone` shims were for QtWebEngine 5.15 (Chromium 87).
- `kiosk/osk.js` — Droidian ships `squeekboard`.
- Any nested-Mir contortion. `cage` works because Droidian's wlroots has an
  hwcomposer backend. **Never** run vanilla-wlroots `cage` on a Halium device:
  it fights the Android HAL for DRM master and powers the phone off.

## Order of operations after the flash

1. Verify over SSH: network, then `install-taos.sh`, then confirm `:6969`.
2. Only then `systemctl start taos-kiosk.service`, **with the phone in view**.
3. Once proven, `systemctl enable` it so it owns the display at boot.
