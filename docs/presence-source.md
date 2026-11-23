# Presence source (phone side)

The controller sends presence beats for the phone: every 120 s with the screen
on, every 900 s with it off, and an extra beat on every screen on/off and every
power plug/unplug. The phone tells it those events through two small files.
sway, `taos-kiosk-screen`, `taos-kiosk-charger` and the controller all run as
the user `taos`.

## Contract

- Directory: `$XDG_RUNTIME_DIR/taos-presence` (on the phone
  `/run/user/<taos uid>/taos-presence`), created mode 0700 by whichever writer
  runs first.
- `screen`: exactly `on\n` or `off\n`. Written by `taos-kiosk-screen` at the
  end of a successful `on`, and after the panel is powered off in `off`. An
  `off` that stands down because a wake arrived does not write `off`.
  `taos-kiosk-screen sync` publishes the real panel state (from
  `swaymsg -t get_outputs`, `power`/`dpms`) and changes nothing else; sway runs
  it once at session start (`exec` in `etc/sway-kiosk.conf`) so the state exists
  soon after a reboot. An unreadable answer writes nothing.
- `power`: exactly `1\n` (on external power) or `0\n`. Written by
  `taos-kiosk-charger` once at startup as the baseline (no animation) and on
  both edges (plug 0 to 1, unplug 1 to 0). On external power means any
  non-Battery supply in `/sys/class/power_supply` with `online` = 1, the rule
  the charger already uses.
- Every write is atomic: a temp file in the same directory, then `mv -f` over
  the target, so the controller can watch the directory with inotify for
  moved-to events. A write happens only when the value differs from what the
  file holds, so every event the controller sees is a change.
- A missing or unreadable file means UNKNOWN to the controller, never `off` or
  `0`.
- Battery percent and charging status are not written. The controller reads
  sysfs (`qcom-battmgr-bat` capacity and status) at beat time.
- Writes never fail the helper: any error writing presence is ignored and the
  helper behaves as before.

## State, not history

The directory is tmpfs under the taos user's runtime dir. It holds current
state only; it is gone at reboot and nothing may treat it as a log or a record.
Device events (joined, removed, wiped, a notification acted on) are history and
go through taOSmd elsewhere, not here.

## Implementation

`pmos/kiosk/bin/taos-presence-lib.sh` defines `presence_write NAME VALUE`. Both
helpers source it from next to themselves; sourcing runs nothing.
`install-kiosk-continuity.sh` installs it to `/usr/local/bin` beside them.
Tests: `pmos/kiosk/test_taos_presence_source.py`.
