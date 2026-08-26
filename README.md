# taOSmobile

Turning a Nothing Phone (1) into a dedicated [taOS](https://github.com/jaylfc/taOS)
device: the taOS controller running natively on the phone, with the taOS PWA as
the full-screen surface.

## Where things stand

**Proven on hardware, but not installed right now.** The device was reflashed to
Droidian on 2026-08-26, so Ubuntu Touch and everything below it is no longer on
the phone. It is reflashable — the bootloader is unlocked, no partition layout
was changed, and a verified UT rollback image is staged. What Ubuntu Touch
demonstrated:

- taOS controller running natively as a systemd service on `:6969`, surviving
  reboots. Every dependency resolved as an aarch64 wheel — the phone has no
  compiler.
- taOS rendering on the device through the platform webview.
- Resilient remote access (Tailscale as a *system* service, so it survives the
  graphical session dying).

**Not working:** full-screen exclusive boot. Ubuntu Touch's session shell is a
nested Mir server, and QtWebEngine cannot obtain a graphics backend in that
role — so a web-rendering shell cannot replace Lomiri. Eight approaches were
tested on hardware; see `docs/android-kiosk-scope.md` for the full table of
what was tried and how each failed.

**In progress:** porting [Droidian](https://droidian.org) to the device. It is
flashed but not yet booting — see `docs/flash-procedure.md` for the attempt log
and the current state. Droidian
is Debian (glibc + systemd, so the controller is a straight lift) on Halium (so
the Android vendor blobs keep camera, RIL and VoLTE working), with wlroots — so
`cage` + Chromium gives an exclusive kiosk without fighting the display stack.

## Layout

```
bridge/     Rust hardware bridge (SMS/dial/battery over D-Bus) — scaffold
kiosk/      Kiosk surface: launchers, systemd units, Plymouth theme, polyfills
droidian/   Droidian port: kernel packaging (debian/, config fragments, CI)
scripts/    Device introspection and deployment helpers
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
  gets reported as protection.
- **Layer A (invariants)** — every `scripts/`, `docs/`, `droidian/`, `kiosk/`
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
