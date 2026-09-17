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

import fcntl
import io
import json
import mmap
import struct
import os
import selectors
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import libcamera as lc
from PIL import Image, ImageStat

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

# ---------------------------------------------------------------------------
# EXPOSURE AND COLOUR, done here because libcamera cannot do them on this
# device. Its own warnings say why, per sensor:
#
#   IPASoft: Failed to create camera sensor helper for imx471
#   No static properties available for 'imx471'
#   No sensor delays found in the database
#   Configuration file 'imx471.yaml' not found for IPA module
#
# Without a sensor helper the soft IPA cannot convert a gain code to a real
# gain, so its AE has no working handle on the sensor -- which is why frames
# come out at a mean of ~9/255 in a room that is merely dim. The proper fix is
# to add imx471/s5kjn1 to libcamera's sensor helpers and ship tuning files;
# that is upstream work. Until then this service closes the loop itself.
#
# ⚠ THE ORDER MATTERS. Exposure is corrected AT THE SENSOR (real photons) and
# only what is left over is corrected in software. Brightening a dark frame in
# PIL amplifies its noise; asking the sensor for more light does not.
#: Mean luminance to aim for, 0-255. 100 is a touch under the midpoint: phone
#: scenes are usually backlit, and chasing 128 blows the highlights out.
AE_TARGET = 100.0
#: Only act on a miss bigger than this, or the loop hunts forever on noise.
AE_DEADBAND = 8.0
#: Per-step cap. A full correction in one frame reads as a strobe.
AE_STEP = 0.35
AE_EXPOSURE_RANGE = (200, 66_000)      # microseconds
AE_GAIN_RANGE = (1.0, 16.0)
#: Grey-world AWB. Clamped hard: an unclamped grey-world on a scene that really
#: IS one colour (a wall, a sky) tints the whole frame the other way.
AWB_CLAMP = (0.55, 1.9)
AWB_SMOOTH = 0.25
#: Ceiling on software gain. Past this it is all noise, and a grey mush reads
#: worse than an honestly dark picture.
SOFT_GAIN_MAX = 8.0

PHOTOS.mkdir(parents=True, exist_ok=True)


# A dmabuf mapped straight with mmap() is NOT coherent with the device that
# wrote it. On this SoC the camera writes through the IOMMU while the CPU holds
# stale cache lines, so a read without this bracket returns a frame that is
# part new and part old -- which is the corruption Jay reported in the gallery,
# and it is a RACE, so some frames look fine and only some are torn.
#
# libcamera's own MappedFrameBuffer does exactly this; the python bindings on
# this device do not ship libcamera.utils, so it is done by hand.
DMA_BUF_SYNC_READ = 1 << 0
DMA_BUF_SYNC_START = 0
DMA_BUF_SYNC_END = 1 << 2
DMA_BUF_IOCTL_SYNC = 0x40086200          # _IOW('b', 0, struct dma_buf_sync)


def _dma_sync(fd: int, end: bool) -> None:
    flags = DMA_BUF_SYNC_READ | (DMA_BUF_SYNC_END if end else DMA_BUF_SYNC_START)
    try:
        fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack("Q", flags))
    except OSError:
        # A heap that does not implement the ioctl is not a reason to stop
        # capturing; it only means the frame may tear.
        pass


