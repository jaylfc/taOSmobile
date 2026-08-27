#!/usr/bin/env python3
"""The way back out of a configured kiosk, on a phone with no keyboard.

WHY THIS EXISTS
---------------
Once ~/.config/taosmobile/shell.conf names a mode, taos-kiosk-launch.sh resolves
to it on every boot. If the controller it names later moves, is renamed or goes
away, the device boots into a target it cannot reach and offers no way out. The
launcher's fallbacks do not help: they fire when the config is MISSING or
MALFORMED, and a stale-but-well-formed URL is neither. Nor does the reachability
check added in c3878c0 -- that stops a wrong address being WRITTEN; it does
nothing for an address that was right when typed. Same dead end when nothing was
ever wrong and the user simply wants to switch modes.

docs/first-run-controller-choice.md requirement 1 forbids exactly this: the
first-run choice must not be a one-way door. This is the door handle.

WHY A HARDWARE KEY AND NOT A LINK
---------------------------------
The escape has to be reachable from a kiosk with no keyboard, no window chrome
and no address bar -- and it must not become a way for a page to reconfigure the
device on its own. In remote mode the kiosk is pointed at a page on someone
else's machine, so "a page the user is looking at" and "a page we trust" are not
the same set. A link, a URL, a local endpoint: a page can navigate to or fetch
any of them without the user doing anything.

A physical key is the one signal a web page cannot produce. That is the whole
reason for the shape.

WHY IT CAN READ THE KEYS AT ALL WHILE THE KIOSK HOLDS THE DISPLAY
-----------------------------------------------------------------
cage owns the Wayland session and libinput has the devices open, but evdev
character devices are not exclusive: EVIOCGRAB is opt-in and libinput does not
take it for keyboards. Several readers see the same events. So this watcher does
NOT need the compositor to offer a hotkey API, and does not need the kiosk to
cooperate or even to be alive. That matters -- the case it exists for is the one
where the thing on screen is unusable.

WHAT IT DOES, AND WHAT IT DELIBERATELY DOES NOT
-----------------------------------------------
On the chord it writes a one-shot sentinel and restarts the kiosk. The launcher
consumes the sentinel and opens the first-run helper for that one start.

It does NOT edit shell.conf. Whoever holds the phone gets the setup screen and
makes the choice there, with the same address check and the same refusals as
first run. A watcher that rewrote the config would be a second writer of the
file the whole design says has exactly one, and "hold two buttons to wipe your
controller" is a footgun rather than a way out.

The sentinel lives in the user's XDG_RUNTIME_DIR, which is tmpfs. That direction
is deliberate: an orphaned sentinel -- written when the restart then failed --
cannot survive a reboot and strand the device in setup forever. Worst case it
costs one extra trip through a screen that can fix things.

IT REFUSES TO RUN BLIND
-----------------------
This watcher has exactly one input: evdev key events. If it is pointed at
devices that cannot report the keys it waits for, it would sit `active` forever
and detect nothing -- an instrument reporting green while measuring nothing,
which is the failure mode this repo has now hit four times (a first-run unit
installed but never enabled; a relay Caddyfile correct and never deployed;
StartLimitBurst in the wrong section; a board check reading a key that does not
exist). So at start it asks the kernel which key codes each device advertises,
and exits non-zero if nothing on this machine can report both. A failed unit is
visible; an active one that can never fire is not.

See docs/first-run-controller-choice.md requirement 1 and the layer README.
"""

from __future__ import annotations

import array
import fcntl
import glob
import os
import pwd
import select
import struct
import subprocess
import sys
import time

# struct input_event on a 64-bit kernel: struct timeval (two longs), then
# __u16 type, __u16 code, __s32 value.
_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)

EV_KEY = 0x01

# From linux/input-event-codes.h. Named rather than inlined because a wrong
# number here is a watcher that runs forever and never fires.
KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115

CHORD = frozenset((KEY_VOLUMEUP, KEY_VOLUMEDOWN))

# Both keys, together, for this long. Long enough that no volume adjustment and
# no pocket press reaches it; short enough to hold while looking at a stuck
# screen and wondering whether anything is happening.
HOLD_SECS = float(os.environ.get("TAOS_ESCAPE_HOLD_SECS", "5"))

