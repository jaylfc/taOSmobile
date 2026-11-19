"""The camera gallery on a locked phone.

The camera opens from the lock screen without signing in, and camerad has no
authentication of its own. Until 2026-11-17 anyone holding the locked phone
could list, view and delete every photo. Locked now means: only the shots taken
under the current lock session, and no delete. Locked is the default; only the
controller saying exactly {"locked": false, "session": <int>} lifts it.
"""
import http.client
import importlib.machinery
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("taos-camerad.py")
JPG = b"\xff\xd8\xff\xd9"


@pytest.fixture
def cam(tmp_path, monkeypatch):
    lc = types.ModuleType("libcamera")
    lc.CameraManager = types.SimpleNamespace(singleton=lambda: types.SimpleNamespace(cameras=[]))
    monkeypatch.setitem(sys.modules, "libcamera", lc)
    monkeypatch.setenv("TAOS_CAMERAD_DIR", str(tmp_path))
    # A port nothing listens on: the real query must fail, not reach a controller.
    monkeypatch.setenv("TAOS_LOCK_STATE_URL", "http://127.0.0.1:9/auth/lock-state")
    loader = importlib.machinery.SourceFileLoader("taos_camerad_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    assert m.PHOTOS == tmp_path
    for n in ("old_1.jpg", "old_2.jpg"):
        (tmp_path / n).write_bytes(JPG)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    m.test_port = srv.server_address[1]
    yield m
    srv.shutdown()
    srv.server_close()


def call(m, method, path):
    c = http.client.HTTPConnection("127.0.0.1", m.test_port, timeout=5)
    c.request(method, path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def names(m):
    status, body = call(m, "GET", "/photos")
    assert status == 200, body
    d = json.loads(body)
    return d["locked"], sorted(p["name"] for p in d["photos"])


def say(m, monkeypatch, answer):
    if isinstance(answer, Exception):
        def ask():
            raise answer
    else:
        def ask():
            return answer
    monkeypatch.setattr(m, "_ask_lock_state", ask)


def _fake_session(monkeypatch, m):
    s = types.SimpleNamespace(frame=lambda: (JPG, 1), frame_after=lambda seq, t: JPG, error=None)
    monkeypatch.setattr(m, "_session", lambda which=None: s)


def test_unlocked_control_everything_works(cam, monkeypatch, tmp_path):
    """The must-succeed half: without it every refusal below could be a broken server."""
    say(cam, monkeypatch, {"locked": False, "session": 8})
    assert names(cam) == (False, ["old_1.jpg", "old_2.jpg"])
    assert call(cam, "GET", "/photo/old_1.jpg") == (200, JPG)
    status, body = call(cam, "DELETE", "/photo/old_1.jpg")
    assert status == 200 and json.loads(body)["ok"] is True
    assert not (tmp_path / "old_1.jpg").exists()


def test_a_locked_phone_lists_views_and_deletes_nothing_old(cam, monkeypatch, tmp_path):
    say(cam, monkeypatch, {"locked": True, "session": 7})
    assert names(cam) == (True, [])
    status, body = call(cam, "GET", "/photo/old_1.jpg")
    assert status == 404 and JPG not in body
    # Indistinguishable from a name that does not exist.
    assert call(cam, "GET", "/photo/nope.jpg")[0] == 404
    status, body = call(cam, "DELETE", "/photo/old_1.jpg")
    assert status == 403, body
    assert (tmp_path / "old_1.jpg").exists() and (tmp_path / "old_2.jpg").exists()


FAIL_CLOSED = [
    None,                                           # no usable answer
    {},                                             # an old controller's unrelated JSON
    {"locked": False},                              # no session: the old contract
    {"locked": True},
    {"locked": False, "session": "7"},
    {"locked": False, "session": 7.0},
    {"locked": False, "session": True},
    {"locked": False, "session": None},
    {"locked": "false", "session": 7},              # a string is not False
    {"locked": 0, "session": 7},                    # nor is zero
    {"locked": None, "session": 7},
    {"locked": False, "session": 7, "extra": 1},    # anything beyond the two keys
    {"locked": False, "session": 7, "user": "x"},
    [False],
    "unlocked",
    OSError("connection refused"),
    ValueError("not json"),
]


@pytest.mark.parametrize("answer", FAIL_CLOSED)
def test_anything_but_an_exact_unlocked_answer_is_locked(cam, monkeypatch, tmp_path, answer):
    say(cam, monkeypatch, answer)
    assert names(cam) == (True, [])
    assert call(cam, "DELETE", "/photo/old_1.jpg")[0] == 403
    assert (tmp_path / "old_1.jpg").exists()


@pytest.mark.parametrize("answer", FAIL_CLOSED)
def test_lock_state_fails_closed_with_no_session(cam, monkeypatch, answer):
    say(cam, monkeypatch, answer)
    assert cam.lock_state() == (True, None)


def test_lock_state_reads_the_exact_contract(cam, monkeypatch):
    say(cam, monkeypatch, {"locked": False, "session": 8})
    assert cam.lock_state() == (False, 8)
    assert cam.phone_locked() is False
    say(cam, monkeypatch, {"locked": True, "session": 7})
    assert cam.lock_state() == (True, 7)
    assert cam.phone_locked() is True


def test_with_no_controller_at_all_the_real_query_fails_closed(cam, tmp_path):
    # Nothing stubbed: _ask_lock_state really tries the dead port.
    assert cam.phone_locked() is True
    assert cam.lock_state() == (True, None)
    assert names(cam) == (True, [])
    assert call(cam, "DELETE", "/photo/old_2.jpg")[0] == 403
    assert (tmp_path / "old_2.jpg").exists()


def test_a_shot_taken_while_locked_is_the_only_one_shown(cam, monkeypatch, tmp_path):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True, "session": 7})
    status, body = call(cam, "POST", "/capture")
    assert status == 200, body
    shot = json.loads(body)["name"]
    assert names(cam) == (True, [shot])
    assert call(cam, "GET", "/photo/" + shot) == (200, JPG)
    # Still no delete, even of its own shot.
    assert call(cam, "DELETE", "/photo/" + shot)[0] == 403
    assert (tmp_path / shot).exists()


