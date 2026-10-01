"""Tests for taos-sttd and install-stt.sh's hash check, with a FAKE recognizer
(no sherpa, no model, runs anywhere). Malformed requests first, because they
are where a daemon on a phone gets hurt; then the happy path; then health.

Mutation check: TAOS_STTD_PATH points the whole file at a mutated copy of the
daemon, e.g.
    sed 's/if n > MAX_BODY:/if False:/' taos-sttd > /tmp/m; TAOS_STTD_PATH=/tmp/m pytest ...
and tests must go red. See the commit message for the runs that were made.
"""
import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.environ.get("TAOS_STTD_PATH", os.path.join(HERE, "taos-sttd"))


def _load():
    loader = importlib.machinery.SourceFileLoader("taos_sttd_under_test", DAEMON)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sttd = _load()


class FakeRecognizer:
    engine = "fake-engine"
    model = "fake-model"

    def __init__(self, loaded=True, text="hello world"):
        self.loaded = loaded
        self.load_ms = 7
        self.text = text
        self.calls = []
        self.gate = None          # threading.Event: transcribe blocks until set
        self.entered = threading.Semaphore(0)
        self.active = 0
        self.max_active = 0
        self.boom = False
        self._m = threading.Lock()

    def transcribe(self, pcm):
        with self._m:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(pcm)
        self.entered.release()
        try:
            if self.gate is not None:
                assert self.gate.wait(10)
            if self.boom:
                raise RuntimeError("engine blew up")
            return self.text
        finally:
            with self._m:
                self.active -= 1


@pytest.fixture
def daemon():
    rec = FakeRecognizer()
    srv = sttd.make_server(rec, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.rec = rec
    srv.port = srv.server_address[1]
    yield srv
    srv.shutdown()
    srv.server_close()


def raw(srv, head_lines, body=b"", timeout=3.0, close_after_send=False):
    """Send a hand-built request; return (status, body_bytes). status None if
    the server gave no response within `timeout` (it was waiting for bytes)."""
    s = socket.create_connection(("127.0.0.1", srv.port), timeout=timeout)
    try:
        s.sendall(("\r\n".join(head_lines) + "\r\n\r\n").encode("latin-1") + body)
        if close_after_send:
            s.shutdown(socket.SHUT_WR)
        buf = b""
        try:
            while True:
                c = s.recv(65536)
                if not c:
                    break
                buf += c
        except socket.timeout:
            if not buf:
                return None, b""
        if not buf:
            return None, b""
        head, _, rest = buf.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), rest
    finally:
        s.close()


def post(srv, length_header, body=b"", extra=(), **kw):
    lines = ["POST /stt HTTP/1.0", "Host: x"]
    if length_header is not None:
        lines.append("Content-Length: " + length_header)
    lines += list(extra)
    return raw(srv, lines, body, **kw)


def pcm(n_samples):
    return b"\x01\x00" * n_samples


def wait_idle(srv, secs=3.0):
    """All admitted requests finished (slot semaphore back to full)."""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if srv.slots._value == sttd.MAX_INFLIGHT:
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------ malformed first ---

def test_no_content_length_is_411(daemon):
    st, _ = post(daemon, None)
    assert st == 411 and daemon.rec.calls == []


def test_chunked_without_content_length_is_411(daemon):
    st, _ = post(daemon, None, extra=["Transfer-Encoding: chunked"],
                 body=b"4\r\nabcd\r\n0\r\n\r\n")
    assert st == 411 and daemon.rec.calls == []


@pytest.mark.parametrize("bad", ["abc", "", "1.5", "+4", "0x10", "4 4", "1e3", "-0"])
def test_non_numeric_content_length_is_400(daemon, bad):
    st, _ = post(daemon, bad, body=pcm(2))
    assert st == 400 and daemon.rec.calls == []


def test_non_ascii_digit_content_length_is_400(daemon):
    # "²" is str.isdigit() == True; the daemon must not take it as a number
    lines = ["POST /stt HTTP/1.0", "Host: x"]
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=3)
    s.sendall(("\r\n".join(lines) + "\r\nContent-Length: ").encode() + b"\xb2\r\n\r\n")
    head = s.recv(4096)
    s.close()
    assert int(head.split(b" ")[1]) == 400 and daemon.rec.calls == []


