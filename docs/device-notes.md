# Device notes — Nothing Phone (1), Ubuntu Touch

Findings from the task 1 validation pass. Tasks 4 (SMS) and 6 (dial) are
written against this file, not against assumptions. Raw logs stay untracked
(`docs/introspect-*.log` is gitignored).

**Validated:** 2026-07-21, over SSH (`taosphone`).

## Platform

| Fact | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS (noble), `VARIANT=Touch` |
| Kernel | 5.4.289-qgki (Halium/Android base) |
| Arch | aarch64 |
| glibc | **2.39** |
| Hostname | `ubuntu-phablet`, user `phablet` (uid 32011) |
| Rootfs | `/` is **read-only** ext4; `/home` is a separate writable partition |

**Correction to the spec/plan:** both assumed a Focal (20.04) base with glibc
2.31. This image is noble with glibc 2.39. Consequences:

- The bridge should still be *built* against a 2.31 baseline (forward
  compatible, and keeps older UT images in reach), but 2.39 is the real floor.
- The Libertine assumptions inherited from #797 (Focal container, glibc 2.31,
  AppImage compatibility) need re-checking against noble before the inference
  worker is installed.

## SSH access

Publickey only; password auth disabled. Getting a key accepted required fixing
the home directory: the image ships `/home/phablet` as `root:root drwxrwxrwx`,
and sshd's `StrictModes` silently rejects keys when the home directory is
world-writable. Fixed to `phablet:phablet drwxr-xr-x`.

`chmod` alone would have been wrong — with root ownership retained, `755`
would strip the phablet user's write access to its own home. Ownership had to
change with it. Contents were already `phablet`-owned; `~/.config` remains
`root:root 777` and was deliberately left alone (apps rely on it).

sshd config is stock: `StrictModes` default yes, `AuthorizedKeysFile` default
`.ssh/authorized_keys`.

## Telephony — oFono (system bus, `org.ofono`)

**Two modems: `/ril_0` and `/ril_1`** (dual SIM). Enumerate via
`org.ofono.Manager.GetModems`; never hardcode a path, and do not assume one.

Both modems currently report:

```
Online: true, Powered: true
Interfaces: org.ofono.VoiceCallManager, org.ofono.SimManager,
            org.nemomobile.ofono.CellInfo, org.nemomobile.ofono.SimInfo
Features:   sim
```

### Blocker: no SIM present

`org.ofono.SimManager.GetProperties` returns `Present: false` on **both**
slots — there is no SIM card in the phone.

Consequence: **`org.ofono.MessageManager` is absent from the Interfaces list.**
oFono only exposes it once a SIM is present and registered, so SMS cannot be
sent, received, or even introspected until a SIM is inserted. The same applies
to real dialing.

Still outstanding, and only doable with a SIM in the phone plus a second phone
to text:

1. Confirm `MessageManager` appears in `Interfaces` once a SIM is in.
2. Capture the `IncomingMessage` signal shape — argument order and the info
   dict keys (`Sender`, `SentTime`) — via
   `dbus-monitor --system "interface='org.ofono.MessageManager'"`.
   Task 4's parser is written against this capture.
3. Send one message with `MessageManager.SendMessage ss <number> <text>`.
4. Note which modem path holds the SIM, and how the bridge should behave with
   two SIMs (pick the first with `Present: true`; multi-SIM selection is out of
   scope for the demo).

## Dialing (session bus)

Both candidate paths exist:

- **`com.lomiri.URLDispatcher`** at `/com/lomiri/URLDispatcher` exposes
  `DispatchURL(ss)`. This is the fallback from the spec, but it turns out to be
  a clean DBus method rather than a subprocess — so the "fallback" is actually
  the tidier implementation. Dial with `tel:<number>`.
- **telephony-service** is present as Telepathy clients
  (`com.lomiri.TelephonyServiceHandler`, `...Approver`,
  `org.freedesktop.Telepathy.ChannelDispatcher`) plus
  `com.lomiri.HistoryService`. Driving these directly means speaking Telepathy,
  which is a much larger surface than `DispatchURL`.

**Decision: use `URLDispatcher.DispatchURL` for the demo.** It reuses UT's own
dialer (and therefore UT's call-audio routing, exactly as the spec intends),
costs one DBus call, and avoids reverse-engineering Telepathy. Dial-out cannot
be verified end-to-end until a SIM is in.

Relevant binaries present: `lomiri-dialer-app`,
`lomiri-telephony-service-handler`, `lomiri-url-dispatcher-*`.

## Battery — UPower (system bus)

Works as expected, no surprises:

```
/org/freedesktop/UPower/devices/DisplayDevice
  Percentage (d) = 100
  State (u)      = 1   # charging
```

Task 5's state mapping (1 charging, 4 fully-charged, 5 pending-charge → true)
stands.

## Impact on the plan

- **Task 4 (oFono SMS) is blocked on a SIM.** The modem-enumeration and
  send/receive code can be written, but selection logic must key off
  `SimManager.Present`, and the signal parser cannot be finalised until the
  capture in step 2 above exists.
- **Task 6 (dial) is unblocked for implementation** via `DispatchURL`, though
  untestable without a SIM.
- **Task 5 (battery) is fully unblocked.**
- Everything non-telephony — bridge uplink, shell app, deployment — is
  unaffected and can proceed to completion.