def test_the_locked_session_ends_when_the_phone_is_seen_unlocked(cam, monkeypatch):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True, "session": 7})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    say(cam, monkeypatch, {"locked": False, "session": 7})
    assert names(cam) == (False, sorted([shot, "old_1.jpg", "old_2.jpg"]))
    say(cam, monkeypatch, {"locked": True, "session": 8})
    # Locked again: last session's shot is as hidden as the rest.
    assert names(cam) == (True, [])
    assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_a_shot_taken_unlocked_is_not_shown_once_locked(cam, monkeypatch):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": False, "session": 7})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    say(cam, monkeypatch, {"locked": True, "session": 8})
    assert names(cam) == (True, [])
    assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_a_shot_observed_in_one_session_is_not_shown_in_the_next(cam, monkeypatch):
    # The race, without threads: the capture observes session 7, the phone is
    # unlocked and locked again (session 8), and only then is the shot noted.
    say(cam, monkeypatch, {"locked": True, "session": 7})
    locked, session = cam.lock_state()
    assert (locked, session) == (True, 7)
    say(cam, monkeypatch, {"locked": True, "session": 8})
    cam.note_shot("x.jpg", locked, session)
    assert cam.visible_while_locked("x.jpg", 8) is False
    assert cam.visible_while_locked("x.jpg", 7) is True


def test_a_shot_in_the_same_session_is_registered(cam, monkeypatch):
    say(cam, monkeypatch, {"locked": True, "session": 7})
    locked, session = cam.lock_state()
    cam.note_shot("x.jpg", locked, session)
    assert cam.visible_while_locked("x.jpg", 7) is True
    assert cam.visible_while_locked("x.jpg", None) is False


def test_unlock_and_relock_with_no_request_between_hides_the_old_shot(cam, monkeypatch):
    """THE BUG: camerad never saw the unlock, only a new session number."""
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True, "session": 7})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    assert names(cam) == (True, [shot])
    say(cam, monkeypatch, {"locked": True, "session": 8})
    assert names(cam) == (True, [])
    assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_the_same_session_across_requests_keeps_the_shot_visible(cam, monkeypatch):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True, "session": 7})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    for _ in range(3):
        assert names(cam) == (True, [shot])
        assert call(cam, "GET", "/photo/" + shot) == (200, JPG)


def test_a_shot_taken_on_a_malformed_answer_is_never_shown_while_locked(cam, monkeypatch):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True})          # no session
    status, body = call(cam, "POST", "/capture")
    assert status == 200, body
    shot = json.loads(body)["name"]
    for later in ({"locked": True, "session": 7}, {"locked": True}, None):
        say(cam, monkeypatch, later)
        assert names(cam) == (True, [])
        assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_a_frame_taken_before_the_phone_locked_is_not_filed_as_a_locked_shot(cam, monkeypatch):
    """The phone locks while the shutter frame is being taken: the answer the
    capture acts on must be the one from BEFORE the frame, which was unlocked."""
    state = {"answer": {"locked": False, "session": 7}}
    monkeypatch.setattr(cam, "_ask_lock_state", lambda: state["answer"])

    def frame_after(seq, timeout):
        state["answer"] = {"locked": True, "session": 8}   # locks during the capture
        return JPG

    s = types.SimpleNamespace(frame=lambda: (JPG, 1), frame_after=frame_after, error=None)
    monkeypatch.setattr(cam, "_session", lambda which=None: s)
    status, body = call(cam, "POST", "/capture")
    assert status == 200, body
    shot = json.loads(body)["name"]
    locked, listed = names(cam)
    assert locked is True
    assert shot not in listed
    assert call(cam, "GET", "/photo/" + shot)[0] == 404
