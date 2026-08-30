#!/bin/bash
# Acceptance test for tsk-ame3lw: prove this device cannot be locked out of its
# own login screen by taOS#2081.
#
# Run ON THE DEVICE, after install-taos.sh. Exit 0 only if every check RAN and
# passed. A check that could not run makes the verdict INCOMPLETE, never PASS --
# the whole point of #2081's mitigation is that it must not be provable by
# something that measured nothing.
#
#   exit 0  PASS         every check ran and passed
#   exit 1  FAIL         something is wrong with the device
#   exit 2  INCOMPLETE   nothing failed, but a check did not run
#   exit 3  SELF-PROOF FAILED -- the suite cannot tell an absent controller from a
#           reachable one, so it issues no verdict. Not a verdict about the device.
#
# Background. verify_csrf's exemption was "no taos_session cookie -> skip", so a
# STALE cookie turned a correct PIN into 403 {"detail":"CSRF token missing"},
# which pin-panel.js rendered as "Incorrect PIN." Keyboard-less kiosk, no way to
# clear the cookie, unrecoverable by retrying. Upstream fix: taOS PR #2543,
# which exempts credential-establishing routes BY PATH. Our mitigation does not
# depend on that fix having landed:
#
#   boot half     -- taos-kiosk.service runs Chromium on an ephemeral /run
#                    profile, so a stale cookie cannot survive a reboot
#   running half  -- taos-kiosk-csrf-guard.service watches for the 403 and
#                    restarts the kiosk, which drops the cookie with the profile
#
# Both halves are checked here, plus the upstream fix itself, because a passing
# "no cookie at boot" says nothing about a session that lapses while the user is
# sitting in front of the device.
set -uo pipefail

PORT="${TAOS_PORT:-6969}"
BASE="${TAOS_BASE:-http://127.0.0.1:$PORT}"
KIOSK_UNIT="${TAOS_KIOSK_UNIT:-taos-kiosk.service}"
GUARD_UNIT="${TAOS_GUARD_UNIT:-taos-kiosk-csrf-guard.service}"
STALE='taos_session=stale-cookie-from-a-lapsed-session'

# Credentials are optional. Without them we can prove the request is not CSRF
# blocked, but not that the sign-in SUCCEEDS -- and that is a weaker claim, so
# it is reported as SKIPPED rather than quietly folded into a pass.
PIN="${TAOS_PIN:-}"
USERNAME="${TAOS_USERNAME:-}"
PASSWORD="${TAOS_PASSWORD:-}"

pass=0; fail=0; skip=0
ok()   { echo "  PASS    $*"; pass=$((pass+1)); }
bad()  { echo "  FAIL    $*"; fail=$((fail+1)); }
none() { echo "  SKIPPED $*"; skip=$((skip+1)); }
# Continuation line: prints, but is not a second failure. Otherwise the tally
# counts sentences instead of checks.
cont() { echo "          $*"; }
hdr()  { echo; echo "== $* =="; }

# 000 is curl's "no HTTP response at all" -- a refused connection or a timeout.
# The arms below MATCH on it, so it has to arrive as exactly three characters.
# curl prints it AND exits non-zero, so the trailing `|| echo 000` that used to be
# here appended a SECOND one: the result was "000000", which matched neither the
# 000 arm nor the 404 arm, fell through to the catch-all, and reported an absent
# controller as "route exists". Four checks then read green against nothing at all
# -- the exact vacuous pass this file's header promises to refuse. Normalise the
# code instead, and prove at every start (below) that the sentinel still lands.
UNREACHABLE=000

status_at() {  # status_at BASE METHOD PATH [--data BODY] [--cookie C]
    local base="$1" method="$2" path="$3"; shift 3
    local out
    out=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X "$method" \
        -H 'Content-Type: application/json' "$@" "$base$path" 2>/dev/null)
    case "$out" in
        [0-9][0-9][0-9]) echo "$out" ;;
        *)               echo "$UNREACHABLE" ;;
    esac
}

status() { status_at "$BASE" "$@"; }

