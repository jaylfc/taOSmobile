#!/bin/bash
# Verify taos-setup-escape.py: that the chord fires when it should, does not
# fire when it should not, fires once per hold, and REFUSES TO START rather than
# sitting active on a device that cannot report the keys it waits for.
#
# Exit 0 PASS, 1 FAIL, 2 INCOMPLETE (could not measure -- NOT a pass).
#
# THE POSITIVE CONTROL, AND WHY IT IS THIS ONE
# --------------------------------------------
# Almost everything here asserts that the chord does NOT fire: one key alone, a
# tap, a release, a second fire from one hold. A detector that never fires at
# all satisfies every one of them. So the first check is that a full, patient
# hold DOES fire, and the script gives up if it does not -- the same shape as
# the forwarding control in check-firstrun-helper.sh and the discrimination
# control in check-kiosk-url.sh.
#
# The timing checks drive a synthetic clock rather than sleeping. A five-second
# hold tested by sleeping five seconds is a test nobody runs twice, and one that
# cannot express "held 4.999s" at all.
#
# Run from anywhere; needs no device. Two checks need root and say SKIP loudly
# if they do not have it, rather than passing quietly.

set -u

HERE="$(dirname "$(readlink -f "$0")")"
ESCAPE="$HERE/taos-setup-escape.py"
UNIT="$HERE/taos-setup-escape.service"
LAUNCH="$HERE/taos-kiosk-launch.sh"
WORK="$(mktemp -d)"
PASS=0; FAIL=0

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
skip() { echo "  SKIP  $1"; }
give_up() { echo "INCOMPLETE: $1"; exit 2; }

want() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want $2, got $3)"; fi; }

command -v python3 >/dev/null || give_up "python3 not present"
[ -f "$ESCAPE" ] || give_up "watcher not found at $ESCAPE"
[ -f "$UNIT" ]   || give_up "unit not found at $UNIT"
[ -f "$LAUNCH" ] || give_up "launcher not found at $LAUNCH"
python3 -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "$ESCAPE" \
    || give_up "watcher does not parse"

# --- the detector, driven on a synthetic clock ---------------------------
# One python process per scenario, each printing PASS/FAIL lines this script
# just relays, so the timing logic is exercised directly rather than through a
# five-second wall-clock sleep.
cat > "$WORK/drive.py" <<'PYEOF'
import importlib.util, sys

