# taOSusb: pair over Bluetooth, join the cluster, appear on the lock screen

Owner: @taOSmobile-dev (lead, Jay 2026-09-23). Server pieces go to @taOS-dev; taos.my/Headscale pieces
go to @taOS-website-dev. Each stage ends with something that works, so no stage waits on the whole plan.

## What Jay asked for

1. Cluster app → **Add device** → **Search Bluetooth** → `taOSusb-<random>` appears → **Pair**. After
   that the board is a cluster node on Jay's account, paired with the phone.
2. The phone's agent and the board's agent agree on a data path (same wifi, phone hotspot, mesh).
   **Bluetooth is always there as the reconnect fallback.**
3. While the board is powered and linked, its agent shows as active on the lock screen. When the board
   loses power, it disappears.
4. Demo: plug it in, it appears, Jay talks to it.
5. Later: a small screen and a button or two, including one that forces pairing mode again.

## Where we start (measured 2026-09-23)

| Piece | State |
|---|---|
| Pi Zero 2W | `taosusb`, **aarch64**, Debian 13 trixie, 415 MB RAM, `hci0` up, BlueZ active, NetworkManager manages wlan0, dwc2 in host mode. Nothing of ours is installed. |
| taosusb repo | agentd (HTTP relay + PicoClaw, one job at a time) plus `provision.sh`. **`provision.sh` refuses 64-bit** (armv6/7 only). PicoClaw v0.3.1 does ship `picoclaw_Linux_arm64.tar.gz` (sha256 `e78ab0de…de060`). No BLE code, no wifi provisioning. |
| Phone half (#3148) | Heartbeat, relay, lock-screen island, thread. It uses **one shared bearer token for all boards**, copied over by hand, and keeps state in memory. Approved; waiting to run on the handset. |
| Cluster app | "Add worker" works only with IP + PIN (`POST /api/cluster/pairing/manual`). Workers get an HMAC signing key. There is no scanning and no Bluetooth. |
| Phone Bluetooth | The radio toggles through rfkill from quick settings; that has been verified on the device. **BLE central on pmOS/FP5 has not been tested yet.** |
| Mesh | `taosnet/mesh.py` drives tailscale against Headscale (`hs.taos.my`). The cluster-join flow hands out preauth keys. Nothing connects it to devices. |
| Accounts | `AuthManager` is single-user in practice: **with more than one user the PIN keypad disappears** (`auth.py:804-822`). |

## Design

**Where the Bluetooth scanning happens.** The taOS controller on the phone scans and connects through
BlueZ over D-Bus, and the Cluster app just calls the controller. This works whichever browser shows the
app. Web Bluetooth would only work in the phone's own chromium, and only behind a flag.

**A board is a cluster node of kind `device`.** It uses the same registry, the same HMAC signing key
and the same revoke/block buttons as a worker. It advertises capability `agent`, and the scheduler
never picks it for a job, the model mesh included. **A placement test proves that**, because a
capability flag alone reads as fine until someone adds a new job type. A registry migration defaults
existing rows to `kind: worker`. This removes #3148's shared token: each board authenticates with its own
key, so revoking one board kills only that board.

**GATT service `taOS device`** (one custom 128-bit UUID). The board runs it with python3-dbus +
BlueZ, which are already in Pi OS.

| Characteristic | Ops | Carries |
|---|---|---|
| `info` | read | `{v, id, name:"taOSusb-7K3Q", caps, state: unpaired\|paired\|seeking, pubkey}` in clear |
| `pair` | write + notify | X25519 handshake. Both ends show a 6-digit code derived from a **transcript hash** (both pubkeys + board id), so a man in the middle who runs two handshakes shows two different codes. The code shows on the phone now and on the board's screen later. **Only after Jay confirms the code** does the phone send, sealed: `{controller_id, controller_urls[], node_key, wifi[], mesh_preauth?}` |
| `link` | write + notify | Sealed, chunked frames: link negotiation, heartbeat, and **chat text itself** |

The crypto is at the app layer (X25519 + ChaCha20-Poly1305, the same primitives as `hub/relay.py`).
This means we don't depend on BlueZ bonding agents working on both distros.

**The pairing window stands in for a screen.** An unpaired board advertises as pairable for 5 minutes
after power-on. A paired board advertises only `seeking` (no name leak beyond `taOSusb-XXXX`) and
accepts only its own controller. `pair` writes are accepted only while the window is open **and**
the board is unpaired. The future button will reopen the window.

**Link manager on the board (the "agents discuss" part).** The negotiation is deterministic: a fixed
ladder, driven by what each side reports over `link`. The agents can talk about it, but they don't
make the decision.

1. **Same LAN**: the controller URL answers. This is the fastest path.
2. **Phone hotspot**: the phone sends its hotspot SSID/PSK, and NetworkManager on the board joins.
3. **Mesh**: the board joins Headscale with the preauth key it got at pairing. This works anywhere with
   internet.
4. **BLE**: always kept. Heartbeat and chat run over GATT, so the board stays on the lock screen and
   stays talkable with no IP link at all. Only chat text and status go over it; bulk traffic does not.

The board re-checks the ladder every 30 s and on every NetworkManager change. The lock-screen pill
shows the current rung: `Online · wifi / hotspot / mesh / bluetooth`.

**Presence stays as it is.** The island is shown only while heartbeats arrive (8 s TTL), whatever the
link. When the board loses power, the heartbeats stop and the island disappears. This is #3148's rule,
carried over unchanged.

## Stages

**S0: the real board works today's way (no Bluetooth).** *Me.* Unblocked except for the handset.
- ✅ `provision.sh` accepts aarch64 with the arm64 checksum pinned; the arm64 PicoClaw runs on the Pi
  (taosusb `c4f749f`). The dwc2 check no longer mistakes Pi OS's `[cm5]` host-mode line for ours.
- Provision the Pi. Run agentd on wifi against the phone's `:6969` using the #3148 token.
- When the handset is back online: deploy #3148 and switch the phone's login to Jay's account (below).
  Then run the demo. **This is the demo Jay can film first.**

**S1: pair over Bluetooth.** *Me for the board and the phone's BlueZ permissions; the controller routes
by me as a PR to tinyagentos, reviewed by @taOS-dev, the same way as #3148.*
- Board: `taosusb-bled` (the GATT service plus a random name suffix stored in `/var/lib/taosusb/id`).
- Controller: `GET /api/cluster/ble/scan`, `POST /api/cluster/ble/pair` (the handshake, shows the
  code, sends the node key), all admin-gated, and node kind `device`. **The browser never sees
  `node_key` or any preauth.**
- Cluster app: **Add device → Bluetooth** tab, with a list, a Pair button and the 6-digit code.
- Phone device layer (taOSmobile): a D-Bus policy that lets the controller's user use BlueZ, and
  BlueZ LE enabled.
- **Risk to test first:** a BLE central on the FP5. It can be tested earlier with omarchy as the
  central against the Pi.

**S2: link manager and Bluetooth fallback.** *Me.* The ladder above, BLE heartbeat and chat, and
hotspot handoff. #3148's device routes switch from the shared token to per-node keys (*@taOS-dev
reviews*). The old token stays accepted for **one release**, with a deprecation log line, so no
board is stranded mid-update.

