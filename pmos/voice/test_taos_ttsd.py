"""Tests for taos-ttsd and install-tts.sh's checks, with a FAKE synthesizer
(no sherpa, no model, runs anywhere). Malformed requests first, because they
are where a daemon on a phone gets hurt; then the stream; then health.

Mutation check: TAOS_TTSD_PATH points the whole file at a mutated copy of the
daemon, e.g.
    sed 's/if n > MAX_BODY:/if False:/' taos-ttsd > /tmp/m; TAOS_TTSD_PATH=/tmp/m pytest ...
and tests must go red. See the commit message for the runs that were made.
"""
import hashlib
import http.client
import importlib.machinery
import importlib.util
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
from array import array

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.environ.get("TAOS_TTSD_PATH", os.path.join(HERE, "taos-ttsd"))


def _load():
    loader = importlib.machinery.SourceFileLoader("taos_ttsd_under_test", DAEMON)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ttsd = _load()

# three "sentences" of distinct, recognisable samples
SENTENCES = [[1000 + i for i in range(2400)],
             [-2000 - i for i in range(1200)],
             [7, -7] * 600]


class FakeSynthesizer:
    engine = "fake-engine"
    model = "fake-model"
    voice = "fake-voice"
    sample_rate = 22050

    def __init__(self, loaded=True):
        self.loaded = loaded
        self.load_ms = 7
        self.sentences = SENTENCES
        self.texts = []
        self.returns = []         # what emit() returned, per sentence offered
        self.gate = None          # threading.Event: each sentence AFTER the first waits on it
        self.entered = threading.Semaphore(0)
        self.first_sent = threading.Event()
        self.active = 0
        self.max_active = 0
        self.boom_at = None       # raise before emitting sentence k
        self.delay = 0.0
        self._m = threading.Lock()

    def synthesize(self, text, emit):
        with self._m:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.texts.append(text)
        self.entered.release()
        try:
            for k, s in enumerate(self.sentences):
                if k and self.gate is not None:
                    assert self.gate.wait(10)
                if self.delay:
                    time.sleep(self.delay)
                if self.boom_at == k:
                    raise RuntimeError("engine blew up")
                ok = emit(array("h", s).tobytes())
                self.returns.append(ok)
                if k == 0:
                    self.first_sent.set()
                if not ok:            # what sherpa does when the callback returns 0
                    return
        finally:
            with self._m:
                self.active -= 1


@pytest.fixture
def daemon():
    eng = FakeSynthesizer()
    srv = ttsd.make_server(eng, port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.eng = eng
    srv.port = srv.server_address[1]
    yield srv
    srv.shutdown()
    srv.server_close()


def raw(srv, head_lines, body=b"", timeout=3.0, close_after_send=False):
    """Send a hand-built request; return (status, head_bytes, body_bytes).
    status None if the server gave no response within `timeout`."""
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
                return None, b"", b""
        if not buf:
            return None, b"", b""
        head, _, rest = buf.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), head, rest
    finally:
        s.close()


def post(srv, body=b"", length=None, extra=(), version="HTTP/1.1", ctype="application/json", **kw):
    """POST /tts with Content-Length = len(body) unless `length` overrides it
    (pass "" to send an empty header, False to send none)."""
    lines = ["POST /tts " + version, "Host: x"]
    if length is None:
        length = str(len(body))
    if length is not False:
        lines.append("Content-Length: " + length)
    if ctype is not None:
        lines.append("Content-Type: " + ctype)
    lines += list(extra)
    st, _, rest = raw(srv, lines, body, **kw)
    return st, rest


def j(obj):
    return json.dumps(obj).encode()


def dechunk(data):
    """Strict chunked decoding: returns (payload, terminated)."""
    out, i = b"", 0
    while True:
        k = data.find(b"\r\n", i)
        if k < 0:
            return out, False
        size = int(data[i:k], 16)
        if size == 0:
            return out, data[k + 2:k + 4] == b"\r\n"
        chunk = data[k + 2:k + 2 + size]
        if len(chunk) < size or data[k + 2 + size:k + 4 + size] != b"\r\n":
            return out + chunk, False
        out += chunk
        i = k + 4 + size


def expected_pcm(sentences=SENTENCES):
    return b"".join(array("h", s).tobytes() for s in sentences)


