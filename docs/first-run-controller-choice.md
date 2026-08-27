# First run: local controller or remote controller

Proposed 2026-07-21. On first boot, taOSmobile should ask whether this device
runs its own taOS controller or attaches to one that already exists.

## Why

The two modes suit genuinely different devices, and the choice is not obvious
enough to make for the user:

- **Local controller.** The phone is self-contained: controller, agents and
  memory all on-device. Works with no network at all, and is the strongest form
  of the sovereignty story — nothing leaves the handset. Costs storage (venv
  plus any local models), battery, and first-run time.
- **Remote controller.** The phone is a surface and a cluster worker attached to
  an existing controller (a Pi, a desktop, a server over Tailscale). Boots
  faster, uses far less storage, and the phone's agents see the whole cluster.
  Requires the controller to be reachable.

A phone bought as a dedicated taOS device wants local. A phone joining a fleet
that already has a controller wants remote. Both are legitimate.

## Flow

First boot, after the kiosk starts and finds no configured controller:

```
        ┌─────────────────────────────┐
        │  Set up this taOS device    │
        ├─────────────────────────────┤
        │  ( ) Run taOS on this phone │   local
        │  ( ) Connect to a controller│   remote
        └─────────────────────────────┘
```

**Local:** install/enable `taos-controller.service`, wait for `:6969`, hand off
to taOS's own onboarding (account creation). The heavy work — venv, wheels —
should be pre-baked into the image rather than downloaded at first run, so this
path works with no network.

**Remote:** attach the phone to a controller, then separately enrol it as a
worker. Both halves are confirmed against `origin/dev` below.

> **Corrected 2026-08-27 from @taOS-dev's read of `origin/dev` (A2A 3415/3416,
> their card `tsk-f5yyby`).** The three questions this section used to defer are
> answered. Route/function references are theirs, given so this can be verified
> rather than believed; re-check the line numbers if anything lands upstream
> before our first boot.

### What the phone actually does

**Attaching to a controller — the render-a-code flow exists, exactly as wanted.**

```
POST /api/devices/pair-requests       -> {pair_request_id, verify_code}
GET  /api/devices/pair-requests/{id}  -> poll status / retrieve token
```

(`tinyagentos/routes/device_pair_requests.py:94` and `:191`.) Both are
**unauthenticated by design** — the device has no credentials yet. Approval is
not a separate route: creation raises a Decision carrying
`{kind: "device_pairing", pair_request_id}`, and answering it "approve" runs
`_apply_device_pairing_grant` in `routes/decisions.py`, minting a `taosdev_`
token bound to the **answering** user. So "approve from another signed-in
device" is already built — the approving surface is anywhere that user can
answer a Decision.

Four constraints to design against, not to discover later:

- **Platform whitelist** is `frozenset({"ios","watchos","android"})` (`:46`).
  Android is in; anything else 400s. Our kiosk must report `android`.
- **`pair_request_id` is the capability** for polling — only the caller who
  received it can poll. Never log it, never put it on screen.
- **`verify_code` is a human-comparison nonce only** (`_generate_verify_code`,
  `:70`; security note F3). Six digits, returned once at creation, **never
  server-checked and never accepted as input**. Display it for the user to
  eyeball. Do **not** build it as a second factor or as something re-submitted —
  this is the single easiest thing to get wrong here.
- **Expiry is enforced at approve time**, not merely hidden from the poll (F6),
  so a stale request can sit pending on our screen and then fail. The UI must
  have a state for that.

**Account sign-in — talk to taos.my directly, NOT to `/api/account/*`.**

> **Corrected again 2026-08-27 (A2A 3418).** An earlier revision of this section
> named `POST /api/account/login`
> (`tinyagentos/routes/account_proxy.py:427`). **That is a controller route** — a
> same-origin convenience for a SPA already running on a controller, which
> forwards upstream. It therefore carries *exactly* the chicken-and-egg
> described above: a phone that does not yet know a controller address cannot
> call it either. Every `/api/account/*` route is ruled out by our auth-ordering
> requirement, by construction.

The phone talks to **taos.my** directly. Strip the `/api/account` prefix. The
upstream surface, read off the controller's forwarding table (`_ACTIONS`, `:45`):

