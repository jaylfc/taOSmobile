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

echo "== bind is loopback only =="
# Ask the kernel, not the source. A helper that bound 0.0.0.0 would pass every
# check above and still be exposed to the LAN.
if python3 - "$HELP_PORT" <<'PYEOF'
import socket, sys
port = int(sys.argv[1])
# Find a non-loopback address of this host and try to reach the helper on it.
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
    t.connect((addr, port)); sys.exit(1)   # reachable off-loopback: BAD
except OSError:
    sys.exit(0)                            # refused: good
finally:
    t.close()
PYEOF
then ok "not reachable on a non-loopback address"
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
