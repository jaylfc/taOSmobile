#!/bin/bash
# Verify taos-kiosk-launch.sh: that it resolves the kiosk URL from shell.conf,
# that every bad case lands on the first-run helper rather than a dead page, and
# that it refuses the display instead of sitting on an error screen.
#
# Exit 0 PASS, 1 FAIL, 2 INCOMPLETE (could not measure -- NOT a pass).
#
# THE POSITIVE CONTROLS, AND WHY THEY ARE THESE ONES
# --------------------------------------------------
# Most expected answers below are the first-run helper URL, because that is what
# every bad config falls back to. So a launcher that ignored shell.conf entirely
# and printed the helper unconditionally would pass most of this file. That is
# the green-and-blind case here, and it is a DIFFERENT one from the helper's
# check script, where the trap was that a dead process refuses everything.
#
# Control 1 therefore proves the launcher DISCRIMINATES: a good remote config
# yields the configured URL and a local config yields the controller. Until both
# hold, no fallback check below means anything.
#
# Control 2 proves the readiness probe can say yes. "Exits 3 when nothing is
# listening" is trivially satisfied by a script that always exits 3.
#
# Run from anywhere; needs nothing on the device.

set -u

LAUNCH="$(dirname "$(readlink -f "$0")")/taos-kiosk-launch.sh"
UNIT="$(dirname "$(readlink -f "$0")")/taos-kiosk.service"
WORK="$(mktemp -d)"
CONF="$WORK/shell.conf"
PASS=0; FAIL=0
LISTEN_PID=""

cleanup() {
    [ -n "$LISTEN_PID" ] && kill "$LISTEN_PID" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
give_up() { echo "INCOMPLETE: $1"; exit 2; }

command -v python3 >/dev/null || give_up "python3 not present"
[ -f "$LAUNCH" ]              || give_up "launcher not found at $LAUNCH"
[ -x "$LAUNCH" ]              || give_up "launcher is not executable"
bash -n "$LAUNCH"             || give_up "launcher does not parse"

HELPER_URL="http://127.0.0.1:6970/"
LOCAL_URL="http://localhost:6969/"

# conf <lines...>  -- write a shell.conf for the next resolution
conf() { printf '%s\n' "$@" > "$CONF"; }
# url_for  -- resolve with the current shell.conf
url_for() { TAOS_SHELL_CONF="$CONF" "$LAUNCH" --print-url 2>/dev/null; }

want() {  # want <label> <expected> <actual>
    if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want $2, got $3)"; fi
}

echo "== positive control 1: the launcher actually reads the config =="
conf "mode=remote" "url=http://box.local:6969"
got="$(url_for)"
want "mode=remote yields the configured URL" "http://box.local:6969" "$got"
[ "$got" = "http://box.local:6969" ] || \
    give_up "control: launcher ignores a valid remote config, so every fallback check below is meaningless"
conf "mode=local"
got="$(url_for)"
want "mode=local yields the local controller" "$LOCAL_URL" "$got"
[ "$got" = "$LOCAL_URL" ] || \
    give_up "control: launcher does not distinguish local mode; fallback checks would pass on a constant"

echo "== every unusable config lands on the first-run helper, never on :6969 =="
# The direction of these fallbacks is the safety property. A phone with no
# keyboard can escape the helper screen; it cannot escape a controller that is
# not there.
rm -f "$CONF"
want "missing file -> helper"            "$HELPER_URL" "$(url_for)"
conf ""
want "empty file -> helper"              "$HELPER_URL" "$(url_for)"
conf "# only a comment"
want "comments only -> helper"           "$HELPER_URL" "$(url_for)"
conf "url=http://box.local:6969"
want "url but no mode -> helper"         "$HELPER_URL" "$(url_for)"
conf "mode="
want "empty mode -> helper"              "$HELPER_URL" "$(url_for)"
conf "mode=sideways"
want "unknown mode -> helper"            "$HELPER_URL" "$(url_for)"
conf "mode=remote"
want "remote without url -> helper"      "$HELPER_URL" "$(url_for)"
conf "mode=remote" "url="
want "remote with empty url -> helper"   "$HELPER_URL" "$(url_for)"
conf "mode=remote" "url=not a url"
want "remote with junk url -> helper"    "$HELPER_URL" "$(url_for)"
conf "mode=remote" "url=javascript:alert(1)"
want "remote with javascript: -> helper" "$HELPER_URL" "$(url_for)"
conf "mode=remote" "url=file:///etc/passwd"
want "remote with file: -> helper"       "$HELPER_URL" "$(url_for)"
conf "this line has no equals sign"
want "malformed line -> helper"          "$HELPER_URL" "$(url_for)"

echo "== a bad line in LOCAL mode still reaches the controller =="
# Deliberately NOT the helper: local mode has a controller, and :6969 is a
# better answer than sending a configured device back to first-run setup.
conf "mode=local" "url=not a url"
want "local with junk url -> controller" "$LOCAL_URL" "$(url_for)"
conf "mode=local" "url=http://localhost:6969"
want "local honours an explicit url"     "http://localhost:6969" "$(url_for)"

echo "== whitespace and comments are tolerated, not silently mis-parsed =="
conf "# leading comment" "" "  mode = remote  " "  url = http://box.local:6969  "
want "padded keys and values parse"      "http://box.local:6969" "$(url_for)"

echo "== the URL cannot smuggle a second chromium argument =="
# --unsafely-treat-insecure-origin-as-secure takes a COMMA-SEPARATED list, so a
# comma in the URL would grant a secure context to an origin nobody chose.
conf "mode=remote" "url=http://a.example,http://evil.example"
want "comma in url refused -> helper"    "$HELPER_URL" "$(url_for)"
conf "mode=remote" "url=http://a.example --disable-web-security"
want "space in url refused -> helper"    "$HELPER_URL" "$(url_for)"