```
GET  /api/auth/me                       <- the only account-credential call that always exists
POST /api/auth/login
POST /api/auth/register
POST /api/auth/logout
     /api/subdomains/{check,claim,release}
     /api/hub/identity/{register,lookup,rotate}
     /api/hub/requests  [+ /{id}/accept, /{id}/decline]
     /api/hub/presence
     /api/hub/edges/revoke
     /api/hub/relay/{drop,poll}
     /api/cluster/join/...               (_JOIN_BASE, :448 — from taos-website PR #35)
```

This removes an ordering problem rather than adding one: **the auth half of
first run has no controller dependency at all.**

There is no code-approval variant, but it is a plain JSON API rather than a
hosted page or an OAuth redirect, so we render our own form against our own
on-screen keyboard. A password gets typed on the device once; that cost is real
and does not go away.

#### Probed against live taos.my, 2026-08-27 — with a negative control

Run from this workstation, unauthenticated, no credentials sent:

| request | result | reading |
|---|---|---|
| `GET /api/auth/me` | **401** | route exists, account-credential gated |
| `POST /api/auth/login` (empty body) | **422** | route exists, rejects the empty body |
| `POST /api/auth/register` (empty body) | **422** | route exists |
| `POST /api/nonexistent-control-probe` | **404** | negative control fires |

The control matters: a bare `GET /api/auth/login` also returns 404, so **404
here means "wrong method", not "absent"**, and reading the GET alone would have
reported a real route as missing. Server is `uvicorn`.

#### The finding that changes the architecture: taos.my sends no CORS headers

A **correctly formed preflight** — `OPTIONS /api/auth/login` with `Origin` and
`Access-Control-Request-Method: POST` — returns **404 with no
`Access-Control-Allow-Origin`**. A cross-origin `GET /api/auth/me` carrying
`Origin` returns 401, also with no `Access-Control-Allow-Origin`. (Checked with
the preflight headers present, because a bare `OPTIONS` proves nothing — CORS
middleware only answers when `Origin` and `Access-Control-Request-Method` are
there, so the first bare probe was not evidence.)

**Consequence: the kiosk page cannot `fetch()` taos.my.** Our first-run UI is
served from the phone, not from taos.my, so every one of these calls is
cross-origin and the browser will block it. "Render our own form and POST it"
was written assuming a browser fetch, and as written it does not work.

**So first run needs a small local helper** that makes the HTTPS calls
process-side and exposes them to the kiosk page same-origin. *Scope narrowed
later in this doc:* this binds anything that **reads** taos.my (enumerating
hosts, checking entitlement). It does **not** bind connecting to a controller —
that is a top-level navigation, which CORS does not govern. See "A handle CAN be
turned into a URL". Note this is
required *precisely in remote mode*, which is the mode defined by there being no
local controller — so it cannot be borrowed from `taos-controller.service`. It
is a new, small, always-present component, and the spec must own it rather than
discover it at bring-up.

The alternative is CORS on taos.my, which is not our repo and not our call.
Raised with @taOS-dev; a local helper is assumed until told otherwise, because
it is the half we control.

**Worker enrolment is a genuinely separate system** — different endpoints,
different credential, different auth scheme. Confirmed orthogonal, so
requirement 5 below stands as written:

```
POST /api/cluster/pairing/announce      (:86)
GET  /api/cluster/pairing/pending       (:101)
POST /api/cluster/pairing/confirm       (:112)
POST /api/cluster/pairing/claim         (:132)  -> {signing_key}
POST /api/cluster/pairing/manual        (:172)
POST /api/cluster/pairing/manual-claim  (:196)  -> {signing_key, url}
```

then `POST /api/cluster/workers` (`routes/cluster.py:350`, `register_worker`).
The credential is a 32-byte HMAC signing key — **not** the `taosdev_` device
token and **not** the account credential. Every worker request is signed
(`tinyagentos/cluster/worker_auth.py`): signing string
`f"{timestamp}.{METHOD}.{path}.{sha256(raw_body).hexdigest()}"`, headers
`X-TAOS-Worker-Name` / `X-TAOS-Timestamp` / `X-TAOS-Signature` (HMAC-SHA256,
hex). The body `name` must equal the header name, so a paired worker cannot act
for another name.

`/api/cluster/pairing/manual-claim` is **unauthenticated and poll-based** and is
the kiosk-friendly shape: the worker displays a code, an admin on another device
calls `/api/cluster/pairing/manual` with the worker's address plus that code,
and the worker polls `manual-claim` — 202 "awaiting" until authorised, then
`{signing_key, url}`. Per-IP rate limited (`_manual_claim_rate_ok`), so back off
on 429.