spec = importlib.util.spec_from_file_location("esc", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

UP, DOWN = m.KEY_VOLUMEUP, m.KEY_VOLUMEDOWN
HOLD, COOL = 5.0, 20.0


def run(script):
    """script: list of ('press'|'release', code, at) or ('tick', at).

    Returns the list of times at which the chord fired.
    """
    det = m.ChordDetector(hold=HOLD, cooldown=COOL)
    fired = []
    for step in script:
        if step[0] == "tick":
            now = step[1]
        else:
            _, code, now = step
            det.feed(code, 1 if step[0] == "press" else 0, now)
        if det.fires(now):
            fired.append(now)
    return fired


def check(label, got, want):
    print(("PASS  " if got == want else "FAIL  ") + label
          + ("" if got == want else f" (want {want}, got {got})"))


# POSITIVE CONTROL. Everything under this is a refusal.
check("a full hold fires",
      run([("press", UP, 0.0), ("press", DOWN, 0.1), ("tick", 5.2)]), [5.2])

check("one key alone never fires",
      run([("press", UP, 0.0), ("tick", 30.0)]), [])
check("the other key alone never fires",
      run([("press", DOWN, 0.0), ("tick", 30.0)]), [])
check("released a hair early does not fire",
      run([("press", UP, 0.0), ("press", DOWN, 0.0),
           ("release", UP, 4.999), ("tick", 30.0)]), [])
check("a tap does not fire",
      run([("press", UP, 0.0), ("press", DOWN, 0.0),
           ("release", UP, 0.2), ("release", DOWN, 0.2), ("tick", 30.0)]), [])
check("repeated taps do not accumulate",
      run([s for i in range(20)
           for s in (("press", UP, i * 0.5), ("press", DOWN, i * 0.5),
                     ("release", UP, i * 0.5 + 0.2),
                     ("release", DOWN, i * 0.5 + 0.2))]
          + [("tick", 60.0)]), [])
# The clock starts when the SECOND key joins, not when the first is pressed.
# Getting this wrong makes "hold volume-down, then tap volume-up" a trigger.
check("the timer starts at the second key",
      run([("press", UP, 0.0), ("press", DOWN, 4.9), ("tick", 5.1)]), [])
check("...and fires once it has genuinely been 5s",
      run([("press", UP, 0.0), ("press", DOWN, 4.9), ("tick", 9.95)]), [9.95])

# One deliberate hold is one restart. Without this a user who keeps holding
# gets the kiosk restarted over and over.
check("one continuous hold fires exactly once",
      run([("press", UP, 0.0), ("press", DOWN, 0.0)]
          + [("tick", t) for t in (5.1, 10.0, 30.0, 60.0, 120.0)]), [5.1])
check("re-holding without releasing does not re-fire",
      run([("press", UP, 0.0), ("press", DOWN, 0.0), ("tick", 5.1),
           ("release", UP, 6.0), ("press", UP, 6.1), ("tick", 40.0)]), [5.1])
check("a full release then a new hold fires again",
      run([("press", UP, 0.0), ("press", DOWN, 0.0), ("tick", 5.1),
           ("release", UP, 6.0), ("release", DOWN, 6.0),
           ("press", UP, 30.0), ("press", DOWN, 30.0), ("tick", 35.2)]),
      [5.1, 35.2])
check("a new hold inside the cooldown does not fire",
      run([("press", UP, 0.0), ("press", DOWN, 0.0), ("tick", 5.1),
           ("release", UP, 6.0), ("release", DOWN, 6.0),
           ("press", UP, 7.0), ("press", DOWN, 7.0), ("tick", 12.1)]), [5.1])

# Autorepeat (value 2) must count as still-held, not as a fresh press that
# restarts the timer. Some keyboards send it; gpio-keys does not, which is why
# the loop is timer-driven and not event-driven.
det = m.ChordDetector(hold=HOLD, cooldown=COOL)
det.feed(UP, 1, 0.0); det.feed(DOWN, 1, 0.0)
for t in (1.0, 2.0, 3.0, 4.0):
    det.feed(UP, 2, t)
check("autorepeat does not restart the timer", [det.fires(5.1)], [True])

# next_deadline is what stops the loop blocking in select() forever on a device
# that sends two events and then nothing at all.
det = m.ChordDetector(hold=HOLD, cooldown=COOL)
check("no deadline while nothing is held", [det.next_deadline(0.0)], [None])
det.feed(UP, 1, 0.0); det.feed(DOWN, 1, 0.0)
check("a deadline appears once the chord is held", [det.next_deadline(1.0)], [4.0])

# Codes that are not the chord must not touch it at all -- a watcher that
# treated any key as part of the chord would fire on ordinary typing.
det = m.ChordDetector(hold=HOLD, cooldown=COOL)
det.feed(UP, 1, 0.0); det.feed(DOWN, 1, 0.0); det.feed(28, 1, 1.0)
det.feed(28, 0, 1.1)
check("an unrelated key does not disturb the chord", [det.fires(5.1)], [True])
PYEOF

echo "== the chord: fires when held, and only then =="
first=1
while IFS= read -r line; do
    verdict="${line%%  *}"; label="${line#*  }"
    case "$verdict" in
        PASS) ok "$label" ;;
        FAIL) bad "$label" ;;
        *) bad "unparseable detector output: $line" ;;
    esac
    if [ "$first" = "1" ]; then
        first=0
        [ "$verdict" = "PASS" ] || give_up "detector positive control failed: the chord can never fire, so every refusal below proves nothing"
    fi
done < <(python3 "$WORK/drive.py" "$ESCAPE")

echo "== it asks the kernel what a device can report =="
# The capability probe is the whole self-proof, so it needs a positive control
# of its own: a REAL evdev node that really does advertise both volume keys.
# Reading /dev/input needs root on most systems, so this SKIPs loudly rather
# than passing when it cannot measure -- an unmeasured probe is not a working
# one.
cat > "$WORK/probe.py" <<'PYEOF2'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("esc", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
fds, covered, names = m.open_devices()
for fd in fds:
    os.close(fd)
print("COVERED", " ".join(str(c) for c in sorted(covered)), "DEVS", len(names))
PYEOF2

if [ "$(id -u)" = "0" ]; then RUNROOT=""; elif sudo -n true 2>/dev/null; then RUNROOT="sudo -n"; else RUNROOT="NO"; fi
if [ "$RUNROOT" = "NO" ]; then
    skip "no root: cannot read /dev/input, so the probe's positive control cannot run"
else
    out="$($RUNROOT python3 "$WORK/probe.py" "$ESCAPE" 2>/dev/null | tail -1)"
    case "$out" in
        "COVERED 114 115 DEVS "*)
            ok "a real evdev node reporting both volume keys is found (${out##*DEVS })" ;;
        "COVERED  DEVS 0")
            skip "this host has no device reporting the volume keys; probe unproven here" ;;
        *)
            bad "capability probe gave an unexpected answer: $out" ;;
    esac
