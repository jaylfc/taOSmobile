# taOSmobile

Turning a Nothing Phone (1) into a dedicated [taOS](https://github.com/jaylfc/taOS)
device: the taOS controller running natively on the phone, with the taOS PWA as
the full-screen surface.

## Where things stand

**The current port is postmarketOS** (`pmos/`, from 2026-09-15). This is the
state last measured on the handset (2026-10-03). The phone has been offline since
about 2026-10-05, so read it as last known, not live:

- It boots straight into taOS full screen: sway runs Chromium as a kiosk
  (`pmos/kiosk/etc/sway-kiosk.conf`, `pmos/kiosk/bin/`). The power menu,
  double-tap to wake and the volume keys are handled below the page
  (`taos-kiosk-power-hold`, `taos-kiosk-dt2w`, `taos-kiosk-volume`).
- The taOS controller runs natively on the phone on `:6969`; the firewall opens
  it on the tailnet and wifi (`pmos/nftables/50_taos.nft`).
- Local speech: Parakeet-TDT v3 speech-to-text and Piper `en_GB-cori-medium`
  text-to-speech (`pmos/voice/`, attributions in `pmos/voice/NOTICE-STT.md` and
  `pmos/voice/NOTICE-TTS.md`).
- Cameras (`pmos/camera/`), audio (`docs/pmos-audio-bringup.md`), and a BLE
  check for pairing the taOS Orb (`pmos/ble/`).

**History:** Ubuntu Touch ran the controller natively but could not give a web
shell an exclusive full screen (`docs/android-kiosk-scope.md`). The Droidian port
came next (`docs/droidian-port-plan.md`, `docs/flash-procedure.md`).

## Layout

```
bridge/     Rust hardware bridge (SMS/dial/battery over D-Bus) — scaffold
kiosk/      Kiosk surface: launchers, systemd units, Plymouth theme, polyfills
droidian/   Droidian port: kernel packaging (debian/, config fragments, CI)
pmos/       postmarketOS port: kiosk, voice, camera, BLE, firewall, kernel patches
scripts/    Device introspection and deployment helpers
            check-device-presence.sh -- is the phone on USB, and in what state?
            Run it on the Linux USB host; exits 0/2/3/4/5, never guesses.
            a2a-post.py -- post to the A2A bus and read the message BACK.
            Use this instead of curl for anything substantive: the bus secret
            scanner MANGLES rather than rejects, silently and with no signal to
            the sender, so a 200 is not evidence the bus holds what you sent.
            Exits 0 verified / 1 mangled / 2 could-not-verify; "not found" is 2,
            never 0.
            phone-checkpoint.sh -- reflashable backup of the pmOS install
            (userdata + active boot slot), taken over ssh from the build host,
            lz4 on the phone, raw bytes counted and hashed on the host. See
            docs/pmos-checkpoint-images.md; restore needs a fresh go-ahead.
docs/       Specs, scopes, and the record of what was tried
```

## Documentation gate

Every change that adds or removes a script, a systemd unit, an adaptation file
or a doc has to touch the doc that covers it. This is enforced mechanically
rather than by intention:

```
python3 scripts/check_doc_gate.py invariants          # Layer A
python3 scripts/check_doc_gate.py diff-gate --staged  # Layer B, pre-commit
bash scripts/install-git-hooks.sh                     # wire it to pre-commit
```

- **Layer A0 (liveness)** — refuses to let the gate pass by measuring nothing.
  Config that names a tree, a doc or a rule target this repo does not have is
  an error, not coverage: the token regex would match nothing and Layer A would
  be green forever. A gate that cannot fail is worse than no gate, because it
  gets reported as protection. It also asserts the reverse direction: every
  `.md` in the repo must be named in `referenced_paths_scan` or declared in
  `unscanned_paths`, because Layer A's coverage is a hand-maintained list and
  without this it shrank silently every time someone added a doc and forgot.
- **Layer A (invariants)** — every `scripts/`, `docs/`, `droidian/`, `kiosk/`, `pmos/`
  or `bridge/` path named in the doc set must exist on disk. This is what
  catches a procedure doc still pointing at a renamed script.
- **Layer B (diff-gate)** — path→doc rules. A rule fires only on a *structural*
  change (a file added or deleted, never a plain edit), because a noisy gate
  gets switched off. Satisfy it by editing one of the docs the rule names, or
  by explaining yourself in a `Docs-Reviewed: <why>` commit trailer.

Rules live in `docs/doc-gate.toml` and are data — cover a new area by adding a
`[[rules]]` entry, not by editing the script. CI
(`.github/workflows/doc-gate.yml`) is authoritative on push and PR, so
`git commit --no-verify` skips the hook but not the gate.

## Upstream issues filed

Found while bringing taOS up on the device:

- [taOS#2080](https://github.com/jaylfc/taOS/issues/2080) — installed package
  cannot serve the SPA; `static/` is not shipped as package data.
- [taOS#2081](https://github.com/jaylfc/taOS/issues/2081) — login is impossible
  once a session cookie exists: a server-rendered form cannot send
  `X-CSRF-Token`.
- [taOS#2082](https://github.com/jaylfc/taOS/issues/2082) — SPA renders blank in
  system webviews; the bundle calls `Object.hasOwn`/`structuredClone`
  (Chromium 93/98).

## Hardware notes

Findings from the device are in `docs/device-notes.md` — including that
`/home/phablet` ships world-writable and root-owned, which makes sshd's
`StrictModes` silently reject every key.

Audio bring-up under postmarketOS is in `docs/pmos-audio-bringup.md`. Most of it
is already solved upstream in an open draft PR against the same kernel fork this
device runs; the speakers need two properties per amp (`sound-name-prefix` and
`sound-channel`), testable by editing the dtb on `/boot` and rebooting rather
than by flashing anything. The microphone is genuinely open. There is no
headphone jack — Bluetooth audio already works.

Reflashable checkpoint images of the phone (what `fastboot flash userdata`
actually covers, how to take one, how to restore) are in
`docs/pmos-checkpoint-images.md`.

The plan for pairing the taOSusb board (a Pi Zero 2W agent) over Bluetooth into
the Cluster app, with Bluetooth kept as its fallback link, is
`docs/taosusb-pairing-plan.md`.

## Licence

taOSmobile is licensed under the GNU Affero General Public License v3.0 or later
(`AGPL-3.0-or-later`); see `LICENSE`. The Linux kernel patches and config diffs under
`pmos/kernel/` are derivative of the kernel and stay under its `GPL-2.0-only` licence.
