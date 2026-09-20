#!/usr/bin/env python3
"""taos-demod -- a button board for demoing the handset.

Jay wants demos he can trigger without touching the phone: press a button on a
laptop, watch the handset do the thing. This is the server behind that.

TWO RULES THAT SHAPE EVERYTHING HERE.

**A demo must go down the SAME path the real thing does.** The wake button runs
`taos-kiosk-screen on`, which is what the power key and the double-tap watcher
run. Doing it "directly" with `swaymsg output * power on` would look identical
for about thirty seconds and then diverge: that script is also the only place
that re-arms the idle watcher, and powering the output on over the IPC produces
no input event, so swayidle stays latched in the idle state it entered and the
screen never blanks again. That bug is already documented in taos-kiosk-screen
in this repo, having been hit once. A demo that quietly disables the behaviour
it is demonstrating is worse than no demo.

**It runs as `taos`, and takes no privilege it does not need.** Every verb here
is a compositor command, and the compositor belongs to `taos`. Nothing goes
through the root drop box, because nothing here needs root -- unlike the camera
launcher, which does.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

#: Bound on ALL interfaces, deliberately: the whole point is to press these
#: from a laptop over the tailnet. That does mean anyone already on the tailnet
#: can wake the screen -- which is the same thing they could do by picking the
#: phone up, and nothing here reads or writes data. Anything that ever does
#: needs a harder look than this comment.
HOST = os.environ.get("TAOS_DEMOD_HOST", "0.0.0.0")
PORT = int(os.environ.get("TAOS_DEMOD_PORT", "6972"))
PAGE = Path(os.environ.get("TAOS_DEMOD_PAGE", str(Path(__file__).with_name("demo.html"))))
#: Icons are GENERATED at install time from the handset's own wordmark rather
#: than committed here: one brand asset, and no binaries in the repo that can
#: drift from it.
ICONS = Path(os.environ.get("TAOS_DEMOD_ICONS", "/usr/lib/taos/demo-icons"))

#: Installable as a PWA, which is why this is served over Tailscale HTTPS and
#: not plain http: a service worker and the install prompt both need a secure
#: context, and a tailnet name gets a real certificate.
MANIFEST = {
    "name": "taOS Demos",
    "short_name": "taOS Demos",
    "description": "Trigger demos on the handset.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#05060a",
    "theme_color": "#05060a",
    "orientation": "portrait",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}

#: The smallest service worker that still makes this installable and usable
#: when the laptop is off the tailnet: it caches the shell, and NEVER caches
#: /run/* -- a cached demo trigger would be a button that lies.
SERVICE_WORKER = """
const CACHE = 'taos-demos-v1';
const SHELL = ['/', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }));
  self.skipWaiting();
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                           .map(function (k) { return caches.delete(k); }));
  }));
  self.clients.claim();
});
self.addEventListener('fetch', function (e) {
  var url = new URL(e.request.url);
  // Triggers and the demo list always go to the phone. Serving either from a
  // cache would show a button that does nothing, or a list that is wrong.
  if (e.request.method !== 'GET' || url.pathname.startsWith('/run/')
      || url.pathname === '/demos') return;
  e.respondWith(
    fetch(e.request).then(function (r) {
      var copy = r.clone();
      caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
      return r;
    }).catch(function () { return caches.match(e.request); })
  );
});
"""

#: Every demo, as a closed map: id -> (label, blurb, argv).
#:
#: A MAP, not a command the page sends. The page picks an id; what runs is only
#: ever something written here. That keeps the exposure above to a list you can
#: read in one screen, however many demos get added later.
DEMOS = {
    "wake-screen": (
        "Wake the screen",
        "Exactly what the power key does. The screen blanks again after 30s "
        "of no activity, on its own.",
        ["/usr/local/bin/taos-kiosk-screen", "on"],
    ),
    "sleep-screen": (
        "Blank the screen",
        "The other half, so a wake demo can be set up without waiting.",
        ["/usr/local/bin/taos-kiosk-screen", "off"],
    ),
}


def _sway_env() -> dict:
    """The compositor's socket, found rather than assumed.

    `wayland-1` and a fixed SWAYSOCK name are both true today and neither is
    promised: the sway ipc socket carries the compositor's pid, so it changes
    every time the session restarts.
    """
    env = dict(os.environ)
    run = Path("/run/user/%d" % os.getuid())
    env.setdefault("XDG_RUNTIME_DIR", str(run))
    socks = sorted(run.glob("sway-ipc.*.sock"))
    if socks:
        env["SWAYSOCK"] = str(socks[0])
    waylands = sorted(p.name for p in run.glob("wayland-[0-9]*") if not p.name.endswith(".lock"))
    if waylands:
        env["WAYLAND_DISPLAY"] = waylands[0]
    return env


def run_demo(demo_id: str) -> dict:
    entry = DEMOS.get(demo_id)
    if entry is None:
        return {"ok": False, "error": "unknown demo"}
    label, _blurb, argv = entry
    try:
        proc = subprocess.run(
            argv, env=_sway_env(), capture_output=True, text=True, timeout=15
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "%s did not return in 15s" % shlex.join(argv)}
    if proc.returncode != 0:
        # The command's OWN words. "Demo failed" tells you to go and read a log;
        # "swaymsg: Unable to connect" tells you the session is down.
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {
            "ok": False,
            "error": detail[-1] if detail else "exit %d" % proc.returncode,
        }
    return {"ok": True, "demo": demo_id, "label": label}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "taos-demod"

    def log_message(self, fmt, *args):
        # One line per press, because the whole point is being able to see that
        # the button reached the phone when the phone is across the room.
        print("demod: %s" % (fmt % args), flush=True)

    def _send(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not PAGE.is_file():
                return self._json({"error": "demo.html is missing"}, 500)
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if path == "/manifest.webmanifest":
            return self._send(json.dumps(MANIFEST).encode(),
                              "application/manifest+json")
        if path == "/sw.js":
            # No-store: the worker is how the page updates itself, so a cached
            # worker is a page that can never be fixed.
            return self._send(SERVICE_WORKER.encode(), "text/javascript",
                              cache="no-store")
        if path in ("/icon-192.png", "/icon-512.png"):
            icon = ICONS / path.lstrip("/")
            if not icon.is_file():
                return self._json({"error": "icon missing"}, 404)
            return self._send(icon.read_bytes(), "image/png",
                              cache="public, max-age=86400")
        if path == "/demos":
            return self._json({"demos": [
                {"id": k, "label": v[0], "blurb": v[1]} for k, v in DEMOS.items()
            ]})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/run/"):
            return self._json({"error": "not found"}, 404)
        result = run_demo(path[len("/run/"):])
        return self._json(result, 200 if result.get("ok") else 400)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("taos-demod on http://%s:%d with %d demos"
          % (HOST, PORT, len(DEMOS)), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
