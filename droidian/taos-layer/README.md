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
| `taos-kiosk-launch.sh` | Resolves the kiosk URL from `shell.conf`, then becomes the kiosk |
| `check-kiosk-url.sh` | Acceptance test: prove the kiosk never opens a dead page |

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

#### The one route that does take a caller-supplied host

`POST /api/check` is the exception to the paragraph above, and it is shaped so
that it is not a hole in it. It exists because the form has to tell the user
whether the address they just typed is a controller **before** it is written to
`shell.conf` — after that the device points Chromium at it on every boot, and a
typo is a dead screen on a phone with no keyboard. The page cannot check for
itself: the controller is a different origin, so the answer is unreadable from
JavaScript.

What keeps it small: the path is fixed (`/api/health`), only the **origin** of
the caller's URL is used (a pasted path is dropped, not appended to), the
response never crosses back — the caller gets a verdict plus two integers read
from fixed key names and range-checked — and it is rate limited to 30/minute
with a 5s timeout, so it is neither a scanner nor an amplifier. What it does
still give a loopback caller is a coarse reachable/not oracle for hosts this
device can reach. That is the cost, it is stated rather than glossed, and it is
why the limiter is there.

> **"The page could already make the device navigate anywhere via `/api/config`"
> is not a defence.** A cross-origin navigation's result is not readable by the
> page that caused it. This is a capability that genuinely did not exist before.

#### A status code is not an answer — the SPA trap

Measured 2026-08-27, and this is why the check tests the response *shape*:

| target | `GET /api/health` | |
|---|---|---|
| taOS controller `:6969` | **200** `{"status":"ok","agents":2,"backends":9}` | a controller |
| taOSmd A2A bus `:7900` | **200** `<!doctype html>…` | **not** a controller |

The bus is a single-page app with a catch-all route, so it answers 200 with
`index.html` for a path it has never heard of. A port check accepts it. A
"200 means yes" check accepts it too. Only the content type and the `status`
key tell them apart, so a stub with exactly that behaviour is a permanent
negative control in the test — relax the check to a status code and it goes red.

Two more things that came out of the same measurement, both worth keeping:

- `GET /api/nonexistent-control-probe-3` on the controller returns **401, not
  404** — it authenticates before it routes. So on *that* host a 404 does not
  mean absent. It is the exact inverse of the `taos.my` trap above, where a 404
  meant wrong-method. **Do not port a 404 reading between the two hosts.**
- A controller that gates `/api/health` would be reported `not_a_controller`.
  That is honest — it is indistinguishable from any other authenticated service
  — and it is why the form offers *Use this address anyway* rather than
  treating a failed check as a wall. A controller that is merely switched off is
  a legitimate thing to configure.

`check-firstrun-helper.sh` runs 39 checks and, importantly, **runs its positive
control first**: it proves forwarding actually reaches upstream before testing
any refusal, because a dead process refuses path traversal perfectly. If the
control fails the script exits `2` INCOMPLETE rather than reporting a pass. The
reachability section has its own positive control for the same reason — a check
that can never say `ok` refuses everything perfectly — and the rate-limit test
runs against its own helper process so that exhausting the shared bucket cannot
turn every other assertion into an undocumented ordering dependency.
Proven red against five deliberately broken builds — open forwarder, `0.0.0.0`
bind, missing `chmod 0600`, upstream errors swallowed as 200, and forwarding
removed entirely — each caught by the check meant to catch it, the last as
INCOMPLETE rather than PASS.

One incidental find while writing the unit: `ProtectHome=read-write` is **not a
valid systemd value**. systemd logs `Invalid argument` and *ignores the line*,
leaving the default in force — the same silently-ignored-setting class as the
`StartLimitBurst`-in-`[Service]` bug fixed in `taos-kiosk.service`. Worth
grepping other units for settings that look plausible but are silently dropped.

### The kiosk URL comes from `shell.conf`, and every fallback points at the helper

`taos-kiosk.service` used to hardcode `--app=http://localhost:6969/`. That is
correct in exactly one of the three states this device can be in:

