# taOS Mirror: see and drive the handset from the MacBook over Tailscale

Owner: @taOSmobile-dev (device layer). Jay asked for it on 2026-10-25: "a remote view/access/control
of the taOSmobile device similar to Apple's iPhone mirroring", so the phone can stay at home while he
develops over Tailscale, with custom apps on the MacBook to use, control and manage it.

## Jay's decisions (2026-10-25)

1. **Route: a custom pipeline plus a native Mac app.** He chose this over wayvnc and over
   Sunshine/Moonlight.
2. **The panel stays dark while mirrored.** As with iPhone Mirroring locking the iPhone, the phone's
   own glass is off for the whole session. This keeps his no-burn-in rule over hours of dev work.
3. **The power button still wakes the phone while it is dark** (Jay: "to prevent lock out"). A press
   on the handset ends mirroring, moves the kiosk back to the panel and lights it. This path lives in
   the existing power-key handler and must not depend on `taos-mirrord`: it has to work when the
   daemon is hung, dead or never started.

## Why not the off-the-shelf servers

| Option | Why not, measured or read on 2026-10-25 |
|---|---|
| Sunshine + Moonlight | Not packaged in Alpine (edge, aarch64: "No matching packages"). Its encoders are NVENC, VAAPI, QSV, AMF and software; Qualcomm's Venus is a V4L2 mem2mem device, so this SoC would fall back to software x264 at 1080x2400. It injects input through uinput, which the pocket-touch guard disables while the panel is off. |
| wayvnc | Packaged (`wayvnc 0.10.0-r1`, edge community). Kept as the fallback and as a bring-up probe, not the product: RFB has no touch, and macOS Screen Sharing is not the custom app Jay asked for. |

## Shape

```
 sway (kiosk)                     taos-mirrord (phone, binds tailscale0 only)          MacBook
 HEADLESS-1 1080x2400 @2x  --->   capture (wlr-screencopy)                     ---->   taOS Mirror.app
 physical panel: power off        -> H.264 (Venus h264_v4l2m2m, x264 fallback)   video  VideoToolbox decode
                                  -> framed stream over one WebSocket                 phone-shaped window
 taos-touch (uinput)       <---   control messages: touch, keys, text, mode    <----   click/drag = touch
 mapped to HEADLESS-1                                                         control  keys, toolbar
```

**Display.** `taos-mirror on` creates a headless output sized like the panel (1080x2400, scale 2, so
the page still sees the 540x1200 CSS viewport it was tuned for). It moves the kiosk workspace onto that
output and powers the physical panel off. `taos-mirror off` reverses it. Ending is forced by any of
these: the Mac disconnects for longer than a grace period, someone presses the phone's power key, or
the daemon dies. The phone must never be left with its kiosk on an output nobody can see. The power key
is the one that cannot fail (decision 3): `taos-kiosk-power` handles it directly, so stage 1 proves it
with the daemon killed (`kill -9`), not just stopped.

**Video.** One H.264 stream, encoded on the Venus hardware encoder when it is present
(`/dev/video*` with an H.264 capture format), and on libx264 at reduced fps when it is not. The stream
only carries frames when they change: an idle kiosk costs close to nothing.

**Input.** Touch, not mouse. The page is a touch UI: swipes, long-press, and the edge gestures all
expect touch events. The daemon drives the `taos-touch` uinput touchscreen (taOSmobile PR #5, not yet merged), mapped to the
headless output with `map_to_output`. `taos-kiosk-screen` exempts that one device from the panel-off
touch guard while mirroring. Hardware keys (power, volume) and text go through a second virtual
device, so the existing key handlers see them as real presses.

**Transport and auth.** One WebSocket on the phone's tailnet address only, never 0.0.0.0. Tailscale
already encrypts the path. The daemon admits a peer only when Tailscale's local API (`whois`) says it
is one of Jay's own devices, so no token is shipped or stored. The protocol is versioned from the
first message.

**Mac app.** SwiftUI, a window shaped like the handset (aspect 1080:2400, corner radius matching the
panel). It decodes the stream with VideoToolbox, sends clicks and drags as touches, the trackpad scroll
as a swipe, and the keyboard as text. A toolbar has power, volume up/down, screenshot, start/stop
mirror and device status. It finds the phone by its MagicDNS name. GitHub's macOS runners build it in
CI, since the repo is public.

## Unknowns, all to be measured on the handset before code depends on them

1. Does sway on the device create a headless output alongside the DSI panel, and does screencopy keep
   delivering frames from it while the physical output is powered off?
2. Is the Venus encoder bound on this kernel (`qcom,sc7280-venus`), with its firmware, and does
   `h264_v4l2m2m` encode 1080x2400 at 30 fps or more? What does it cost in CPU and battery over 10
   minutes, compared with libx264?
3. Does a uinput touchscreen with `map_to_output HEADLESS-1` reach chromium on the headless output,
   with every physical touch device still disabled?
4. What is the glass-to-Mac latency over the tailnet when the Mac is off the home network (DERP relay
   vs direct)?

## Stages, each one usable on its own

| Stage | Delivers | Who |
|---|---|---|
| 0 | Device probes for the four unknowns above, with wayvnc as a quick end-to-end check of the headless-output idea. | @taOSmobile-dev, on the handset |
| 1 | `taos-mirror on/off` plus the headless output, panel off, touch exemption, and every forced-off path. Installer and unit in `pmos/`. | @taOSmobile-dev |
| 2 | `taos-mirrord`: capture, encode, WebSocket, whois admission, control messages into taos-touch. A protocol doc that the Mac app is written against. | @taOSmobile-dev, with lane cards for pieces that are testable off-device |
| 3 | Mac app: view-only first (connect, decode, phone-shaped window), then touch, keys and toolbar. Built in CI on macOS. | Lane cards against the stage-2 protocol doc |
| 4 | Management: status (battery, network, taOS version), restart the kiosk, reboot, pull a screenshot, file drop. Audio is a later stage. | Cards |
