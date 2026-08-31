#!/bin/bash
# Is the phone on this host's USB, and in what state? One script, one name, so
# the answer stops being reassembled from memory after every clear.
#
# WHY THIS FILE EXISTS -- tsk-wtc2zn. A freshness pass checked device presence
# with, among others:
#
#     ip -o link | grep -ci "usb\|rndis"
#
# and got 3. All three were false: `ptusb0` (the Pi's own USB gadget) and the
# Incus bridges `incusbr0` / `incusbr-999`, every one NO-CARRIER and state
# DOWN. Substring "usb" matched all three. An RNDIS link up on the flash host
# is the single strongest signal that the phone has BOOTED DROIDIAN, so that 3
# would have reported a successful flash while the device was absent. It was
# caught only because it disagreed with fastboot=0 / adb=0 / lsusb=0 and I
# looked; a count that had happened to agree would have been believed.
#
# So the fix is not a better grep. It is:
#   - EXACT interface names, never a substring, and a carrier requirement --
#     a DOWN interface is never evidence of a booted phone;
#   - positive corroboration before reporting present: the link is the medium,
#     not the device, so `ping 172.16.42.1` has to answer;
#   - the negative signals (fastboot / adb / lsusb) kept as a cross-check, with
#     DISAGREEMENT between them reported LOUDLY rather than silently resolved
#     in favour of whichever one we happened to trust.
#
# THE ABSENT ARM IS THE DANGEROUS ONE, in both directions. "Nothing found"
# and "nothing could look" produce the same silence, so every probe here is
# tri-state -- yes / no / UNMEASURED -- and a missing instrument yields
# INCOMPLETE (exit 2), never ABSENT. Reporting an absent phone because
# `fastboot` is not installed is the same defect one level up.
#
# EXIT CODES (a caller is meant to branch on these, not to grep the text):
#   0  PRESENT  -- and the verdict line names which state: BOOTED, FASTBOOT, ADB
#   2  INCOMPLETE -- too few instruments could run to conclude anything
#   3  ABSENT   -- every instrument that matters ran, and all of them say no
#   4  DISAGREEMENT -- signals conflict; believe none of them until it is resolved
#   5  EDL      -- the device is in Qualcomm emergency download mode (see below)
#   1  usage or internal error
#
# Run it ON THE USB HOST (the Pi, jay@192.168.55.52). Run anywhere else and it
# is answering about that host's USB, which is a true answer to a question
# nobody asked.
set -uo pipefail

# Exact interface names only. `ptusb0` is not in this list and must never
# match one; that is the whole point of the file.
RNDIS_NAMES="usb0 usb1 rndis0 rndis1"
# Droidian's RNDIS address on spacewar. docs/flash-procedure.md step 4.
PHONE_IP="172.16.42.1"
# Bound every invocation of a thing that can wait: a wedged USB device hangs
# fastboot indefinitely, and a hanging check is not a red, it is an instrument
# that stopped reporting.
T="timeout 10"

case "${1:-}" in
    -h|--help)
        echo "usage: $(basename "$0")   # run on the USB host; exits 0/2/3/4/5, see header"
        exit 0 ;;
    "") ;;
    *)  echo "usage: $(basename "$0")   (no arguments)" >&2; exit 1 ;;
esac

# USB ids that would be the phone. DELIBERATELY UNVERIFIED and marked as such:
# spacewar has not been on this host's USB since this list was written, so a
# no-match here is NOT evidence of absence, and the verdict below never uses it
# as such -- lsusb is a cross-check only. Confirm these the first time the
# phone appears (run `lsusb` with it attached) rather than trusting them now.
# The one id here that is not a guess is the EDL one, and it is the one that
# matters most: 05c6:9008 is Qualcomm emergency download mode, the single
# unrecoverable failure mode on this device.
CANDIDATE_IDS="18d1: 2d95:"
EDL_ID="05c6:9008"

# Tri-state probe results. "?" means the instrument never ran; nothing below
# may read "?" as "no".
LINK_M="?"; LINK_UP_IFS=""; LINK_DOWN_IFS=""
PING_M="?"; PING_OK="?"
FB_M="?";   FB_SERIALS=""
ADB_M="?";  ADB_LINES=""
USB_M="?";  USB_CANDS=""; USB_EDL=""; USB_NONHUB=0

