#!/usr/bin/env python3
"""taos-camerad -- the device half of taOS's camera.

WHY THIS EXISTS AS A DEVICE SERVICE rather than inside the taOS controller:
the camera is device hardware, and per Jay's ownership ruling anything that
must survive a reflash lives in taOSmobile as a file. The controller gets a
page; this gets the sensors.

WHY libcamera AND NOT megapixels: megapixels 2.1 is packaged, but it drives
V4L2 itself through libmegapixels, which needs a per-device config -- and
libmegapixels ships configs for pinephone, librem5, midas and a handful of
xiaomis, NOT for spacewar. libcamera is PROVEN on this handset (both sensors
capture, 2026-09-17), so this builds on the thing that works rather than on a
config nobody has written yet.

WHAT IS DELIBERATELY NOT HERE:
- Autofocus. No VCM/lens-actuator driver is bound on either sensor, so there is
  nothing to drive. That is DT + driver work of the same shape as the s5kjn1
  MCLK fix, and claiming an AF button that does nothing would be worse than
  having none.
- A tuning file. Both sensors report `Failed to create camera sensor helper`,
  which is why frames carry a magenta cast: libcamera has no sensor helper for
  imx471 or s5kjn1, so its AWB has nothing to anchor to. Upstreamable, bounded,
  and separate from this.

THE ONE THING TO KNOW ABOUT EXPOSURE: a cold capture is nearly black. The
software ISP's AE converges over ~20 frames, so a still must be taken from a
RUNNING preview, never from a freshly opened camera. That is why capture reads
the live stream instead of configuring a one-shot.
"""
from __future__ import annotations

import io
import json
import mmap
import os
import selectors
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import libcamera as lc
from PIL import Image

HOST = "127.0.0.1"
PORT = int(os.environ.get("TAOS_CAMERAD_PORT", "6971"))
#: The app page, beside this file when run from the repo and in /usr/lib/taos
#: once installed. Served by camerad itself rather than by the controller: the
#: camera is device hardware, and this makes the camera the first taOS app to
#: run as its OWN window (chromium --app), which is the shape every app is
#: meant to take.
APP_HTML = Path(os.environ.get("TAOS_CAMERAD_APP", str(Path(__file__).with_name("app.html"))))
PHOTOS = Path(os.environ.get("TAOS_CAMERAD_DIR", str(Path.home() / "Pictures" / "taOS")))
PREVIEW = (1280, 960)
#: How long a still may wait for a frame newer than the shutter press. A still
#: must not be the frame that was already on screen when the finger landed.
SHUTTER_TIMEOUT = 2.0
#: A cold open costs about a second before the first frame lands.
FIRST_FRAME_TIMEOUT = 5.0

PHOTOS.mkdir(parents=True, exist_ok=True)


