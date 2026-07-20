# taOSmobile demo — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-07-20-taosmobile-demo-design.md` — read it first; the interface contract there is normative.

**Goal:** Ship the taOSmobile side of the demo: the `taosd-bridge` Rust daemon (oFono SMS, UT dial, UPower battery, worker-registration uplink) and the Click shell app that locks the phone into the taOS surface. The tinyagentos side (SMS channels, dial endpoint, Phone app, capability display) is a handoff package for taOS-dev over the taOSmd A2A bus, not tasks in this plan.

**Architecture:** Two independent deliverables. `bridge/` is a tokio daemon: DBus providers behind traits → core dispatch → one WebSocket uplink speaking the contract. `shell/` is a clickable QML app: full-screen `WebEngineView` + `taos-shell://exit` intercept. All device work over SSH via the `taosphone` alias — no adb, no IPs in the repo.

**Tech stack:** Rust (tokio, zbus 5, tokio-tungstenite, serde, toml, tracing), cargo-zigbuild for aarch64/glibc-2.31; QML + clickable for the shell; bash for device scripts.

**Ordering constraint:** Task 1 (device validation) gates Tasks 4 and 6 — do not write oFono/telephony code against guessed interfaces. Tasks 2–3 and 9 can proceed in parallel with Task 1.

---

## File structure

### New
- `scripts/introspect.sh` — SSH introspection pass, output → `docs/device-notes.md`
- `scripts/deploy-bridge.sh` — build + scp + restart service on `taosphone`
- `docs/device-notes.md` — recorded findings (Task 1, updated in Task 10)
- `bridge/Cargo.toml`
- `bridge/src/main.rs`
- `bridge/src/config.rs`
- `bridge/src/protocol.rs`
- `bridge/src/telephony/mod.rs` — `Telephony` trait
- `bridge/src/telephony/ofono.rs`
- `bridge/src/telephony/dial.rs` — telephony-service or url-dispatcher (per Task 1 findings)
- `bridge/src/battery.rs`
- `bridge/src/uplink.rs`
- `bridge/systemd/taosd-bridge.service`
- `bridge/config.example.toml`
- `shell/` — clickable project (`clickable create` output: `manifest.json`, `taosmobile.apparmor`, `qml/Main.qml`, `CMakeLists.txt` or plain)

---

## Task 1: Device validation pass (gates Tasks 4 & 6)

**Files:** Create `scripts/introspect.sh`, `docs/device-notes.md`.