**S3: the mesh as a fallback anywhere.** *The pattern is the existing GUEST preauth flow.* taos.my (@taOS-website-dev) mints
`device-preauth`: tag:device, ACL device → its controller only, single use, short expiry. The
controller half is `/api/account/cluster/join/device-preauth` in `account_proxy.py`, with the same
key stripping (`_STRIP_KEYS`) and server-side consumption; @taOS-dev will card it once the S1
`device` kind lands. The key reaches the board only inside the sealed BLE `pair` payload.

**S4: screen and buttons.** *Me, once Jay adds the hardware.* The pairing code shows on the board, one
button reopens the pairing window, and a long press forgets the controller.

## Phone login → Jay's personal account

The credentials are in `~/.config/taos-agent/handset-secrets.json` (`taos_account`) and **never in a
repo**. Because the PIN keypad disappears when there are two users, the phone should stay a
**single-user** box. **Rename in place; do not replace the user.** Data keys on the user's random
`id` (workspace/users/<id>/, projects, grants, sessions), so delete + create would orphan all of it.
With the controller stopped, change `username` on the existing record and keep its `id`. Then use
the normal setters for the password and PIN. Two things legitimately stay on the old name: A2A bus
history (the handle becomes @jaylfc) and the `taos_username` of apps installed earlier. Then link the taos.my account (the email in the
secrets file) through the account proxy, which S3 needs for mesh preauth. A multi-user lock screen (a
user picker) is a separate @taOS-dev item and is not on this path.

## Decisions I have taken (tell me to change any)

- The phone controller scans; the browser does not use Web Bluetooth.
- A board is a cluster node of kind `device`, not a job-running worker (415 MB of RAM).
- App-layer crypto plus a 5-minute pairing window, not BlueZ bonding. The 6-digit code is shown in the
  app now and on the board's screen later.
- S0 ships the demo before any Bluetooth work, so the film never waits on S1.