echo "== instruments =="

# ---- link ------------------------------------------------------------------
# Parsed out of `ip -o link` rather than /sys so the whole probe is one
# external command, which is what makes it stub-able by selftest-device-presence.sh.
if command -v ip >/dev/null 2>&1 && LINKS=$($T ip -o link 2>/dev/null); then
    LINK_M="yes"
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        name=${line#*: }; name=${name%%:*}; name=${name%%@*}
        # Exact match against the name list -- never a prefix, never a
        # substring. `ptusb0`, `incusbr0` and `incusbr-999` all fail here,
        # which is the regression this file exists to prevent.
        matched=no
        for want in $RNDIS_NAMES; do [ "$name" = "$want" ] && matched=yes; done
        [ "$matched" = yes ] || continue

        flags=${line#*<}; flags=${flags%%>*}
        state=$(printf '%s\n' "$line" | sed -n 's/.* state \([A-Z_-]*\) .*/\1/p')
        # LOWER_UP is the carrier. NO-CARRIER is checked explicitly too rather
        # than inferred from its absence, because the two are set by different
        # code paths in the kernel and an interface that reports both is an
        # instrument fault worth failing on, not something to average out.
        carrier=no
        case ",$flags," in *,LOWER_UP,*) carrier=yes ;; esac
        case ",$flags," in *NO-CARRIER*) carrier=no ;; esac
        # A gadget/point-to-point driver commonly reports state UNKNOWN while
        # carrying traffic, so UNKNOWN is accepted; DOWN never is. The name
        # list is what keeps lo and tailscale0 (both UNKNOWN) out of here.
        case "$state" in UP|UNKNOWN) ;; *) carrier=no ;; esac

        if [ "$carrier" = yes ]; then
            LINK_UP_IFS="$LINK_UP_IFS $name"
            echo "  link      $name UP with carrier (flags=$flags state=$state)"
        else
            LINK_DOWN_IFS="$LINK_DOWN_IFS $name"
            echo "  link      $name present but NOT usable (flags=$flags state=$state)"
        fi
    done <<<"$LINKS"
    [ -n "$LINK_UP_IFS$LINK_DOWN_IFS" ] || echo "  link      no interface named in '$RNDIS_NAMES' exists"
else
    echo "  link      UNMEASURED: 'ip' is missing or failed"
fi

# ---- ping ------------------------------------------------------------------
# The link is the medium, not the device. Something has to answer.
if command -v ping >/dev/null 2>&1; then
    PING_M="yes"
    if $T ping -c1 -W2 "$PHONE_IP" >/dev/null 2>&1; then
        PING_OK="yes"; echo "  ping      $PHONE_IP ANSWERS"
    else
        PING_OK="no";  echo "  ping      $PHONE_IP silent"
    fi
else
    echo "  ping      UNMEASURED: 'ping' is missing"
fi

# ---- fastboot --------------------------------------------------------------
# Not run under sudo. On the Pi an unprivileged `fastboot devices` can return
# nothing for a device that IS attached (udev permissions), which would be a
# false ABSENT -- but that case shows up as lsusb-sees-it/fastboot-does-not,
# which is a DISAGREEMENT below and is reported loudly. Requiring sudo here
# would instead make the whole check unrunnable non-interactively.
if command -v fastboot >/dev/null 2>&1 && FBOUT=$($T fastboot devices 2>/dev/null); then
    FB_M="yes"
    FB_SERIALS=$(printf '%s\n' "$FBOUT" | awk 'NF{print $1}')
    if [ -n "$FB_SERIALS" ]; then
        echo "  fastboot  $(printf '%s' "$FB_SERIALS" | tr '\n' ' ')"
    else
        echo "  fastboot  no devices"
    fi
else
    echo "  fastboot  UNMEASURED: 'fastboot' is missing or failed"
fi

