#!/bin/bash
# Drive check-device-presence.sh through every state it can reach, and assert
# BOTH the verdict and the exit code in each one.
#
# WHY A STUB HARNESS -- tsk-wtc2zn asked for "a positive control: a case where
# the check MUST say present", and noted it was hard while the device is
# absent. It is only hard against real hardware. Every probe in the check is a
# single external command (`ip`, `ping`, `fastboot`, `adb`, `lsusb`), so PATH
# is sealed to a directory of stubs and each state writes exactly the output
# the real tool would have produced. That buys the present case, the EDL case
# and the disagreement cases, none of which can be reached on demand with a
# phone in a drawer.
#
# The sealing is total, not a prepend: PATH becomes the stub dir and nothing
# else (plus symlinks to the handful of coreutils the check needs). A prepend
# would leave the host's real /usr/bin/fastboot reachable, and the states that
# assert INCOMPLETE-because-an-instrument-is-missing would then be measuring
# the host rather than the check.
#
# The fixture for the absent state is the REAL `ip -o link` output from the Pi
# (jay@192.168.55.52) on 2026-08-31, ptusb0 and both Incus bridges included --
# the exact three interfaces that made `grep -ci "usb\|rndis"` return 3.
#
# Runs anywhere: no device, no sudo, no network. Exit 0 = every state matched.
set -uo pipefail
cd "$(dirname "$0")"
CHECK="$PWD/check-device-presence.sh"