def _rgb_from_plane(data: bytes, stride: int, width: int, height: int) -> Image.Image:
    """ABGR8888 with a PADDED stride -- front 2304 px for 2296 visible.

    Decoding at the VISIBLE width instead of the stride shears the image
    diagonally, which reads as a broken sensor rather than as arithmetic.
    """
    img = Image.frombytes("RGBA", (stride // 4, height), data).convert("RGB")
    return img.crop((0, 0, width, height))


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
        self.exposure = 16_000.0      # us, a sane indoor starting point
        self.gain = 2.0
        self.awb = [1.0, 1.0]         # red, blue gains
        self.controls_ok = True       # cleared if the sensor refuses controls
        self.mean = 0.0
        self.soft_gain = 1.0
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
        status = cfg.validate()
        cam.configure(cfg)
        # RE-FETCH THE STREAM CONFIG AFTER configure(), and keep `cfg` alive.
        #
        # `cfg.at(0)` hands back a reference INTO the configuration, and
        # validate()/configure() can move what it points at. Reading the old
        # reference afterwards returned a width of 2304 -- which is stride/4,
        # not any size libcamera ever validated (2296) -- so frames were
        # decoded at the wrong geometry: vertical-striped noise across the
        # left third and black for the rest, which is the corruption Jay saw.
        self.cfg = cfg
        scfg = cfg.at(0)
        self.scfg = scfg
        self.width = int(scfg.size.width)
        self.height = int(scfg.size.height)
        self.stride = int(scfg.stride)
        self.frame_bytes = self.stride * self.height
        # A size that came back different from the one asked for is not fatal,
        # but it must be VISIBLE: it changes every buffer arithmetic below.
        if (self.width, self.height) != PREVIEW:
            print("camerad: asked %dx%d, got %dx%d (validate: %s)"
                  % (PREVIEW[0], PREVIEW[1], self.width, self.height, status),
                  flush=True)
        # RAW MEANS THE CONVERTER IS MISSING, AND RAW MUST NEVER REACH A JPEG.
        #
        # A converted stream is ABGR8888, so its stride is 4 bytes per pixel.
        # This camera came back with stride 2880 for a 2304-wide frame --
        # 2304 x 1.25, which is 10-bit PACKED BAYER. libcamera had registered
        # the camera with no software ISP attached, so `Viewfinder` could only
        # be satisfied by the sensor's own raw output, and validate() said
        # Adjusted rather than failing.
        #
        # Decoding that as ABGR is what produced the striped left third and
        # black remainder in the gallery. It is not recoverable in here: the
        # converter is chosen when the CameraManager enumerates, which has
        # already happened. Exiting lets systemd bring the process back with a
        # fresh manager, which is the one thing that fixes it.
        if self.stride < self.width * 4:
            print("camerad: FATAL: %s came up RAW (stride %d for %d px = packed "
                  "bayer, not ABGR). libcamera registered it with no software "
                  "ISP. Exiting so systemd restarts with a fresh CameraManager."
                  % (self.cam.id, self.stride, self.width), flush=True)
            # Not an exception: this thread is not the one that can fix it, and
            # a raise here would leave the service up and serving garbage.
            os._exit(75)          # EX_TEMPFAIL
        print("camerad: %s at %dx%d stride %d, %d bytes/frame"
              % (self.cam.id.split("/")[-1], self.width, self.height,
                 self.stride, self.frame_bytes), flush=True)
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
            self._apply_controls(r)
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
                        self._apply_controls(req)
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

    def _apply_controls(self, req) -> None:
        """Ask the sensor for the exposure this service worked out.

        Wrapped because a sensor that refuses a control raises, and a raise
        here would kill the capture loop over a nicety. If it refuses once it
        is not asked again, and _publish falls back to correcting in software.
        """
        if not self.controls_ok:
            return
        try:
            req.set_control(lc.controls.ExposureTime, int(self.exposure))
            req.set_control(lc.controls.AnalogueGain, float(self.gain))
        except Exception as exc:  # noqa: BLE001
            self.controls_ok = False
            print("camerad: sensor refused exposure controls (%s); "
                  "falling back to software gain" % exc, flush=True)

    def _auto_expose(self, mean: float) -> None:
        """One step of AE, at the sensor.

        Exposure time first, gain only once time is spent: gain is noise, time
        is light. Coming back DOWN reverses that order for the same reason.
        """
        self.mean = mean
        if abs(mean - AE_TARGET) <= AE_DEADBAND:
            return
        # A frame with nothing in it at all gives no usable ratio.
        want = AE_TARGET / max(mean, 1.0)
        want = 1.0 + (want - 1.0) * AE_STEP
        if want > 1.0:
            room = AE_EXPOSURE_RANGE[1] / self.exposure
            take = min(want, room)
            self.exposure *= take
            left = want / take
            if left > 1.0:
                self.gain = min(self.gain * left, AE_GAIN_RANGE[1])
        else:
            floor = AE_GAIN_RANGE[0] / self.gain
            take = max(want, floor)
            self.gain *= take
            left = want / take
            if left < 1.0:
                self.exposure = max(self.exposure * left, AE_EXPOSURE_RANGE[0])

    def _auto_white_balance(self, img: Image.Image) -> Image.Image:
        """Grey world, smoothed, clamped.

        The scene averages to grey, so whatever the channel means disagree
        about is the cast. Smoothed because a per-frame correction visibly
        breathes on a preview, and clamped because a genuinely monochrome
        scene is not a cast.
        """
        stat = ImageStat.Stat(img)
        r, g, b = stat.mean[:3]
        if g < 2:
            return img
        want_r = max(AWB_CLAMP[0], min(AWB_CLAMP[1], g / max(r, 1.0)))
        want_b = max(AWB_CLAMP[0], min(AWB_CLAMP[1], g / max(b, 1.0)))
        self.awb[0] += (want_r - self.awb[0]) * AWB_SMOOTH
        self.awb[1] += (want_b - self.awb[1]) * AWB_SMOOTH
        if abs(self.awb[0] - 1.0) < 0.02 and abs(self.awb[1] - 1.0) < 0.02:
            return img
        # A per-channel point map: one pass, no float image, no numpy.
        red = [min(255, int(i * self.awb[0])) for i in range(256)]
        blue = [min(255, int(i * self.awb[1])) for i in range(256)]
        return img.point(red + list(range(256)) + blue)

    def _publish(self, fb) -> None:
        pl = fb.planes[0]
        mm = self.maps[id(fb)]
        _dma_sync(pl.fd, end=False)
        data = mm[pl.offset:pl.offset + pl.length]
        _dma_sync(pl.fd, end=True)
        # A short buffer is a torn frame, not a picture. Better to drop it
        # than to hand the gallery something decoded from the wrong length.
        if len(data) < self.frame_bytes:
            return
        img = _rgb_from_plane(data[:self.frame_bytes], self.stride,
                              self.width, self.height)
        # Measured on a SHRUNK copy: the numbers are the same to within a
        # rounding error and it costs a fraction of a full-frame pass, which
        # matters when it runs on every preview frame.
        small = img.resize((160, 120))
        mean = sum(ImageStat.Stat(small).mean[:3]) / 3.0
        self._auto_expose(mean)
        img = self._auto_white_balance(img)
        # SOFTWARE GAIN IS KEYED ON THE RESULT, not on whether the sensor
        # accepted the control. Measured on this device: ExposureTime and
        # AnalogueGain are ACCEPTED and then have no effect -- exposure sat
        # pinned at 66000us with gain 16.0 while the frame stayed at a mean of
        # 9/255. A control that returns success and changes nothing is
        # indistinguishable from a working one unless you read the picture
        # back, which is what this does.
        if mean > 0.5 and mean < AE_TARGET - AE_DEADBAND:
            want = min(SOFT_GAIN_MAX, AE_TARGET / mean)
            self.soft_gain += (want - self.soft_gain) * AE_STEP
        elif mean > AE_TARGET + AE_DEADBAND:
            want = max(1.0, AE_TARGET / mean)
            self.soft_gain += (want - self.soft_gain) * AE_STEP
        if self.soft_gain > 1.02:
            g = self.soft_gain
            img = img.point([min(255, int(i * g)) for i in range(256)] * 3)
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
                s = _SESSION
                return self._json({
                    "ok": err is None, "cameras": sorted(_cameras()),
                    "active": _WHICH, "error": err,
                    # The exposure loop, visible. A dark frame is either a dark
                    # room or a stuck loop, and these tell them apart.
                    "exposure_us": int(s.exposure) if s else None,
                    "gain": round(s.gain, 2) if s else None,
                    "mean": round(s.mean, 1) if s else None,
                    "awb": [round(x, 3) for x in s.awb] if s else None,
                    "sensor_controls": s.controls_ok if s else None,
                    "size": ("%dx%d" % (s.width, s.height)) if s else None,
                    "soft_gain": round(s.soft_gain, 2) if s else None,
                })
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
            if u.path == "/back":
                # THE WAY OUT. Until there is a nav bar, an app on its own
                # workspace is a room with no door: the kiosk is on workspace
                # 1 and nothing on screen goes back to it. camerad runs as the
                # same user as the compositor, so it can ask sway directly.
                sock = sorted(Path("/run/user/%d" % os.getuid()).glob("sway-ipc.*.sock"))
                if not sock:
                    return self._json({"error": "no sway socket"}, 500)
                env = dict(os.environ, SWAYSOCK=str(sock[0]))
                rc = subprocess.run(["swaymsg", "workspace", "1"], env=env,
                                    capture_output=True, timeout=5)
                if rc.returncode != 0:
                    return self._json({"error": rc.stderr.decode()[:200]}, 500)
                return self._json({"ok": True})
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
