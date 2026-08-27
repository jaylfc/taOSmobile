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
process-side and exposes them to the kiosk page same-origin. Note this is
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

### The gap: there is no controller enumeration, so the picker has no backing API

**`GET /api/account/mesh/status` is not it.** Backed by `mesh_status()`
(`tinyagentos/taosnet/mesh.py:130`), it reports only **this** host from
`tailscale status --json` — `{joined, online, tailnet, node_ip, hostname,
guests}` — and `guests` is only peers tagged `tag:guest` that joined *this*
host's mesh (`_is_guest_node`, `:107`). Self is `self_node.get("HostName")`: a
tailscale hostname, no UUID, no user-set display name.

**Now definitive on both sides (A2A 3418):** with the upstream surface in hand,
there is no controller-enumeration endpoint on the controller *and* none in
taos.my's surface as the controller's forwarding table knows it. The nearest
neighbour is `/api/cluster/join/*` (`_JOIN_BASE`, taos-website PR #35) — account
-credential-based and cluster-shaped, but it enumerates **join requests**, not
controllers. It is not the answer; it is evidence that taos.my already does
account-scoped cluster bookkeeping, so this is the right neighbourhood.

**Carded upstream as `tsk-ckvyps`** (repo `jaylfc/taos-website`, not ours to
land) for the cheap shape agreed below.

This is load-bearing for the flow, because of an ordering constraint that is
easy to miss: **`/api/devices/pair-requests` lives on a controller, not on
taos.my.** To create a pair request the phone must already have a controller
address. So enumeration is not a convenience on top of pairing — it is the step
*before* pairing, and without it the remote branch cannot start.

**Consequence, and the correction that matters most here:** the
directly-entered URL below is not a fallback today. **It is the only working
remote path**, and the spec is written that way until an enumeration API exists.
Shipping the picker as the primary flow would be exactly the "wrong spec
implemented faithfully" failure this section was written to avoid.

**Manual URL entry** — a directly-entered controller URL, no account involved.
Works on a LAN, for development, and for a fleet running fully offline. This is
the path to build first.

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
2. **Legible failure.** If the remote controller is unreachable, say so and
   offer retry / switch / manual entry. Never a blank screen — we hit exactly
   that failure mode during bring-up and it is indistinguishable from a crash.
   Three states must be distinguishable in the UI, because they need different
   actions from the user and confusing them is how a kiosk becomes a brick:
   *not signed in*; *signed in but the controller is unreachable*; and *the
   pair request expired* — the last is real rather than theoretical, since
   expiry is enforced at approve time (F6) and so a request can look pending on
   our screen right up until it fails.
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