PASS=0; FAIL=0
ok()  { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
give_up() { echo "GIVING UP: $1"; exit 2; }

[ -f "$CHECK" ] || give_up "check-device-presence.sh not found next to this script"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/selftest-device-presence.XXXXXX")
BIN="$WORK/bin"; SCEN="$WORK/scen"
mkdir -p "$BIN" "$SCEN"
trap 'rm -rf "$WORK"' EXIT
export SCEN

# The real tools the check itself needs. If one of these is missing the states
# below would fail for a reason that has nothing to do with the check, so give
# up loudly instead of reporting a red.
for tool in bash timeout sed awk grep tr head basename; do
    real=$(command -v "$tool") || give_up "$tool not on PATH; cannot seal a usable PATH"
    ln -sf "$real" "$BIN/$tool"
done

# ---- stubs -----------------------------------------------------------------
# A stub exists only when the state creates its backing file, so "the tool is
# not installed" is expressed by simply not writing one.
# The stub body uses ONLY bash builtins -- no cat, no external anything. That
# is not tidiness: the first version of this file shelled out to `cat`, `cat`
# was not one of the symlinks in the sealed PATH, and so EVERY stub silently
# emitted nothing and exited 0. That reads to the check as "every instrument
# ran and saw no device", which is a plausible-looking state, so 14 states
# printed PASS while measuring nothing at all. A stub that needs no external
# tool cannot fail that way again -- and the stub probe below checks anyway.
mk_stub() {
    cat >"$BIN/$1" <<EOF
#!/bin/bash
rc=0
[ -f "\$SCEN/$1.rc" ] && read -r rc < "\$SCEN/$1.rc"
[ -s "\$SCEN/$1.out" ] && printf '%s\\n' "\$(<"\$SCEN/$1.out")"
exit "\$rc"
EOF
    chmod +x "$BIN/$1"
}

# ---- fixtures --------------------------------------------------------------
# Verbatim from the Pi. Nothing here is named usb0 or rndis0; all three of the
# interfaces whose names CONTAIN "usb" are NO-CARRIER and state DOWN.
PI_LINKS='1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000\    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
2: eth0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc mq state DOWN mode DEFAULT group default qlen 1000\    link/ether dc:a6:32:da:61:22 brd ff:ff:ff:ff:ff:ff
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP mode DORMANT group default qlen 1000\    link/ether dc:a6:32:da:61:23 brd ff:ff:ff:ff:ff:ff
4: ptusb0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc pfifo_fast state DOWN mode DEFAULT group default qlen 1000\    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff
5: incusbr-999: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN mode DEFAULT group default qlen 1000\    link/ether 10:66:6a:6c:f0:a5 brd ff:ff:ff:ff:ff:ff
6: incusbr0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN mode DEFAULT group default qlen 1000\    link/ether 10:66:6a:c2:b6:19 brd ff:ff:ff:ff:ff:ff'

# A genuinely connected RNDIS gadget, which is what a booted Droidian looks like.
up_iface() {
    printf '7: %s: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP mode DEFAULT group default qlen 1000\\    link/ether 02:11:22:33:44:66 brd ff:ff:ff:ff:ff:ff\n' "$1"
}
LSUSB_HUBS='Bus 003 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub
Bus 001 Device 002: ID 2109:3431 VIA Labs, Inc. Hub
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub'
LSUSB_PHONE="$LSUSB_HUBS
Bus 001 Device 007: ID 18d1:4ee0 Google Inc. Nexus/Pixel Device (fastboot)"
LSUSB_EDL="$LSUSB_HUBS
Bus 001 Device 008: ID 05c6:9008 Qualcomm, Inc. Gobi Wireless Modem (QDL mode)"
ADB_HEADER='List of devices attached'

# ---- state driver ----------------------------------------------------------
# scenario() resets to "every tool exists and reports nothing"; a state then
# overrides only what it is about. Reset is total (rm -rf) so a leftover file
# from the previous state can never make the next one pass.
scenario() {
    rm -rf "$SCEN" "$BIN"/{ip,ping,fastboot,adb,lsusb}
    mkdir -p "$SCEN"
    for t in ip ping fastboot adb lsusb; do mk_stub "$t"; done
    printf '%s\n' "$PI_LINKS"   > "$SCEN/ip.out"
    echo 1                      > "$SCEN/ping.rc"     # silent
    : > "$SCEN/fastboot.out"
    printf '%s\n' "$ADB_HEADER" > "$SCEN/adb.out"
    printf '%s\n' "$LSUSB_HUBS" > "$SCEN/lsusb.out"
}
drop_tool() { rm -f "$BIN/$1" "$SCEN/$1.out" "$SCEN/$1.rc"; }

LAST_OUT=""; LAST_RC=0
run_check() {
    LAST_OUT=$(env -i PATH="$BIN" SCEN="$SCEN" HOME="$WORK" \
               timeout 60 "$BIN/bash" "$CHECK" 2>&1)
    LAST_RC=$?
}

# want_verdict is a substring of the VERDICT line; want_rc is exact. Both are
# asserted, because they are two different claims: a verdict string that is
# right while the exit code is wrong makes the check unusable to a caller, and
# a caller branching on the code is the whole reason the codes exist.
assert_state() {
    local name="$1" want_verdict="$2" want_rc="$3" verdict_line
    run_check
    verdict_line=$(printf '%s\n' "$LAST_OUT" | grep '^VERDICT:' | head -1)
    local vd=ok rc=ok
    case "$verdict_line" in *"$want_verdict"*) ;; *) vd=no ;; esac
    [ "$LAST_RC" = "$want_rc" ] || rc=no
    if [ "$vd" = ok ] && [ "$rc" = ok ]; then
        ok "$name -> ${verdict_line#VERDICT: } (exit $LAST_RC)"
        return 0
    fi
    [ "$vd" = no ] && bad "$name: wanted verdict containing '$want_verdict', got '${verdict_line:-<no VERDICT line>}'"
    [ "$rc" = no ] && bad "$name: wanted exit $want_rc, got $LAST_RC"
    printf '%s\n' "$LAST_OUT" | sed 's/^/        | /'
    return 1
}

echo "== stub probe: the stubs actually return what the scenario wrote =="
# Prove the harness's OWN instruments fire before believing anything they say.
# An inert stub does not error -- it returns empty and exits 0, which the check
# reads as a real "nothing is attached" measurement. Every state below would
# then be scored against a fixture that never reached the check.
scenario
printf 'STUB-SENTINEL-%s\n' ip       > "$SCEN/ip.out"
printf 'STUB-SENTINEL-fastboot\n'     > "$SCEN/fastboot.out"
printf 'STUB-SENTINEL-adb\n'          > "$SCEN/adb.out"
printf 'STUB-SENTINEL-lsusb\n'        > "$SCEN/lsusb.out"
for t in ip fastboot adb lsusb; do
    got=$(env -i PATH="$BIN" SCEN="$SCEN" HOME="$WORK" timeout 10 "$BIN/$t" 2>&1)
    case "$got" in
        *"STUB-SENTINEL-$t"*) ok "stub '$t' returns the scenario's own content" ;;
        *) bad "stub '$t' is INERT: wanted STUB-SENTINEL-$t, got '${got:-<empty>}' -- every state below is scored against a fixture the check never saw" ;;
    esac
