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
| `taos-kiosk-csrf-guard.service` | Watches for a taOS#2081 lockout and restarts the kiosk |
| `kiosk-csrf-guard.sh` | The guard itself; installed to `/usr/local/lib/taos/` |
| `check-csrf-lockout.sh` | Acceptance test: prove the device cannot lock itself out |

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

**The kiosk browser profile is ephemeral, and there is a guard for the half
that does not cover.** This is the mitigation for taOS#2081, which matters more
here than anywhere else it is filed. `verify_csrf` is attached router-wide and
its exemption was *"no `taos_session` cookie → skip"*, so a **stale** cookie
turned a correct PIN into `403 {"detail":"CSRF token missing"}` — which
`pin-panel.js` rendered as **"Incorrect PIN."** A keyboard-less kiosk cannot
clear a cookie, and the error actively misdirects the user, so the device was
unrecoverable by retrying.

There are two halves to that bug, and one mitigation only covers one of them:

| Half | When | Covered by |
|---|---|---|
| Cookie already stale at boot | Kiosk starts against an old session | `--user-data-dir=/run/taos-kiosk/profile`, wiped at every start; `/run` is tmpfs so a reboot cannot carry a cookie forward |
| Session lapses while running | User sits down, cookie is present and looks valid | `taos-kiosk-csrf-guard.service` — sees the 403 in the controller's access log and restarts the kiosk, which drops the cookie with the profile |

Getting only the first half was the trap: an acceptance test asserting "no
cookie at boot" passes while the running-kiosk case stays broken. So
`check-csrf-lockout.sh` asserts the **sign-in itself succeeds with a stale
cookie present**, on both the PIN and the password surface, and reports
`INCOMPLETE` rather than `PASS` when it lacks the credentials to prove that.

Upstream fixed the root cause in taOS PR #2543 — `verify_csrf` now exempts
credential-establishing routes *by path* rather than by the accident of having
no cookie, and `pin-panel.js` reads `detail` as well as `error`. We ship the
mitigation anyway: the device must not depend on an upstream release landing.

**The guard fails rather than watching nothing.** Its only input is uvicorn's
access log on the controller's journal. If access logging is off, or the unit
name is wrong, it would sit `active` forever and detect nothing — green while
measuring nothing, the exact failure the doc gate's Layer A0 exists to refuse.
So at every start it issues a request of its own and confirms the resulting
access-log line is readable, and exits non-zero if it is not. A red unit here is
information; a green blind one would not be.

**We have no credential-establishing form POST of our own.** @taOS-dev found a
second instance of the same class upstream at `POST /setup/complete`: any route
that *mints* a credential can never satisfy a double-submit check, because a
server-rendered form has no JS to attach the header. Checked here rather than
assumed — this repo ships no HTML form and no `fetch()` of its own; every login
surface on the device is taOS's.

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
2. Run `TAOS_PIN=… ./check-csrf-lockout.sh` and get a `PASS`. `INCOMPLETE` means
   a check did not run, which is not the same as a clean device.
3. Only then `systemctl start taos-kiosk.service`, **with the phone in view**.
4. Once proven, `systemctl enable` it so it owns the display at boot.
