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
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
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
  <small>Enter the controller address. Picking from an account is not available
  yet &mdash; taos.my has no endpoint that lists an account&rsquo;s controllers.</small>
</div>
<button id="go">Continue</button>
<div id="msg" role="status" aria-live="polite"></div>
</main>
<script>
 var mode=null;
 var L=document.getElementById('local'), R=document.getElementById('remote');
 var box=document.getElementById('remotebox'), msg=document.getElementById('msg');
 function pick(m){mode=m;L.classList.toggle('sel',m==='local');
   R.classList.toggle('sel',m==='remote');box.hidden=(m!=='remote');msg.textContent='';}
 L.onclick=function(){pick('local')}; R.onclick=function(){pick('remote')};
 document.getElementById('go').onclick=function(){
   if(!mode){msg.textContent='Choose one of the two options above.';return}
   var body={mode:mode};
   if(mode==='remote'){body.url=document.getElementById('url').value.trim();
     if(!body.url){msg.textContent='Enter the controller address.';return}}
   msg.textContent='Saving\\u2026';
   fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
                        body:JSON.stringify(body)})
    .then(function(r){return r.json().then(function(j){return {s:r.status,j:j}})})
    .then(function(o){
      if(o.s===200){msg.style.color='#9ae6b4';
        msg.textContent='Saved. Restarting the kiosk\\u2026';return}
      /* Name the failure. A blank screen here is indistinguishable from a
         crash, which is exactly what bring-up hit. */
      msg.style.color='#ffb4a2';
      msg.textContent=(o.j&&o.j.error==='bad_url')
        ? 'That address does not look like a URL. Include http:// or https://.'
        : 'Could not save the setting ('+((o.j&&o.j.error)||o.s)+').';})
    .catch(function(){msg.style.color='#ffb4a2';
      msg.textContent='The setup service is not responding.';});
 };
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