done
# The rc file has to be honoured too, or the ping probe silently always answers.
echo 1 > "$SCEN/ping.rc"
env -i PATH="$BIN" SCEN="$SCEN" HOME="$WORK" timeout 10 "$BIN/ping" -c1 172.16.42.1 >/dev/null 2>&1
[ "$?" = 1 ] && ok "stub 'ping' honours ping.rc (1 -> silent)" \
             || bad "stub 'ping' ignores ping.rc: it would report ANSWERS in every state"
echo 0 > "$SCEN/ping.rc"
env -i PATH="$BIN" SCEN="$SCEN" HOME="$WORK" timeout 10 "$BIN/ping" -c1 172.16.42.1 >/dev/null 2>&1
[ "$?" = 0 ] && ok "stub 'ping' honours ping.rc (0 -> answers)" \
             || bad "stub 'ping' cannot report success, so the BOOTED states can never be reached"

echo
echo "== self-proof: the assertion can go RED =="
# Without this the whole file could be passing because assert_state cannot
# fail. Run a state that is known to be ABSENT and demand PRESENT: the
# comparison must reject it. Counted as a pass only when it DISAGREES.
scenario
run_check
PROOF_LINE=$(printf '%s\n' "$LAST_OUT" | grep '^VERDICT:' | head -1)
if [ "$LAST_RC" = 3 ] && case "$PROOF_LINE" in *"PRESENT / BOOTED"*) false ;; *) true ;; esac; then
    ok "a known-ABSENT state does not match 'PRESENT / BOOTED', so the matcher discriminates"
else
    bad "self-proof: the matcher accepted 'PRESENT / BOOTED' for an absent state, or the rc was not 3 (rc=$LAST_RC, line='$PROOF_LINE')"
    bad "  -> every PASS below this line is meaningless; fix the harness before reading them"
fi

echo
echo "== fixture probe: the absent fixture really is the one that broke =="
# A fixture that did not take is not a green either. If the Pi fixture ever
# loses ptusb0, the regression states below would still pass while testing
# nothing at all -- so assert the fixture's own content before trusting it.
FIX="$SCEN/ip.out"
for f in ptusb0 incusbr0 incusbr-999; do
    grep -q "$f:" "$FIX" \
        && ok "fixture contains $f (a name containing 'usb' that is NOT the phone)" \
        || bad "fixture has lost $f; the substring regression is no longer being tested"
done
grep -Eq '^[0-9]+: (usb0|rndis0):' "$FIX" \
    && bad "fixture already contains a real RNDIS interface; the absent states are not absent" \
    || ok "fixture contains no interface named exactly usb0 or rndis0"
# And prove the OLD instrument still says 3 on it, so the fixture is provably
# the failing input rather than one that has quietly drifted honest.
OLD=$(grep -ci "usb\|rndis" "$FIX")
[ "$OLD" = 3 ] \
    && ok "the original 'grep -ci usb\\|rndis' still returns 3 on this fixture" \
    || bad "the original grep returns $OLD, not 3: this is no longer the input that produced the false positive"

echo
echo "== absent =="
scenario
assert_state "the real Pi, phone in a drawer" "ABSENT" 3

scenario
{ printf '%s\n' "$PI_LINKS"; printf '7: usb0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc pfifo_fast state DOWN mode DEFAULT group default qlen 1000\\    link/ether 02:11:22:33:44:66 brd ff:ff:ff:ff:ff:ff\n'; } > "$SCEN/ip.out"
assert_state "usb0 exists but has NO CARRIER" "ABSENT" 3

# The sharpest one. ptusb0 is the Pi's own gadget and it CAN come up with a
# carrier when something else is plugged into it. An exact-name check is
# unmoved; a substring check reports the phone.
scenario
{ printf '%s\n' "$PI_LINKS"
  printf '7: ptusb0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP mode DEFAULT group default qlen 1000\\    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff\n'; } > "$SCEN/ip.out"
assert_state "ptusb0 UP with a carrier is still not the phone" "ABSENT" 3