### Enumeration EXISTS — `GET /api/hosts`. This section used to say it did not.

**Corrected 2026-08-27 (A2A 3424).** Everything below the fold in the previous
version of this section was built on "there is no controller-enumeration
endpoint". That was wrong, and it was wrong in the direction that costs most:
it declared a requirement unmeetable and sent the design down a fallback path.

```
GET /api/hosts                         taos-website server/main.py:864
    user: dict = Depends(current_user)     <- ACCOUNT session credential
    -> {"hosts": [{handle, created_at}], "count": N}
```

`Depends(current_user)` is the account credential and nothing else — no device
token, no controller address, no hardware. **That is the hard ordering
requirement met verbatim**, which is the thing this section said was missing.
Backing store is real: `accounts.py:78`, `hosts(id, account_id, headscale_user,
pairing_code, handle UNIQUE, created_at)`; `list_hosts` orders by `created_at`.

**Verified against the live host, not just read:** `GET /api/hosts` → **401**,
with `GET /api/nonexistent-control-probe-2` → 404 as the negative control. The
method is correct for this route, so the 404-means-wrong-method trap that bit
the `/api/auth/login` probe does not apply here. 401 rather than 404 is the
route existing and refusing an anonymous caller.

**`GET /api/auth/me` is *not* controller-shaped** — `server/main.py:408` returns
`user_id, email, username, email_verified, taosgo{status, trial_ends_at,
current_period_end}` and nothing about hosts. That question is closed.

### But a handle is not an address, and that is the real constraint

This is the part that changes the screen, and it presents as an auth bug exactly
the way the CORS finding did.

`hosts` stores a **handle**. Turning a handle into something the phone can
connect to goes through the relay, and that path is **entitlement-gated**:

```
server/main.py:937-938
    if user["taosgo_status"] not in ("trialing", "active"):
        raise HTTPException(403, "not_entitled")
```

So on a free account, `GET /api/hosts` will tell the phone a controller **exists
and name it**, and taos.my will then decline to hand back an address for it.
Off-LAN reach is a paid taOSgo feature by design — not a bug, and not something
to file. **taos.my stores no LAN address at all**, so there is nothing to fall
back to on the local network either.

**Consequence: "list controllers, tap one, connect" does not work as a whole.**
The list step is free and available today; the connect step needs either an
entitled account or an address the user supplies. A picker that enumerates and
then cannot connect is worse than no picker, because it shows the user a
controller by name and then fails — which reads as our bug.

**Manual URL entry** — a directly-entered controller URL, no account involved.
Works on a LAN, for development, and for a fleet running fully offline. This is
still the path to build first.

> **The conclusion survived; its reason did not.** The previous version said
> manual entry was the only remote path *because enumeration did not exist*.
> Enumeration does exist. Manual entry is still first because enumeration yields
> a **name, not a route**, and the name→route step is paid. Same answer, wholly
> different reason — worth writing down, because a right answer resting on a
> wrong premise breaks silently the moment the premise is fixed. Had `tsk-ckvyps`
> landed while this doc still said "absent", the spec would have kept steering
> away from an API that was already there.
>
> **And then it happened again, one layer down.** "The name→route step is paid"
> was itself superseded within the day: the route is constructible for free, and
> the real blocker is that `*.taos.my` has no ingress deployed. Third reason,
> same conclusion. See "A handle CAN be turned into a URL" below.

**Sequencing that follows:** build manual entry first, as before. Add
enumeration as an *assist* on top — offer the list from `/api/hosts` to name
what the account has, and require an address for any host whose route cannot be
resolved, rather than presenting the list as a connect flow. `tsk-ckvyps` is
therefore "confirm and close the gap", not "build from nothing".

**The ordering constraint still holds, and still matters.**
`/api/devices/pair-requests` lives on a **controller**, not on taos.my, so the
phone needs a controller address before it can pair. Enumeration is the step
*before* pairing. `/api/hosts` satisfies the credential ordering — it is callable
with only the account credential — but because it returns a handle rather than an
address, it does not by itself put the phone in a position to pair. The address
still has to come from entitlement or from the user.

### A handle CAN be turned into a URL — and that URL does not resolve today

@taOS-dev (A2A 3430) answered the address question: do not wait for an address
field, **construct the hostname client-side**.

```
https://{handle}.{username}.taos.my     handle from /api/hosts, username from /api/auth/me
https://{username}.taos.my              bare form: relay resolves the account's primary host
```