def wait_idle(srv, secs=3.0):
    """All admitted requests finished (slot semaphore back to full)."""
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if srv.slots._value == ttsd.MAX_INFLIGHT:
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------ malformed first ---

def test_no_content_length_is_411(daemon):
    st, _ = post(daemon, j({"text": "hi"}), length=False)
    assert st == 411 and daemon.eng.texts == []


def test_chunked_upload_without_content_length_is_411(daemon):
    st, _ = post(daemon, b"4\r\nabcd\r\n0\r\n\r\n", length=False,
                 extra=["Transfer-Encoding: chunked"])
    assert st == 411 and daemon.eng.texts == []


@pytest.mark.parametrize("bad", ["abc", "", "1.5", "+4", "0x10", "4 4", "1e3", "-0", "-1"])
def test_bad_content_length_is_400(daemon, bad):
    st, _ = post(daemon, j({"text": "hi"}), length=bad)
    assert st == 400 and daemon.eng.texts == []


def test_non_ascii_digit_content_length_is_400(daemon):
    # "²" is str.isdigit() == True; the daemon must not take it as a number
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=3)
    s.sendall(b"POST /tts HTTP/1.1\r\nHost: x\r\nContent-Length: \xb2\r\n\r\n")
    head = s.recv(4096)
    s.close()
    assert int(head.split(b" ")[1]) == 400 and daemon.eng.texts == []


def test_conflicting_duplicate_content_length_is_400(daemon):
    st, _ = post(daemon, j({"text": "hi"}), extra=["Content-Length: 99"])
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("n", [16385, 16386, 10 ** 9, 10 ** 30])
def test_over_cap_is_413_without_reading_the_body(daemon, n):
    # Only headers are sent. A daemon that tries to read the body first waits
    # for bytes that never come and the client sees NO status (None) instead.
    st, _ = post(daemon, b"", length=str(n), timeout=2.0)
    assert st == 413
    assert daemon.eng.texts == []


def test_over_cap_does_not_hold_a_slot(daemon):
    for _ in range(ttsd.MAX_INFLIGHT + 2):
        assert post(daemon, b"", length="16385", timeout=2.0)[0] == 413
    assert wait_idle(daemon)


def test_empty_body_is_400_before_reading(daemon):
    st, body = post(daemon, b"", length="0")
    assert st == 400 and daemon.eng.texts == []
    # decided from the header (no slot taken), not by failing to parse b""
    assert json.loads(body)["error"] == "empty body"


@pytest.mark.parametrize("ct", ["text/plain", "audio/pcm", "application/x-www-form-urlencoded",
                                "application/jsonx", ""])
def test_wrong_content_type_is_415(daemon, ct):
    st, _ = post(daemon, j({"text": "hi"}), ctype=ct)
    assert st == 415 and daemon.eng.texts == []


def test_http10_is_505_not_a_chunked_reply_it_cannot_read(daemon):
    st, _ = post(daemon, j({"text": "hi"}), version="HTTP/1.0")
    assert st == 505 and daemon.eng.texts == []


@pytest.mark.parametrize("body", [b"hello", b"{", b"{\"text\": \"hi\"", b"\xff\xfe{}",
                                  b"{\"text\": \"\xff\"}", b"NaN?", b"   "])
def test_non_json_body_is_400(daemon, body):
    st, _ = post(daemon, body)
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("obj", [[], ["text", "hi"], "hi", 1, None, True])
def test_non_object_is_400(daemon, obj):
    st, _ = post(daemon, j(obj))
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("obj", [{}, {"txt": "hi"}, {"Text": "hi"}])
def test_missing_text_is_400(daemon, obj):
    st, body = post(daemon, j(obj))
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("val", [1, 1.5, None, True, ["hi"], {"t": "hi"}])
def test_text_not_a_string_is_400(daemon, val):
    st, _ = post(daemon, j({"text": val}))
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("val", ["", " ", "   ", "\n\t \r", "　"])
def test_empty_or_whitespace_text_is_400(daemon, val):
    st, _ = post(daemon, j({"text": val}))
    assert st == 400 and daemon.eng.texts == []


@pytest.mark.parametrize("extra", [{"voice": "cori"}, {"speed": 1.5}, {"sid": 1},
                                   {"format": "wav"}, {"": 0}])