# ---- adb -------------------------------------------------------------------
# `unauthorized`, `offline` and `recovery` are all PRESENT. Stock recovery on
# spacewar draws no authorisation prompt at all, so unauthorized is the state
# this device actually sits in -- treating it as absent would have hidden the
# phone for the whole recovery investigation.
if command -v adb >/dev/null 2>&1 && ADBOUT=$($T adb devices 2>/dev/null); then
    ADB_M="yes"
    ADB_LINES=$(printf '%s\n' "$ADBOUT" | awk 'NF>=2 && $1 != "List" {print $1" "$2}')
    if [ -n "$ADB_LINES" ]; then
        echo "  adb       $(printf '%s' "$ADB_LINES" | tr '\n' ',')"
    else
        echo "  adb       no devices"
    fi
else
    echo "  adb       UNMEASURED: 'adb' is missing or failed"
fi

# ---- lsusb -----------------------------------------------------------------
if command -v lsusb >/dev/null 2>&1 && USBOUT=$($T lsusb 2>/dev/null); then
    USB_M="yes"
    # Root hubs (1d6b:) are the host's own controllers and are always there.
    # This count is PRINTED EVIDENCE ONLY -- no verdict arm reads it; see the
    # note in the disagreement block for why an arm that did was removed.
    USB_NONHUB=$(printf '%s\n' "$USBOUT" | grep 'ID ' | grep -vc '1d6b:')
    USB_EDL=$(printf '%s\n' "$USBOUT" | grep -F "$EDL_ID")
    for id in $CANDIDATE_IDS; do
        hit=$(printf '%s\n' "$USBOUT" | grep -F "ID $id")
        [ -n "$hit" ] && USB_CANDS="$USB_CANDS$hit"$'\n'
    done
    echo "  lsusb     $USB_NONHUB non-root-hub device(s); candidate match: ${USB_CANDS:-none}"
else
    echo "  lsusb     UNMEASURED: 'lsusb' is missing or failed"
fi

# ---- corroboration, reported but NOT part of the verdict -------------------
# An SSH banner is the strongest evidence there is that the thing answering
# 172.16.42.1 is a booted Droidian and not some other host that owns that
# address. It is printed as evidence and deliberately decides nothing: it needs
# the phone to have finished starting sshd, so making the verdict depend on it
# would turn a slow boot into a reported absence.
# Bounded at 3s, not the 10s the other probes get: a banner arrives on connect
# or not at all, and a blackholing address otherwise burns the full SYN-retry
# budget here for a line that decides nothing.
if [ "$PING_OK" = "yes" ]; then
    BANNER=$(timeout 3 bash -c "exec 3<>/dev/tcp/$PHONE_IP/22 && head -c 40 <&3" 2>/dev/null)
    [ -n "$BANNER" ] \
        && echo "  ssh       banner from $PHONE_IP: $BANNER   (evidence only, not part of the verdict)" \
        || echo "  ssh       no banner on $PHONE_IP:22        (evidence only, not part of the verdict)"
fi

# ---- verdict ---------------------------------------------------------------
echo
echo "== verdict =="

verdict() { echo "VERDICT: $1"; }

# EDL first: it outranks everything else, because the response to it is "stop
# touching the device", not "carry on with the flash".
if [ -n "$USB_EDL" ]; then
    verdict "EDL -- the device is in Qualcomm emergency download mode (9008)"
    echo "  $USB_EDL"
    echo "  Do NOT write xbl/abl or touch the partition table. This is the one"
    echo "  unrecoverable failure mode on spacewar; see docs/flash-procedure.md."
    exit 5
fi

# INCOMPLETE before any conclusion. ABSENT requires that the instruments which
# could have seen the device actually RAN: link and ping for the booted case,
# and at least one of fastboot/adb/lsusb for the bootloader case. Without that
# last one, "no RNDIS link" cannot be told apart from "sitting in fastboot".
MISSING=""
[ "$LINK_M" = "yes" ] || MISSING="$MISSING ip"
[ "$PING_M" = "yes" ] || MISSING="$MISSING ping"
if [ "$FB_M" != "yes" ] && [ "$ADB_M" != "yes" ] && [ "$USB_M" != "yes" ]; then
    MISSING="$MISSING fastboot-or-adb-or-lsusb"