fi

# The negative half needs no root: something that is not an evdev node must
# advertise nothing. Without this, a probe that returned "everything" for
# everything would still pass the check above.
out="$(TAOS_INPUT_GLOB=/dev/null python3 "$WORK/probe.py" "$ESCAPE" 2>/dev/null | tail -1)"
want "a non-evdev node advertises nothing" "COVERED  DEVS 0" "$out"

echo "== it refuses to run blind =="
# The failure this repo keeps meeting: a component that is installed, active and
# measuring nothing. Pointed at devices that cannot report the chord, the
# watcher must EXIT rather than wait forever.
#
# EVERY RUN HERE IS BOUNDED BY `timeout`, and that is not defensive tidiness --
# it is the difference between a red and an instrument that stopped reporting.
# Removing the self-proof, which is the mutation this section exists to catch,
# makes the watcher fall through into select() with no descriptors and block
# for ever. Unbounded, this script would then HANG rather than fail, and a
# suite that hangs on the bug it is written for is worth nothing. 124 is
# timeout's own exit status, so a hang reports as a wrong exit code.
BLIND_TIMEOUT="${TAOS_ESCAPE_BLIND_TIMEOUT:-10}"
timeout "$BLIND_TIMEOUT" env TAOS_INPUT_GLOB=/dev/null python3 "$ESCAPE" >"$WORK/blind.log" 2>&1
rc=$?
[ "$rc" = "124" ] && bad "the watcher HUNG instead of refusing: it would sit active and never fire"
want "exits 78 (EX_CONFIG) when nothing reports the chord" 78 "$rc"
grep -q "refusing to run" "$WORK/blind.log" \
    && ok "and says why, on the journal" \
    || bad "exited without explaining itself"
timeout "$BLIND_TIMEOUT" env TAOS_INPUT_GLOB="$WORK/no-such-device-*" python3 "$ESCAPE" >/dev/null 2>&1
rc=$?
[ "$rc" = "124" ] && bad "the watcher HUNG with no devices at all"
want "exits 78 when there are no devices at all" 78 "$rc"

# The positive half of the same thing: given devices that DO report the chord it
# must get past the self-proof. Otherwise "exits 78" is satisfied by a watcher
# that can never start.
if [ "$RUNROOT" = "NO" ]; then
    skip "no root: cannot prove the watcher gets PAST the self-proof"
else
    $RUNROOT timeout "${TAOS_ESCAPE_BLIND_TIMEOUT:-10}" \
        env TAOS_ESCAPE_SELFPROOF_ONLY=1 python3 "$ESCAPE" >/dev/null 2>&1
    rc=$?
    [ "$rc" = "124" ] && bad "the self-proof-only run hung instead of exiting"
    if [ "$rc" = "0" ]; then
        ok "passes its self-proof on a host that has the keys (exit 0)"
    elif [ "$rc" = "78" ]; then
        skip "this host has no device reporting the volume keys; cannot prove the pass path"
    else
        bad "self-proof gave exit $rc, expected 0 or 78"
    fi
fi