def test_unknown_keys_are_400_not_silently_ignored(daemon, extra):
    obj = {"text": "hello there"}
    obj.update(extra)
    st, body = post(daemon, j(obj))
    assert st == 400 and daemon.eng.texts == []
    assert "unknown" in json.loads(body)["error"]


def test_duplicate_text_key_is_400(daemon):
    st, _ = post(daemon, b'{"text": "one", "text": "two"}')
    assert st == 400 and daemon.eng.texts == []


def test_nul_in_text_is_400(daemon):
    # c_char_p would silently cut the text at the NUL
    st, _ = post(daemon, j({"text": "speak this\u0000not this"}))
    assert st == 400 and daemon.eng.texts == []


def test_lone_surrogate_in_text_is_400(daemon):
    st, _ = post(daemon, b'{"text": "a\\ud800b"}')
    assert st == 400 and daemon.eng.texts == []


def test_text_over_2000_characters_is_413(daemon):
    st, _ = post(daemon, j({"text": "a" * 2001}))
    assert st == 413 and daemon.eng.texts == []


def test_text_cap_counts_characters_not_bytes(daemon):
    # 2001 two-byte characters: 4002+ bytes, well under the byte cap, still 413
    st, _ = post(daemon, j({"text": "é" * 2001}, ), timeout=5)
    assert st == 413 and daemon.eng.texts == []
    # 2000 of them (sent raw UTF-8, not \u-escaped: 4000 bytes) is accepted
    body = json.dumps({"text": "é" * 2000}, ensure_ascii=False).encode("utf-8")
    st, _ = post(daemon, body, timeout=5)
    assert st == 200 and daemon.eng.texts == ["é" * 2000]


def test_client_sends_fewer_bytes_than_declared_then_closes_no_synthesis(daemon):
    st, _ = post(daemon, b'{"text": "hel', length="100", close_after_send=True)
    assert st is None                      # no reply to a dead upload
    assert wait_idle(daemon)
    assert daemon.eng.texts == []


def test_client_that_stalls_mid_upload_is_dropped_no_synthesis(daemon, monkeypatch):
    monkeypatch.setattr(ttsd.Handler, "timeout", 0.4)
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=5)
    s.sendall(b"POST /tts HTTP/1.1\r\nContent-Length: 100\r\n\r\n" + b'{"text": "he')
    # never send the rest, never close: the daemon must give up by itself
    assert wait_idle(daemon, 4.0)
    s.close()
    assert daemon.eng.texts == []


def test_unknown_paths_and_methods(daemon):
    assert raw(daemon, ["GET /nope HTTP/1.1", "Host: x"])[0] == 404
    assert raw(daemon, ["POST /health HTTP/1.1", "Host: x", "Content-Length: 2"], b"{}")[0] == 404
    assert raw(daemon, ["PUT /tts HTTP/1.1", "Host: x", "Content-Length: 2"], b"{}")[0] == 501
    assert daemon.eng.texts == []


def test_malformed_requests_leave_the_daemon_serving(daemon):
    post(daemon, b"", length="abc")
    post(daemon, b"", length="16385", timeout=2.0)
    post(daemon, j({"text": "x", "voice": "y"}))
    post(daemon, b"{")
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 200 and dechunk(body) == (expected_pcm(), True)


# --------------------------------------------------------------- stream ---

