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
| `taos-firstrun.service` | The first-run helper, loopback only |
| `taos-firstrun.py` | The helper itself; installed to `/usr/local/lib/taos/` |
| `check-firstrun-helper.sh` | Acceptance test: prove the helper is not a proxy |

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

### The first-run helper is not a proxy, and the test exists to keep it that way

`taos.my` sends **no CORS headers**. Measured 2026-08-27 against the live host,
with a negative control: a correctly formed preflight (`Origin` *and*
`Access-Control-Request-Method` present — a bare `OPTIONS` proves nothing,
because CORS middleware only answers when those headers are there) returns 404
with no `Access-Control-Allow-Origin`, and a cross-origin `GET /api/auth/me`
returns 401 with none either. The control matters: `GET /api/auth/login` also
returns 404 because it is POST-only, so **404 on that host means wrong-method as
often as absent**, and reading the GET alone reports live routes as missing.

Our first-run UI is served from the phone, so every call to `taos.my` is
cross-origin and Chromium blocks it. The page cannot make these calls. Something
process-side has to, and that is `taos-firstrun.py`.

The timing is the sharp part: it is needed **precisely in remote mode**, which is
the mode defined by there being no local controller. So it cannot be folded into
`taos-controller.service` — in the one case that needs it, that service is not
running.

**The upstream path is never taken from the request.** The client names an
*action* from a fixed table and the table supplies the method and the path. There
is no route that forwards a caller-supplied path or host. An open forwarder on
loopback inside a kiosk browser would be worse than no helper at all: every page
the kiosk ever loads could reach arbitrary hosts through it, with the device's
identity. A prefix allowlist would not do — prefixes invite traversal and
encoding tricks — whereas a closed action table has no user-controlled component
in the URL to trick.

`check-firstrun-helper.sh` runs 26 checks and, importantly, **runs its positive
control first**: it proves forwarding actually reaches upstream before testing
any refusal, because a dead process refuses path traversal perfectly. If the
control fails the script exits `2` INCOMPLETE rather than reporting a pass.
Proven red against five deliberately broken builds — open forwarder, `0.0.0.0`
bind, missing `chmod 0600`, upstream errors swallowed as 200, and forwarding
removed entirely — each caught by the check meant to catch it, the last as
INCOMPLETE rather than PASS.

One incidental find while writing the unit: `ProtectHome=read-write` is **not a
valid systemd value**. systemd logs `Invalid argument` and *ignores the line*,
leaving the default in force — the same silently-ignored-setting class as the
`StartLimitBurst`-in-`[Service]` bug fixed in `taos-kiosk.service`. Worth
grepping other units for settings that look plausible but are silently dropped.

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
