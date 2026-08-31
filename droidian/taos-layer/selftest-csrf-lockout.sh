#!/bin/bash
# Red-proof harness for check-csrf-lockout.sh -- tsk-cupx7r.
#
# Run on a WORKSTATION, not the device. It needs no taOS, no controller and no
# root: a stub controller (csrf-stub-controller.py) supplies the HTTP side and
# two throwaway systemd USER units supply the unit side.
#
# Why this exists. cmt-ilsmt7 on tsk-ame3lw recorded that check-csrf-lockout.sh
# had been red-proven against a stub in five named states before it was called
# done. The stub was thrown away, so not one of the five could be re-run -- and
# those five states are the ONLY evidence that suite is not vacuous. b0cc558 and
# d4a1935 then each found a state nobody could re-run that had gone silently
# wrong: an absent controller, and a 5xx from a proxy, both reading as PASS on
# four checks. A proof that cannot be re-executed decays into a sentence, and a
# sentence that reads as satisfied is this repo's recurring failure mode.
#
#   exit 0  every state reproduced
#   exit 1  a state did NOT reproduce -- check-csrf-lockout.sh has drifted, or
#           this harness has. Either way the five states are no longer proven.
#   exit 2  INCOMPLETE -- this machine could not host the fixtures, so nothing
#           was measured. Never reported as a pass.
#
# Each state asserts the exit code AND named lines of output, as separate
# assertions, so no single arm can carry another. Several states also assert
# lines that must be ABSENT: the failure this file guards against is a check
# that passes for the wrong reason, and "expected text is present" cannot see
# that on its own.
#
# Before each state runs, the harness probes the fixture ITSELF and refuses to
# read a result out of a fixture that is not in the state it claims -- a mutant
# that did not apply is not a green, measured twice on 2026-08-30.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE="$HERE/check-csrf-lockout.sh"
STUB="$HERE/csrf-stub-controller.py"

PIN="4242"
USERNAME="kiosk"
PASSWORD="hunter2"
STALE='taos_session=stale-cookie-from-a-lapsed-session'

states_run=0; states_ok=0; states_bad=0; states_incomplete=0
states_bad_this=0; STUB_PORT=""

incomplete() { echo; echo "INCOMPLETE: $*"; exit 2; }

# --- preflight --------------------------------------------------------------
[ -x "$SUITE" ] || incomplete "$SUITE is missing or not executable"
[ -r "$STUB" ]  || incomplete "$STUB is missing"
command -v python3 >/dev/null || incomplete "python3 not on PATH"
command -v curl    >/dev/null || incomplete "curl not on PATH"
REAL_SYSTEMCTL="$(command -v systemctl || true)"
[ -n "$REAL_SYSTEMCTL" ] || incomplete "systemctl not on PATH"
# `is-system-running` exits non-zero for "degraded", which is normal and is not
# a reason to refuse; what matters is that the user manager ANSWERS.
"$REAL_SYSTEMCTL" --user is-system-running >/dev/null 2>&1 \
  || "$REAL_SYSTEMCTL" --user show -p Version >/dev/null 2>&1 \
  || incomplete "no systemd user manager here. Sections 4 and 5 of the suite ask
systemd directly and there is no honest way to answer for it, so this harness
refuses rather than faking one. Run it on a machine with a user session."

TMP="$(mktemp -d)"
SUFFIX="selftest-$$"
UNIT_DIR="$HOME/.config/systemd/user"
KIOSK_UNIT="taos-kiosk-$SUFFIX.service"
GUARD_UNIT="taos-kiosk-csrf-guard-$SUFFIX.service"
ABSENT_GUARD_UNIT="taos-kiosk-csrf-guard-absent-$SUFFIX.service"
PROFILE="/run/taos-kiosk-profile-$SUFFIX"
PERSIST_PROFILE="/var/lib/taos-kiosk-profile-$SUFFIX"
STUB_PID=""

cleanup() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    "$REAL_SYSTEMCTL" --user stop "$GUARD_UNIT" >/dev/null 2>&1
    rm -f "$UNIT_DIR/$KIOSK_UNIT" "$UNIT_DIR/$GUARD_UNIT"
    "$REAL_SYSTEMCTL" --user daemon-reload >/dev/null 2>&1
    rm -rf "$TMP"
}
trap cleanup EXIT