fi
if [ -n "$MISSING" ]; then
    verdict "INCOMPLETE -- nothing can be concluded on this host"
    echo "  unmeasured:$MISSING"
    echo "  This is NOT 'the device is absent'. Run it on the USB host (the Pi)."
    exit 2
fi

L=no; [ -n "$LINK_UP_IFS" ] && L=yes
P=$PING_OK
F=no; [ -n "$FB_SERIALS" ] && F=yes
A=no; [ -n "$ADB_LINES" ]  && A=yes
C=no; [ -n "$USB_CANDS" ]  && C=yes

# Disagreements are checked BEFORE the positive verdicts, so a conflict can
# never be resolved into a confident answer. Each arm is separately reportable;
# selftest-device-presence.sh has a state that fires each one alone.
DIS=""
[ "$L" = yes ] && [ "$P" != yes ] && \
    DIS="$DIS"$'\n'"  RNDIS link up ($LINK_UP_IFS ) but $PHONE_IP does not answer: the medium is up and nothing is on it (a boot still in progress looks like this)"
[ "$P" = yes ] && [ "$L" != yes ] && \
    DIS="$DIS"$'\n'"  $PHONE_IP answers but no exactly-named RNDIS link is up: something OTHER than the phone owns that address on this host"
{ [ "$L" = yes ] || [ "$P" = yes ]; } && { [ "$F" = yes ] || [ "$A" = yes ]; } && \
    DIS="$DIS"$'\n'"  a booted-Droidian signal and a bootloader/adb signal are present at once; one device cannot be in both states"
[ "$F" = yes ] && [ "$A" = yes ] && \
    DIS="$DIS"$'\n'"  fastboot AND adb both list a device; one device cannot be in both, so at least one instrument is lying"
[ "$C" = yes ] && [ "$F" != yes ] && [ "$A" != yes ] && [ "$L" != yes ] && \
    DIS="$DIS"$'\n'"  lsusb matches a candidate device that fastboot and adb both fail to see: most likely udev permissions (try sudo) or an unexpected USB mode"
# THERE IS DELIBERATELY NO "fastboot names a serial but lsusb sees nothing"
# ARM. It was written and then removed, measured: it counted devices that are
# not ROOT hubs, and the Pi's own baseline already has a VIA Labs hub on the
# bus, so the count is never 0 there and the arm could never fire. Any host
# with a keyboard attached is the same. It would have read as "fastboot is
# cross-checked against lsusb" while really meaning "cross-checked only on a
# host with nothing whatsoever plugged in" -- a stated scope wider than the
# real one, which is the defect this whole file exists to avoid. The
# fabrication case that CAN be measured is the arm above (lsusb sees a
# candidate that fastboot and adb do not) and the fastboot-and-adb arm.

if [ -n "$DIS" ]; then
    verdict "DISAGREEMENT -- the signals conflict; do not act on any one of them"
    printf '%s\n' "${DIS#$'\n'}"
    exit 4
fi

if [ "$L" = yes ] && [ "$P" = yes ]; then
    verdict "PRESENT / BOOTED -- RNDIS link$LINK_UP_IFS is up AND $PHONE_IP answers"
    echo "  ssh droidian@$PHONE_IP   (password 1234)"
    exit 0
fi
if [ "$F" = yes ]; then
    verdict "PRESENT / FASTBOOT -- $(printf '%s' "$FB_SERIALS" | tr '\n' ' ')"
    echo "  Check the slot before flashing: fastboot getvar current-slot"
    exit 0
fi
if [ "$A" = yes ]; then
    verdict "PRESENT / ADB -- $(printf '%s' "$ADB_LINES" | tr '\n' ',')"
    echo "  'unauthorized' still means PRESENT: stock recovery on spacewar draws"
    echo "  no authorisation prompt, so adb cannot be driven from here."
    exit 0
fi

verdict "ABSENT -- every instrument ran and none of them sees the device"
[ -n "$LINK_DOWN_IFS" ] && \
    echo "  (interfaces$LINK_DOWN_IFS exist but have no carrier, which is not presence)"
echo "  lsusb candidate ids used:$CANDIDATE_IDS -- UNVERIFIED against the real"
echo "  device, so this arm is a cross-check only and no part of this ABSENT"
echo "  verdict rests on it."
exit 3
