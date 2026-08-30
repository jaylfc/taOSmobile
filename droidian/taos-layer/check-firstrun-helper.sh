#!/bin/bash
# Verify taos-firstrun.py: that it forwards what it should, refuses everything
# else, and never lets a caller choose the upstream path or host.
#
# Exit 0 PASS, 1 FAIL, 2 INCOMPLETE (could not measure -- NOT a pass).
#
# The order matters. The POSITIVE CONTROL runs FIRST and must succeed, because
# every check after it is a rejection, and rejections are trivially satisfied by
# a helper that is simply broken. A dead process refuses path traversal
# perfectly. Without the control this script is the green-and-blind instrument
# it exists to prevent.

set -u

HELPER="$(dirname "$(readlink -f "$0")")/taos-firstrun.py"
WORK="$(mktemp -d)"
STUB_LOG="$WORK/stub.log"
CONF="$WORK/shell.conf"
PASS=0; FAIL=0
HELPER_PID=""; STUB_PID=""

cleanup() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    [ -n "$HELPER_PID" ] && kill "$HELPER_PID" 2>/dev/null
    rm -rf "$WORK"
}
trap cleanup EXIT

ok()   { PASS=$((PASS+1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL  $1"; }
give_up() { echo "INCOMPLETE: $1"; exit 2; }

check() {  # check <label> <expected-code> <actual-code>
    if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (want $2, got $3)"; fi
}

command -v python3 >/dev/null || give_up "python3 not present"
command -v curl >/dev/null    || give_up "curl not present"
[ -f "$HELPER" ]              || give_up "helper not found at $HELPER"

free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
STUB_PORT="$(free_port)"; HELP_PORT="$(free_port)"

# --- stub upstream: records every path it is asked for -------------------
cat > "$WORK/stub.py" <<'PYEOF'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
LOG = sys.argv[2]
class H(BaseHTTPRequestHandler):
    def _note(self):
        with open(LOG, "a") as f:
            f.write(self.command + " " + self.path + "\n")
        # 418 is deliberately unusual: it cannot be confused with anything the
        # helper generates itself, so seeing it proves the STUB answered.
        self.send_response(418)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")
    do_GET = do_POST = _note
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF
python3 "$WORK/stub.py" "$STUB_PORT" "$STUB_LOG" & STUB_PID=$!

TAOS_FIRSTRUN_PORT="$HELP_PORT" \
TAOS_UPSTREAM="http://127.0.0.1:$STUB_PORT" \
TAOS_SHELL_CONF="$CONF" \
    python3 "$HELPER" >"$WORK/helper.log" 2>&1 & HELPER_PID=$!

for _ in $(seq 1 50); do
    curl -fsS -o /dev/null "http://127.0.0.1:$HELP_PORT/health" 2>/dev/null && break
    sleep 0.1
done
curl -fsS -o /dev/null "http://127.0.0.1:$HELP_PORT/health" 2>/dev/null \
    || give_up "helper did not come up; see $WORK/helper.log"

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
J='Content-Type: application/json'

echo "== positive control: the forwarding path actually works =="
# If this is not 418 then the helper never reached the stub, and every
# rejection below would be meaningless.
c=$(code "http://127.0.0.1:$HELP_PORT/api/upstream/me")
check "GET  me forwards to upstream" 418 "$c"
[ "$c" = "418" ] || give_up "positive control failed: forwarding is broken, so refusals prove nothing"
grep -q "GET /api/auth/me" "$STUB_LOG" || give_up "control: stub did not record the mapped path"
c=$(code -X POST -H "$J" -d '{}' "http://127.0.0.1:$HELP_PORT/api/upstream/login")
check "POST login forwards to upstream" 418 "$c"

echo "== the action table is closed: no caller-supplied path or host =="
before=$(wc -l < "$STUB_LOG")
check "unknown action refused"          404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/whoami")"
check "traversal refused"               404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/../../etc/passwd")"
check "encoded traversal refused"       404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/..%2f..%2fetc%2fpasswd")"
check "absolute URL refused"            404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/https://example.invalid/x")"
check "nested path refused"             404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/auth/login")"
check "empty action refused"            404 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/")"
after=$(wc -l < "$STUB_LOG")
if [ "$before" = "$after" ]; then
    ok "none of the refused requests reached upstream"
else
    bad "a refused request REACHED upstream ($((after-before)) new stub hits)"
fi

echo "== method and size limits =="
check "GET on a POST action refused"    405 "$(code "http://127.0.0.1:$HELP_PORT/api/upstream/login")"
check "POST on a GET action refused"    405 "$(code -X POST -H "$J" -d '{}' "http://127.0.0.1:$HELP_PORT/api/upstream/me")"
python3 -c "open('$WORK/big','w').write('x'*100000)"
check "oversized body refused"          413 "$(code -X POST -H "$J" --data-binary "@$WORK/big" "http://127.0.0.1:$HELP_PORT/api/upstream/login")"

echo "== config writing =="
check "missing mode refused"            400 "$(code -X POST -H "$J" -d '{}' "http://127.0.0.1:$HELP_PORT/api/config")"
check "bogus mode refused"              400 "$(code -X POST -H "$J" -d '{"mode":"sideways"}' "http://127.0.0.1:$HELP_PORT/api/config")"
check "remote without url refused"      400 "$(code -X POST -H "$J" -d '{"mode":"remote"}' "http://127.0.0.1:$HELP_PORT/api/config")"
check "non-url refused"                 400 "$(code -X POST -H "$J" -d '{"mode":"remote","url":"not a url"}' "http://127.0.0.1:$HELP_PORT/api/config")"
check "javascript: url refused"         400 "$(code -X POST -H "$J" -d '{"mode":"remote","url":"javascript:alert(1)"}' "http://127.0.0.1:$HELP_PORT/api/config")"
check "malformed json refused"          400 "$(code -X POST -H "$J" -d '{oops' "http://127.0.0.1:$HELP_PORT/api/config")"
[ -f "$CONF" ] && bad "a REFUSED config write created the file" || ok "no config file written by refused requests"

check "valid remote config accepted"    200 "$(code -X POST -H "$J" -d '{"mode":"remote","url":"http://box.local:6969"}' "http://127.0.0.1:$HELP_PORT/api/config")"
if [ -f "$CONF" ]; then
    grep -q '^mode=remote$'            "$CONF" && ok "mode written" || bad "mode not written"
    grep -q '^url=http://box.local:6969$' "$CONF" && ok "url written" || bad "url not written"
    perm=$(stat -c '%a' "$CONF")
    [ "$perm" = "600" ] && ok "config is 0600" || bad "config is $perm, want 600"
else
    bad "accepted config was not written"
fi
check "valid local config accepted"     200 "$(code -X POST -H "$J" -d '{"mode":"local"}' "http://127.0.0.1:$HELP_PORT/api/config")"

echo "== admission: another origin cannot drive this service =="
# THE BUG THIS SECTION EXISTS FOR, measured against this helper on 2026-08-27
# before the fix, with a POST to a bogus route as a control so the 200 could
# not have come from a catch-all:
#
#     POST /api/config  Origin: http://attacker.example  Content-Type: text/plain
#         -> 200, and shell.conf on disk then named the attacker's controller
#     POST /api/nonexistent  (control)
#         -> 404
#
# In remote mode the kiosk is pointed at a page on someone else's machine, and
# that page shares a browser with this loopback service. Absent CORS headers do
# not help: CORS governs whether the caller may READ the reply, and that request
# is "simple" -- text/plain, no custom header -- so it is not even preflighted.
# One fetch and the device opens their controller on every boot afterwards.
#
# Its OWN helper and OWN config file, for the same reason the rate limiter has
# one: the assertion at the end is "the attacks changed nothing", and sharing
# $CONF with the section above would make that depend on running in order.
CSRF_CONF="$WORK/csrf.conf"
CSRF_PORT="$(free_port)"
TAOS_FIRSTRUN_PORT="$CSRF_PORT" TAOS_UPSTREAM="http://127.0.0.1:$STUB_PORT" \
TAOS_SHELL_CONF="$CSRF_CONF" python3 "$HELPER" >"$WORK/h4.log" 2>&1 &
H4=$!
for _ in $(seq 1 50); do
    curl -fsS -o /dev/null "http://127.0.0.1:$CSRF_PORT/health" 2>/dev/null && break
    sleep 0.1
done
if ! curl -fsS -o /dev/null "http://127.0.0.1:$CSRF_PORT/health" 2>/dev/null; then
    bad "admission helper did not come up; see $WORK/h4.log"
else
    SELF="Origin: http://127.0.0.1:$CSRF_PORT"
    SAME="Sec-Fetch-Site: same-origin"
    ALIEN="Origin: http://attacker.example"
    CFG="http://127.0.0.1:$CSRF_PORT/api/config"

    # POSITIVE CONTROL FIRST. Everything after this is a refusal, and a service
    # that refuses EVERYTHING passes all of them while being useless -- which is
    # a worse outcome than the bug, because the device could no longer be set up
    # at all. This is the exact shape Chromium sends from our own page.
    c=$(code -X POST -H "$SELF" -H "$SAME" -H "$J" -d '{"mode":"local"}' "$CFG")
    check "our own page's fetch is accepted" 200 "$c"
    [ "$c" = "200" ] || give_up "admission positive control failed: the setup form itself is now refused, so the refusals below prove nothing"
    grep -q '^mode=local$' "$CSRF_CONF" \
        && ok "control: the accepted write reached the file" \
        || bad "control: accepted write did not reach the file"

    # No Origin and no Sec-Fetch-Site is curl on loopback -- this verifier, and
    # anyone with a shell on the device, who can already edit shell.conf with a
    # text editor. Refusing it would buy nothing and would break every other
    # check in this file. Allowed deliberately, and asserted so that a future
    # "tighten it further" has to face the decision rather than discover it.
    check "headerless loopback caller still allowed" 200 \
        "$(code -X POST -H "$J" -d '{"mode":"local"}' "$CFG")"

    # The attack, verbatim.
    check "foreign Origin + text/plain refused"  403 \
        "$(code -X POST -H "$ALIEN" -H 'Content-Type: text/plain' -d '{"mode":"remote","url":"http://attacker.example:6969/"}' "$CFG")"
    check "foreign Origin + JSON refused"        403 \
        "$(code -X POST -H "$ALIEN" -H "$J" -d '{"mode":"remote","url":"http://attacker.example:6969/"}' "$CFG")"
    # Origin stripped but the fetch metadata still tells the truth. A page
    # cannot set either header, so this is the belt to the Origin braces.
    check "Sec-Fetch-Site: cross-site refused"   403 \
        "$(code -X POST -H 'Sec-Fetch-Site: cross-site' -H "$J" -d '{"mode":"remote","url":"http://attacker.example:6969/"}' "$CFG")"
    check "Sec-Fetch-Site: same-site refused"    403 \
        "$(code -X POST -H 'Sec-Fetch-Site: same-site' -H "$J" -d '{"mode":"local"}' "$CFG")"

    # /api/config is not the only thing worth stealing. /api/check is a
    # reachable/not oracle for this device's network, and /api/upstream
    # forwards the caller's Authorization header to taos.my.
    check "foreign origin cannot use /api/check" 403 \
        "$(code -X POST -H "$ALIEN" -H "$J" -d '{"url":"http://127.0.0.1:1/"}' "http://127.0.0.1:$CSRF_PORT/api/check")"
    check "foreign origin cannot reach /api/upstream" 403 \
        "$(code -H "$ALIEN" "http://127.0.0.1:$CSRF_PORT/api/upstream/me")"

    # The second lock: the content types a cross-origin POST may use without a
    # preflight are refused even from our own origin, so there is no
    # no-preflight path left to find.
    check "text/plain refused even same-origin"  415 \
        "$(code -X POST -H "$SELF" -H "$SAME" -H 'Content-Type: text/plain' -d '{"mode":"remote","url":"http://x.local:6969/"}' "$CFG")"
    check "form encoding refused even same-origin" 415 \
        "$(code -X POST -H "$SELF" -H "$SAME" -H 'Content-Type: application/x-www-form-urlencoded' -d 'mode=local' "$CFG")"

    # The point of all of it: nothing above moved the config. Grepping for the
    # attacker's host rather than for mode= catches a partial write too.
    if grep -q 'attacker\.example' "$CSRF_CONF"; then
        bad "a refused request WROTE the attacker's controller into shell.conf"
    else
        ok "no refused request changed shell.conf"
    fi
fi
kill "$H4" 2>/dev/null

echo "== the reachability check: /api/check =="
# The check exists so a typo does not become a dead screen on a keyboardless
# phone. Everything here is about one question: does a VERDICT of "ok" actually
# mean a controller, or does it just mean something answered?
#
# THE SPA CASE IS THE REASON THIS SECTION IS NOT TRIVIAL. Measured against the
# live taOSmd A2A bus on 2026-08-27: it is a single-page app with a catch-all
# route, so GET /api/health returns 200 -- with index.html in the body. A port
# check accepts it. A "200 means yes" check accepts it too. Only the response
# SHAPE tells them apart, so a stub with exactly that behaviour is a permanent
# negative control here: if someone ever relaxes the check to a status code,
# this is the test that goes red.
cat > "$WORK/ctlstub.py" <<'PYEOF2'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
MODE = sys.argv[2]
LOG = sys.argv[3]
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        with open(LOG, "a") as f:
            f.write(self.command + " " + self.path + "\n")
        if MODE == "controller":
            body, ctype, code = json.dumps(
                {"status": "ok", "agents": 2, "backends": 9}).encode(), "application/json", 200
        elif MODE == "spa":
            # The A2A bus shape: 200 + HTML for every path, health included.
            body, ctype, code = b"<!doctype html><title>not a controller</title>", "text/html", 200
        elif MODE == "wrongjson":
            body, ctype, code = b'{"hello":"world"}', "application/json", 200
        else:  # auth401
            body, ctype, code = b'{"error":"Authentication required"}', "application/json", 401
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PYEOF2

CTL_PIDS=""
start_ctl() {  # start_ctl <mode> -> echoes port
    local port; port="$(free_port)"
    python3 "$WORK/ctlstub.py" "$port" "$1" "$WORK/ctl-$1.log" >/dev/null 2>&1 &
    CTL_PIDS="$CTL_PIDS $!"
    local i
    for i in $(seq 1 50); do
        curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$port/api/health" 2>/dev/null && break
        # auth401 never returns 2xx, so -f fails; a connection is enough.
        curl -sS -o /dev/null --max-time 2 "http://127.0.0.1:$port/api/health" 2>/dev/null && break
        sleep 0.1
    done
    printf '%s\n' "$port"
}
verdict() {  # verdict <url> -> echoes the verdict field, or the error field
    curl -s --max-time 15 -X POST -H "$J" -d "{\"url\":\"$1\"}" \
        "http://127.0.0.1:$HELP_PORT/api/check" \
    | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: print("UNPARSEABLE"); raise SystemExit
print(d.get("verdict") or d.get("error") or "NEITHER")'
}
vcheck() { if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (want $2, got $3)"; fi; }

CTL_PORT="$(start_ctl controller)"
SPA_PORT="$(start_ctl spa)"
WJ_PORT="$(start_ctl wrongjson)"
A4_PORT="$(start_ctl auth401)"

# POSITIVE CONTROL FIRST, same reason as the top of this file: every assertion
# below is a rejection, and a check that can never say "ok" rejects perfectly.
v="$(verdict "http://127.0.0.1:$CTL_PORT")"
vcheck "a real controller verifies" ok "$v"
[ "$v" = "ok" ] || give_up "check positive control failed: /api/check can never succeed, so its refusals prove nothing"
grep -q "GET /api/health" "$WORK/ctl-controller.log" \
    || bad "control: the check did not request /api/health"

# The projection: fixed keys, and they must actually arrive or the success
# screen silently loses the detail that makes it convincing.
got="$(curl -s --max-time 15 -X POST -H "$J" -d "{\"url\":\"http://127.0.0.1:$CTL_PORT\"}" \
      "http://127.0.0.1:$HELP_PORT/api/check" \
      | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d.get("agents"),d.get("backends"))')"
[ "$got" = "2 9" ] && ok "agents/backends projected ($got)" || bad "projection wrong: got '$got', want '2 9'"

vcheck "an SPA answering 200 is NOT a controller" not_a_controller "$(verdict "http://127.0.0.1:$SPA_PORT")"
vcheck "JSON without a status key is NOT a controller" not_a_controller "$(verdict "http://127.0.0.1:$WJ_PORT")"
vcheck "a 401 service is NOT a controller" not_a_controller "$(verdict "http://127.0.0.1:$A4_PORT")"

DEAD1="$(free_port)"
vcheck "nothing listening is unreachable" unreachable "$(verdict "http://127.0.0.1:$DEAD1")"

vcheck "non-url refused"        bad_url "$(verdict "not a url")"
vcheck "javascript: refused"    bad_url "$(verdict "javascript:alert(1)")"
vcheck "file: refused"          bad_url "$(verdict "file:///etc/passwd")"

# The check must not become a read of the upstream. Nothing from the SPA's body
# may appear in what comes back.
raw="$(curl -s --max-time 15 -X POST -H "$J" -d "{\"url\":\"http://127.0.0.1:$SPA_PORT\"}" \
      "http://127.0.0.1:$HELP_PORT/api/check")"
case "$raw" in
    *"not a controller"*|*doctype*|*"<title"*) bad "the check echoed the upstream body back: $raw" ;;
    *) ok "no upstream body crosses back" ;;
esac

# A caller-supplied path must not survive into the request. The path comes from
# CHECK_PATH; the caller supplies a host.
: > "$WORK/ctl-controller.log"
verdict "http://127.0.0.1:$CTL_PORT/some/where/else" >/dev/null
seen="$(sort -u "$WORK/ctl-controller.log" 2>/dev/null | tr '\n' '|')"
if [ "$seen" = "GET /api/health|" ]; then
    ok "only /api/health is ever requested"
else
    bad "a caller-supplied path reached the target (saw: $seen)"
fi

# The limiter gets its OWN helper. Exhausting the shared bucket in the main one
# would make every later check depend on running before this line -- an
# ordering dependency nobody would see until it broke.
echo "== the check is rate limited =="
P3="$(free_port)"
TAOS_FIRSTRUN_PORT="$P3" TAOS_UPSTREAM="http://127.0.0.1:$STUB_PORT" \
TAOS_SHELL_CONF="$WORK/y.conf" python3 "$HELPER" >"$WORK/h3.log" 2>&1 &
H3=$!
for _ in $(seq 1 50); do
    curl -fsS -o /dev/null "http://127.0.0.1:$P3/health" 2>/dev/null && break
    sleep 0.1
done
if curl -fsS -o /dev/null "http://127.0.0.1:$P3/health" 2>/dev/null; then
    limited=0
    for _ in $(seq 1 40); do
        c=$(code -X POST -H "$J" -d "{\"url\":\"http://127.0.0.1:$CTL_PORT\"}" \
            "http://127.0.0.1:$P3/api/check")
        [ "$c" = "429" ] && { limited=1; break; }
    done
    [ "$limited" = "1" ] && ok "a burst is refused with 429" || bad "40 checks in a row were all allowed"
    # And a malformed URL must not spend the budget it is not allowed to use.
    c=$(code -X POST -H "$J" -d '{"url":"not a url"}' "http://127.0.0.1:$P3/api/check")
    check "bad_url still refused while rate limited" 400 "$c"
else
    bad "third helper did not come up; see $WORK/h3.log"
fi
kill "$H3" 2>/dev/null
for pid in $CTL_PIDS; do kill "$pid" 2>/dev/null; done

echo "== bind is loopback only =="
# Ask the kernel, not the source. A helper that bound 0.0.0.0 would pass every
# check above and still be exposed to the LAN.
#
# The probe returns 0 = refused (good), 1 = reachable (bad), 2 = this host has
# no non-loopback address to test from. It used to be inline with no control,
# which made it the tsk-n26qlg shape: OSError is the PASS branch, and a dropped
# SYN -- a local firewall, a slow route -- is an OSError, so on such a host it
# passed regardless of what the helper actually bound. Nothing anywhere showed
# it could see a listener it was SUPPOSED to see. So bind one deliberately and
# make it prove that first.
offloopback_probe() {
    python3 - "$1" <<'PROBEEOF'
import socket, sys
port = int(sys.argv[1])
# Find a non-loopback address of this host and try to reach the port on it.
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.0.2.1", 9)); addr = s.getsockname()[0]
except OSError:
    addr = None
finally:
    s.close()
if addr is None or addr.startswith("127."):
    sys.exit(2)          # no external address to test from; cannot measure
t = socket.socket(); t.settimeout(2)
try:
    t.connect((addr, port)); sys.exit(1)   # reachable off-loopback
except OSError:
    sys.exit(0)                            # refused
finally:
    t.close()
PROBEEOF
}

# POSITIVE CONTROL. Bind 0.0.0.0 on a free port and require the probe to SEE it.
# If it cannot, the probe cannot detect exposure at all and the verdict below is
# worth nothing, so withhold the verdict rather than issue a green from a dead
# instrument.
CTRL_PORT=$(python3 -c "
import socket
s = socket.socket(); s.bind(('0.0.0.0', 0)); print(s.getsockname()[1]); s.close()")
python3 - "$CTRL_PORT" >"$WORK/ctrl-bind.log" 2>&1 <<'CTRLEOF' &
import socket, sys, time
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", int(sys.argv[1]))); s.listen(8)
time.sleep(20)
CTRLEOF
CTRL_PID=$!
sleep 1
offloopback_probe "$CTRL_PORT"; ctrl_rc=$?
kill "$CTRL_PID" 2>/dev/null
CTRL_DEAD=0
case "$ctrl_rc" in
    1) ok "control: the probe SEES a socket deliberately bound to 0.0.0.0" ;;
    2) echo "  SKIP  no non-loopback address on this host; the probe cannot measure here" ;;
    *) bad "control: the probe cannot see a socket bound to 0.0.0.0 -- it would report"
       bad "  any helper as loopback-only, so no bind verdict is issued from it"
       CTRL_DEAD=1 ;;