# The suite calls bare `systemctl`, meaning the SYSTEM manager. A workstation
# selftest has no business installing units there, so a shim on PATH re-points
# it at the user manager. This is a redirect, not a fake: real systemd still
# parses the units and still decides what `is-active` and `list-unit-files`
# return, so their exit-code semantics are not this harness's guesses.
# (`systemd-analyze verify` being blind to RestartPreventExitStatus is the
# standing reason we do not hand-roll systemd's answers.)
mkdir -p "$TMP/bin"
cat > "$TMP/bin/systemctl" <<SHIM
#!/bin/sh
exec "$REAL_SYSTEMCTL" --user "\$@"
SHIM
chmod +x "$TMP/bin/systemctl"

# --- fixtures: two throwaway user units -------------------------------------
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$KIOSK_UNIT" <<UNIT
[Unit]
Description=selftest stand-in for taos-kiosk.service (never started)
[Service]
Type=simple
ExecStart=/bin/echo chromium --kiosk --user-data-dir=$PROFILE
UNIT
cat > "$UNIT_DIR/$GUARD_UNIT" <<UNIT
[Unit]
Description=selftest stand-in for taos-kiosk-csrf-guard.service
[Service]
Type=simple
ExecStart=/bin/sleep 3600
UNIT
# Section 4 has three arms and the unit above only exercises one. These two
# stand-ins cover the other two. They are read as files, never loaded, so they
# live in the temp dir rather than in the user manager.
cat > "$TMP/kiosk-persistent.service" <<UNIT
[Unit]
Description=selftest stand-in: kiosk whose profile SURVIVES a reboot
[Service]
ExecStart=/bin/echo chromium --kiosk --user-data-dir=$PERSIST_PROFILE
UNIT
cat > "$TMP/kiosk-noprofile.service" <<UNIT
[Unit]
Description=selftest stand-in: kiosk that names no profile at all
[Service]
ExecStart=/bin/echo chromium --kiosk
UNIT

"$REAL_SYSTEMCTL" --user daemon-reload >/dev/null 2>&1 \
    || incomplete "systemctl --user daemon-reload failed"
"$REAL_SYSTEMCTL" --user start "$GUARD_UNIT" >/dev/null 2>&1 \
    || incomplete "could not start the stand-in guard unit $GUARD_UNIT"

# Read every fixture back rather than trusting the start's exit code -- an exit
# code reports that the REQUEST was accepted, not that the state changed.
"$REAL_SYSTEMCTL" --user is-active --quiet "$GUARD_UNIT" \
    || incomplete "$GUARD_UNIT did not become active; section 5 could not be set up"
if "$REAL_SYSTEMCTL" --user is-active --quiet "$KIOSK_UNIT"; then
    incomplete "$KIOSK_UNIT is active; section 4's profile-absent arm needs it stopped"
fi
if "$REAL_SYSTEMCTL" --user list-unit-files "$ABSENT_GUARD_UNIT" >/dev/null 2>&1; then
    incomplete "$ABSENT_GUARD_UNIT exists; the guard-absent state cannot be set up"
fi
if [ -e "$PROFILE" ]; then
    incomplete "$PROFILE already exists; section 4's profile-absent arm cannot be set up"
fi

# --- harness plumbing -------------------------------------------------------
BASE=""; OUT=""; RC=0; STATE=""; fixture_broken=0

start_stub() {  # start_stub MODE
    local mode="$1" waited=0 port=""
    : > "$TMP/port"
    STUB_MODE="$mode" STUB_PIN="$PIN" STUB_USERNAME="$USERNAME" STUB_PASSWORD="$PASSWORD" \
        python3 "$STUB" >"$TMP/port" 2>"$TMP/stub.err" &
    STUB_PID=$!
    while [ "$waited" -lt 100 ]; do
        port=$(head -1 "$TMP/port" 2>/dev/null | tr -dc '0-9')
        [ -n "$port" ] && break
        kill -0 "$STUB_PID" 2>/dev/null || break
        sleep 0.1; waited=$((waited+1))
    done
    if [ -z "$port" ]; then
        echo "    FIXTURE BROKEN: stub ($mode) never reported a port"
        sed 's/^/      stub stderr: /' "$TMP/stub.err"
        fixture_broken=1
        return 1
    fi
    BASE="http://127.0.0.1:$port"
    STUB_PORT="$port"
}

