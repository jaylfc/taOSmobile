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

**Remote:** discover controllers, let the user pick or type one, pair, write the
URL, point the kiosk at it.

- **Discovery:** taOS already publishes over mDNS/zeroconf
  (`tinyagentos/services/mdns_publisher.py`), so LAN controllers can be listed
  without the user knowing an IP. Offer manual entry (host/IP or Tailscale name)
  as a fallback, since mDNS is unreliable across subnets and some Wi-Fi APs
  block multicast.
- **Pairing:** reuse an existing taOS mechanism rather than inventing one — the
  invite-link + PIN flow (`/i/<id>` → `POST /api/projects/invites/redeem`) or
  the OTP worker pairing in taOS#212. Typing a PIN shown on the controller is
  the right ergonomics on a phone.

## Requirements

1. **Reversible.** Switching modes later must be possible from settings — not a
   first-run one-way door. Local → remote should offer to keep or discard local
   data; remote → local must not silently orphan the remote pairing.
2. **Legible failure.** If the remote controller is unreachable, say so and
   offer retry / switch / manual entry. Never a blank screen — we hit exactly
   that failure mode during bring-up and it is indistinguishable from a crash.
3. **Config, not code.** One file the kiosk launcher reads (it already does
   this: `~/.config/taosmobile/shell.conf`). Mode + URL live there so the
   session is a pure consumer.
4. **Works offline.** Local mode must complete with no network. That means the
   image ships the venv and wheels rather than fetching them.
5. **Worker registration is orthogonal.** In remote mode the phone should still
   register as a *worker* (its CPU, and its telephony capabilities) — that is
   the `sms`/`dial`/`battery` capability work already specced in
   `docs/superpowers/specs/2026-07-20-taosmobile-demo-design.md`. Attaching a
   surface and offering hardware are separate concerns.

## Notes

- This mirrors the hybrid-vs-local decision taken early in this project; the
  conclusion then was that a phone is more useful as its own controller, but
  that was for a demo device with a Pi already present. Making it a first-run
  choice removes the need to decide on the user's behalf.
- Local mode's storage cost is the main reason to keep remote: a controller venv
  plus a 4GB quant is real space on a 128GB phone that also holds photos.