# --- 0. self-proof: the unreachable sentinel still reaches the arms ----------
# Every check below is gated on telling "the controller answered" apart from
# "nothing is listening". When that distinction breaks, the gate opens and the
# suite reports PASS against an absent controller -- which is what it did until
# this was added. So prove the distinction rather than assume it: port 1 on
# loopback has nothing on it, and a closed loopback port REFUSES instantly, so
# this cannot turn into a wait the way a blackholed address would.
CLOSED_PORT="${TAOS_CLOSED_PORT:-1}"
sentinel=$(status_at "http://127.0.0.1:$CLOSED_PORT" POST /auth/pin-login --data '{}')
if [ "$sentinel" != "$UNREACHABLE" ]; then
    echo "SELF-PROOF FAILED: an unreachable controller yielded '$sentinel', not"
    echo "'$UNREACHABLE'. The 'unreachable' arm cannot fire, so every check below"
    echo "would read as PASS against a controller that is not running. Refusing to"
    echo "report a verdict at all -- a broken instrument must not issue one."
    exit 3
fi

# --- 1. the routes exist ----------------------------------------------------
# Without this, every check below "passes" against a 404 and proves nothing.
hdr "route reachability (guards against a vacuous pass)"
routes_ok=1
for path in /auth/pin-login /auth/login; do
    code=$(status POST "$path" --data '{}')
    # The PASS arm is an explicit list, and the catch-all REFUSES. Only the
    # controller itself answers a POST with an empty body this way -- it routed
    # the request and then validated, authenticated or accepted it. 403 belongs
    # here on purpose: it is #2081's own symptom, so it proves the route exists
    # and section 2 below is what judges it.
    #
    # This arm used to be `*) ok "$path exists"`, which is the same vacuous pass
    # b0cc558 fixed at the other end of this file. That commit normalised the
    # code so the 000 sentinel reaches the arms; it left the catch-all wide, so
    # the pass came straight back through any other code. Measured 2026-08-30
    # against a stub answering 502 for both paths -- a reverse proxy that is up
    # while the controller behind it is down, which is exactly how this device
    # fails in production:
    #     PASS  /auth/pin-login exists (POST with no cookie -> 502)
    #     PASS  /auth/pin-login -> 502 with a stale cookie (not 403)
    # Four green checks, two of them asserting the #2081 mitigation holds, with
    # nothing behind the proxy to measure. An unknown code is not evidence.
    case "$code" in
        000) bad "$path unreachable -- is the controller up on :$PORT?"; routes_ok=0 ;;
        404) bad "$path returns 404. This check cannot measure #2081 against a route"
             cont "that does not exist; do not read the rest as a clean bill of health."
             routes_ok=0 ;;
        200|204|302|303|400|401|403|422)
             ok  "$path exists (POST with no cookie -> $code)" ;;
        5*)  bad "$path answered $code. A 5xx is what a proxy returns when the controller"
             cont "behind it is DOWN, so it does not show the route exists. Refusing to"
             cont "measure #2081 against it."
             routes_ok=0 ;;
        *)   bad "$path answered $code, which does not establish that the route exists"
             cont "(405, say, means the path is not routed for POST). Refusing to measure"
             cont "#2081 against it."
             routes_ok=0 ;;
    esac
done

# --- 2. a stale cookie must not CSRF-block a credential route ---------------
hdr "stale cookie present -> credential routes are not CSRF-blocked"
if [ "$routes_ok" -eq 1 ]; then
    for path in /auth/pin-login /auth/login; do
        code=$(status POST "$path" --data '{}' --cookie "$STALE")
        if [ "$code" = "403" ]; then
            bad "$path -> 403 with a stale cookie: taOS#2081 is LIVE on this install"
            cont "(PR #2543 has not landed here). The kiosk mitigation must cover it."
        else
            ok "$path -> $code with a stale cookie (not 403)"
        fi
    done
else
    none "stale-cookie check: routes not reachable"
fi

