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

**Account sign-in — no device-code flow, but no browser chain either.**

The only path is `POST /api/account/login`
(`tinyagentos/routes/account_proxy.py:427`), which forwards credentials
upstream to taos.my. There is no code-approval variant. But it is a **plain
JSON API, not a hosted page and not an OAuth redirect** — siblings are
`/api/account/register` (`:432`), `/api/account/logout` (`:437`),
`/api/account/me` (`:422`). So we render our own form against our own on-screen
keyboard and POST it. A password gets typed on the device once; that cost is
real and does not go away.

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
3. **Config, not code.** One file the kiosk launcher reads (it already does
   this: `~/.config/taosmobile/shell.conf`). Mode + URL live there so the
   session is a pure consumer.
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

## Notes

- This mirrors the hybrid-vs-local decision taken early in this project; the
  conclusion then was that a phone is more useful as its own controller, but
  that was for a demo device with a Pi already present. Making it a first-run
  choice removes the need to decide on the user's behalf.
- Local mode's storage cost is the main reason to keep remote: a controller venv
  plus a 4GB quant is real space on a 128GB phone that also holds photos.