stop_stub() {
    if [ -n "$STUB_PID" ]; then
        kill "$STUB_PID" 2>/dev/null
        wait "$STUB_PID" 2>/dev/null
    fi
    STUB_PID=""
}

probe() {  # probe PATH [extra curl args...] -> prints the status code
    local path="$1"; shift
    curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST \
        -H 'Content-Type: application/json' "$@" "$BASE$path" 2>/dev/null
}

fixture() {  # fixture DESC EXPECTED ACTUAL
    if [ "$3" = "$2" ]; then
        echo "    fixture ok:  $1 -> $3"
    else
        echo "    FIXTURE BROKEN: $1 -> $3, expected $2"
        fixture_broken=1
    fi
}

state() { STATE="$1"; fixture_broken=0; echo; echo "== state: $STATE =="; }

run_suite() {  # run_suite [NAME=VALUE ...]
    OUT=$(env PATH="$TMP/bin:$PATH" \
        TAOS_BASE="$BASE" TAOS_PORT="$STUB_PORT" \
        TAOS_KIOSK_UNIT="$KIOSK_UNIT" TAOS_KIOSK_UNIT_PATH="$UNIT_DIR/$KIOSK_UNIT" \
        TAOS_GUARD_UNIT="$GUARD_UNIT" \
        "$@" timeout 120 "$SUITE" 2>&1)
    RC=$?
    if [ "$RC" -eq 124 ]; then
        echo "    FAIL  the suite HUNG (timeout 120s). A hang is not a red; it is an"
        echo "          instrument that stopped reporting."
    fi
}

s_ok()  { echo "    ok    $*"; }
s_bad() { echo "    FAIL  $*"; states_bad_this=$((states_bad_this+1)); }

must_rc()  { if [ "$RC" = "$1" ]; then s_ok "exit $RC"; else s_bad "exit $RC, expected $1"; fi; }
must()     { if grep -qF -- "$1" <<<"$OUT"; then s_ok "says: $1"; else s_bad "MISSING: $1"; fi; }
must_not() { if grep -qF -- "$1" <<<"$OUT"; then s_bad "must NOT say: $1"; else s_ok "silent on: $1"; fi; }

begin() { states_bad_this=0; }
end() {
    states_run=$((states_run+1))
    if [ "$fixture_broken" -ne 0 ]; then
        echo "    -> INCOMPLETE: the fixture was not in the state this run claims,"
        echo "       so its result is not evidence either way."
        states_incomplete=$((states_incomplete+1))
    elif [ "$states_bad_this" -eq 0 ]; then
        states_ok=$((states_ok+1))
    else
        echo "    ---- suite output ----"
        sed 's/^/    | /' <<<"$OUT"
        echo "    ----------------------"
        states_bad=$((states_bad+1))
    fi
    stop_stub
}

# ============================================================================
# state 0 -- the self-proof itself (section 0, added by b0cc558)
# Everything below is gated on telling "the controller answered" from "nothing
# is listening". Hand the suite a CLOSED_PORT that is in fact open and it must
# refuse to issue a verdict at all. Without this state, the one guard standing
# between the suite and a vacuous pass is itself unproven.
# ============================================================================
state "self-proof fires when the unreachable sentinel is reachable"
begin
if start_stub post2543; then
    fixture "something IS listening on the 'closed' port" 401 "$(probe /auth/pin-login --data '{}')"
    run_suite TAOS_CLOSED_PORT="$STUB_PORT" TAOS_PIN="$PIN"
    must_rc 3
    must "SELF-PROOF FAILED:"
    must "Refusing to"
    must_not "VERDICT:"
    must_not "checks:"
fi
end

# ============================================================================
# state 1 -- pre-#2543: taOS#2081 live. LOAD-BEARING.
# A stale cookie 403s a credential route, so a correct PIN renders as
# "Incorrect PIN." on a device with no keyboard to clear the cookie with.
# ============================================================================
state "pre-#2543: a stale cookie 403s the credential routes"
begin
if start_stub pre2543; then
    fixture "stale cookie -> 403"  403 "$(probe /auth/pin-login --data '{}' --cookie "$STALE")"
    fixture "no cookie -> not 403" 401 "$(probe /auth/pin-login --data '{}')"
    run_suite TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "/auth/pin-login -> 403 with a stale cookie: taOS#2081 is LIVE on this install"
    must "/auth/login -> 403 with a stale cookie: taOS#2081 is LIVE on this install"
    must "checks: 5 passed, 4 failed, 0 skipped"
    must "VERDICT: FAIL"
    # The bug is present, so the suite must not be able to say the opposite.
    must_not "with a stale cookie (not 403)"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 2 -- routes absent. LOAD-BEARING.
