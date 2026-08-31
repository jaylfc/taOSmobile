#!/usr/bin/env python3
"""Post to the A2A bus and READ THE MESSAGE BACK -- tsk-k3m5ft.

The bus secret scanner MANGLES rather than rejects. It matched a Swift argument
label followed by a colon inside a method signature and silently rewrote the
STORED copy two bytes short of what was sent, with no signal to the sender
(@taosc-dev, A2A 3459/3460). A 200 on /a2a/send is evidence the request was
ACCEPTED, not that the bus holds what you sent.

So the read-back is a STEP OF POSTING here, not a thing anyone remembers to do.
That is the whole card: the previous implementation was a sentence in a report
promising to keep doing it by hand, and a habit stated in a report does not
survive a /clear.

WHAT "VERIFIED" IS ALLOWED TO MEAN
----------------------------------
Only: the message was found by id, and its stored bytes equal the bytes sent.
Everything else is INCOMPLETE and exits 2 -- specifically including "the message
could not be found". A verifier that returns success when it cannot locate what
it is verifying is the tsk-n26qlg defect exactly: I read "nothing printed" as
"nothing found" for a key the endpoint never returned, three times, and was
right by luck. Not found here means the post MAY have landed and is UNVERIFIED,
which is a different and louder thing than "fine".

The comparison is on BYTES, never eyeballed. The two known corruption shapes are
asymmetric: a `[REDACTED:...]` marker is loud and a reader spots it, but the
silent two-byte rewrite that started this card is only ever caught by comparing
sent length to stored length. A same-length substitution would defeat a length
check alone, so the full body is compared and the marker is scanned for
independently.

REPORTING A FAILURE IS ITSELF CONSTRAINED
-----------------------------------------
@taOS-dev measured the scanner eating a correction ABOUT the corruption, twice,
at precisely the quoted span (A2A 3474/3476/3478). Their conclusion: the defect
cannot be reported over the bus, because an accurate report has to quote the
text that triggers the scanner. So on mismatch this script writes the sent and
stored bodies to FILES and prints their paths. It never echoes the offending
text into another bus message, and it never posts anything on its own.

Usage:
    a2a-post.py --thread build --body-file msg.txt
    a2a-post.py --thread build --body-file -        # stdin
    a2a-post.py --thread build --body-file msg.txt --dry-run

    --bus URL        override the bus (the selftest points this at a stub)
    --evidence-dir   where a mismatch is written (default: alongside cwd)

Exit 0  posted and verified byte-identical
Exit 1  MANGLED -- stored bytes differ from sent bytes
Exit 2  INCOMPLETE -- could not verify (not found, fetch failed, no id, ...)
        NOT a pass. The message may or may not have landed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Both overridable so the selftest can drive this script against a stub bus with
# a throwaway identity -- it must exercise THIS code, not a copy of it.
CRED = Path(os.environ.get("TAOS_AGENT_CRED",
                           Path.home() / ".config/taos-agent/credentials.json"))
DEFAULT_BUS = os.environ.get("TAOS_BUS", "http://100.78.225.80:7900")

# How long to keep looking for our own message before giving up. Bounded on
# purpose: a read-back that waits forever is not a red, it is an instrument that
# stopped reporting.
FIND_BUDGET_SECS = float(os.environ.get("TAOS_A2A_FIND_BUDGET", "20"))
FIND_POLL_SECS = 1.0
HTTP_TIMEOUT = 20


def die(code: int, verdict: str, *lines: str) -> None:
    print(f"RESULT: {verdict}")
    for ln in lines:
        print(f"  {ln}")
    sys.exit(code)


def load_identity() -> tuple[str, str]:
    try:
        c = json.loads(CRED.read_text())
    except OSError as e:
        die(2, "INCOMPLETE", f"cannot read {CRED}: {e}",
            "Nothing was sent.")
    return c["canonical_id"], c["token"]


def http_json(url: str, payload: dict | None, token: str | None) -> tuple[int, dict | list]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def fetch_by_id(bus: str, token: str, since: float, want_id) -> str | None:
    """Return the STORED body for want_id, or None if it is not there yet.

    `since` is an epoch timestamp -- not a message id, and since=0 is a 400.
    """
    status, body = http_json(f"{bus}/a2a/messages?since={since}", None, token)
    if status != 200:
        return None
    if not isinstance(body, dict) or "messages" not in body:
        # Do NOT treat an unexpected shape as "no messages". That is the
        # tsk-n26qlg trap: a key that does not exist reads as an empty truth.
        raise KeyError(f"/a2a/messages returned no 'messages' key (keys: "
                       f"{list(body) if isinstance(body, dict) else type(body).__name__})")
    for m in body["messages"]:
        if str(m.get("id")) == str(want_id):
            return m.get("body")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thread", required=True)
    ap.add_argument("--body-file", required=True,
                    help="file holding the exact body; '-' for stdin. Never pass "
                         "the body as an argv string: the shell is itself a "
                         "mangling source, and the point is to compare bytes.")
    ap.add_argument("--bus", default=DEFAULT_BUS)
    ap.add_argument("--evidence-dir", default=".")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    body = (sys.stdin.read() if a.body_file == "-"
            else Path(a.body_file).read_text())
    if not body.strip():
        die(2, "INCOMPLETE", "refusing to post an empty body.")
    sent_bytes = body.encode()

    me, token = load_identity()
    if a.dry_run:
        print(f"DRY RUN: would post {len(sent_bytes)} bytes as {me} to "
              f"thread {a.thread} on {a.bus}")
        return 0

    # `since` must bracket our own message. Taken BEFORE the send, with a margin
    # for clock skew between here and the bus.
    since = time.time() - 120

    status, resp = http_json(f"{a.bus}/a2a/send",
                             {"from": me, "thread": a.thread, "body": body}, token)
    if status < 200 or status >= 300:
        die(2, "INCOMPLETE", f"/a2a/send returned {status}: {json.dumps(resp)[:300]}",
            "Nothing to verify. Note a sub-vs-`from` MISMATCH is what 403s this "
            "endpoint; the bearer itself is optional and does not.")

    mid = resp.get("id") if isinstance(resp, dict) else None
    if mid is None:
        die(2, "INCOMPLETE",
            f"/a2a/send accepted the post but returned no id: {json.dumps(resp)[:300]}",
            "The message may have landed. It CANNOT be verified without an id.")
    print(f"sent: id={mid} bytes={len(sent_bytes)} thread={a.thread} as {me}")

    deadline = time.time() + FIND_BUDGET_SECS
    stored = None
    while True:
        try:
            stored = fetch_by_id(a.bus, token, since, mid)
        except KeyError as e:
            die(2, "INCOMPLETE", str(e),
                "Refusing to read an unexpected response shape as 'no messages'.")
        if stored is not None:
            break
        if time.time() >= deadline:
            die(2, "INCOMPLETE",
                f"message {mid} was NOT FOUND on the bus within {FIND_BUDGET_SECS:.0f}s.",
                "The post MAY have landed and is UNVERIFIED. This is not a pass: "
                "a verifier that succeeds when it cannot find its subject is the "
                "defect this script exists to prevent.")
        time.sleep(FIND_POLL_SECS)

    stored_bytes = stored.encode()
    marker = "[REDACTED" in stored

    if stored_bytes == sent_bytes and not marker:
        print(f"read-back: {len(stored_bytes)}/{len(sent_bytes)} bytes, byte-identical")
        die(0, "VERIFIED", f"message {mid} on the bus matches what was sent.")

    # MANGLED. Write evidence to files and print PATHS -- never quote the
    # offending span into another bus message; the scanner has eaten corrections
    # about itself at precisely the quoted span, three times.
    ev = Path(a.evidence_dir)
    ev.mkdir(parents=True, exist_ok=True)
    sp = ev / f"a2a-{mid}-sent.txt"
    tp = ev / f"a2a-{mid}-stored.txt"
    sp.write_bytes(sent_bytes)
    tp.write_bytes(stored_bytes)

    delta = len(stored_bytes) - len(sent_bytes)
    first = next((i for i, (x, y) in enumerate(zip(sent_bytes, stored_bytes)) if x != y),
                 min(len(sent_bytes), len(stored_bytes)))
    die(1, "MANGLED",
        f"message {mid}: sent {len(sent_bytes)} bytes, bus stored "
        f"{len(stored_bytes)} ({delta:+d}).",
        f"redaction marker present: {marker}",
        f"first differing byte offset: {first}",
        f"sent   -> {sp}",
        f"stored -> {tp}",
        "DO NOT report this by quoting the affected text into a bus message: "
        "the scanner has eaten corrections about itself at the quoted span. "
        "Reference these paths, or use the board.")


if __name__ == "__main__":
    sys.exit(main())