@pytest.mark.parametrize("neg", ["-1", "-2", "-960000"])
def test_negative_content_length_is_400(daemon, neg):
    st, _ = post(daemon, neg)
    assert st == 400 and daemon.rec.calls == []


def test_conflicting_duplicate_content_length_is_400(daemon):
    st, _ = post(daemon, "4", body=pcm(2), extra=["Content-Length: 8"])
    assert st == 400 and daemon.rec.calls == []


@pytest.mark.parametrize("n", [960001, 960002, 10 ** 9, 10 ** 30])
def test_over_cap_is_413_without_reading_the_body(daemon, n):
    # Only headers are sent. A daemon that tries to read the body first waits
    # for bytes that never come and the client sees NO status (None) instead.
    st, _ = post(daemon, str(n), body=b"", timeout=2.0)
    assert st == 413
    assert daemon.rec.calls == []


def test_over_cap_does_not_hold_a_slot(daemon):
    for _ in range(sttd.MAX_INFLIGHT + 2):
        assert post(daemon, "960001", timeout=2.0)[0] == 413
    assert wait_idle(daemon)


def test_odd_byte_count_is_400(daemon):
    st, _ = post(daemon, "5", body=b"\x00" * 5)
    assert st == 400 and daemon.rec.calls == []


def test_odd_byte_count_is_refused_before_the_body_is_read(daemon):
    st, _ = post(daemon, "3", body=b"", timeout=2.0)
    assert st == 400


def test_empty_body_is_400(daemon):
    st, _ = post(daemon, "0")
    assert st == 400 and daemon.rec.calls == []


@pytest.mark.parametrize("sr", ["8000", "44100", "abc", "", "16000.0", "-16000"])
def test_bad_sample_rate_is_400(daemon, sr):
    st, _ = post(daemon, "4", body=pcm(2), extra=["X-Sample-Rate: " + sr])
    assert st == 400 and daemon.rec.calls == []


def test_client_sends_fewer_bytes_than_declared_then_closes_no_decode(daemon):
    st, _ = post(daemon, "1000", body=b"\x01\x00" * 200, close_after_send=True)
    assert st is None                      # no reply to a dead upload
    assert wait_idle(daemon)
    assert daemon.rec.calls == []


def test_client_that_stalls_mid_upload_is_dropped_no_decode(daemon, monkeypatch):
    monkeypatch.setattr(sttd.Handler, "timeout", 0.4)
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=5)
    s.sendall(b"POST /stt HTTP/1.0\r\nContent-Length: 1000\r\n\r\n" + b"\x01\x00" * 100)
    # never send the rest, never close: the daemon must give up by itself
    assert wait_idle(daemon, 4.0)
    s.close()
    assert daemon.rec.calls == []


def test_unknown_paths_and_methods(daemon):
    assert raw(daemon, ["GET /nope HTTP/1.0"])[0] == 404
    assert raw(daemon, ["POST /health HTTP/1.0", "Content-Length: 2"], b"ab")[0] == 404
    assert raw(daemon, ["PUT /stt HTTP/1.0", "Content-Length: 2"], b"ab")[0] == 501


def test_malformed_requests_leave_the_daemon_serving(daemon):
    post(daemon, "abc")
    post(daemon, "960001", timeout=2.0)
    post(daemon, "3", body=b"abc")
    st, body = post(daemon, "4", body=pcm(2))
    assert st == 200 and json.loads(body)["text"] == "hello world"


# ------------------------------------------------------------ happy path ---

def test_stt_returns_text_and_passes_the_exact_bytes(daemon):
    audio = bytes(range(256)) * 8
    st, body = post(daemon, str(len(audio)), body=audio)
    assert st == 200
    assert json.loads(body) == {"text": "hello world"}
    assert daemon.rec.calls == [audio]


def test_stt_accepts_sample_rate_16000_header(daemon):
    st, _ = post(daemon, "4", body=pcm(2), extra=["X-Sample-Rate: 16000"])
    assert st == 200