def _rgb_from_plane(data: bytes, stride: int, size) -> Image.Image:
    """ABGR8888 with a PADDED stride -- front 2304 px for 2296 visible.

    Decoding at the VISIBLE width instead of the stride shears the image
    diagonally, which reads as a broken sensor rather than as arithmetic.
    """
    img = Image.frombytes("RGBA", (stride // 4, size.height), data).convert("RGB")
    return img.crop((0, 0, size.width, size.height))


class CameraSession:
    """One acquired camera, running continuously so AE stays converged."""

    _next_tag = 1

    def __init__(self, cam) -> None:
        self.cam = cam
        # A COOKIE PER SESSION. CameraManager.get_ready_requests() is
        # MANAGER-WIDE, not per camera: after a front->rear switch it hands
        # back the requests the PREVIOUS camera still had in flight, and
        # queueing one of those to the new camera fails with "Request was not
        # created by this camera" / EXDEV. The loop below drops anything that
        # is not its own.
        self.tag = CameraSession._next_tag
        CameraSession._next_tag += 1
        self.error: str | None = None
        self.lock = threading.Lock()
        self.latest: bytes | None = None      # JPEG
        self.latest_at = 0.0
        self.seq = 0
        self.stop_flag = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        cam = self.cam
        cam.acquire()
        cfg = cam.generate_configuration([lc.StreamRole.Viewfinder])
        scfg = cfg.at(0)
        scfg.size = lc.Size(*PREVIEW)
        cfg.validate()
        cam.configure(cfg)
        self.scfg = scfg
        self.stream = scfg.stream
        self.alloc = lc.FrameBufferAllocator(cam)
        self.alloc.allocate(self.stream)
        self.bufs = self.alloc.buffers(self.stream)
        # mmap ONCE per buffer. libcamera hands back the same set of buffers
        # for the life of the session, so re-mapping every frame would be a
        # syscall pair per frame for no gain.
        self.maps = {}
        for b in self.bufs:
            pl = b.planes[0]
            self.maps[id(b)] = mmap.mmap(
                pl.fd, pl.length + pl.offset, mmap.MAP_SHARED, mmap.PROT_READ
            )
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        cam, stream = self.cam, self.stream
        reqs = []
        for b in self.bufs:
            r = cam.create_request(self.tag)
            r.add_buffer(stream, b)
            reqs.append(r)
        cam.start()
        for r in reqs:
            cam.queue_request(r)
        sel = selectors.DefaultSelector()
        sel.register(cam.manager.event_fd if hasattr(cam, "manager") else _CM.event_fd,
                     selectors.EVENT_READ)
        try:
            while not self.stop_flag.is_set():
                sel.select(1)
                for req in _CM.get_ready_requests():
                    if req.cookie != self.tag:
                        continue        # the other camera's, mid-switch
                    fb = req.buffers.get(stream)
                    if fb is not None:
                        self._publish(fb)
                    req.reuse()
                    if not self.stop_flag.is_set():
                        cam.queue_request(req)
        except Exception as exc:  # noqa: BLE001
            # RECORDED, not swallowed. This runs in a thread, so an exception
            # here used to kill the loop silently and the only symptom was
            # /capture answering "no frame" -- which reads as a dead sensor.
            self.error = "%s: %s" % (type(exc).__name__, exc)
            print("camerad loop failed: %s" % self.error, flush=True)
        finally:
            cam.stop()
            cam.release()

    def _publish(self, fb) -> None:
        pl = fb.planes[0]
        mm = self.maps[id(fb)]
        data = mm[pl.offset:pl.offset + pl.length]
        img = _rgb_from_plane(data, self.scfg.stride, self.scfg.size)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80)
        with self.lock:
            self.latest = out.getvalue()
            self.latest_at = time.time()
            self.seq += 1

    def frame(self):
        with self.lock:
            return self.latest, self.seq

    def frame_after(self, seq: int, timeout: float):
        """A frame STRICTLY newer than `seq`.

        The shutter must not hand back the frame that was already on screen
        when the finger landed -- on a 10fps preview that is up to 100ms of
        staleness, which is exactly the motion a user is trying to catch.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if self.seq > seq and self.latest:
                    return self.latest
            time.sleep(0.02)
        with self.lock:
            return self.latest

    def stop(self) -> None:
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=5)
        for mm in getattr(self, "maps", {}).values():
            try:
                mm.close()
            except Exception:
                pass


_CM = lc.CameraManager.singleton()
_STATE_LOCK = threading.Lock()
_SESSION: CameraSession | None = None
_WHICH = "front"


def _cameras() -> dict:
    """front/rear by the CCI bus in the camera id.

    Not by list position: `cam -l` numbering and the CameraManager's order are
    not promised to agree, and an older note in the checkpoint had them the
    other way round. cci@ac4a000 is the imx471 (front), cci@ac4b000 the s5kjn1.
    """
    out = {}
    for c in _CM.cameras:
        if "ac4a000" in c.id:
            out["front"] = c
        elif "ac4b000" in c.id:
            out["rear"] = c
    return out


def _session(which: str | None = None) -> CameraSession:
    global _SESSION, _WHICH
    with _STATE_LOCK:
        want = which or _WHICH
        if _SESSION is not None and want == _WHICH:
            return _SESSION
        if _SESSION is not None:
            _SESSION.stop()
            _SESSION = None
        cams = _cameras()
        if want not in cams:
            raise KeyError(want)
        _SESSION = CameraSession(cams[want])
        _WHICH = want
        _SESSION.start()
        return _SESSION


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # journald carries enough already
        pass

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                if not APP_HTML.is_file():
                    return self._json({"error": "app.html is missing"}, 500)
                body = APP_HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                # The page changes when the service is reinstalled and the
                # window is long-lived, so it must not be cached.
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if u.path == "/health":
                err = _SESSION.error if _SESSION else None
                return self._json({"ok": err is None, "cameras": sorted(_cameras()),
                                   "active": _WHICH, "error": err})
            if u.path == "/frame.jpg":
                s = _session(q.get("cam", [None])[0])
                # WAIT for the first frame rather than 503-ing. Opening a
                # camera takes about a second, and a page that asks for a
                # frame the moment it loads would otherwise get an error on
                # every cold start and look broken when it is merely early.
                jpg = s.frame_after(-1, FIRST_FRAME_TIMEOUT)
                if not jpg:
                    return self._json({"error": "no frame yet"}, 503)
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                return self.wfile.write(jpg)
            if u.path == "/preview.mjpg":
                return self._stream(q.get("cam", [None])[0])
            if u.path == "/photos":
                shots = sorted(PHOTOS.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
                return self._json({"photos": [
                    {"name": p.name, "at": int(p.stat().st_mtime), "bytes": p.stat().st_size}
                    for p in shots
                ]})
            if u.path.startswith("/photo/"):
                return self._serve_photo(u.path[len("/photo/"):])
            return self._json({"error": "not found"}, 404)
        except KeyError as exc:
            return self._json({"error": "no such camera: %s" % exc.args[0]}, 400)
        except BrokenPipeError:
            return
        except Exception as exc:  # noqa: BLE001 -- the detail IS the report
            return self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/capture":
                s = _session(q.get("cam", [None])[0])
                _, seq = s.frame()
                jpg = s.frame_after(seq, SHUTTER_TIMEOUT)
                if not jpg:
                    # Say WHY. "no frame" alone sent me to the sensor when the
                    # fault was a request queued to the wrong camera.
                    return self._json({"error": s.error or "no frame"}, 503)
                name = datetime.now().strftime("IMG_%Y%m%d_%H%M%S_%f")[:-3] + ".jpg"
                (PHOTOS / name).write_bytes(jpg)
                return self._json({"ok": True, "name": name, "bytes": len(jpg), "cam": _WHICH})
            if u.path == "/switch":
                want = q.get("cam", [None])[0]
                if want not in ("front", "rear"):
                    return self._json({"error": "cam must be front or rear"}, 400)
                _session(want)
                return self._json({"ok": True, "active": _WHICH})
            return self._json({"error": "not found"}, 404)
        except KeyError as exc:
            return self._json({"error": "no such camera: %s" % exc.args[0]}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 500)

    def do_DELETE(self) -> None:
        u = urlparse(self.path)
        if not u.path.startswith("/photo/"):
            return self._json({"error": "not found"}, 404)
        target = self._resolve(u.path[len("/photo/"):])
        if target is None:
            return self._json({"error": "not found"}, 404)
        target.unlink()
        return self._json({"ok": True, "deleted": target.name})

    def _resolve(self, name: str):
        """A name, never a path.

        `../` in a delete would reach out of the gallery, and this service
        answers unauthenticated on loopback -- the page in front of it is the
        only thing between it and whoever is holding the phone.
        """
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        p = (PHOTOS / name).resolve()
        if p.parent != PHOTOS.resolve() or not p.is_file():
            return None
        return p

    def _serve_photo(self, name: str) -> None:
        p = self._resolve(name)
        if p is None:
            return self._json({"error": "not found"}, 404)
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, which) -> None:
        s = _session(which)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=taosframe")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = -1
        while True:
            jpg, seq = s.frame()
            if jpg is None or seq == last:
                time.sleep(0.02)
                continue
            last = seq
            self.wfile.write(b"--taosframe\r\nContent-Type: image/jpeg\r\n")
            self.wfile.write(b"Content-Length: %d\r\n\r\n" % len(jpg))
            self.wfile.write(jpg)
            self.wfile.write(b"\r\n")


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("taos-camerad on http://%s:%d, photos in %s" % (HOST, PORT, PHOTOS), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
