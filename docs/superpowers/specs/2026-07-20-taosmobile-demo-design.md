# taOSmobile demo — design

**Status:** Approved 2026-07-20.
**Device:** Nothing Phone (1) (SM7325, 12GB RAM) running Ubuntu Touch (Halium-based).
**Purpose:** Demonstration only. Not a product surface, not OpenStore-distributable.
**Parent work:** tinyagentos #797 (phone as node + surface), #590, #737, #36/#207.

## Goal

Turn the Ubuntu Touch phone into a first-class taOS surface and cluster node for demos, without replacing Ubuntu Touch. The phone keeps the UT lock screen, boot, and incoming-call UI. A sideloaded app "locks" the foreground into the taOS web UI; exiting it returns to Ubuntu Touch. The phone's telephony hardware (SMS, dialing) and battery state become shareable cluster capabilities, advertised like any other worker capability.

## Demo script (acceptance criteria)

1. Phone boots and unlocks as normal Ubuntu Touch.
2. Tapping the **taOS** app icon fills the screen with the taOS mobile shell, served by the Pi controller over LAN.
3. The fleet view shows the phone as a worker advertising `sms`, `dial`, `battery` (alongside the #797 Libertine inference worker).
4. An inbound SMS appears as a channel in taOS talk on the phone **and** on a desktop surface.
5. Replying from the desktop sends the SMS through the phone's SIM.
6. The taOS Phone app dials a real number; UT's in-call screen handles the call; ending it returns to taOS.
7. **Shut down** in the taOS mobile shell exits the app back to the UT home screen.

## Decisions

1. **Topology: hybrid.** Pi is the controller and serves the web UI; the phone runs the hardware bridge (host) and the #797 inference worker (Libertine) and displays the Pi's UI over LAN.
2. **Calls: delegate to UT telephony.** taOS provides the dialer UI; calls are placed via UT's telephony stack so UT owns call-audio routing and the in-call screen. Answering calls stays native UT.
3. **SMS: sync to the cluster.** SMS threads are taOS talk channels on the controller, visible and usable from every surface; agents can read/send.
4. **SMS transport: oFono direct** (`org.ofono.MessageManager`, system bus) rather than UT's undocumented telephony-service/history-service APIs. Trade-off accepted: taOS-sent SMS won't appear in UT's native Messaging app.
5. **Bridge: Rust zbus sidecar now** (production-shaped), not a Python extension of the worker.
6. **Bridge registers as a worker.** No bespoke "device gateway": the bridge uses the existing worker registration with capabilities `["sms", "dial", "battery"]`. Phone hardware becomes a routable cluster resource in the protocol taOS already has.
7. **Coordination:** taOSmobile work (shell + bridge) is built here; tinyagentos work is handed to the taOS dev agent over the taOSmd A2A bus, with the interface contract below as the shared source of truth.

## Components

### 1. Shell app — `shell/` (this repo)

Ubuntu Touch Click package built with `clickable`.

- QML `Main.qml` hosting a full-screen `WebEngineView` (same engine as Morph, which already renders the mobile shell acceptably per #797).
- Loads the controller URL from a config file (`~/.config/taosmobile/shell.conf`), e.g. `http://<controller-host>:<port>/?shell=mobile`. No hardcoded hosts in the repo.
- `onNavigationRequested`: a navigation to `taos-shell://exit` calls `Qt.quit()`. All other `taos-shell://` URLs are reserved and ignored.
- AppArmor: `unconfined` template; sideload only (`clickable install` / adb). Not OpenStore-eligible and not intended to be.
- UT lock screen, notifications, and incoming-call UI are untouched because this is an ordinary foreground app.

### 2. Hardware bridge — `bridge/` (this repo)

Rust daemon (`taosd-bridge`), running on the UT host (not Libertine) as a `phablet` systemd user service.

- **Stack:** `tokio`, `zbus`, `tokio-tungstenite`, `serde`, TOML config at `~/.config/taosd-bridge/config.toml` (`controller_url`, `device_token`).
- **System bus:** oFono — enumerate modems via `org.ofono.Manager.GetModems` (do not hardcode `/ril_0`); subscribe to `MessageManager.IncomingMessage`; send via `MessageManager.SendMessage`. UPower — battery percent/charging from the display device.
- **Session bus:** dial via UT telephony-service. Exact interface to be confirmed by on-device introspection (see Validation). Fallback if unusable: `url-dispatcher` with a `tel:` URI (opens UT dialer pre-filled; one extra confirm tap — acceptable for the demo).
- **Backend abstraction:** telephony access sits behind a provider trait (`OfonoTelephony` now; `ModemManagerTelephony` slot for future mainline devices).
- **Uplink:** one outbound WebSocket to the controller, `Authorization: Bearer <device_token>` on the handshake. Registers as a worker, then speaks the message protocol below. Reconnect with exponential backoff (1s → 60s cap); re-register on reconnect.
- **Build:** cross-compiled `aarch64-unknown-linux-gnu` compatible with the UT base's glibc (Focal = 2.31). Deployed to `~/bin`, unit file `taosd-bridge.service` (`WantedBy=default.target`).

### 3. Server side — tinyagentos repo (taOS-dev work package)

1. Accept capability strings `sms`, `dial`, `battery` in worker registration; surface them in the fleet view.
2. **SMS channel type** in taOS talk: one channel per peer E.164 number, auto-created on first inbound message, titled with the number (contact names out of scope). Outbound messages on an `sms` channel route to a connected worker advertising `sms`. If none is connected: queue, and mark the channel "phone unreachable".
3. **Dial intent endpoint** used by the Phone web app; routes to a worker advertising `dial`.
4. **Phone web app** (responsive SPA like every taOS app): dial pad + recent calls. Recent calls = calls placed through taOS (server-side log of dial intents); reading UT's native call history is out of scope.
5. Mobile shell power menu gains **Shut down → back to Ubuntu Touch**, which navigates to `taos-shell://exit` (harmless no-op outside the shell app).
6. Auth: static per-device bearer token, config-side on the controller. Demo-grade stopgap, explicitly under the #737 umbrella.

### 4. Existing #797 worker (unchanged)

The Python inference worker keeps running in Libertine and registers separately. Demo shows the phone as a node with both compute and telephony capabilities; merging the two registrations into one node identity is a later refinement, not v1.

## Interface contract (bridge ↔ controller)

This section is the handoff brief for taOS-dev. Transport and registration reuse the existing worker protocol; only the payloads below are new.

**Registration** — standard `WorkerInfo` with:

```json
{
  "platform": "ubuntu-touch",
  "tier_id": "arm-snapdragon-12gb",
  "capabilities": ["sms", "dial", "battery"],
  "hardware": { "model": "Nothing Phone (1)", "soc": "SM7325", "ram_gb": 12, "sim": true }
}
```

**Bridge → controller messages:**

| type | payload |
|---|---|
| `sms.incoming` | `{ "from": "<E.164>", "body": "<text>", "received_at": "<ISO8601>" }` |
| `sms.send_result` | `{ "client_ref": "<uuid>", "ok": true \| false, "error": "<string?>" }` |
| `call.dial_result` | `{ "client_ref": "<uuid>", "ok": true \| false, "error": "<string?>" }` |
| `battery.status` | `{ "percent": 0-100, "charging": true \| false }` — on change and every 60s |

**Controller → bridge messages:**

| type | payload |
|---|---|
| `sms.send` | `{ "client_ref": "<uuid>", "to": "<E.164>", "body": "<text>" }` |
| `call.dial` | `{ "client_ref": "<uuid>", "number": "<E.164>" }` |

Every controller→bridge request carries a `client_ref`; the bridge always answers with the matching `*_result`, including on DBus failure. Timestamps are UTC ISO8601. Unknown message types are logged and ignored on both sides (forward compatibility).

## Data flows

- **Inbound SMS:** modem → oFono `IncomingMessage` → bridge → WS `sms.incoming` → controller creates/updates the SMS channel → renders on all surfaces.
- **Outbound SMS:** composer (any surface) → controller → WS `sms.send` → bridge → `MessageManager.SendMessage` → `sms.send_result` → message state on the channel.
- **Dial:** Phone app → controller dial intent → WS `call.dial` → bridge → telephony-service → UT in-call UI foregrounds → call ends → taOS shell regains foreground.
- **Battery:** UPower → bridge → `battery.status` → fleet view + worker throttling input.

## Error handling

- Bridge WS drop: controller marks SMS channels "phone unreachable"; outbound messages queue server-side and flush on reconnect. Bridge reconnects with backoff and re-registers.
- DBus call failure (no signal, modem offline, send error): bridge returns `ok: false` with the error string; UI shows the failure on the message.
- Bad/missing bearer token: WS handshake rejected with 401; bridge logs and retries with backoff (token fix requires operator action).
- Shell app loses the controller (LAN drop): WebEngineView shows taOS's own offline state; app stays up.

## Validation before build

The single biggest unknown is the exact DBus shape on this specific UT image. Before writing bridge code against it, run an on-device introspection pass over SSH and record results in `docs/device-notes.md`:

1. `busctl list` (system + `--user`) — confirm oFono, UPower, telephony-service names.
2. Introspect the oFono modem object; confirm `MessageManager` exists and `IncomingMessage` fires on a real inbound SMS (`dbus-monitor`).
3. Confirm `SendMessage` sends a real SMS.
4. Introspect telephony-service; attempt a dial. If unusable → adopt the `url-dispatcher tel:` fallback and record it.
5. Confirm UPower display-device properties update on charge state change.

## Development workflow

All on-device work happens over SSH (Wi-Fi) — no USB/adb required. One-time setup on the phone: add the dev machine's public key to `~/.ssh/authorized_keys` and enable SSH (`android-gadget-service enable ssh` or UT Tweak Tool). Then: bridge binary + unit file deploy via `scp`; the Click app deploys via `clickable install --ssh`; validation and Libertine setup run in SSH sessions. During dev sessions keep the phone plugged in with suspend disabled (or hold `powerd-cli active`) — UT suspend drops Wi-Fi and kills SSH. The phone is addressed only by a `taosphone` alias in `~/.ssh/config`; no device IPs in the repo.

## Testing

- **Bridge:** unit tests for protocol serialization and provider-trait logic with mocked DBus; on-device smoke script (send test SMS, dial, battery read).
- **End-to-end demo checklist** (manual, on device): inbound SMS while locked → appears on desktop; reply from desktop → received on a second phone; dial out → call connects with audio; Shut down → UT home; relaunch → reconnects and channels intact.
- **Server/web:** tested in tinyagentos per its own conventions; the contract tables above are the fixture source for both sides.

## Out of scope (demo)

Answering calls inside taOS (native UT UI handles inbound), MMS, contacts/name resolution, native call-history import, cellular-data sharing (file as a follow-up tinyagentos issue — the capability framing already covers it), push/wake when the shell app is closed, Click confinement, OpenStore distribution, merging bridge+worker node identity, dynamic auth (#737 proper).

## Risks

- **telephony-service DBus is undocumented** — mitigated by the validation pass and the `url-dispatcher tel:` fallback.
- **oFono object paths vary by device** — mitigated by `GetModems` enumeration.
- **Screen blanking / suspend during demo** — UT may kill the Libertine worker or blank mid-demo; run plugged in with display timeout maxed. (#797 open question 2 tracks the real fix.)
- **glibc mismatch on the bridge binary** — build against ≤ 2.31; verify with `ldd` on device before demo day.

## Repo layout

```
taOSmobile/
├── shell/    # Click app (clickable project, QML)
├── bridge/   # taosd-bridge (Rust, zbus)
└── docs/
    ├── superpowers/specs/   # this spec
    └── device-notes.md      # introspection findings (created during validation)
```

Server and web-app work is tracked as tinyagentos issues owned by taOS-dev, coordinated over the taOSmd A2A bus (blocked as of 2026-07-20 on the agent ID minting fix).