echo "== firing writes a sentinel the launcher then consumes =="
# End to end across the two languages: the watcher's sentinel_path/request_setup
# and the launcher's SENTINEL expression have to name the SAME file, and nothing
# but this check would notice if they drifted apart.
SENT="$WORK/setup-requested"
TAOS_SETUP_SENTINEL="$SENT" TAOS_KIOSK_UNIT="taos-no-such-unit-control.service" \
    python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('esc', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.request_setup()
" "$ESCAPE" >"$WORK/fire.log" 2>&1
[ -f "$SENT" ] && ok "the watcher wrote the sentinel" || bad "no sentinel was written"

CONF="$WORK/shell.conf"
printf 'mode=remote\nurl=http://gone.example:6969/\n' > "$CONF"
got="$(TAOS_SHELL_CONF="$CONF" TAOS_SETUP_SENTINEL="$SENT" "$LAUNCH" --print-url 2>/dev/null)"
want "the launcher honours it" "http://127.0.0.1:6970/" "$got"
[ -e "$SENT" ] && bad "the launcher did not consume it" || ok "and consumes it"

# A restart that fails must not swallow the sentinel: the user held the buttons,
# and the next start of the kiosk -- including one forced with the power button
# -- still has to land on setup. The unit named above does not exist, so the
# systemctl call above genuinely failed.
# The `\|setup requested` alternative that used to be here made this check
# UNFAILABLE. request_setup() logs the sentinel line unconditionally at
# taos-setup-escape.py:273, before the restart is even attempted, and the check
# two lines above has already asserted the sentinel was written -- so the second
# alternative matched on every run and the first was dead. It passed while the
# failure it is named for was genuinely being swallowed: subprocess.run uses
# check=False, systemctl EXITS NONZERO for a missing unit rather than raising,
# and only OSError/TimeoutExpired were caught. Match the failure line alone.
grep -q "restart of taos-no-such-unit-control.service failed" "$WORK/fire.log" \
    && ok "a failed restart is logged, not swallowed" \
    || bad "nothing was logged about the restart attempt"
# Negative control for the line above: the sentinel line must NOT be sufficient.
# If this ever stops being true the check has gone vacuous again.
grep -q "setup requested" "$WORK/fire.log" \
    && ok "control: the sentinel line is present, so the check above is not passing on its absence" \
    || bad "control: no sentinel line at all -- the fixture did not run"

echo "== the unit's settings are actually in force =="
# systemd-analyze verify is BLIND to RestartPreventExitStatus -- it accepts
# `notanumber` without a word. The only way to prove a setting is in force is to
# load the unit into a systemd USER manager and read the PARSED property back,
# with an invalid value as the negative control. Same technique that caught
# StartLimitBurst sitting in [Service] where systemd ignores it.
grep -q '^PrivateDevices=yes' "$UNIT" \
    && bad "PrivateDevices=yes hides /dev/input: the watcher would exit 78 forever" \
    || ok "the unit does not set PrivateDevices=yes"
grep -q '^RestartPreventExitStatus=78' "$UNIT" \
    && ok "the unit names exit 78 as non-retryable" \
    || bad "exit 78 is not in RestartPreventExitStatus, so a blind watcher flaps"

USERD="$HOME/.config/systemd/user"
if ! systemctl --user show-environment >/dev/null 2>&1; then
    skip "no systemd user manager here; cannot read parsed properties back"
else
    mkdir -p "$USERD"
    NAME="taos-escape-verify-$$.service"
    # User=root cannot be honoured by a user manager and would make the unit
    # unloadable there; the properties under test are unrelated to it.
    grep -v '^User=' "$UNIT" > "$USERD/$NAME"
    systemctl --user daemon-reload
    v="$(systemctl --user show -p RestartPreventExitStatus --value "$NAME" 2>/dev/null)"
    case "$v" in
        *78*) ok "RestartPreventExitStatus=78 is parsed and in force ($v)" ;;
        *)    bad "systemd did not take RestartPreventExitStatus (read back: '$v')" ;;
    esac
    v="$(systemctl --user show -p StartLimitBurst --value "$NAME" 2>/dev/null)"
    want "StartLimitBurst is in force (it is in [Unit], where systemd reads it)" 3 "$v"

    # NEGATIVE CONTROL: an invalid value must NOT read back as in force. If it
    # does, the check above is measuring nothing and would pass on any unit.
    BADNAME="taos-escape-control-$$.service"
    sed 's/^RestartPreventExitStatus=78/RestartPreventExitStatus=notanumber/' \
        "$USERD/$NAME" > "$USERD/$BADNAME"
    systemctl --user daemon-reload
    # PROVE THE MEASUREMENT HAPPENED BEFORE READING THE VALUE. systemd handles an
    # invalid RestartPreventExitStatus by warning and leaving the property EMPTY
    # -- which is byte-identical to what a unit that never loaded reads back.
    # Measured 2026-08-30 against a unit name that does not exist:
    #     LoadState=not-found  RestartPreventExitStatus=''  -> this arm said PASS
    # so the negative control, whose whole job is to prove the check above is not
    # vacuous, was itself vacuous. LoadState is the discriminator; the value on
    # its own cannot be one.
    ls="$(systemctl --user show -p LoadState --value "$BADNAME" 2>/dev/null)"
    v="$(systemctl --user show -p RestartPreventExitStatus --value "$BADNAME" 2>/dev/null)"
    if [ "$ls" != "loaded" ]; then
        bad "control: the control unit did not load (LoadState='$ls'), so its empty"
        echo "        read-back proves nothing about whether systemd refused the value"
    else
        case "$v" in
            *78*|*notanumber*) bad "control: an invalid value read back as in force ('$v')" ;;
            *) ok "control: an invalid value does NOT read back as in force (unit loaded, value empty)" ;;
        esac
    fi
    rm -f "$USERD/$NAME" "$USERD/$BADNAME"
    systemctl --user daemon-reload
fi

echo
echo "checks passed: $PASS   failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