esac

if [ "$CTRL_DEAD" = "1" ]; then
    echo "  SKIP  bind verdict withheld: the probe failed its own control"
elif offloopback_probe "$HELP_PORT"; then
    ok "not reachable on a non-loopback address"
else
    rc=$?
    [ "$rc" = "2" ] && echo "  SKIP  no non-loopback address on this host" || bad "helper is reachable off-loopback"
fi

echo "== upstream unreachable is named, not swallowed =="
# Nothing is listening on DEAD_PORT: free_port() binds, reads the number and
# closes, so the port is known-free rather than known-live. That is exactly the
# "controller is not there" case a phone meets on a bad network.
DEAD_PORT="$(free_port)"
P2="$(free_port)"
TAOS_FIRSTRUN_PORT="$P2" TAOS_UPSTREAM="http://127.0.0.1:$DEAD_PORT" \
TAOS_SHELL_CONF="$WORK/x.conf" python3 "$HELPER" >"$WORK/h2.log" 2>&1 &
H2=$!
for _ in $(seq 1 50); do
    curl -fsS -o /dev/null "http://127.0.0.1:$P2/health" 2>/dev/null && break
    sleep 0.1
done
if curl -fsS -o /dev/null "http://127.0.0.1:$P2/health" 2>/dev/null; then
    check "dead upstream reported as 504" 504 "$(code "http://127.0.0.1:$P2/api/upstream/me")"
else
    bad "second helper did not come up; see $WORK/h2.log"
fi
kill "$H2" 2>/dev/null

echo
echo "checks passed: $PASS   failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: PASS"