# Every check below section 1 would otherwise "pass" against a 404.
# ============================================================================
state "routes absent: a 404 is not a clean bill of health"
begin
if start_stub routes-absent; then
    fixture "pin-login -> 404" 404 "$(probe /auth/pin-login --data '{}')"
    fixture "login -> 404"     404 "$(probe /auth/login --data '{}')"
    run_suite TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "/auth/pin-login returns 404."
    must "/auth/login returns 404."
    must "do not read the rest as a clean bill of health."
    must "checks: 3 passed, 2 failed, 2 skipped"
    must "VERDICT: FAIL"
    # The gate held: sections 2 and 3 must have declined to measure, not passed.
    must_not "exists (POST with no cookie"
    must_not "with a stale cookie (not 403)"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 3 -- 5xx from a proxy. The regression test for d4a1935.
# Measured 2026-08-30 producing four green checks, two of them asserting the
# #2081 mitigation holds, with nothing behind the proxy to measure.
# ============================================================================
state "proxy up, controller down: a 5xx does not show the route exists"
begin
if start_stub proxy-down; then
    fixture "pin-login -> 502" 502 "$(probe /auth/pin-login --data '{}')"
    run_suite TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "A 5xx is what a proxy returns when the controller"
    must "checks: 3 passed, 2 failed, 2 skipped"
    must "VERDICT: FAIL"
    must_not "exists (POST with no cookie"
    must_not "with a stale cookie (not 403)"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 3b -- an unknown code. The ONLY state that reaches section 1's `*)`
# catch-all: 000, 404, 5xx and every success code land on an explicit arm, so
# without this the pre-d4a1935 `*) ok "$path exists"` -- the same vacuous pass
# d4a1935 fixed one arm above -- could be restored and every other state would
# stay green. Measured 2026-08-31: that mutant SURVIVED nine states.
# ============================================================================
state "path not routed for POST: a 405 does not show the route exists"
begin
if start_stub method-405; then
    fixture "pin-login -> 405" 405 "$(probe /auth/pin-login --data '{}')"
    fixture "login -> 405"     405 "$(probe /auth/login --data '{}')"
    run_suite TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "/auth/pin-login answered 405, which does not establish that the route exists"
    must "(405, say, means the path is not routed for POST). Refusing to measure"
    must "checks: 3 passed, 2 failed, 2 skipped"
    must "VERDICT: FAIL"
    must_not "exists (POST with no cookie"
    must_not "with a stale cookie (not 403)"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 4 -- post-#2543, CSRF side clean, but the running-half guard is absent.
# cmt-ilsmt7's "post-#2543 -> PASS on the CSRF checks" is exactly that and no
# more: the CSRF checks pass while the verdict does not, because a session that
# lapses while the kiosk is running still has no recovery.
# ============================================================================
state "post-#2543 but the guard is not installed: CSRF checks pass, verdict does not"
begin
if start_stub post2543; then
    fixture "stale cookie is irrelevant" 401 "$(probe /auth/pin-login --data '{}' --cookie "$STALE")"
    run_suite TAOS_GUARD_UNIT="$ABSENT_GUARD_UNIT" \
              TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "/auth/pin-login -> 401 with a stale cookie (not 403)"
    must "/auth/login -> 401 with a stale cookie (not 403)"
    must "is not installed; a session that lapses while the kiosk is"
    must "checks: 8 passed, 1 failed, 0 skipped"
    must "VERDICT: FAIL"
    must_not "taOS#2081 is LIVE"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 5 -- units installed, fix landed, credentials supplied: PASS (9/9).