def test_stream_headers_and_valid_s16le_body(daemon):
    c = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=5)
    c.request("POST", "/tts", j({"text": "Hello there. How are you?"}),
              {"Content-Type": "application/json"})
    r = c.getresponse()
    assert r.status == 200
    assert r.chunked                                       # Transfer-Encoding: chunked
    assert r.getheader("Content-Type") == "audio/pcm"
    assert r.getheader("X-Sample-Rate") == "22050"
    assert r.getheader("X-Channels") == "1"
    assert r.getheader("X-Sample-Format") == "s16le"
    assert r.getheader("Content-Length") is None
    pcm = r.read()
    assert len(pcm) == 2 * sum(len(s) for s in SENTENCES)
    assert pcm == expected_pcm()
    got = struct.unpack("<%dh" % (len(pcm) // 2), pcm)     # explicitly little-endian
    assert list(got[:3]) == [1000, 1001, 1002] and got[2400] == -2000
    assert daemon.eng.texts == ["Hello there. How are you?"]


def test_one_chunk_per_sentence_and_a_clean_terminator(daemon):
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 200
    sizes = [int(m, 16) for m in re.findall(rb"(?:^|\r\n)([0-9a-f]+)\r\n", body)]
    assert sizes[:3] == [2 * len(s) for s in SENTENCES]
    assert dechunk(body) == (expected_pcm(), True)


def test_first_sentence_is_on_the_wire_before_the_rest_is_synthesised(daemon):
    daemon.eng.gate = threading.Event()                    # sentence 2 waits for us
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=5)
    b = j({"text": "a. b. c."})
    s.sendall(b"POST /tts HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(b) + b)
    want = 2 * len(SENTENCES[0])
    buf = b""
    while len(buf.partition(b"\r\n\r\n")[2]) < want + 8:  # chunk size line + data + CRLF
        c = s.recv(65536)                                  # times out (fails) if it never comes
        assert c
        buf += c
    head, _, rest = buf.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    payload, done = dechunk(rest)
    assert payload == array("h", SENTENCES[0]).tobytes() and not done
    daemon.eng.gate.set()
    s.close()


def test_text_is_passed_exactly(daemon):
    t = "Café at 7:30, naïve “quotes” — and 日本."
    st, _ = post(daemon, json.dumps({"text": t}, ensure_ascii=False).encode("utf-8"))
    assert st == 200 and daemon.eng.texts == [t]


def test_content_type_with_charset_and_absent_are_accepted(daemon):
    assert post(daemon, j({"text": "hi"}), ctype="application/json; charset=utf-8")[0] == 200
    assert post(daemon, j({"text": "hi"}), ctype="Application/JSON")[0] == 200
    assert post(daemon, j({"text": "hi"}), ctype=None)[0] == 200


def test_text_at_exactly_2000_characters_is_accepted(daemon):
    st, _ = post(daemon, j({"text": "a" * 2000}))
    assert st == 200 and len(daemon.eng.texts[0]) == 2000


def test_body_at_exactly_the_byte_cap_is_accepted(daemon):
    pad = ttsd.MAX_BODY - len(j({"text": "hi"}))
    body = b'{"text": "hi"}' + b" " * pad
    assert len(body) == 16384
    assert post(daemon, body)[0] == 200


def test_disconnect_mid_stream_stops_generation_and_frees_the_lock(daemon):
    eng = daemon.eng
    eng.sentences = [[5] * 22050 for _ in range(60)]       # 60 one-second sentences
    eng.gate = threading.Event()
    s = socket.create_connection(("127.0.0.1", daemon.port), timeout=5)
    b = j({"text": "a long reply"})
    s.sendall(b"POST /tts HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(b) + b)
    assert eng.first_sent.wait(3)
    s.recv(1024)                                           # the reply really started
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()                                              # RST: the client is gone
    eng.delay = 0.02
    eng.gate.set()
    assert wait_idle(daemon, 5.0)
    assert False in eng.returns                            # a write failed and said so
    assert eng.returns[-1] is False                        # ...and nothing was offered after it
    assert len(eng.returns) < 60                           # generation stopped early
    # the lock is free: the next request is served in full
    eng.sentences, eng.gate, eng.delay = SENTENCES, None, 0.0
    st, body = post(daemon, j({"text": "next"}))
    assert st == 200 and dechunk(body) == (expected_pcm(), True)


def test_failure_before_any_audio_is_500(daemon):
    daemon.eng.boom_at = 0
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 500 and json.loads(body)["error"]
    daemon.eng.boom_at = None
    assert post(daemon, j({"text": "hi"}))[0] == 200


def test_failure_after_audio_truncates_without_a_terminal_chunk(daemon):
    daemon.eng.boom_at = 2
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 200
    payload, terminated = dechunk(body)
    assert payload == expected_pcm(SENTENCES[:2]) and terminated is False
    daemon.eng.boom_at = None
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 200 and dechunk(body) == (expected_pcm(), True)


def test_no_audio_at_all_is_an_empty_terminated_stream(daemon):
    daemon.eng.sentences = []
    st, body = post(daemon, j({"text": "hi"}))
    assert st == 200 and dechunk(body) == (b"", True)


def test_syntheses_never_overlap(daemon):
    daemon.eng.gate = threading.Event()
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(daemon, j({"text": "hi"}), timeout=8)[0]))
          for _ in range(3)]
    for t in ts:
        t.start()
    assert daemon.eng.entered.acquire(timeout=3)          # one synthesis inside
    time.sleep(0.3)
    assert daemon.eng.active == 1                         # the other two wait on the lock
    daemon.eng.gate.set()
    for t in ts:
        t.join(8)
    assert out == [200, 200, 200] and daemon.eng.max_active == 1