| State | What listens on `:6969` | What the hardcoded unit showed |
|---|---|---|
| Fresh device, no `shell.conf` | nothing | a dead page, with no way to reach the first-run helper on `:6970` |
| `mode=local` | the controller | correct |
| `mode=remote` | nothing — remote mode is *defined* by having no local controller | a dead page |

So the URL is resolved at start from `~/.config/taosmobile/shell.conf`, which is
what `docs/first-run-controller-choice.md` requirement 3 asks for and what the
first-run helper already writes. systemd cannot do this in the unit file:
`ExecStart=` performs no command substitution. Hence `taos-kiosk-launch.sh`.

**Which way to fail is the whole safety argument.** Every fallback resolves to
the first-run helper, never to `:6969`. A user holding a phone with no keyboard
can escape the helper screen — it is a form that writes the config. They cannot
escape a controller that is not there. The one deliberate exception is
`mode=local` with a malformed `url=`: that device *has* a controller, so it gets
`:6969` rather than being sent back to first-run setup.

**A dead loopback target is an exit 3, not a launch.** A kiosk sitting on
Chromium's "site can't be reached" is a *successful* start, so `Restart=` and
`OnFailure=` never fire and the screen is stuck — the "successful start showing
the wrong page" that `OnFailure=taos-kiosk-recover.service` explicitly does not
cover. Refusing the display instead lets the recover unit hand it back to Phosh,
which has a keyboard and a way back in. This required
`RestartPreventExitStatus=3 64` in the unit: `Restart=on-failure` parks a unit in
*auto-restart*, not *failed*, and `OnFailure=` only fires on *failed*, so without
it the kiosk would flap forever and never fail over. The start limit could not
rescue it either — the launcher waits 45 s internally, so three attempts span
~150 s and never fall inside a 60 s window.

**Remote targets are deliberately *not* readiness-gated.** A phone in remote mode
is a surface whose network comes and goes; handing the display back to Phosh on a
blip would be worse than the unreachable-controller state requirement 2 already
puts in the UI. Only loopback targets are gated.

**The insecure-origin flag now follows the resolved URL.** It was hardcoded to
`http://localhost:6969`, which had it backwards: Chromium already treats
`localhost` as trustworthy without any flag, while a remote `http://` controller
— the case that actually needs it — was not covered. It is also why the URL
grammar rejects commas: that flag takes a *comma-separated list*, so a comma in
`shell.conf` would grant a secure context to an origin nobody chose.

`check-kiosk-url.sh` runs 32 checks off-device against constructed `shell.conf`
files. Its positive control is **different from the helper's**, because the trap
here is different: most expected answers are the helper URL, so a launcher that
ignored `shell.conf` entirely and always printed the helper would pass most of
the file. The control therefore proves the launcher *discriminates* — a good
remote config yields the configured URL and a local config yields the controller
— and exits `2` INCOMPLETE if it does not. Proven red against eight deliberately
broken builds: config ignored (caught as INCOMPLETE, not FAIL), fallbacks
pointed at `:6969`, launching anyway on a dead target, the origin flag hardcoded
back, `RestartPreventExitStatus` deleted, a loosened URL grammar, remote targets
gated, and the unit's `ExecStart` reverted.

Every readiness call in the checker runs under an **outer** `timeout`, and the
probe bounds each connect attempt. A bare `/dev/tcp` connect to an address that
*blackholes* rather than refusing blocks for the kernel's SYN-retry budget —
minutes — which made `WAIT_SECS` a lower bound rather than a budget and turned
one broken-build run into a hang. A hanging check is not a red; it is an
instrument that stopped reporting.

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
3. Confirm what the kiosk would open: `./taos-kiosk-launch.sh --print-url`. On an
   unconfigured device this is the first-run helper, and
   `systemctl is-active taos-firstrun.service` must say `active` — otherwise the
   launcher will refuse the display rather than show a dead page.
4. Only then `systemctl start taos-kiosk.service`, **with the phone in view**.
5. Once proven, `systemctl enable` it so it owns the display at boot.