Read at the source on `jaylfc/taos-website` **`origin/dev`** — note `main` is a
diverged tree (817 lines vs 1067), so line numbers only mean anything on `dev`:

- `relay_tls_allow` (`main.py:895`) takes 1 *or* 2 labels and reads the username
  as the **last**, so `hostlabel.username.taos.my` is an intended first-class
  form, not an accident. Its docstring says so.
- `relay_authorize` (`main.py:924`) reads the specific host from the
  `x-taos-host-handle` header Caddy sets from the left label; with no handle it
  falls back to the account's **first linked host by `created_at`** (`:945`).
- the relay Caddy reference config, upstream at
  `jaylfc/taos-website/docs/relay.Caddyfile` (not a path in this repo), turns each auth failure into a browser journey rather
  than raw JSON: **401 → 302 to `taos.my/login.html?return=<this URL>`**,
  403 → 302 to `account.html`, 404 → plain-English "host is offline".

**Two consequences that would have changed the screen — if the URL worked.**

1. **The remote-connect path needs no CORS and no helper.** The kiosk never
   `fetch()`es taos.my; it **navigates**, and top-level navigation is not
   governed by CORS. The 401 redirect lands on taos.my's *own* login page, which
   is same-origin with itself, and the session cookie is issued with
   `Domain=.taos.my` (`main.py:306,318`) so it is sent back up to the subdomain.
   The CORS finding below is still true, and still forces a helper for anything
   that *reads* taos.my — it does **not** force one to connect.
2. **Entitlement never needs to be inferred from a 403.** `/api/auth/me` returns
   `taosgo.status`, and the gate at `:937` is literally
   `status in ("trialing","active")` — the same predicate, readable from a 200 body.

#### Measured, not read: `*.taos.my` has no ingress at all

The above is what the code and the reference config say. **It is not what the
deployment does.** Probed 2026-08-27:

```
tls-allow?domain=taos.taos.my            -> 200 {"ok":true}      gate says: issue a cert
tls-allow?domain=nosuchuser-….taos.my    -> 404 unknown_host     gate discriminates correctly
https://taos.taos.my/                    -> TLS fails, CN=TRAEFIK DEFAULT CERT
https://nosuchuser-….taos.my/            -> TLS fails, SAME default cert
http://taos.taos.my/       (plaintext)   -> 404          <- no router for the wildcard
http://taos.my/            (control)     -> 302 to https, LE cert on the apex
```

**The relay Caddy is not deployed.** What answers `*.taos.my:443` is Coolify's
Traefik, handing out its default self-signed cert for *every* subdomain —
identically for a name the gate allows and one it refuses. Three independent
controls agree: the allowed and refused names are indistinguishable (so nothing
consults the gate); plaintext :80 404s on the wildcard while the apex 302s (so
no router matches, with no cert confusion in the way); and a fresh domain gives
exactly 5 hits before `429` against the 5/hour limiter (`main.py:892`), i.e. the
counter started at zero, so production traffic is not calling it. The cert was
still Traefik's minutes after the first hit, so this is not first-hit issuance lag.

A phone sent to that URL gets `ERR_CERT_AUTHORITY_INVALID` — an unskippable
interstitial in a kiosk with no keyboard.

> **The third instance of the same failure, and the reason to keep measuring.**
> The server code is right, the Caddyfile is right, and the thing is unreachable,
> because that Caddyfile is a reference artifact that was never deployed. This
> doc has now recorded that shape twice before: `taos-firstrun.service` was
> installed but never enabled, and requirement 3 claimed the launcher "already
> does this" when nothing did. *A component nobody depends on is a component
> nobody can tell is broken* — and reading four source files end to end is not
> the same as reaching the host end to end.

**Manual URL entry stays first, now for the only reason that is about the world
rather than the API:** the constructed hostname does not currently serve a
trusted certificate, so enumeration and entitlement are both moot until the
relay is actually deployed. When it lands, the remote branch collapses to **one
field — the username** — and the relay handles sign-in, entitlement and
offline-host messaging itself. That is a much smaller screen than the helper-
mediated flow specced above; build manual entry so that it does not have to be
unbuilt.

### One earlier claim in this doc was half wrong

This doc used to say pairing is "taOSgo account-based over headscale" and that
"mDNS is gone". The second half misleads on the part we care about: the **worker
pairing path still has a LAN-address branch**. `/api/cluster/pairing/manual`
takes the worker's LAN address typed by an admin — its docstring calls it "the
free-tier 'Add worker' path ... No announce or network discovery: the admin
supplies the address by hand". Worker enrolment therefore needs no account-based
discovery at all, which is cheaper for us than anything account-mediated.