def test_busy_when_all_slots_are_taken(daemon):
    daemon.eng.gate = threading.Event()
    out = []
    ts = [threading.Thread(target=lambda: out.append(post(daemon, j({"text": "hi"}), timeout=8)[0]))
          for _ in range(ttsd.MAX_INFLIGHT)]
    for t in ts:
        t.start()
    assert daemon.eng.entered.acquire(timeout=3)
    deadline = time.monotonic() + 3
    while daemon.slots._value and time.monotonic() < deadline:
        time.sleep(0.02)
    assert daemon.slots._value == 0
    assert post(daemon, j({"text": "hi"}))[0] == 503
    daemon.eng.gate.set()
    for t in ts:
        t.join(8)
    assert sorted(out) == [200] * ttsd.MAX_INFLIGHT
    assert post(daemon, j({"text": "hi"}))[0] == 200


# ------------------------------------------------------------ conversion ---

def test_pcm16_clamps_scales_and_is_little_endian():
    got = ttsd.pcm16([0.0, 0.5, -0.5, 1.0, -1.0, 1.7, -3.0, float("nan"), float("inf")])
    assert struct.unpack("<9h", got) == (0, 16383, -16383, 32767, -32767, 32767, -32767, 0, 32767)


def test_voice_names_follow_the_sid():
    assert ttsd.voice_name(0) == "cori" and ttsd.voice_name(1) == "sid-1"
    assert ttsd.voice_name(99) == "sid-99"


# ---------------------------------------------------------------- health ---

def test_health_is_503_before_load_and_200_after(daemon):
    daemon.eng.loaded = False
    st, _, body = raw(daemon, ["GET /health HTTP/1.1", "Host: x"])
    assert st == 503 and json.loads(body)["ok"] is False
    st, _ = post(daemon, j({"text": "hi"}))
    assert st == 503 and daemon.eng.texts == []
    daemon.eng.loaded = True
    st, _, body = raw(daemon, ["GET /health HTTP/1.1", "Host: x"])
    h = json.loads(body)
    assert st == 200 and h == {"ok": True, "engine": "fake-engine", "model": "fake-model",
                               "voice": "fake-voice", "sample_rate": 22050, "load_ms": 7}


def test_real_engine_identity():
    e = ttsd.SherpaSynthesizer("/nonexistent.so", "/nonexistent")
    assert (e.engine, e.model, e.voice, e.sample_rate) == (
        "sherpa-onnx", "vits-piper-en_GB-cori-high", "cori", 22050)
    assert e.loaded is False


