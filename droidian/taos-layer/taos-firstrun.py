#!/usr/bin/env python3
"""First-run helper for taOSmobile: a loopback HTTP service the kiosk can talk to.

WHY THIS EXISTS
---------------
taos.my sends no CORS headers. Measured 2026-08-27 against the live host, with a
negative control:

    OPTIONS /api/auth/login  (Origin + Access-Control-Request-Method present)
        -> 404, no Access-Control-Allow-Origin
    GET     /api/auth/me     (Origin present)
        -> 401, no Access-Control-Allow-Origin
    POST    /api/nonexistent-control-probe
        -> 404          (control: 404 really does mean absent here)

Our first-run UI is served from the phone, so every call to taos.my is
cross-origin and Chromium blocks it. "Render our own form and POST it" does not
work from the page. Something has to make those calls process-side, and this is
it: the page talks to 127.0.0.1 same-origin, and this process talks to taos.my.

The sharp part of the timing: this is needed precisely in REMOTE mode, which is
the mode defined by there being no local controller. So it cannot be folded into
taos-controller.service -- in the case that needs it, that service is not there.

See docs/first-run-controller-choice.md, requirement 6.

THIS IS NOT A PROXY, AND MUST NEVER BECOME ONE
----------------------------------------------
The upstream path is never taken from the request. The client names an ACTION
from a fixed table (_ACTIONS) and the table supplies the method and the path.
There is no route that forwards a caller-supplied path or host.

That is deliberate and it is the whole security design. An open forwarder
listening on loopback inside a kiosk browser is worse than no helper at all:
every page the kiosk ever loads could use it to reach arbitrary hosts from
inside the device's network, with the device's identity. An allowlist of path
PREFIXES would not be enough either -- prefixes invite traversal and encoding
tricks. A closed action table has no user-controlled component in the URL at
all, so there is nothing to trick.

If you add a capability here, add an entry to _ACTIONS. If you ever find
yourself wanting to pass a path through, stop: that is the bug.

THE ONE ROUTE THAT DOES TAKE A CALLER-SUPPLIED HOST
---------------------------------------------------
/api/check is the exception, and it is deliberately shaped so that it is not a
hole in the paragraph above. It exists because the first-run form has to be
able to tell the user whether the address they just typed is a taOS controller
BEFORE it is written to shell.conf -- after that the device points Chromium at
it on every boot, and a typo is a dead screen on a phone with no keyboard.

The page cannot do that check itself: the controller is a different origin and
sends no CORS headers to us, so the answer is unreadable from JavaScript. It
has to happen process-side, which means this process connects to a host the
caller named. That is a real new capability and it is worth being precise
about what keeps it small:

  - The PATH is fixed (/api/health). The caller supplies a host, never a path.
  - Only http/https, via the same _URL_RE that gates what may be written to
    the config. What is verified is the ORIGIN of that string: scheme, host
    and port. A path the user pasted is deliberately dropped rather than
    appended to, and is therefore not verified -- see probe_controller.
  - The response is never returned. The caller gets a VERDICT, plus two
    integers read from fixed key names and range-checked. There is no code
    path that hands back a body, a header or an upstream status code.
  - Rate limited and short-timeout, so it is not a usable scanner and not an
    amplifier.

What it does still give a loopback caller is a coarse reachable/not oracle for
hosts this device can reach. That is stated rather than glossed: it is the cost
of the check, it is why the limiter is there, and it is why the verdict is
three words instead of a response. Note that "the page can already make the
device navigate anywhere via /api/config" is NOT a defence -- a cross-origin
navigation's result is not readable by the page that caused it, so this is a
capability that genuinely did not exist before.
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Loopback only. Binding anything else exposes an authenticated call surface to
# the LAN; there is no reason ever to do it and no option to.
BIND_HOST = "127.0.0.1"
BIND_PORT = int(os.environ.get("TAOS_FIRSTRUN_PORT", "6970"))

# Fixed at start from the environment (i.e. from the unit file), never from a
# request. A request that could choose the upstream host would be the open
# forwarder this file exists to not be.
UPSTREAM = os.environ.get("TAOS_UPSTREAM", "https://taos.my").rstrip("/")

UPSTREAM_TIMEOUT = float(os.environ.get("TAOS_UPSTREAM_TIMEOUT", "20"))

CONFIG_PATH = Path(
    os.environ.get(
        "TAOS_SHELL_CONF",
        str(Path.home() / ".config" / "taosmobile" / "shell.conf"),
    )
)

# The closed action table. local action -> (method, upstream path).
#
# Only these four are here because only these four are needed to finish first
# run. /api/auth/me is the one call that is account-credential-only and needs no
# prior controller knowledge, which is why the remote branch can start with it.
#
# NOT here yet, deliberately: GET /api/hosts. It exists and is account-credential
# gated (taos-website server/main.py:864; probed live 2026-08-27 -> 401 with a
# 404 control), so it would satisfy the ordering requirement. It is absent
# because it would let the UI name a controller it then cannot connect to, which
# is worse than not offering the list at all.
#
# The REASON for that has now changed twice; keep the current one, not the old
# ones. It is no longer "a handle is not an address" -- a handle IS turnable
# into a URL client-side, https://{handle}.{username}.taos.my, confirmed against
# taos-website origin/dev (relay_tls_allow main.py:895 takes the username as the
# LAST of 1-2 labels, so that form is intended). It is that the URL does not
# resolve: measured 2026-08-27, *.taos.my has NO ingress deployed -- Coolify's
# Traefik answers every subdomain with CN=TRAEFIK DEFAULT CERT, identically for
# a name /api/relay/tls-allow allows (200) and one it refuses (404), and
# plaintext :80 404s on the wildcard while the apex 302s. A phone sent there
# gets ERR_CERT_AUTHORITY_INVALID, unskippable on a kiosk with no keyboard.
#
# So: add it WITH a working relay, not before -- see
# docs/first-run-controller-choice.md.
_ACTIONS = {
    "me":       ("GET",  "/api/auth/me"),
    "login":    ("POST", "/api/auth/login"),
    "register": ("POST", "/api/auth/register"),
    "logout":   ("POST", "/api/auth/logout"),
}

# Body caps. The upstream bodies here are small JSON credentials; anything
# larger is a mistake or an attack, and streaming it upstream would make this
# process a useful amplifier.
MAX_REQUEST_BODY = 64 * 1024
MAX_UPSTREAM_BODY = 1024 * 1024

# --- the reachability check ------------------------------------------------
# Shorter than UPSTREAM_TIMEOUT (20s) because a person is watching this one:
# it runs while they are still looking at the form, and a 20s stall on a typo
# reads as a hang.
CHECK_TIMEOUT = float(os.environ.get("TAOS_CHECK_TIMEOUT", "5"))

# Fixed. The caller names a host; it never names this.
#
# Measured against the live controller at :6969 on 2026-08-27, with controls:
#     GET /api/health   -> 200 {"status":"ok","agents":2,"backends":9}
#     GET /api/version  -> 200 {"version":"1.0.0-beta.50"}
#     GET /            -> 401 {"error":"Authentication required"}
#     GET /api/nonexistent-control-probe-3 -> 401   <- NOT 404
# That last one matters for anyone extending this: the controller authenticates
# before it routes, so on THIS host a 404 does not mean absent. It is the exact
# inverse of the taos.my trap recorded in docs/first-run-controller-choice.md,
# where a 404 meant wrong-method rather than missing route. Do not port a
# 404-means-absent reading between the two hosts.
CHECK_PATH = "/api/health"

MAX_CHECK_BODY = 64 * 1024

# Enough for a person retyping an address; useless as a network scanner.
_CHECK_RATE = 30
_CHECK_WINDOW = 60.0
_check_times: list[float] = []
_check_lock = threading.Lock()


def check_allowed() -> bool:
    """Token bucket over a sliding minute. Shared across threads and clients.

    Deliberately global rather than per-URL: a per-target limit would still let
    a caller sweep a /24 at full speed, which is the case the limit is for.
    """
    now = time.monotonic()
    with _check_lock:
        cutoff = now - _CHECK_WINDOW
        while _check_times and _check_times[0] < cutoff:
            _check_times.pop(0)
        if len(_check_times) >= _CHECK_RATE:
            return False
        _check_times.append(now)
        return True


def probe_controller(url: str) -> dict:
    """Is there a taOS controller at `url`? Verdict only, never the response.

    THE STATUS CODE IS NOT THE ANSWER, AND THIS IS THE WHOLE POINT.
    Measured 2026-08-27: the taOSmd A2A bus on :7900 is a single-page app with
    a catch-all route, so GET /api/health against it returns **200** -- with a
    body of index.html. A port check accepts it. A 200-means-yes check accepts
    it too. Only the SHAPE of the response tells the two apart, so that is what
    is checked: JSON content type, a JSON object, and a "status" key.

    Verdicts, which map one-to-one onto the three states requirement 2 needs to
    be distinguishable:
        ok               a controller answered
        not_a_controller something answered and it was not one
        unreachable      nothing answered
    """
    # ORIGIN ONLY. Appending to the URL as typed would let a path the caller
    # supplied survive as a prefix (".../some/where/else/api/health"), which
    # would quietly make this the caller-supplied-path route the module
    # docstring says does not exist. It is also simply where the endpoint is:
    # a controller serves /api/health at the root of its origin, not under
    # whatever path the user happened to paste.
    parts = urllib.parse.urlsplit(url)
    target = urllib.parse.urlunsplit((parts.scheme, parts.netloc, CHECK_PATH, "", ""))
    req = urllib.request.Request(target, method="GET")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(
            req, timeout=CHECK_TIMEOUT, context=ssl.create_default_context()
        ) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(MAX_CHECK_BODY)
    except urllib.error.HTTPError as exc:
        # It answered, just not the way a controller does. An older controller
        # that gates /api/health would land here too, which is honest: we
        # cannot tell it from any other authenticated service, and the form
        # offers "use it anyway" for exactly that case.
        log(f"check: answered {exc.code}, not a controller")
        return {"verdict": "not_a_controller"}
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as exc:
        log(f"check: unreachable: {type(exc).__name__}")
        return {"verdict": "unreachable"}

    if status != 200 or "json" not in ctype.lower():
        log(f"check: {status} ctype-mismatch, not a controller")
        return {"verdict": "not_a_controller"}
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log("check: unparseable body, not a controller")
        return {"verdict": "not_a_controller"}
    if not isinstance(body, dict) or "status" not in body:
        log("check: wrong shape, not a controller")
        return {"verdict": "not_a_controller"}

    # A fixed, tiny projection -- named keys, type- and range-checked. This is
    # the only thing that crosses back from upstream, and it is here because
    # "found a controller: 2 agents, 9 backends" is a far more convincing
    # success state on a 6-inch screen than a green tick. It cannot grow into a
    # general read: adding a key means editing this tuple.
    out = {"verdict": "ok"}
    for key in ("agents", "backends"):
        val = body.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val < 10000:
            out[key] = val
    log("check: ok")
    return out


# Accepted for mode=remote. Deliberately strict: a URL that lands in shell.conf
# is what the kiosk will point Chromium at on every subsequent boot, so a
# malformed one is a dead screen at a moment when there is no keyboard to fix
# it with.
_URL_RE = re.compile(r"^https?://[A-Za-z0-9._~\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/%?=&]*)?$")


def log(*parts: object) -> None:
    """Log to stderr, which the unit sends to the journal.

    Never pass a request or response BODY to this. Bodies here carry account
    passwords and session tokens; the journal is readable by anyone who can
    read the journal, and a password in a log outlives the session that typed
    it. Method, action and status only.
    """
    print(*parts, file=sys.stderr, flush=True)


class _Handler(BaseHTTPRequestHandler):
    server_version = "taos-firstrun"
    protocol_version = "HTTP/1.1"

    # --- plumbing -------------------------------------------------------

    def log_message(self, fmt: str, *args: object) -> None:
        # BaseHTTPRequestHandler logs the full request line by default, which
        # would put query strings in the journal. Route logging through ours.
        log("http:", fmt % args)

    def _send(self, code: int, payload: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        # This service is same-origin by construction. Say so explicitly rather
        # than relying on the absence of a header: no CORS, no framing, no
        # sniffing, and no referrer leaking a local URL upstream.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "bad_content_length"})
            return None
        if length < 0 or length > MAX_REQUEST_BODY:
            self._json(413, {"error": "body_too_large"})
            return None
        return self.rfile.read(length) if length else b""

    # --- routes ---------------------------------------------------------

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "upstream": UPSTREAM})
        elif path == "/":
            self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._json(200, read_config())
        elif path.startswith("/api/upstream/"):
            self._upstream(path[len("/api/upstream/"):])
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            self._write_config()
        elif path == "/api/check":
            self._check()
        elif path.startswith("/api/upstream/"):
            self._upstream(path[len("/api/upstream/"):])
        else:
            self._json(404, {"error": "not_found"})

    # --- the forwarding half --------------------------------------------

    def _upstream(self, action: str) -> None:
        entry = _ACTIONS.get(action)
        if entry is None:
            # Deliberately does not echo the action back. Reflecting caller
            # input into a response body is how a helpful error becomes an
            # injection surface.
            log("upstream: rejected unknown action")
            self._json(404, {"error": "unknown_action"})
            return

        method, upstream_path = entry
        if method != self.command:
            self._json(405, {"error": "method_not_allowed", "expected": method})
            return

        body = self._read_body()
        if body is None:
            return

        req = urllib.request.Request(
            UPSTREAM + upstream_path,          # path from the TABLE, not the caller
            data=body if method == "POST" else None,
            method=method,
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        # Pass the account credential through if the page has one. This is the
        # only caller-supplied header that is forwarded, and it is forwarded to
        # a host the caller cannot choose.
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)

        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT, context=ctx) as resp:
                payload = resp.read(MAX_UPSTREAM_BODY)
                status = resp.status
                ctype = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            # An upstream 401/422 is a real answer the page must see, not an
            # error to swallow. 401 in particular is how "not signed in" is
            # distinguished from "signed in but no controller", which is one of
            # the three UI states requirement 2 demands be distinguishable.
            payload = exc.read(MAX_UPSTREAM_BODY)
            status = exc.code
            ctype = exc.headers.get("Content-Type", "application/json")
        except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as exc:
            # Offline is the expected case on a phone, not an exception. Name it
            # so the page can say "no network" instead of showing a blank panel,
            # which during bring-up was indistinguishable from a crash.
            log(f"upstream: {method} {action} unreachable: {type(exc).__name__}")
            self._json(504, {"error": "upstream_unreachable"})
            return

        log(f"upstream: {method} {action} -> {status}")
        self._send(status, payload, ctype)

    def _check(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        if not isinstance(data, dict):
            self._json(400, {"error": "bad_json"})
            return
        url = str(data.get("url", "")).strip()
        # Same gate as the config write, on purpose: the check must test the
        # exact string that would be saved, or it is checking something else.
        if not _URL_RE.match(url):
            self._json(400, {"error": "bad_url"})
            return
        # After validation, before the network call: a malformed URL should not
        # be able to burn the budget that protects the network call.
        if not check_allowed():
            log("check: rate limited")
            self._json(429, {"error": "rate_limited"})
            return
        self._json(200, probe_controller(url))

    # --- config half ----------------------------------------------------

    def _write_config(self) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "bad_json"})
            return
        if not isinstance(data, dict):
            self._json(400, {"error": "bad_json"})
            return

        mode = data.get("mode")
        if mode not in ("local", "remote"):
            self._json(400, {"error": "bad_mode"})
            return

        url = ""
        if mode == "remote":
            url = str(data.get("url", "")).strip()
            if not _URL_RE.match(url):
                self._json(400, {"error": "bad_url"})
                return

        try:
            write_config(mode, url)
        except OSError as exc:
            log(f"config: write failed: {type(exc).__name__}")
            self._json(500, {"error": "write_failed"})
            return

        log(f"config: mode={mode}")
        self._json(200, {"ok": True, "mode": mode, "url": url})


def read_config() -> dict:
    """Read shell.conf. A missing file is 'not configured', not an error."""
    out: dict[str, str] = {}
    try:
        text = CONFIG_PATH.read_text()
    except FileNotFoundError:
        return {"configured": False}
    except OSError:
        return {"configured": False}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    out["configured"] = bool(out.get("mode"))
    return out


def write_config(mode: str, url: str) -> None:
    """Write shell.conf atomically, 0600.

    Atomically because the kiosk reads this file at every boot and a half
    written config is a dead screen on a device with no keyboard. 0600 because
    once remote mode grows a stored token this file stops being merely
    configuration.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".conf.tmp")
    lines = [
        "# Written by taos-firstrun. Mode + URL only; the session is a pure consumer.",
        f"mode={mode}",
    ]
    if mode == "remote":
        lines.append(f"url={url}")
    else:
        lines.append("url=http://localhost:6969")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


