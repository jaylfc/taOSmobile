#!/usr/bin/env python3
"""Prove a2a-post.py's read-back can FAIL -- tsk-k3m5ft, tsk-amhjqp.

A verifier nobody has watched refuse is not a verifier. This drives the REAL
script (as a subprocess, not an imported copy) against a stub bus that corrupts
on demand, and asserts the verdict AND the exit code in each state.

WHY A STUB AND NOT THE REAL BUS
-------------------------------
The corruption this guards against is the bus's own secret scanner, which fires
on content nobody can reliably reproduce on purpose -- and reproducing it would
mean posting scanner-triggering text to a shared bus other agents read. The
stub lets every corruption SHAPE be exercised without putting a single test
message on the real bus.

THE STATE THAT MATTERS MOST IS "NOT FOUND"
------------------------------------------
tsk-n26qlg is this seat's recurring defect: reading an absence as an answer. A
read-back that cannot locate its own message must exit 2 INCOMPLETE and say the
post is UNVERIFIED -- never 0. Two states cover it (never stored, and the
listing endpoint returning a shape with no `messages` key at all), because they
are different bugs with the same symptom.

A LENGTH CHECK ALONE WOULD PASS ONE OF THESE
--------------------------------------------
State 3 substitutes bytes without changing the length, which is why the script
compares full bodies rather than counts. The card's own note says the habit has
to be byte comparison; this is the state that proves the habit was implemented
rather than described.

  exit 0  every state produced the verdict and exit code it names
  exit 1  a state came out wrong
  exit 2  INCOMPLETE -- the positive control failed, so nothing was measured
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "a2a-post.py"

MODE = {"how": "clean"}      # mutated per state by the harness
STORE: dict[str, str] = {}   # id -> stored body, as the stub holds it
NEXT_ID = {"n": 9000}


class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        how = MODE["how"]
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode())["body"]
        if how == "send-500":
            return self._send(500, {"detail": "stub: send is down"})
        NEXT_ID["n"] += 1
        mid = NEXT_ID["n"]
        if how == "no-id":
            return self._send(200, {"from": "x", "thread": "t"})   # no id key
        if how == "never-stored":
            pass                                   # accepted, never retrievable
        elif how == "truncate2":
            STORE[str(mid)] = body[:-2]            # the silent two-byte rewrite
        elif how == "same-length":
            STORE[str(mid)] = ("X" + body[1:]) if body else body
        elif how == "redacted":
            STORE[str(mid)] = body.replace(" ", " [REDACTED:bearer] ", 1)
        else:
            STORE[str(mid)] = body
        return self._send(200, {"id": mid, "from": "x", "thread": "t"})

    def do_GET(self):
        how = MODE["how"]
        if how == "list-500":
            return self._send(500, {"detail": "stub: listing is down"})
        if how == "no-messages-key":
            # The tsk-n26qlg shape: a valid 200 whose key the reader assumed.
            return self._send(200, {"items": []})
        return self._send(200, {"messages": [
            {"id": int(k), "ts": 1.0, "from": "x", "thread": "t", "body": v}
            for k, v in STORE.items()]})


def run(body: str, tmp: Path, port: int, evdir: str = "ev") -> tuple[int, str]:
    bf = tmp / "body.txt"
    bf.write_text(body)
    cred = tmp / "cred.json"
    cred.write_text(json.dumps({"canonical_id": "selftest-seat", "token": "t0ken"}))
    env = dict(os.environ,
               TAOS_AGENT_CRED=str(cred),
               TAOS_A2A_FIND_BUDGET="2")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--thread", "build", "--body-file", str(bf),
         "--bus", f"http://127.0.0.1:{port}", "--evidence-dir", str(tmp / evdir)],
        capture_output=True, text=True, timeout=120, env=env)
    return p.returncode, p.stdout + p.stderr


BODY = ("A body with a colon: and a url https://taos.my/api/auth/me plus\n"
        "a dict literal {'k': 'v'} -- the shapes that trip the scanner.\n")

# (label, mode, expected exit, substring that must appear in the output)
STATES = [
    ("clean post verifies byte-identical",      "clean",           0, "byte-identical"),
    ("silent two-byte truncation is caught",    "truncate2",       1, "MANGLED"),
    ("same-length substitution is caught",      "same-length",     1, "MANGLED"),
    ("a REDACTED marker is caught",             "redacted",        1, "MANGLED"),
    ("never stored -> INCOMPLETE, not a pass",  "never-stored",    2, "NOT FOUND"),
    ("send 500 -> INCOMPLETE",                  "send-500",        2, "INCOMPLETE"),
    ("accepted with no id -> INCOMPLETE",       "no-id",           2, "no id"),
    ("listing without a messages key -> INCOMPLETE", "no-messages-key", 2, "no 'messages' key"),
    ("listing 500 -> INCOMPLETE",               "list-500",        2, "NOT FOUND"),
]


def main() -> int:
    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    tmp = Path(tempfile.mkdtemp(prefix="selftest-a2a-post."))
    passed = failed = 0

    def check(label, rc, out, want_rc, want_txt):
        nonlocal passed, failed
        if rc == want_rc and want_txt in out:
            print(f"  PASS  {label}")
            passed += 1
        else:
            print(f"  FAIL  {label} (want exit {want_rc} + {want_txt!r}, "
                  f"got exit {rc}: {out.strip()[:200]!r})")
            failed += 1

    print("== positive control: a clean post must VERIFY, or nothing below means anything ==")
    MODE["how"] = "clean"
    STORE.clear()
    rc, out = run(BODY, tmp, port)
    if rc != 0 or "byte-identical" not in out:
        print(f"INCOMPLETE: control failed (exit {rc}): {out.strip()[:400]}")
        print("A verifier that cannot pass a clean post cannot be trusted to fail "
              "a dirty one -- every state below would be satisfied by a broken script.")
        return 2
    print("  PASS  control: clean post verifies")
    passed += 1

    print("== every corruption shape is caught, and each names itself ==")
    for label, mode, want_rc, want_txt in STATES:
        MODE["how"] = mode
        STORE.clear()
        rc, out = run(BODY, tmp, port)
        check(label, rc, out, want_rc, want_txt)

    print("== an empty body is refused before anything is sent ==")
    MODE["how"] = "clean"
    STORE.clear()
    rc, out = run("   \n", tmp, port)
    check("empty body -> INCOMPLETE, nothing sent", rc, out, 2, "empty body")
    if STORE:
        print("  FAIL  the empty-body path still reached the bus")
        failed += 1
    else:
        print("  PASS  control: nothing reached the bus on the empty-body path")
        passed += 1

    print("== evidence is written to FILES, never echoed for re-posting ==")
    MODE["how"] = "truncate2"
    STORE.clear()
    # Its OWN evidence dir. Counting a shared one summed the three mangling
    # states above and reported 8 where 2 was correct -- the assertion was
    # measuring the whole run, not this state.
    rc, out = run(BODY, tmp, port, evdir="ev-final")
    evd = tmp / "ev-final"
    ev = sorted(evd.glob("a2a-*-s*.txt")) if evd.is_dir() else []
    if rc == 1 and len(ev) == 2:
        print(f"  PASS  mismatch wrote {len(ev)} evidence files and printed their paths")
        passed += 1
    else:
        print(f"  FAIL  expected 2 evidence files on mismatch, got {len(ev)} (exit {rc})")
        failed += 1
    # The sent copy must hold the ORIGINAL bytes -- evidence that reproduces the
    # corruption is not evidence.
    sent = [p for p in ev if p.name.endswith("-sent.txt")]
    if sent and sent[0].read_text() == BODY:
        print("  PASS  the sent-side evidence file holds the original bytes")
        passed += 1
    else:
        print("  FAIL  the sent-side evidence file does not match what was sent")
        failed += 1

    print(f"\nchecks passed: {passed}   failed: {failed}")
    if failed:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