def test_server_binds_loopback_only():
    srv = ttsd.make_server(FakeSynthesizer(), port=0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_load_fills_the_vits_config_not_kitten():
    # the ctypes struct layout is the ABI; the engine must fill VitsCfg with the
    # bench's parameters (bench-tts2.c): noise_scale 0.667, noise_scale_w 0.8,
    # length_scale 1.0, lexicon and dict_dir left empty
    src = open(DAEMON).read()
    load = src[src.index("    def load(self):"):src.index("    def synthesize(")]
    assert "cfg.model.vits" in load and "kitten" not in load.lower()
    assert "voices.bin" not in load
    for want in ("noise_scale = 0.667", "noise_scale_w = 0.8", "length_scale = 1.0"):
        assert want in load, want
    assert "num_speakers" in src.lower() or "NumSpeakers" in load


def test_constants():
    assert ttsd.DEFAULT_PORT == 6976 and ttsd.MAX_BODY == 16384 and ttsd.MAX_TEXT == 2000


# ------------------------------------------------ install-tts.sh checks ---

INSTALL = os.path.join(HERE, "install-tts.sh")
INSTALL_STT = os.path.join(HERE, "install-stt.sh")


def _sh(snippet):
    return subprocess.run(["sh", "-c", ". '%s'; %s" % (INSTALL, snippet)],
                          env=dict(os.environ, INSTALL_TTS_SOURCE_ONLY="1"),
                          capture_output=True, text=True)


def _pin(path, name):
    m = re.search(r"^%s=(\S+)$" % name, open(path).read(), re.M)
    assert m, "%s not pinned in %s" % (name, path)
    return m.group(1)


def test_both_installers_pin_the_same_sherpa_commit():
    # install-tts.sh never builds sherpa: it loads install-stt.sh's library and
    # refuses it unless it was built at this commit
    assert _pin(INSTALL, "SHERPA_COMMIT") == _pin(INSTALL_STT, "SHERPA_COMMIT")
    assert re.fullmatch(r"[0-9a-f]{40}", _pin(INSTALL, "SHERPA_COMMIT"))


def test_install_stt_builds_with_tts_and_stamps_it():
    s = open(INSTALL_STT).read()
    assert "-DSHERPA_ONNX_ENABLE_TTS=ON" in s and "TTS=OFF" not in s
    assert _pin(INSTALL_STT, "SHERPA_FEATURES") == "tts"
    assert _pin(INSTALL, "SHERPA_FEATURES") == "tts"


def _pins(name):
    return _pin(INSTALL, name)


def test_installer_pins_the_cori_high_piper_voice():
    assert _pins("MODEL_NAME") == "vits-piper-en_GB-cori-high"
    assert _pins("MODEL_URL") == ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                                  "tts-models/vits-piper-en_GB-cori-high.tar.bz2")
    assert _pins("MODEL_ARCHIVE_SHA256") == \
        "42922f07738fcde2e49eed4e959635692f73b933de35a6b7c1010162ff566292"
    assert _pins("SHA_voice_onnx") == \
        "006bb4db48e066f7f1be91d218db3b76617a707196271694ca6455d7bbd13842"
    assert _pins("SHA_voice_onnx_json") == \
        "9e7fb5b5671612c22f3c81cbe46c1ae87b031a4632bcb509e499dad6f1e2adec"
    assert _pins("SHA_tokens_txt") == \
        "ef3a7e4a8d1af0c9d4dc45aaae1a6242ebe24a7ed6f3d025a49eb29682784c6d"
    assert _pins("SHA_model_card") == \
        "136e7bd168b6c35b4a5df01a0253297e5773b5775ceae0af5160f264aa58208f"
    assert _pins("TREE_espeak_ng_data") == \
        "1c2ec0747e40d30f8f123b65c93dcba64f2404195b3344ed614df41237df8332"
    assert "voices.bin" not in open(INSTALL).read() and "SHA_voices_bin" not in open(INSTALL).read()


def test_installer_model_dir_and_verify_lists():
    s = open(INSTALL).read()
    assert 'MODEL_DIR="$TAOS_MODELS_ROOT/sherpa-onnx/piper/$MODEL_NAME"' in s
    assert "sherpa-onnx/kitten" not in s
    for f in ("en_GB-cori-high.onnx", "en_GB-cori-high.onnx.json", "tokens.txt", "MODEL_CARD"):
        assert f in s, f
    assert "24000" not in s and "kitten" not in s.lower()


CORI_CARD = """# Model card for cori (high)

* Language: en_GB (English, Great Britain)
* Speakers: 1

## Dataset

* URL: https://librivox.org
* License: public domain

## Training

UK English female voice. All recordings came from LibriVox.org.
"""


def _licence(tmp_path, card):
    f = tmp_path / "MODEL_CARD"
    f.write_text(card)
    return _sh("check_voice_licence '%s'" % f)


def test_licence_allowlist_accepts_the_cori_card(tmp_path):
    assert _licence(tmp_path, CORI_CARD).returncode == 0
    for ok in ("CC0-1.0", "CC0", "CC BY 4.0", "CC-BY-4.0", "cc by 3.0", "Public Domain"):
        r = _licence(tmp_path, CORI_CARD.replace("public domain", ok))
        assert r.returncode == 0, (ok, r.stderr)


@pytest.mark.parametrize("lic", [
    "CC BY-NC-SA 4.0",                       # hfc_female / hfc_male
    "CC BY-NC 4.0", "CC BY-ND 4.0", "CC BY-SA 4.0", "CC-BY-NC-4.0",
    "See URL",                               # lessac: Blizzard 2013, research only
    "https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html",
    "research only", "non-commercial", "unknown", "", "public domain, research only",
    "CC0 NC",
])
def test_licence_allowlist_refuses_everything_else(tmp_path, lic):
    r = _licence(tmp_path, CORI_CARD.replace("public domain", lic))
    assert r.returncode != 0 and "voice licence REFUSED" in r.stderr, (lic, r.stderr)