echo "== the launched command line follows the resolved URL =="
argv_for() { TAOS_SHELL_CONF="$CONF" "$LAUNCH" --print-argv 2>/dev/null; }
conf "mode=remote" "url=http://box.local:6969/x?a=1"
A="$(argv_for)"
grep -qx -- "--app=http://box.local:6969/x?a=1" <<<"$A" \
    && ok "--app carries the resolved URL" || bad "--app does not carry the resolved URL"
grep -qx -- "--unsafely-treat-insecure-origin-as-secure=http://box.local:6969" <<<"$A" \
    && ok "insecure-origin flag names the ORIGIN, not the URL" \
    || bad "insecure-origin flag wrong: $(grep -- '--unsafely' <<<"$A")"
grep -q "localhost:6969" <<<"$A" \
    && bad "the old hardcoded :6969 is still in the command line" \
    || ok "no hardcoded :6969 left in the command line"
conf "mode=remote" "url=https://box.example/"
grep -q -- "--unsafely-treat-insecure-origin-as-secure" <<<"$(argv_for)" \
    && bad "https origin was given the insecure-origin flag" \
    || ok "https origin gets no insecure-origin flag"

echo "== readiness: refuse the display rather than show a dead page =="
free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }

# Always call the launcher's readiness paths through an OUTER timeout. The probe
# has its own budget, but if that budget ever stops being enforced the check
# would hang, and a hanging check is not a red -- it is an instrument that
# stopped reporting. 124 is timeout's own exit code and is reported as such.
preflight() { timeout 25 env "$@" "$LAUNCH" --preflight >/dev/null 2>&1; }
LIVE_PORT="$(free_port)"
python3 -c "
import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(5)
while True: s.accept()
" "$LIVE_PORT" >/dev/null 2>&1 & LISTEN_PID=$!
for _ in $(seq 1 50); do
    (exec 3<>"/dev/tcp/127.0.0.1/$LIVE_PORT") 2>/dev/null && break
    sleep 0.1
done

# Control 2: with something listening, preflight must succeed.
rm -f "$CONF"
preflight TAOS_SHELL_CONF="$CONF" TAOS_FIRSTRUN_PORT="$LIVE_PORT" TAOS_KIOSK_WAIT=3
rc=$?
[ "$rc" = "124" ] && give_up "control: preflight HUNG against a live listener; the readiness budget is not being enforced"
if [ "$rc" != "0" ]; then
    give_up "control: preflight failed ($rc) against a LIVE listener, so an exit 3 below would prove nothing"
fi
ok "preflight succeeds when the helper is listening"

DEAD_PORT="$(free_port)"
preflight TAOS_SHELL_CONF="$CONF" TAOS_FIRSTRUN_PORT="$DEAD_PORT" TAOS_KIOSK_WAIT=2
want "dead helper -> exit 3, not a launch" 3 "$?"

conf "mode=local"
preflight TAOS_SHELL_CONF="$CONF" TAOS_KIOSK_WAIT=2
rc=$?
# :6969 may genuinely be up on a workstation running taOS; either answer is
# correct, but nothing else is.
case "$rc" in
    3) ok "dead local controller -> exit 3, not a launch" ;;
    0) ok "local controller is up on this host; readiness passed" ;;
    124) bad "local mode preflight HUNG; readiness budget not enforced" ;;
    *) bad "local mode preflight returned $rc (want 0 or 3)" ;;
esac

echo "== an unreachable REMOTE controller is NOT gated =="
# On purpose. A phone in remote mode is a surface whose network comes and goes;
# handing the display back to Phosh on a blip is worse than the unreachable
# state requirement 2 already puts in the UI. Only loopback targets are gated.
# 192.0.2.1 is TEST-NET-1: it BLACKHOLES rather than refusing, so if this target
# ever became gated the connect would stall on SYN retries. The outer timeout
# turns that into a red instead of a hung test.
conf "mode=remote" "url=http://192.0.2.1:6969"
preflight TAOS_SHELL_CONF="$CONF" TAOS_KIOSK_WAIT=2
rc=$?
case "$rc" in
    0)   ok "unreachable remote still launches" ;;
    124) bad "remote target was GATED and the probe hung on a blackholed address" ;;
    *)   bad "unreachable remote returned $rc, want 0 (remote must not be gated)" ;;
esac

echo "== a bad argument is rejected, and is not retryable =="
"$LAUNCH" --wat >/dev/null 2>&1
want "unknown argument -> exit 64" 64 "$?"

echo "== the unit actually calls the launcher and can fail over =="
if [ -f "$UNIT" ]; then
    grep -q "^ExecStart=/usr/local/lib/taos/taos-kiosk-launch.sh$" "$UNIT" \
        && ok "unit ExecStart calls the launcher" || bad "unit ExecStart does not call the launcher"
    grep -q -- "--app=http://localhost:6969" "$UNIT" \
        && bad "unit still hardcodes the :6969 app URL" || ok "unit no longer hardcodes the app URL"
    # Without this, Restart=on-failure parks the unit in auto-restart instead of
    # failed, and OnFailure= never hands the display back -- the exact "dead
    # screen on a keyboard-less device" this card exists to avoid.
    grep -q "^RestartPreventExitStatus=3 64$" "$UNIT" \
        && ok "exit 3 and 64 reach the failed state" \
        || bad "RestartPreventExitStatus missing: exit 3 would flap, never reaching OnFailure"
    grep -q "^Wants=taos-firstrun.service$" "$UNIT" \
        && ok "unit wants the first-run helper" || bad "unit does not want the first-run helper"
else
    bad "taos-kiosk.service not found next to the launcher"
fi

echo
echo "checks passed: $PASS   failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
