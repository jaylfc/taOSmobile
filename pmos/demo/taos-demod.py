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
import urllib.error
import urllib.request
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
    # The SAME script a real plug-in runs (taos-kiosk-charger calls it), so it
    # honours the rule the same way: from a dark screen it wakes and goes dark
    # again after, from a lit one it plays and leaves the user where they were.
    # Detached, because the show lasts ten seconds and the button should not.
    "charge": (
        "Charger connected",
        "The charging animation, exactly as a plug-in plays it: from a dark "
        "screen it wakes and goes dark again after, from a lit one it plays "
        "over whatever is showing.",
        ["/bin/sh", "-c",
         "/usr/local/bin/taos-kiosk-charge-play >/dev/null 2>&1 &"],
    ),
}


# ---------------------------------------------------------------------------
# THE STAND-IN BOARD. Some demos are not a command, they are a process that
# has to outlive the request that started it -- so they are handled here
# rather than in DEMOS, which runs an argv to completion.
#
# Owned by THIS process, not systemd-run: the fake board runs as `taos`, binds
# loopback and reads a token file taos can already read, so it needs no
# privilege at all. Taking root for it would be borrowing a bigger hammer than
# the job, and demod outlives any single request anyway.
_FAKE = {"proc": None}
_FAKE_SCRIPT = Path(os.environ.get(
    "TAOS_FAKE_DEVICE", "/usr/lib/taos/taos-fake-device.py"))
#: "The stand-in was switched on", kept across restarts. The stand-in is a
#: child of this service, so systemd takes it down on every restart -- every
#: redeploy -- and the island vanished from the lock screen with nobody having
#: pressed Stop (Jay: "taOSusb Agent isnt showing on the lockscreen,
#: intentional?"). StateDirectory= in the unit makes this writable.
_FAKE_MARK = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/taos-demod")) / "stand-in-on"


def _fake_running() -> bool:
    proc = _FAKE["proc"]
    return proc is not None and proc.poll() is None


def _fake_start() -> dict:
    if _fake_running():
        return {"ok": True, "already": True, "label": "Stand-in board"}
    if not _FAKE_SCRIPT.is_file():
        return {"ok": False, "error": "%s is missing" % _FAKE_SCRIPT}
    _FAKE["proc"] = subprocess.Popen(
        ["/usr/bin/python3", str(_FAKE_SCRIPT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _FAKE_MARK.touch()
    except OSError:
        pass   # still running; it just will not come back after a restart
    return {"ok": True, "label": "Stand-in board", "pid": _FAKE["proc"].pid}


def _fake_stop() -> dict:
    try:
        _FAKE_MARK.unlink()
    except OSError:
        pass
    proc = _FAKE["proc"]
    if proc is None or proc.poll() is not None:
        return {"ok": True, "already": True, "label": "Stand-in board"}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    _FAKE["proc"] = None
    # The island does NOT vanish here -- it fades out when the phone stops
    # hearing heartbeats, which takes the liveness window. That delay is the
    # real behaviour and worth seeing in rehearsal.
    return {"ok": True, "label": "Stand-in board"}


# ---------------------------------------------------------------------------
# THE INCOMING CALL. Scripted and demo-only, like everything the lock screen
# shows before sign-in: the controller holds the call's state behind its own
# TAOS_LOCK_DEMO_CALL flag, and there is no phone line anywhere behind it.
#
# Over LOOPBACK, deliberately: /auth/lock-call/* is console-only, and a
# request from 127.0.0.1 with no forwarding headers is what console means.
# demod runs on the handset, so it needs no token and no exemption.
CONTROLLER = os.environ.get("TAOS_DEMOD_CONTROLLER", "http://127.0.0.1:6969")


def _controller_post(path: str) -> dict:
    req = urllib.request.Request(CONTROLLER + path, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        # 404 is the flag being off, which is worth saying in so many words.
        if exc.code == 404:
            return {"ok": False, "error": "call demo is off (TAOS_LOCK_DEMO_CALL)"}
        return {"ok": False, "error": "controller said %d" % exc.code}
    except OSError as exc:
        return {"ok": False, "error": "controller unreachable: %s" % exc}
    return {"ok": True}


def _call_ring() -> dict:
    # A call wakes the phone. Same path as the wake button, for the reason at
    # the top of this file; a failed wake does not stop the ring.
    run_demo("wake-screen")
    out = _controller_post("/auth/lock-call/ring")
    out.setdefault("label", "Incoming call")
    return out


def _call_reset() -> dict:
    out = _controller_post("/auth/lock-call/reset")
    out.setdefault("label", "Call reset")
    return out


#: Demos that are a process rather than a command.
_ACTIONS = {
    "call-ring": (
        "Incoming call from Mary",
        "Wakes the screen and rings. Send it to your PA to watch the live "
        "transcript; the callback lands as a new calendar notification.",
        _call_ring,
    ),
    "call-reset": (
        "Reset the call",
        "Ends any call and clears the demo's calendar notification, ready "
        "for another take.",
        _call_reset,
    ),
    "fake-device-on": (
        "Start the stand-in taOSusb",
        "Fakes the whole board -- heartbeat AND replies -- so a flaky Wi-Fi "
        "moment cannot kill a take. It announces itself as a stand-in.",
        _fake_start,
    ),
    "fake-device-off": (
        "Stop the stand-in taOSusb",
        "The island fades once the phone stops hearing heartbeats, which is "
        "the real unplug behaviour.",
        _fake_stop,
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
    action = _ACTIONS.get(demo_id)
    if action is not None:
        return action[2]()
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
            # BOTH maps. Listing DEMOS alone meant every _ACTIONS entry -- the
            # stand-in board included -- could be run but never shown.
            return self._json({"demos": [
                {"id": k, "label": v[0], "blurb": v[1]}
                for k, v in list(DEMOS.items()) + list(_ACTIONS.items())
            ]})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/run/"):
            return self._json({"error": "not found"}, 404)
        result = run_demo(path[len("/run/"):])
        return self._json(result, 200 if result.get("ok") else 400)


def main():
    if _FAKE_MARK.exists():
        _fake_start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("taos-demod on http://%s:%d with %d demos"
          % (HOST, PORT, len(DEMOS)), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