# ============================================================================
state "post-#2543, units installed, credentials supplied: PASS 9/9"
begin
if start_stub post2543; then
    fixture "correct pin with a stale cookie -> 200" 200 \
        "$(probe /auth/pin-login --data "{\"pin\":\"$PIN\"}" --cookie "$STALE")"
    fixture "correct password with a stale cookie -> 200" 200 \
        "$(probe /auth/login --data "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" --cookie "$STALE")"
    run_suite TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 0
    must "pin-login with a stale cookie -> 200"
    must "password login with a stale cookie -> 200"
    must "$PROFILE absent while the kiosk is stopped"
    must "checks: 9 passed, 0 failed, 0 skipped"
    must "VERDICT: PASS"
    must_not "  FAIL "
    must_not "  SKIPPED "
fi
end

# ============================================================================
# state 6 -- the same, credentials withheld: INCOMPLETE, not PASS.
# The header's whole promise is that a check which did not RUN cannot be folded
# into a pass, so this state is what makes the previous one mean anything.
# ============================================================================
state "credentials withheld: INCOMPLETE, never PASS"
begin
if start_stub post2543; then
    fixture "stale cookie is irrelevant" 401 "$(probe /auth/pin-login --data '{}' --cookie "$STALE")"
    run_suite
    must_rc 2
    must "set TAOS_PIN to prove the PIN actually gets in"
    must "set TAOS_USERNAME and TAOS_PASSWORD to prove sign-in"
    must "checks: 7 passed, 0 failed, 2 skipped"
    must "VERDICT: INCOMPLETE"
    must_not "VERDICT: PASS"
    must_not "  FAIL "
fi
end

# ============================================================================
# state 7 -- boot half: the profile is NOT ephemeral.
# The ephemeral profile IS the boot-half mitigation: it is what makes a stale
# cookie unable to survive a reboot. Without a state here, section 4's /run
# test could be weakened to accept any absolute path and every other state
# would stay green -- measured, as mutant M7 below.
# ============================================================================
state "boot half: a profile outside /run survives a reboot"
begin
if start_stub post2543; then
    fixture "stand-in unit names a non-/run profile" 1 \
        "$(grep -c -- "--user-data-dir=$PERSIST_PROFILE" "$TMP/kiosk-persistent.service")"
    fixture "that profile is really outside /run" 0 \
        "$(grep -c -- '--user-data-dir=/run/' "$TMP/kiosk-persistent.service")"
    run_suite TAOS_KIOSK_UNIT_PATH="$TMP/kiosk-persistent.service" \
              TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "profile is $PERSIST_PROFILE, not under /run -- it survives a reboot"
    must "checks: 7 passed, 1 failed, 0 skipped"
    must "VERDICT: FAIL"
    must_not "tmpfs, gone at every boot"
    must_not "VERDICT: PASS"
fi
end

# ============================================================================
# state 8 -- boot half: no --user-data-dir at all, so Chromium falls back to
# ~/.config/chromium and the stale cookie outlives every reboot.
# ============================================================================
state "boot half: no --user-data-dir means the default persistent profile"
begin
if start_stub post2543; then
    fixture "stand-in unit assigns no profile" 0 \
        "$(grep -c -- '--user-data-dir=' "$TMP/kiosk-noprofile.service")"
    run_suite TAOS_KIOSK_UNIT_PATH="$TMP/kiosk-noprofile.service" \
              TAOS_PIN="$PIN" TAOS_USERNAME="$USERNAME" TAOS_PASSWORD="$PASSWORD"
    must_rc 1
    must "has no --user-data-dir: Chromium will use ~/.config/chromium,"
    must "checks: 7 passed, 1 failed, 0 skipped"
    must "VERDICT: FAIL"
    must_not "tmpfs, gone at every boot"
    must_not "VERDICT: PASS"
fi
end

# --- verdict ----------------------------------------------------------------
echo
echo "states: $states_run run, $states_ok reproduced, $states_bad drifted, $states_incomplete incomplete"
if [ "$states_bad" -gt 0 ]; then
    echo "VERDICT: FAIL -- check-csrf-lockout.sh no longer behaves as cmt-ilsmt7 recorded."
    echo "Either the suite drifted or this harness did; do not close tsk-ame3lw on it."
    exit 1
elif [ "$states_incomplete" -gt 0 ] || [ "$states_run" -eq 0 ]; then
    echo "VERDICT: INCOMPLETE -- a fixture was not in the state its run claimed, so"
    echo "that state measured nothing. This is not a pass."
    exit 2
fi
echo "VERDICT: PASS -- all $states_run states reproduced, verdict and exit code."