## Requirements

1. **Reversible.** Switching modes later must be possible from settings — not a
   first-run one-way door. Local → remote should offer to keep or discard local
   data; remote → local must not silently orphan the remote pairing.

   > **Not built, and it is the largest remaining gap on this card.** Once
   > `shell.conf` names a mode, the launcher resolves to it on every boot and
   > there is no route back to the helper from the kiosk — on a device with no
   > keyboard that is a one-way door, which is exactly what this requirement
   > forbids. The check in requirement 2 narrows how often a wrong address gets
   > written; it does not make a written one reversible. Tracked as `tsk-l3ntdg`.
   >
   > **Its prerequisite landed 2026-08-27, and it was not the constraint it was
   > filed as.** `tsk-l3ntdg` names one shape the escape must not take: an
   > escape that is merely a link would be a one-click drive-by, *because*
   > `POST /api/config` is reachable from any page the kiosk loads. Measuring
   > that rather than reasoning from it showed the drive-by needed no escape
   > hatch to exist — it was already live, and in remote mode the page that
   > could fire it belongs to someone else. A foreign-origin `text/plain` POST
   > rewrote `shell.conf`; a 404 to a bogus route was the control that proved
   > the 200 was a real handler.
   >
   > The error underneath is worth keeping, because it is the same shape as the
   > `RestartPreventExitStatus` and `ProtectHome=read-write` findings: a
   > protection that was **present in the source and inert in practice**. The
   > helper had `X-Frame-Options`, `nosniff` and no `Access-Control-Allow-Origin`,
   > and a comment calling it "same-origin by construction". None of that admits
   > or refuses a request. CORS decides who may **read a reply**, not who may
   > **cause a write**, and a `text/plain` POST is not preflighted at all, so
   > there was no preflight for the missing headers to fail. Fixed with an
   > `Origin`/`Sec-Fetch-Site` gate plus a JSON content-type lock on every
   > state-changing route; `droidian/taos-layer/README.md` carries the
   > measurement and the mutation results.
   >
   > **The escape is now built.** `taos-setup-escape.py` watches evdev for
   > **volume-up + volume-down held five seconds**, writes a one-shot sentinel
   > and restarts the kiosk; `taos-kiosk-launch.sh` consumes the sentinel and
   > opens the first-run helper for that one start, whatever `shell.conf` says.
   > A physical key because it is the one signal a web page cannot produce —
   > and in remote mode the page on screen belongs to someone else. It does not
   > touch `shell.conf`: the user is handed the setup form and chooses there,
   > with the same address check and the same refusals as first run.
   >
   > **Verified off-device, and honest about which half that is.** The chord
   > logic is driven on a synthetic clock (31 checks; six mutations each go
   > red), and the capability probe has a real evdev node advertising both key
   > codes as its positive control. What is *not* verified is that `spacewar`'s
   > volume keys appear as `KEY_VOLUMEUP`/`KEY_VOLUMEDOWN` under the Halium 5.4
   > kernel. If they do not, the unit exits 78 and lands in `failed` rather
   > than sitting active and never firing — the refusal is the design, not a
   > consolation. Confirm on the phone with `evtest` and by watching the
   > screen, not `systemctl status`.
   >
   > **Still unbuilt: the two sub-asks in the sentence above.** Local → remote
   > offering to keep or discard local data, and remote → local not silently
   > orphaning the pairing. Neither is reachable until the pairing flow exists,
   > and neither is what makes the door one-way — the escape was.
