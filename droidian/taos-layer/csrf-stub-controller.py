#!/usr/bin/env python3
"""Stub taOS controller for selftest-csrf-lockout.sh.

cmt-ilsmt7 on tsk-ame3lw recorded that check-csrf-lockout.sh had been
red-proven against a stub controller in five states. The stub was never
committed, so none of the five could be re-run -- by a later session, by
another agent, or by CI. tsk-cupx7r: this is that stub, committed.

It is not a taOS controller and does not try to be. It answers the two
credential routes the suite probes, in whichever of the named states the
driver asks for, and nothing else. Anything the suite does not read is
deliberately absent -- a stub that grows features grows ways to be wrong
about the thing under test.

Modes (STUB_MODE, default post2543):

  pre2543        taOS#2081 as it actually shipped: verify_csrf's exemption was
                 "no taos_session cookie -> skip", so a request carrying a
                 STALE cookie is 403'd on a credential route before the
                 credential is ever looked at. Without a cookie the route
                 answers normally. This is the state the mitigation exists for.
  post2543       taOS PR #2543 landed: credential routes are exempt BY PATH, so
                 the cookie is irrelevant and a correct PIN or password gets in.
  routes-absent  404 on everything. The state that must not read as a clean
                 bill of health -- every check below section 1 would otherwise
                 "pass" against a route that is not there.
  proxy-down     502 on everything: a reverse proxy up with the controller
                 behind it down. Measured 2026-08-30 to produce four green
                 checks against nothing at all; d4a1935 closed that arm and
                 this mode is its regression test.
  method-405     405 on the credential routes: the path exists but is not routed
                 for POST. This is the code the suite's own catch-all names, and
                 it is the ONLY mode that reaches that arm -- every other mode
                 lands on an explicit one. Without it the pre-d4a1935 `*) ok`
                 catch-all survives untouched, measured 2026-08-31.

Binds 127.0.0.1 only. Given port 0 (the default) it prints the port it got on
stdout as a bare number, so the driver never has to guess a free one.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = os.environ.get("STUB_MODE", "post2543")
PIN = os.environ.get("STUB_PIN", "4242")
USERNAME = os.environ.get("STUB_USERNAME", "kiosk")
PASSWORD = os.environ.get("STUB_PASSWORD", "hunter2")

ROUTES = ("/auth/pin-login", "/auth/login")
MODES = ("pre2543", "post2543", "routes-absent", "proxy-down", "method-405")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler's spelling)
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if MODE == "proxy-down":
            # A proxy answers this itself; it never reaches an application.
            return self._send(502, {"detail": "Bad Gateway"})
        if MODE == "routes-absent" or path not in ROUTES:
            return self._send(404, {"detail": "Not Found"})
        if MODE == "method-405":
            # The path is known; POST is not routed to it. Nothing about #2081
            # can be measured here, and the suite must say so.
            return self._send(405, {"detail": "Method Not Allowed"})

        if MODE == "pre2543" and "taos_session=" in (self.headers.get("Cookie") or ""):
            # The bug, verbatim: the 403 lands before the credential is read,
            # which is why a CORRECT pin rendered as "Incorrect PIN."
            return self._send(403, {"detail": "CSRF token missing"})

        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        if path == "/auth/pin-login":
            good = data.get("pin") == PIN
        else:
            good = data.get("username") == USERNAME and data.get("password") == PASSWORD
        if good:
            return self._send(200, {"ok": True})
        # 401, not 403: a wrong credential is not a CSRF refusal, and the suite
        # reads "not 403" as the mitigation holding.
        return self._send(401, {"detail": "Incorrect PIN."})

    # The suite only POSTs. A stray GET must not 501 into a code the suite
    # would have to interpret, so it takes the same path.
    do_GET = do_POST  # noqa: N815

    def log_message(self, *args) -> None:  # keep the driver's output readable
        pass


if __name__ == "__main__":
    if MODE not in MODES:
        sys.exit(f"STUB_MODE={MODE!r} is not one of {', '.join(MODES)}")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    # Threading, not the plain HTTPServer: with HTTP/1.1 keep-alive a single
    # -threaded server can sit inside a lingering connection while the next
    # probe waits, and a harness that HANGS is not a red -- it is an
    # instrument that stopped reporting.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