def test_licence_allowlist_refuses_malformed_cards(tmp_path):
    assert _licence(tmp_path, "").returncode != 0                      # empty card
    assert _licence(tmp_path, "# Model card\n\nno licence line\n").returncode != 0
    assert _sh("check_voice_licence '%s/absent'" % tmp_path).returncode != 0
    # a good licence line does not launder a restriction stated elsewhere
    r = _licence(tmp_path, CORI_CARD + "\nFor research use only.\n")
    assert r.returncode != 0 and "voice licence REFUSED" in r.stderr
    # a second, disallowed licence line is not hidden by the first
    r = _licence(tmp_path, CORI_CARD + "\n* License: CC BY-NC-SA 4.0\n")
    assert r.returncode != 0
    # lowercase key, no bullet
    assert _licence(tmp_path, "license: CC BY-NC-SA 4.0\n").returncode != 0
    # the allowed value only in prose, no License: field
    assert _licence(tmp_path, "this voice is public domain\n").returncode != 0


def test_installer_runs_the_licence_check_on_the_archive_card():
    s = open(INSTALL).read()
    assert 'check_voice_licence "$src/MODEL_CARD"' in s
    assert "check_voice_licence" in s[s.index("tar -xjf"):s.index("verify_all ||")]


def test_verify_sha256_accepts_a_good_file_and_rejects_corruption(tmp_path):
    f = tmp_path / "m.bin"
    f.write_bytes(b"the model")
    good = hashlib.sha256(b"the model").hexdigest()
    assert _sh("verify_sha256 '%s' %s" % (f, good)).returncode == 0
    f.write_bytes(b"the modeL")                       # one flipped byte
    r = _sh("verify_sha256 '%s' %s" % (f, good))
    assert r.returncode != 0 and "MISMATCH" in r.stderr


def test_verify_sha256_rejects_a_missing_file_and_a_short_hash(tmp_path):
    assert _sh("verify_sha256 '%s/nope' %s" % (tmp_path, "0" * 64)).returncode != 0
    f = tmp_path / "x"
    f.write_bytes(b"")
    assert _sh("verify_sha256 '%s' abc" % f).returncode != 0
    assert _sh("verify_sha256 '%s' ''" % f).returncode != 0


def _tree(root):
    (root / "voices" / "!v").mkdir(parents=True)
    (root / "en_dict").write_bytes(b"dict")
    (root / "voices" / "!v" / "Mr serious").write_bytes(b"a voice with a space in its name")
    (root / "phontab").write_bytes(b"\x00\x01")


def _tree_sha(path):
    r = _sh("tree_sha256 '%s'" % path)
    return r.returncode, r.stdout.strip(), r.stderr


def test_tree_sha256_is_stable_and_handles_spaces(tmp_path):
    _tree(tmp_path / "a")
    _tree(tmp_path / "b")
    rc, ha, _ = _tree_sha(tmp_path / "a")
    assert rc == 0 and re.fullmatch(r"[0-9a-f]{64}", ha)
    assert _tree_sha(tmp_path / "b")[1] == ha


def test_tree_sha256_sees_a_flipped_byte_an_extra_file_a_rename_and_a_removal(tmp_path):
    base = tmp_path / "t"
    _tree(base)
    h0 = _tree_sha(base)[1]
    (base / "phontab").write_bytes(b"\x00\x02")
    assert _tree_sha(base)[1] != h0
    (base / "phontab").write_bytes(b"\x00\x01")
    assert _tree_sha(base)[1] == h0
    (base / "extra").write_bytes(b"")
    assert _tree_sha(base)[1] != h0
    os.remove(base / "extra")
    os.rename(base / "en_dict", base / "en_dicT")
    assert _tree_sha(base)[1] != h0
    os.rename(base / "en_dicT", base / "en_dict")
    os.remove(base / "voices" / "!v" / "Mr serious")
    assert _tree_sha(base)[1] != h0


def test_tree_sha256_refuses_a_symlink_and_a_missing_dir(tmp_path):
    base = tmp_path / "t"
    _tree(base)
    os.symlink("/etc/passwd", base / "link")
    rc, out, err = _tree_sha(base)
    assert rc != 0 and out == "" and "symlink" in err
    assert _tree_sha(tmp_path / "nope")[0] != 0
