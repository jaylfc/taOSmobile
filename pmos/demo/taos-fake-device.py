#!/usr/bin/env python3
"""A fake taOSusb, for when the real board cannot be on set.

@taOS-dev's spec asks for "a fallback button on taos-demod that fakes the
heartbeat, so a flaky Wi-Fi moment on set doesn't kill a take". This fakes the
WHOLE board rather than only the heartbeat, because a heartbeat alone gets you
an island that cannot be talked to -- and the shot is Jay asking it for a
health check, not Jay looking at an icon.

So it does both halves of the device contract:
  * heartbeats /auth/device-agent/heartbeat every 5s, and
  * serves /chat, replying through /auth/device-agent/message with canned
    output that has the SHAPE of the real thing (progress lines, then a
    summary, monospace columns).

⚠ IT SAYS WHAT IT IS. The name is "taOSusb (stand-in)" and every reply is
marked, because a fallback that is indistinguishable from the real board is a
fallback you will one day ship a demo video from by accident.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PHONE = os.environ.get("TAOS_FAKE_PHONE", "http://127.0.0.1:6969")
PORT = int(os.environ.get("TAOS_FAKE_PORT", "8787"))
SLUG = os.environ.get("TAOS_FAKE_SLUG", "taosusb")
#: The same file the controller reads. A stand-in that skipped the token would
#: not exercise the one gate that stands between the Wi-Fi and the lock screen.
TOKEN_FILE = os.environ.get(
    "TAOS_DEVICE_AGENT_TOKEN_FILE", "/etc/taos/device-agent.token"
)
BEAT_SECS = 5.0

#: Canned, and shaped like the real commands: a couple of progress lines and
#: then columns. Not a paragraph -- the sheet renders agent output monospace
#: precisely because df and free speak in columns.
REPLIES = {
    "health": [
        "Running health check…",
        "$ df -h /\n"
        "Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/mmcblk0p2   29G  4.1G   24G  15% /",
        "$ free -m\n"
        "               total        used        free\n"
        "Mem:             427         118         196",
        "$ vcgencmd measure_temp\ntemp=44.2'C   up 3 days, 2:14   load 0.08",
        "$ ip -br addr\nusb0   UP   172.16.42.1/24\nwlan0  UP   10.0.0.5/24  -52 dBm",
        "Health check complete. Nothing needs attention.",
    ],
    "update": [
        "Running apt-get update…",
        "$ apt-get update\nHit:1 http://deb.debian.org/debian bookworm InRelease\n"
        "Get:2 http://deb.debian.org/debian bookworm-updates InRelease [55.4 kB]",
        "$ apt-get -s upgrade\n14 packages can be upgraded.",
        "Update finished. 14 upgradable, none held back.",
    ],
}


def _token() -> str:
    try:
        return Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _post(path: str, payload: dict) -> int:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        PHONE.rstrip("/") + path, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer %s" % _token()},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return 0


def heartbeat_forever() -> None:
    misses = 0
    while True:
        code = _post("/auth/device-agent/heartbeat", {
            "slug": SLUG,
            "name": "taOSusb (stand-in)",
            "framework": "picoclaw",
            "url": "http://127.0.0.1:%d" % PORT,
            # A stand-in is not plugged into anything, and the real device
            # MEASURES this from /sys/class/udc/*/state. Claiming "usb" here
            # would be the one lie that matters, because the island's status
            # line is what the video shows.
            "link": "power",
        })
        if code != 200:
            misses += 1
            if misses in (1, 12):
                print("fake-device: phone answered %s (token? flag?)" % code, flush=True)
        else:
            misses = 0
        time.sleep(BEAT_SECS)


def _answer(msg_id: str, text: str) -> None:
    """Post the canned reply back, one line at a time, as the board would."""
    lowered = text.lower()
    lines = REPLIES["update"] if ("update" in lowered or "upgrade" in lowered) \
        else REPLIES["health"]
    for seq, line in enumerate(lines, start=1):
        # Paced, because the point of the fallback is to look like work
        # happening. Instant output reads as a script, which it is, but the
        # shot is about a device doing something.
        time.sleep(1.2 if seq > 1 else 0.4)
        _post("/auth/device-agent/message", {
            "slug": SLUG, "id": msg_id, "seq": seq,
            "text": line, "done": seq == len(lines),
        })


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("fake-device: %s" % (fmt % args), flush=True)

    def do_POST(self):
        if self.path.rstrip("/") != "/chat":
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        msg_id = str(body.get("id", "")) or "fake"
        text = str(body.get("text", ""))
        # 202 IMMEDIATELY, then answer in the background -- the same contract
        # the real board keeps, because `apt` takes minutes and a lock screen
        # whose send button hangs reads as broken.
        payload = json.dumps({"id": msg_id}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        threading.Thread(target=_answer, args=(msg_id, text), daemon=True).start()


def main() -> None:
    threading.Thread(target=heartbeat_forever, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("fake taOSusb: beating to %s every %gs, /chat on :%d"
          % (PHONE, BEAT_SECS, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