# After firing, ignore the chord until every key has been released and this much
# time has passed. Without it one long hold would restart the kiosk repeatedly.
COOLDOWN_SECS = float(os.environ.get("TAOS_ESCAPE_COOLDOWN_SECS", "20"))

KIOSK_UNIT = os.environ.get("TAOS_KIOSK_UNIT", "taos-kiosk.service")

# Set from the unit file, which the installer fills in the same way it fills in
# the kiosk unit's User=. The sentinel has to land where the launcher -- running
# as that user, not as root -- can both read and DELETE it, which is why it is
# chowned rather than left owned by this process.
KIOSK_USER = os.environ.get("TAOS_KIOSK_USER", "")

INPUT_GLOB = os.environ.get("TAOS_INPUT_GLOB", "/dev/input/event*")


def log(*parts: object) -> None:
    print("taos-setup-escape:", *parts, file=sys.stderr, flush=True)


# --- what the kernel says a device can report -------------------------------
#
# EVIOCGBIT(EV_KEY, len) returns a bitmap of the key codes a device advertises.
# Asking is the difference between "watching the volume keys" and "watching
# whatever happened to be in /dev/input", and only one of those is a claim that
# can be checked.


def _eviocgbit(ev: int, length: int) -> int:
    # _IOC(_IOC_READ, 'E', 0x20 + ev, length) -- asm-generic layout:
    # dir << 30 | size << 16 | type << 8 | nr.
    return (2 << 30) | (length << 16) | (ord("E") << 8) | (0x20 + ev)