Precondition (manual, one-time, on the phone's Terminal app): public key in `~/.ssh/authorized_keys`, `android-gadget-service enable ssh`, `taosphone` alias in `~/.ssh/config`. Phone plugged in, suspend disabled.

- [ ] **Step 1:** Write `scripts/introspect.sh` — runs over `ssh taosphone`, tees to a timestamped log:
  - `busctl list --no-pager | grep -iE 'ofono|upower|telephony|url-dispatcher'` and `busctl --user list --no-pager | grep -iE 'telephony|history|dispatcher'`
  - `busctl call org.ofono / org.ofono.Manager GetModems` — record real modem object path(s).
  - `busctl introspect org.ofono <modem-path>` — confirm `org.ofono.MessageManager` is present.
  - `busctl --user introspect <telephony-service name> <object>` for every telephony-service name found.
  - `busctl introspect org.freedesktop.UPower /org/freedesktop/UPower/devices/DisplayDevice`
- [ ] **Step 2:** Live-signal check: run `dbus-monitor --system "interface='org.ofono.MessageManager'"` while sending the phone a real SMS from another phone. Record the exact signal (`IncomingMessage` args: text + info dict with `Sender`, `SentTime`).
- [ ] **Step 3:** Send a real SMS out: `busctl call org.ofono <modem-path> org.ofono.MessageManager SendMessage ss <test-number> "taOS bridge test"`. Record result.
- [ ] **Step 4:** Dial probe: attempt a call via the introspected telephony-service interface; if unusable within a timebox (~1h), record the fallback decision and verify `lomiri-url-dispatcher tel:<number>` (or `url-dispatcher tel:`) opens the dialer pre-filled.
- [ ] **Step 5:** UPower probe: read `Percentage`/`State` from DisplayDevice, unplug/replug, confirm `PropertiesChanged` fires.
- [ ] **Step 6:** Write all findings into `docs/device-notes.md` (interface names, object paths, signal shapes, dial decision: `telephony-service` | `url-dispatcher`). No IPs/numbers in the committed file. Commit.

---

## Task 2: Bridge scaffold + config

**Files:** `bridge/Cargo.toml`, `bridge/src/main.rs`, `bridge/src/config.rs`, `bridge/config.example.toml`.

- [ ] **Step 1:** `cargo init bridge --name taosd-bridge`. Deps: `tokio` (full), `zbus = "5"`, `tokio-tungstenite`, `serde` + `serde_json`, `toml`, `tracing` + `tracing-subscriber`, `anyhow`, `uuid` (v4), `futures-util`. Dev-deps: none yet.
- [ ] **Step 2 (failing test):** in `config.rs`, tests: parses `controller_url` + `device_token` from TOML; error on missing field; `Config::load()` reads `$XDG_CONFIG_HOME/taosd-bridge/config.toml` with `~/.config` fallback.
- [ ] **Step 3:** Implement `Config` (`#[derive(Deserialize)] { controller_url: String, device_token: String }`). Tests green.
- [ ] **Step 4:** `config.example.toml` with placeholder values only. `main.rs`: load config, init tracing, print "not yet wired". Commit.

---

## Task 3: Protocol module (the contract, verbatim)

**Files:** `bridge/src/protocol.rs`.

- [ ] **Step 1 (failing tests):** serde round-trip tests pinning the exact wire JSON from the spec's contract tables — one test per message type, asserting against literal JSON strings:
  - Outbound: `sms.incoming`, `sms.send_result`, `call.dial_result`, `battery.status`, plus the registration payload (`platform`, `tier_id`, `capabilities`, `hardware`).
  - Inbound: `sms.send`, `call.dial`.
  - Unknown inbound type deserializes to `Inbound::Unknown` (logged-and-ignored per spec), not an error.
- [ ] **Step 2:** Implement as two tagged enums:

```rust
#[derive(Serialize, Debug)]
#[serde(tag = "type", content = "payload", rename_all = "snake_case")]
pub enum Outbound {
    #[serde(rename = "sms.incoming")]
    SmsIncoming { from: String, body: String, received_at: String },
    #[serde(rename = "sms.send_result")]
    SmsSendResult { client_ref: String, ok: bool, #[serde(skip_serializing_if = "Option::is_none")] error: Option<String> },
    #[serde(rename = "call.dial_result")]
    CallDialResult { client_ref: String, ok: bool, #[serde(skip_serializing_if = "Option::is_none")] error: Option<String> },
    #[serde(rename = "battery.status")]
    BatteryStatus { percent: u8, charging: bool },
}

#[derive(Deserialize, Debug)]
#[serde(tag = "type", content = "payload")]
pub enum Inbound {
    #[serde(rename = "sms.send")]
    SmsSend { client_ref: String, to: String, body: String },
    #[serde(rename = "call.dial")]
    CallDial { client_ref: String, number: String },
    #[serde(other)]
    Unknown,
}
```

  (If the existing tinyagentos worker protocol wraps messages differently — flat `type` field, no `content` — match *it*; check with taOS-dev via A2A before pinning. The tests are the contract either way.)
- [ ] **Step 3:** Tests green. Commit.

---

## Task 4: Telephony trait + oFono backend (needs Task 1 findings)

**Files:** `bridge/src/telephony/mod.rs`, `bridge/src/telephony/ofono.rs`.

- [ ] **Step 1:** Define the trait:

```rust
#[async_trait::async_trait]
pub trait Telephony: Send + Sync {
    async fn send_sms(&self, to: &str, body: &str) -> anyhow::Result<()>;
    fn incoming(&self) -> tokio::sync::mpsc::Receiver<IncomingSms>; // {from, body, received_at}
}
```

- [ ] **Step 2 (failing tests):** a `MockTelephony` in `mod.rs` tests proving the dispatch layer (Task 7) can consume the trait; oFono-specific parsing tests for the `IncomingMessage` signal shape recorded in `docs/device-notes.md` (text arg + info dict → `IncomingSms`), using captured signal data as fixtures.
- [ ] **Step 3:** Implement `OfonoTelephony` with zbus `#[proxy]` interfaces for `org.ofono.Manager` (GetModems — enumerate, take first modem with `MessageManager`, never hardcode `/ril_0`) and `org.ofono.MessageManager` (SendMessage + IncomingMessage signal stream → mpsc). Timestamps: prefer `SentTime` from the info dict, else received wall-clock, UTC ISO8601.
- [ ] **Step 4:** Unit tests green (DBus-touching paths covered on-device in Task 10). Commit.

---

## Task 5: Battery provider

**Files:** `bridge/src/battery.rs`.

- [ ] **Step 1 (failing test):** state→`charging` mapping (UPower `State`: 1 Charging, 4 FullyCharged, 5 PendingCharge → `true`; else `false`); change-detection emits only on percent/charging change or 60s heartbeat.
- [ ] **Step 2:** Implement: zbus proxy on `org.freedesktop.UPower` DisplayDevice, `PropertiesChanged` stream + 60s `tokio::time::interval`, emits `Outbound::BatteryStatus` into a channel. Commit.

---

## Task 6: Dial provider (needs Task 1 findings)

**Files:** `bridge/src/telephony/dial.rs`.

- [ ] **Step 1:** Implement whichever path Task 1 recorded:
  - **telephony-service:** zbus `#[proxy]` against the introspected interface, session bus.
  - **fallback:** spawn `lomiri-url-dispatcher "tel:<number>"` (number validated as E.164-ish first: `^\+?[0-9]{3,15}$`).
- [ ] **Step 2 (test):** number validation rejects shell-hostile input; dial errors map to `call.dial_result { ok: false, error }`. Commit.

---

## Task 7: Uplink + dispatch

**Files:** `bridge/src/uplink.rs`, wire-up in `main.rs`.

- [ ] **Step 1 (failing test):** integration test using a local `tokio-tungstenite` mock server:
  - On connect: bridge sends `Authorization: Bearer <token>` header, then the registration payload.
  - Server sends `sms.send` → bridge calls `MockTelephony::send_sms` → server receives matching `sms.send_result`.
  - Server sends garbage/unknown type → no crash, no reply.
  - Server drops connection → bridge reconnects (assert re-registration; use tiny backoff in tests).
- [ ] **Step 2:** Implement: connect → register → `select!` loop over {inbound WS messages → dispatch to telephony/dial, telephony `incoming` channel → `sms.incoming`, battery channel → `battery.status`}. Exponential backoff 1s→60s, jitter, infinite retry. Every inbound request gets exactly one `*_result`, including on error.
- [ ] **Step 3:** `main.rs`: config → providers (real oFono/UPower/dial) → uplink; graceful shutdown on SIGTERM. Tests green. Commit.

---

## Task 8: Cross-compile, systemd, deploy script

**Files:** `bridge/systemd/taosd-bridge.service`, `scripts/deploy-bridge.sh`.

- [ ] **Step 1:** Build: `cargo zigbuild --release --target aarch64-unknown-linux-gnu.2.31` (install `cargo-zigbuild` + zig if absent). Verify no glibc symbol > 2.31: `objdump -T | grep GLIBC_2\.3[2-9]` empty.
- [ ] **Step 2:** Unit file (user service): `ExecStart=%h/bin/taosd-bridge`, `Restart=on-failure`, `RestartSec=5`, `WantedBy=default.target`.
- [ ] **Step 3:** `scripts/deploy-bridge.sh`: build → `scp` binary to `taosphone:~/bin/` + unit to `~/.config/systemd/user/` → `ssh taosphone systemctl --user daemon-reload && systemctl --user restart taosd-bridge`. First-run note: config file must exist on device (never scp'd from repo — token lives only on device and controller).
- [ ] **Step 4:** Deploy; `journalctl --user -u taosd-bridge -f` shows connect-retry loop (controller side not live yet — expected). Commit.

---

## Task 9: Shell app (parallel-safe with 2–8)

**Files:** `shell/` clickable project.

- [ ] **Step 1:** `clickable create` (QML-only template), app id `taosmobile.jaylfc`, unconfined AppArmor policy, fullscreen flag in the desktop file (`X-Ubuntu-Supported-Orientations=portrait`, fullscreen via `MainView` / window state).
- [ ] **Step 2:** `Main.qml`: read controller URL from `~/.config/taosmobile/shell.conf` (plain `key=value`; `Qt.labs.settings` or `XMLHttpRequest` file read); if missing, show a one-field URL entry screen that writes it (keeps IPs out of the repo and makes first-run self-serve).
- [ ] **Step 3:** `WebEngineView { url: controllerUrl; anchors.fill: parent }` with:

```qml
onNavigationRequested: function(request) {
    if (request.url.toString().indexOf("taos-shell://") === 0) {
        request.action = WebEngineNavigationRequest.IgnoreRequest;
        if (request.url.toString() === "taos-shell://exit") Qt.quit();
    }
}
```

- [ ] **Step 4:** Deploy over Wi-Fi: `clickable install --ssh taosphone` (clickable uses the ssh alias). Manual check: app fills screen, loads any reachable URL, typing `taos-shell://exit` navigation (temporary test link page) quits to UT home. Commit.

---

## Task 10: On-device smoke + demo checklist

**Files:** update `docs/device-notes.md`.

Requires the tinyagentos side deployed on the Pi (taOS-dev handoff complete).

- [ ] Bridge registers; fleet view shows `sms`/`dial`/`battery`.
- [ ] Inbound SMS while phone locked → channel appears on desktop surface.
- [ ] Reply from desktop → real SMS received on a second phone.
- [ ] Phone app dial → UT in-call screen, audio both ways → hang up → back to taOS shell.
- [ ] Shut down in taOS UI → UT home; relaunch app → reconnects, channels intact.
- [ ] Record quirks (suspend behavior, reconnect timing) in `docs/device-notes.md`. Commit.

---

## Task 11: taOS-dev handoff package (blocked on A2A ID minting)

- [ ] **Step 1:** Draft the work package: spec's "Server side" section + interface contract tables + the protocol-envelope question from Task 3 Step 2. Save as `docs/handoff-taos-dev.md`, commit.
- [ ] **Step 2:** When A2A join works (`taosmd-a2a` skill): open/join the project channel, post the package, agree the envelope framing, and file the follow-up tinyagentos issue for cellular-data sharing as a capability.
- [ ] **Step 3:** Track taOS-dev progress on: capability strings in fleet view, SMS channel type, dial endpoint, Phone app, Shut down menu item.

---

## Definition of done

The demo script in the spec (7 steps) passes end-to-end on the Nothing Phone (1) with the Pi as controller, driven entirely over SSH/Wi-Fi, with no device IPs, phone numbers, or tokens committed to either repo.