INDEX_HTML = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up this taOS device</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#101014;color:#e8e8ea;
      display:flex;min-height:100vh;align-items:center;justify-content:center;padding:1.5rem}
 main{width:100%;max-width:26rem}
 h1{font-size:1.35rem;margin:0 0 1.25rem}
 button{width:100%;padding:1rem;margin:.4rem 0;font:inherit;border-radius:.6rem;
        border:1px solid #33343c;background:#1c1d24;color:inherit;text-align:left}
 button.sel{border-color:#6c8cff;background:#1b2340}
 input{width:100%;padding:.9rem;font:inherit;border-radius:.6rem;border:1px solid #33343c;
       background:#1c1d24;color:inherit;margin:.4rem 0}
 #go{background:#3d5afe;border-color:#3d5afe;text-align:center;margin-top:1rem}
 #go[disabled]{opacity:.6}
 #anyway{text-align:center;background:#1c1d24;border-color:#5a4a2c;color:#f0c674}
 #msg{margin-top:1rem;min-height:1.5rem;color:#ffb4a2}
 small{color:#9a9aa4;display:block;margin-top:.35rem}
</style>
<main>
<h1>Set up this taOS device</h1>
<button id="local">Run taOS on this phone
  <small>Self-contained. Works with no network.</small></button>
<button id="remote">Connect to a controller
  <small>This phone becomes a surface for a controller you already run.</small></button>
<div id="remotebox" hidden>
  <input id="url" inputmode="url" autocapitalize="none" autocorrect="off"
         placeholder="http://controller.local:6969">
  <small>Enter the controller address. Your account can name its controllers
  but not tell this phone how to reach them &mdash; that needs taOSgo, or an
  address on this network.</small>
</div>
<button id="go">Continue</button>
<div id="msg" role="status" aria-live="polite"></div>
<!-- Requirement 2: never a dead end. A controller that is merely switched off
     right now is a legitimate thing to configure, so a failed check must not
     be a wall -- it must be a warning with a way past it. -->
<button id="anyway" hidden>Use this address anyway</button>
</main>
<script>
 var mode=null;
 var L=document.getElementById('local'), R=document.getElementById('remote');
 var box=document.getElementById('remotebox'), msg=document.getElementById('msg');
 function pick(m){mode=m;L.classList.toggle('sel',m==='local');
   R.classList.toggle('sel',m==='remote');box.hidden=(m!=='remote');msg.textContent='';
   /* A stale "use it anyway" from a previous address must not survive a mode
      change -- it would offer to commit a verdict that was about something
      else entirely. */
   var a=document.getElementById('anyway'); if(a){a.hidden=true}
   document.getElementById('go').disabled=false;}
 L.onclick=function(){pick('local')}; R.onclick=function(){pick('remote')};
 var GO=document.getElementById('go'), ANY=document.getElementById('anyway');
 var J={'Content-Type':'application/json'};
 function say(color,text){msg.style.color=color;msg.textContent=text}
 function post(path,body){
   return fetch(path,{method:'POST',headers:J,body:JSON.stringify(body)})
     .then(function(r){return r.json().then(function(j){return {s:r.status,j:j}})});
 }

 /* Writing the config is the last step in every branch, so it lives in one
    place. Reached directly for local mode, after a clean check for remote, and
    from "use it anyway" when the check was unhappy. */
 function save(body){
   ANY.hidden=true; GO.disabled=true; say('#e8e8ea','Saving\\u2026');
   post('/api/config',body).then(function(o){
     if(o.s===200){say('#9ae6b4','Saved. Restarting the kiosk\\u2026');return}
     GO.disabled=false;
     /* Name the failure. A blank screen here is indistinguishable from a
        crash, which is exactly what bring-up hit. */
     say('#ffb4a2',(o.j&&o.j.error==='bad_url')
       ? 'That address does not look like a URL. Include http:// or https://.'
       : 'Could not save the setting ('+((o.j&&o.j.error)||o.s)+').');
   }).catch(function(){GO.disabled=false;
     say('#ffb4a2','The setup service is not responding.');});
 }

 /* The three states requirement 2 wants kept apart, because each one asks the
    user for something different. Guessing between them is how a kiosk with no
    keyboard becomes a brick. */
 function check(url,body){
   GO.disabled=true; ANY.hidden=true;
   say('#e8e8ea','Checking that address\\u2026');
   post('/api/check',{url:url}).then(function(o){
     GO.disabled=false;
     var v=o.j&&o.j.verdict;
     if(o.s===200&&v==='ok'){
       var extra='';
       if(typeof o.j.agents==='number'&&typeof o.j.backends==='number')
         extra=' \\u2014 '+o.j.agents+' agents, '+o.j.backends+' backends';
       say('#9ae6b4','Found a taOS controller'+extra+'.');
       save(body); return;
     }
     if(o.s===400&&o.j&&o.j.error==='bad_url'){
       say('#ffb4a2','That address does not look like a URL. Include http:// or https://.');
       return;
     }
     if(o.s===429){
       say('#ffb4a2','Too many checks just now. Wait a moment and try again.');
       return;
     }
     if(v==='not_a_controller'){
       say('#f0c674','Something answered at that address, but it is not a taOS '
         +'controller. A controller normally listens on port 6969.');
     }else{
       say('#f0c674','Nothing answered at that address. Check that the controller '
         +'is running and that this phone is on the same network.');
     }
     /* Not an error state -- an unverified one. The address may be right and
        the controller simply off. Let it through, deliberately. */
     ANY.hidden=false;
   }).catch(function(){GO.disabled=false;
     say('#ffb4a2','The setup service is not responding.');});
 }

 function collect(){
   if(!mode){say('#ffb4a2','Choose one of the two options above.');return null}
   var body={mode:mode};
   if(mode==='remote'){
     body.url=document.getElementById('url').value.trim();
     if(!body.url){say('#ffb4a2','Enter the controller address.');return null}
   }
   return body;
 }

 GO.onclick=function(){
   var body=collect(); if(!body) return;
   /* Local mode has nothing to check: the controller is this device, and it
      may not be installed yet at the moment this form is filled in. */
   if(body.mode==='local'){save(body); return}
   check(body.url,body);
 };
 ANY.onclick=function(){var body=collect(); if(body) save(body)};
</script>
"""


def main() -> int:
    httpd = ThreadingHTTPServer((BIND_HOST, BIND_PORT), _Handler)
    log(f"taos-firstrun listening on {BIND_HOST}:{BIND_PORT}, upstream {UPSTREAM}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