def key_codes(fd: int) -> set[int]:
    """The EV_KEY codes this device advertises. Empty if it cannot be asked."""
    nbytes = (max(KEY_VOLUMEUP, KEY_VOLUMEDOWN) // 8) + 1
    buf = array.array("B", [0] * nbytes)
    try:
        fcntl.ioctl(fd, _eviocgbit(EV_KEY, nbytes), buf)
    except OSError:
        # Not an evdev node. A regular file, a pipe or a device of another kind
        # lands here, and "cannot be asked" is treated as "advertises nothing"
        # rather than as an error -- the caller's job is to notice that NOTHING
        # advertises the chord, which is the check that matters.
        return set()
    return {c for c in (KEY_VOLUMEUP, KEY_VOLUMEDOWN) if buf[c // 8] & (1 << (c % 8))}


def open_devices() -> tuple[list[int], set[int], list[str]]:
    """Open every input device that can report part of the chord.

    Returns (fds, codes_covered, names). Devices that advertise neither key are
    closed again: holding them open would mean waking on every mouse move.
    """
    fds: list[int] = []
    names: list[str] = []
    covered: set[int] = set()
    for path in sorted(glob.glob(INPUT_GLOB)):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            log(f"cannot open {path}: {type(exc).__name__}")
            continue
        codes = key_codes(fd)
        if not codes & CHORD:
            os.close(fd)
            continue
        fds.append(fd)
        names.append(path)
        covered |= codes & CHORD
    return fds, covered, names


# --- the decision --------------------------------------------------------
#
# Split out from the reading so it can be driven with synthetic events by
# check-setup-escape.sh. Timing bugs here -- fires on a tap, never fires, fires
# twice -- are exactly the ones that cannot be found on a device you are holding
# in your hand.


class ChordDetector:
    """Both chord keys held together, continuously, for HOLD_SECS."""

    def __init__(self, hold: float = HOLD_SECS, cooldown: float = COOLDOWN_SECS):
        self.hold = hold
        self.cooldown = cooldown
        self.held: set[int] = set()
        self.since: float | None = None
        self.blocked_until: float = 0.0
        # Set when the chord fires, cleared only on a full release. Stops one
        # continuous hold from firing again the moment the cooldown lapses.
        self.armed = True

    def feed(self, code: int, value: int, now: float) -> None:
        """One EV_KEY event. value 0 release, 1 press, 2 autorepeat."""
        if code not in CHORD:
            return
        if value == 0:
            self.held.discard(code)
        else:
            self.held.add(code)
        if not self.held:
            # A full release re-arms. Requiring this, rather than the cooldown
            # alone, is what makes "fires once per deliberate hold" true.
            self.armed = True
        if self.held >= CHORD:
            if self.since is None:
                self.since = now
        else:
            self.since = None

    def fires(self, now: float) -> bool:
        """Has the chord been held long enough? Consumes the trigger."""
        if self.since is None or not self.armed or now < self.blocked_until:
            return False
        if now - self.since < self.hold:
            return False
        self.since = None
        self.armed = False
        self.blocked_until = now + self.cooldown
        return True

    def next_deadline(self, now: float) -> float | None:
        """When select() should wake even with no events, or None."""
        if self.since is None or not self.armed:
            return None
        return max(0.0, (self.since + self.hold) - now)


# --- the effect ----------------------------------------------------------


def sentinel_path() -> str:
    """Where the launcher looks. Same expression, in two languages, on purpose.

    The launcher runs as the kiosk user and derives XDG_RUNTIME_DIR from its own
    uid; this runs as root and must derive it from the configured user's.
    """
    override = os.environ.get("TAOS_SETUP_SENTINEL")
    if override:
        return override
    uid = os.geteuid()
    if KIOSK_USER:
        try:
            uid = pwd.getpwnam(KIOSK_USER).pw_uid
        except KeyError:
            log(f"no such user {KIOSK_USER!r}; using own uid for the sentinel")
    return f"/run/user/{uid}/taos-setup-requested"


def request_setup() -> None:
    path = sentinel_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("requested by taos-setup-escape\n")
        if KIOSK_USER:
            try:
                pw = pwd.getpwnam(KIOSK_USER)
                os.chown(path, pw.pw_uid, pw.pw_gid)
            except (KeyError, PermissionError, OSError) as exc:
                # Not fatal by itself: the launcher only needs to READ it to
                # reach the helper. It would fail to delete it afterwards, which
                # costs one extra pass through the setup screen on the next
                # start and is cleared by any reboot, /run being tmpfs.
                log(f"could not chown sentinel: {type(exc).__name__}")
        os.chmod(path, 0o644)
    except OSError as exc:
        log(f"could not write sentinel at {path}: {type(exc).__name__}")
        return
    log(f"setup requested; sentinel at {path}, restarting {KIOSK_UNIT}")
    try:
        subprocess.run(
            ["systemctl", "restart", KIOSK_UNIT],
            check=False, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Say so and carry on. The sentinel is already written, so the next
        # start of the kiosk -- including one the user forces by holding power
        # -- still lands on the setup screen.
        log(f"restart of {KIOSK_UNIT} failed: {type(exc).__name__}")


# --- the loop ------------------------------------------------------------


def main() -> int:
    fds, covered, names = open_devices()

    # THE SELF-PROOF. Everything above this line is capability, not evidence.
    if covered != CHORD:
        missing = sorted(CHORD - covered)
        log(f"no input device reports {missing} (looked at {INPUT_GLOB})")
        log("refusing to run: a watcher that cannot see its keys would sit "
            "active and never fire, which is worse than a failed unit")
        for fd in fds:
            os.close(fd)
        # 78 is sysexits' EX_CONFIG, and it is distinct from a plain crash on
        # purpose: the unit names it in RestartPreventExitStatus so this
        # refusal reaches `failed` and stays there, while a transient crash is
        # still retried. An exit code that means two things cannot be given two
        # restart policies.
        return 78
    log(f"watching {len(fds)} device(s): {', '.join(names)}")
    log(f"chord: volume-up + volume-down held {HOLD_SECS:g}s")

    if os.environ.get("TAOS_ESCAPE_SELFPROOF_ONLY"):
        # Used by the verifier to test the self-proof itself: the interesting
        # answer is the exit status, not the watching.
        for fd in fds:
            os.close(fd)
        return 0

    det = ChordDetector()
    try:
        while True:
            timeout = det.next_deadline(time.monotonic())
            ready, _, _ = select.select(fds, [], [], timeout)
            for fd in ready:
                try:
                    data = os.read(fd, _EVENT_SIZE * 64)
                except OSError:
                    continue
                for off in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                    _, _, etype, code, value = struct.unpack_from(
                        _EVENT_FMT, data, off)
                    if etype == EV_KEY:
                        det.feed(code, value, time.monotonic())
            # Checked on every pass, not only on an event: gpio-keys does not
            # autorepeat, so a held chord produces exactly two events and then
            # silence. Waiting for an event to re-check would mean the timer
            # never expires.
            if det.fires(time.monotonic()):
                request_setup()
    except KeyboardInterrupt:
        pass
    finally:
        for fd in fds:
            os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