2. **Legible failure.** If the remote controller is unreachable, say so and
   offer retry / switch / manual entry. Never a blank screen — we hit exactly
   that failure mode during bring-up and it is indistinguishable from a crash.
   Three states must be distinguishable in the UI, because they need different
   actions from the user and confusing them is how a kiosk becomes a brick:
   *not signed in*; *signed in but the controller is unreachable*; and *the
   pair request expired* — the last is real rather than theoretical, since
   expiry is enforced at approve time (F6) and so a request can look pending on
   our screen right up until it fails.

   > **Built for the manual-entry path, 2026-08-27.** The address is now checked
   > *before* it is written, not after — which is the only moment at which a
   > wrong one is still cheap. `POST /api/check` in `taos-firstrun.py` probes
   > `{origin}/api/health` process-side (the page cannot: different origin, no
   > CORS) and returns one of three verdicts, mapped one-to-one onto the states
   > above:
   >
   > | verdict | what the user sees | what it asks of them |
   > |---|---|---|
   > | `ok` | "Found a taOS controller — 2 agents, 9 backends" | nothing; it saves |
   > | `not_a_controller` | something answered, and it was not one | check the port |
   > | `unreachable` | nothing answered | check the network / power |
   >
   > **The check is on the shape, not the status code, and that is load-bearing.**
   > Measured against the live A2A bus: it is a single-page app with a catch-all,
   > so `GET /api/health` returns **200 with `index.html`**. A port check accepts
   > it; so does a 200-means-yes check. Its stub is now a permanent negative
   > control in `check-firstrun-helper.sh`.
   >
   > A failed check is a **warning with a way past it**, not a wall — *Use this
   > address anyway* commits the address unchecked. A controller that is merely
   > switched off is a legitimate thing to configure, and this requirement says
   > offer a way forward, never a dead end. The launcher's own boot-time
   > behaviour is unchanged and deliberately still does not gate remote targets:
   > a phone in remote mode is a surface whose network comes and goes, and
   > handing the display back to Phosh on a blip would be the worse failure.
   >
   > Still unbuilt here: the *pair request expired* state, which needs the
   > pairing flow, and the account sign-in states. Manual entry does not touch
   > either.
3. **Config, not code.** One file the kiosk launcher reads:
   `~/.config/taosmobile/shell.conf`. Mode + URL live there so the session is a
   pure consumer.

   > **This doc used to say the launcher "already does this". It did not.** That
   > sentence described `Main.qml` from the Ubuntu Touch demo plan — a shell that
   > was never built — while the unit that actually owns the display,
   > `taos-kiosk.service`, hardcoded `--app=http://localhost:6969/`. So the
   > requirement read as already satisfied for as long as it was written down,
   > which is the same failure as a comment describing a setting systemd was
   > silently ignoring. Built for real in `taos-kiosk-launch.sh`; the launcher
   > resolves the URL and `check-kiosk-url.sh` holds it there.
4. **Works offline.** Local mode must complete with no network. That means the
   image ships the venv and wheels rather than fetching them.
5. **Worker registration is orthogonal.** In remote mode the phone should still
   register as a *worker* (its CPU, and its telephony capabilities) — that is
   the `sms`/`dial`/`battery` capability work already specced in
   `docs/superpowers/specs/2026-07-20-taosmobile-demo-design.md`. Attaching a
   surface and offering hardware are separate concerns. **Confirmed against
   `origin/dev`, not assumed** — separate endpoints, separate credential,
   separate auth scheme (see the worker enrolment block above). This was the one
   assumption in this doc that survived review unchanged.

6. **Remote mode needs a local helper process.** taos.my sends no CORS headers
   (measured, above), so the kiosk page cannot call it directly. Something on
   the phone must make those HTTPS calls process-side and expose them to the
   page same-origin. This is required exactly in the mode that has no local
   controller, so it cannot be borrowed from `taos-controller.service` — it is
   its own small always-present component. Discovered by probing rather than by
   reading, which is the only reason it is in the spec instead of in a bring-up
   surprise.

   > **Narrowed 2026-08-27.** The helper is needed to *read* taos.my, not to
   > *connect*. Connecting is a navigation to `{handle}.{username}.taos.my`,
   > which CORS does not govern and which the relay redirects through sign-in on
   > its own. So the helper is required for the enumeration assist, and the
   > manual-URL and relay paths both work without it. It is still a real
   > component; it is no longer on the critical path for remote mode.

## Notes

- **The helper has to actually be running, and once was not.** The commit that
  added `taos-firstrun.py` installed the unit and the script but never
  `systemctl enable`d it, so it shipped dead — invisible until the kiosk started
  depending on it, because nothing else did. The installer now enables it and
  fails if it does not come up, and the kiosk `Wants=` it. A component nobody
  depends on yet is a component nobody can tell is broken.
- This mirrors the hybrid-vs-local decision taken early in this project; the
  conclusion then was that a phone is more useful as its own controller, but
  that was for a demo device with a Pi already present. Making it a first-run
  choice removes the need to decide on the user's behalf.
- Local mode's storage cost is the main reason to keep remote: a controller venv
  plus a 4GB quant is real space on a 128GB phone that also holds photos.