# --- 3. the sign-in itself succeeds, stale cookie and all -------------------
# @taOS-dev, A2A 3409: asserting "not 403" is not the same as asserting the user
# gets in. Assert the real thing when we have something to sign in with.
hdr "sign-in SUCCEEDS with a stale cookie present"
if [ "$routes_ok" -ne 1 ]; then
    none "sign-in: routes not reachable"
else
    if [ -n "$PIN" ]; then
        code=$(status POST /auth/pin-login --data "{\"pin\":\"$PIN\"}" --cookie "$STALE")
        case "$code" in
            200|204|302|303) ok "pin-login with a stale cookie -> $code" ;;
            *) bad "pin-login with a stale cookie -> $code (expected a success)" ;;
        esac
    else
        none "pin surface: set TAOS_PIN to prove the PIN actually gets in"
    fi
    if [ -n "$USERNAME" ] && [ -n "$PASSWORD" ]; then
        code=$(status POST /auth/login \
            --data "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" \
            --cookie "$STALE")
        case "$code" in
            200|204|302|303) ok "password login with a stale cookie -> $code" ;;
            *) bad "password login with a stale cookie -> $code (expected a success)" ;;
        esac
    else
        none "password surface: set TAOS_USERNAME and TAOS_PASSWORD to prove sign-in"
    fi
fi

# --- 4. boot half: the kiosk profile is ephemeral ---------------------------
hdr "boot half: kiosk browser profile is ephemeral"
unit_path="${TAOS_KIOSK_UNIT_PATH:-/etc/systemd/system/$KIOSK_UNIT}"
if [ -r "$unit_path" ]; then
    profile=$(grep -o -- '--user-data-dir=[^ \\]*' "$unit_path" | head -1 | cut -d= -f2-)
    if [ -z "$profile" ]; then
        bad "$KIOSK_UNIT has no --user-data-dir: Chromium will use ~/.config/chromium,"
        cont "which persists a stale cookie across reboots"
    elif [[ "$profile" != /run/* ]]; then
        bad "profile is $profile, not under /run -- it survives a reboot"
    else
        ok "profile is $profile (tmpfs, gone at every boot)"
        if systemctl is-active --quiet "$KIOSK_UNIT"; then
            none "profile-absent-when-stopped: kiosk is running, not stopping it here"
        elif [ -e "$profile" ]; then
            bad "$KIOSK_UNIT is stopped but $profile still exists"
        else
            ok "$profile absent while the kiosk is stopped"
        fi
    fi
else
    none "kiosk unit not readable at $unit_path -- run install-taos.sh first"
fi

# --- 5. running half: the guard is armed ------------------------------------
# The guard fails its own start unless it can read access-log lines, so "active"
# here means the detection channel was proven end to end at its last start.
hdr "running half: lockout guard is armed"
if ! systemctl list-unit-files "$GUARD_UNIT" >/dev/null 2>&1 \
   || ! systemctl cat "$GUARD_UNIT" >/dev/null 2>&1; then
    bad "$GUARD_UNIT is not installed; a session that lapses while the kiosk is"
    cont "running has no recovery short of an SSH login"
elif systemctl is-active --quiet "$GUARD_UNIT"; then
    ok "$GUARD_UNIT active (its start-time self-proof passed)"
else
    bad "$GUARD_UNIT is installed but not active: $(systemctl is-active "$GUARD_UNIT")"
    cont "check 'journalctl -u $GUARD_UNIT' -- a failed self-proof lands here"
fi

# --- verdict ----------------------------------------------------------------
echo
echo "checks: $pass passed, $fail failed, $skip skipped"
if [ "$fail" -gt 0 ]; then
    echo "VERDICT: FAIL -- this device can still be locked out. tsk-ame3lw stays open."
    exit 1
elif [ "$skip" -gt 0 ]; then
    echo "VERDICT: INCOMPLETE -- nothing failed, but $skip check(s) did not run, so this"
    echo "is not proof. Supply what they name and re-run before closing tsk-ame3lw."
    exit 2
fi
echo "VERDICT: PASS -- lockout covered at boot and while running, sign-in proven."