echo
echo "== present =="
scenario
{ printf '%s\n' "$PI_LINKS"; up_iface usb0; } > "$SCEN/ip.out"
echo 0 > "$SCEN/ping.rc"
assert_state "usb0 up and 172.16.42.1 answers" "PRESENT / BOOTED" 0

scenario
{ printf '%s\n' "$PI_LINKS"; up_iface rndis0; } > "$SCEN/ip.out"
echo 0 > "$SCEN/ping.rc"
assert_state "rndis0 spelling is honoured too" "PRESENT / BOOTED" 0

scenario
printf 'ABC12345\tfastboot\n' > "$SCEN/fastboot.out"
printf '%s\n' "$LSUSB_PHONE" > "$SCEN/lsusb.out"
assert_state "sitting in fastboot" "PRESENT / FASTBOOT" 0

# unauthorized is the state this device is actually in under stock recovery.
scenario
printf '%s\nABC12345\tunauthorized\n' "$ADB_HEADER" > "$SCEN/adb.out"
printf '%s\n' "$LSUSB_PHONE" > "$SCEN/lsusb.out"
assert_state "adb sees it as 'unauthorized', which is still PRESENT" "PRESENT / ADB" 0

echo
echo "== disagreement =="
scenario
{ printf '%s\n' "$PI_LINKS"; up_iface usb0; } > "$SCEN/ip.out"
assert_state "link up but nothing answers the ping" "DISAGREEMENT" 4

scenario
echo 0 > "$SCEN/ping.rc"
assert_state "the ping answers but no RNDIS link is up" "DISAGREEMENT" 4

scenario
{ printf '%s\n' "$PI_LINKS"; up_iface usb0; } > "$SCEN/ip.out"
echo 0 > "$SCEN/ping.rc"
printf 'ABC12345\tfastboot\n' > "$SCEN/fastboot.out"
printf '%s\n' "$LSUSB_PHONE" > "$SCEN/lsusb.out"
assert_state "booted AND in the bootloader at once" "DISAGREEMENT" 4

scenario
printf 'ABC12345\tfastboot\n' > "$SCEN/fastboot.out"
printf '%s\nABC12345\tdevice\n' "$ADB_HEADER" > "$SCEN/adb.out"
printf '%s\n' "$LSUSB_PHONE" > "$SCEN/lsusb.out"
assert_state "fastboot and adb both claim it" "DISAGREEMENT" 4

# The udev-permissions case: this is what an unprivileged `fastboot devices`
# looks like with the phone plugged in, and it must never read as ABSENT.
scenario
printf '%s\n' "$LSUSB_PHONE" > "$SCEN/lsusb.out"
assert_state "lsusb sees a candidate that fastboot and adb do not" "DISAGREEMENT" 4

# Deliberate, and asserted so it cannot drift back: fastboot naming a serial
# while lsusb shows only hubs is PRESENT, not a disagreement. lsusb's candidate
# ids are unverified against the real device, so they are never allowed to
# override an instrument that named an actual serial. The arm that used to
# claim otherwise could not fire on any host with a hub or a keyboard attached.
scenario
printf 'ABC12345\tfastboot\n' > "$SCEN/fastboot.out"
assert_state "fastboot names a serial that lsusb cannot identify: believe fastboot" "PRESENT / FASTBOOT" 0

echo
echo "== EDL outranks everything =="
scenario
printf '%s\n' "$LSUSB_EDL" > "$SCEN/lsusb.out"
assert_state "05c6:9008 is emergency download mode" "EDL" 5

echo
echo "== a missing instrument is INCOMPLETE, never ABSENT =="
# This is the arm that matters most for honesty: run on a host with no
# fastboot and the check must refuse to conclude, not report the phone gone.
scenario; drop_tool ip
assert_state "no 'ip'" "INCOMPLETE" 2

scenario; drop_tool ping
assert_state "no 'ping'" "INCOMPLETE" 2

scenario; drop_tool fastboot; drop_tool adb; drop_tool lsusb
assert_state "no fastboot, no adb, no lsusb" "INCOMPLETE" 2

# ...but ONE of the three is enough to tell fastboot mode from absence, so
# losing only fastboot must still yield a usable answer. Without this the
# INCOMPLETE arm could be widened to "any tool missing" and still pass.
scenario; drop_tool fastboot
assert_state "no fastboot, but adb and lsusb ran" "ABSENT" 3

echo
echo "states: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
