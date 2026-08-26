#!/bin/bash
# Recover the taOS kiosk from a CSRF lockout without a keyboard (taOS#2081).
#
# The bug: verify_csrf is attached router-wide, and its exemption is "no
# taos_session cookie -> skip". A *stale* cookie therefore turns a correct PIN
# into 403 {"detail":"CSRF token missing"}, which pin-panel.js renders as
# "Incorrect PIN." On a keyboard-less kiosk the user has no way to clear the
# cookie, so the device is locked out until someone SSHes in.
#
# The ephemeral profile in taos-kiosk.service covers the half of this bug that
# is present at BOOT. It does not cover a session that lapses while the kiosk
# is RUNNING: the cookie is there, it looks valid, and pre-#2543 that is a 403
# with no reboot in between. This guard covers that half. It watches the
# controller's access log and, on a 403 to a credential-establishing route,
# restarts the kiosk -- which wipes the profile, and with it the cookie.
#
# It refuses to run blind: see "self-proof" below.
set -uo pipefail

CONTROLLER_UNIT="${TAOS_CONTROLLER_UNIT:-taos-controller.service}"
KIOSK_UNIT="${TAOS_KIOSK_UNIT:-taos-kiosk.service}"
PORT="${TAOS_PORT:-6969}"
COOLDOWN="${TAOS_GUARD_COOLDOWN:-60}"
SELFTEST_TIMEOUT="${TAOS_GUARD_SELFTEST_TIMEOUT:-20}"

# Credential-establishing routes: a form/JSON POST that MINTS a session can
# never carry an X-CSRF-Token header, because there is no authenticated page
# to have handed one out yet. @taOS-dev found a second instance of exactly
# this shape at POST /setup/complete, so match the class, not one path.
LOCKOUT_RE='"POST /(auth/(pin-)?login|auth/setup|auth/complete|setup/complete)[^"]*" 403'
# Any access-log line at all -- used only by the self-proof.
ACCESSLOG_RE='"(GET|POST|PUT|DELETE) [^"]*" [0-9]{3}'

log() { echo "taos-kiosk-csrf-guard: $*"; }

wait_for_controller() {
    local waited=0
    until (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; do
        [ "$waited" -ge 120 ] && { log "controller never opened :$PORT"; return 1; }
        sleep 3; waited=$((waited+3))
    done
    exec 3<&- || true
    return 0
}

# --- self-proof -------------------------------------------------------------
# This guard has exactly one input: uvicorn's access log, on the controller's
# journal. If access logging is off, or the unit name is wrong, or journald is
# not readable, then the guard sits ACTIVE forever and detects nothing -- an
# instrument reporting green while measuring nothing, which is the failure mode
# the doc gate's Layer A0 exists to refuse. So prove the channel end to end
# with a signal we generate ourselves, and FAIL the unit if it is not there.
self_proof() {
    local since; since="$(date '+%Y-%m-%d %H:%M:%S')"
    sleep 1
    curl -s -o /dev/null -m 5 "http://127.0.0.1:$PORT/" || true
    local waited=0
    while [ "$waited" -lt "$SELFTEST_TIMEOUT" ]; do
        if journalctl -u "$CONTROLLER_UNIT" --since "$since" --no-pager --output=cat 2>/dev/null \
             | grep -Eq "$ACCESSLOG_RE"; then
            log "self-proof OK: access-log lines are readable from $CONTROLLER_UNIT"
            return 0
        fi
        sleep 2; waited=$((waited+2))
    done
    log "SELF-PROOF FAILED: generated a request to :$PORT and saw no access-log"
    log "line on $CONTROLLER_UNIT within ${SELFTEST_TIMEOUT}s. This guard cannot"
    log "detect anything in that state, so it is failing rather than pretending."
    log "Check: uvicorn access logging enabled? correct unit name? journal readable?"
    return 1
}

main() {
    wait_for_controller || exit 1
    self_proof || exit 1

    log "watching $CONTROLLER_UNIT for CSRF lockouts on credential routes"
    local last=0 now
    journalctl -u "$CONTROLLER_UNIT" -f -n 0 --output=cat 2>/dev/null |
    while IFS= read -r line; do
        [[ "$line" =~ $LOCKOUT_RE ]] || continue

        # Never take the display. If the kiosk is not running, Phosh is, and
        # restarting the kiosk here would steal the screen out from under it.
        if ! systemctl is-active --quiet "$KIOSK_UNIT"; then
            log "lockout seen but $KIOSK_UNIT is not active; not touching the display"
            continue
        fi

        now=$(date +%s)
        if [ $((now - last)) -lt "$COOLDOWN" ]; then
            log "lockout seen again within ${COOLDOWN}s; already restarted, ignoring"
            continue
        fi
        last=$now

        log "CSRF lockout (taOS#2081): $line"
        log "restarting $KIOSK_UNIT to drop the stale cookie with the profile"
        systemctl restart "$KIOSK_UNIT" \
            || log "restart of $KIOSK_UNIT FAILED; the user is still locked out"
    done

    # The follow only ends if journalctl died. Exiting non-zero is what makes
    # systemd's Restart=on-failure put the guard back; exiting 0 here would
    # leave the unit "inactive (dead)" and the device silently unguarded.
    log "journal follow ended unexpectedly"
    return 1
}

main "$@"