def test_stt_accepts_exactly_the_cap(daemon):
    audio = pcm(sttd.MAX_BODY // 2)
    assert len(audio) == 960000
    st, body = post(daemon, "960000", body=audio, timeout=10.0)
    assert st == 200 and len(daemon.rec.calls[0]) == 960000


def test_stt_non_ascii_text_is_utf8_json(daemon):
    daemon.rec.text = "café naïve"
    st, body = post(daemon, "4", body=pcm(2))
    assert json.loads(body.decode("utf-8"))["text"] == "café naïve"


def test_decodes_never_overlap(daemon):
    daemon.rec.gate = threading.Event()
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(daemon, "4", body=pcm(2), timeout=8)[0]))
          for _ in range(3)]
    for t in ts:
        t.start()
    assert daemon.rec.entered.acquire(timeout=3)      # one decode inside
    time.sleep(0.3)
    assert daemon.rec.active == 1                      # the other two wait on the lock
    daemon.rec.gate.set()
    for t in ts:
        t.join(8)
    assert out == [200, 200, 200] and daemon.rec.max_active == 1


def test_busy_when_all_slots_are_taken(daemon):
    daemon.rec.gate = threading.Event()
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(daemon, "4", body=pcm(2), timeout=8)[0]))
          for _ in range(sttd.MAX_INFLIGHT)]
    for t in ts:
        t.start()
    assert daemon.rec.entered.acquire(timeout=3)
    deadline = time.monotonic() + 3
    while daemon.slots._value and time.monotonic() < deadline:
        time.sleep(0.02)
    assert post(daemon, "4", body=pcm(2))[0] == 503
    daemon.rec.gate.set()
    for t in ts:
        t.join(8)
    assert sorted(out) == [200] * sttd.MAX_INFLIGHT
    assert post(daemon, "4", body=pcm(2))[0] == 200


def test_decode_failure_is_500_and_the_lock_is_released(daemon):
    daemon.rec.boom = True
    st, body = post(daemon, "4", body=pcm(2))
    assert st == 500 and "text" not in json.loads(body)
    daemon.rec.boom = False
    assert post(daemon, "4", body=pcm(2))[0] == 200


# ---------------------------------------------------------------- health ---

def test_health_is_503_before_load_and_200_after(daemon):
    daemon.rec.loaded = False
    st, body = raw(daemon, ["GET /health HTTP/1.0"])
    assert st == 503 and json.loads(body)["ok"] is False
    st, _ = post(daemon, "4", body=pcm(2))
    assert st == 503 and daemon.rec.calls == []
    daemon.rec.loaded = True
    st, body = raw(daemon, ["GET /health HTTP/1.0"])
    j = json.loads(body)
    assert st == 200 and j["ok"] is True
    assert j["engine"] == "fake-engine" and j["model"] == "fake-model"


def test_server_binds_loopback_only():
    srv = sttd.make_server(FakeRecognizer(), port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_default_port_is_6975():
    assert sttd.DEFAULT_PORT == 6975 and sttd.MAX_BODY == 960000


# ------------------------------------------------ install-stt.sh hashes ---

INSTALL = os.path.join(HERE, "install-stt.sh")


def _sh(snippet):
    return subprocess.run(["sh", "-c", ". '%s'; %s" % (INSTALL, snippet)],
                          env=dict(os.environ, INSTALL_STT_SOURCE_ONLY="1"),
                          capture_output=True, text=True)


def test_verify_sha256_accepts_a_good_file_and_rejects_corruption(tmp_path):
    import hashlib
    f = tmp_path / "m.bin"
    f.write_bytes(b"the model")
    good = hashlib.sha256(b"the model").hexdigest()
    assert _sh("verify_sha256 '%s' %s" % (f, good)).returncode == 0
    f.write_bytes(b"the modeL")                       # one flipped byte
    r = _sh("verify_sha256 '%s' %s" % (f, good))
    assert r.returncode != 0 and "MISMATCH" in r.stderr


def test_verify_sha256_rejects_a_missing_file_and_a_short_hash(tmp_path):
    good = "0" * 64
    assert _sh("verify_sha256 '%s/nope' %s" % (tmp_path, good)).returncode != 0
    f = tmp_path / "x"
    f.write_bytes(b"")
    assert _sh("verify_sha256 '%s' abc" % f).returncode != 0
    assert _sh("verify_sha256 '%s' ''" % f).returncode != 0


def test_verify_sha256_empty_file_hash(tmp_path):
    import hashlib
    f = tmp_path / "e"
    f.write_bytes(b"")
    assert _sh("verify_sha256 '%s' %s" % (f, hashlib.sha256(b"").hexdigest())).returncode == 0
