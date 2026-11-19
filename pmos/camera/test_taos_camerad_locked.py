"""The camera gallery on a locked phone.

The camera opens from the lock screen without signing in, and camerad has no
authentication of its own. Until 2026-11-17 anyone holding the locked phone
could list, view and delete every photo. Locked now means: only the shots taken
since the phone was last seen unlocked, and no delete. Locked is the default;
only the controller saying exactly {"locked": false} lifts it.
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


def test_unlocked_control_everything_works(cam, monkeypatch, tmp_path):
    """The must-succeed half: without it every refusal below could be a broken server."""
    say(cam, monkeypatch, {"locked": False})
    assert names(cam) == (False, ["old_1.jpg", "old_2.jpg"])
    assert call(cam, "GET", "/photo/old_1.jpg") == (200, JPG)
    status, body = call(cam, "DELETE", "/photo/old_1.jpg")
    assert status == 200 and json.loads(body)["ok"] is True
    assert not (tmp_path / "old_1.jpg").exists()


def test_a_locked_phone_lists_views_and_deletes_nothing_old(cam, monkeypatch, tmp_path):
    say(cam, monkeypatch, {"locked": True})
    assert names(cam) == (True, [])
    status, body = call(cam, "GET", "/photo/old_1.jpg")
    assert status == 404 and JPG not in body
    # Indistinguishable from a name that does not exist.
    assert call(cam, "GET", "/photo/nope.jpg")[0] == 404
    status, body = call(cam, "DELETE", "/photo/old_1.jpg")
    assert status == 403, body
    assert (tmp_path / "old_1.jpg").exists() and (tmp_path / "old_2.jpg").exists()


@pytest.mark.parametrize("answer", [
    None,                              # no usable answer
    {},                                # an old controller's unrelated JSON
    {"locked": "false"},               # a string is not False
    {"locked": 0},                     # nor is zero
    {"locked": None},
    [False],
    "unlocked",
    {"locked": False, "unexpected": True},   # anything beyond the one key
    {"locked": False, "user": "x"},
    OSError("connection refused"),
    ValueError("not json"),
])
def test_anything_but_an_exact_unlocked_answer_is_locked(cam, monkeypatch, tmp_path, answer):
    say(cam, monkeypatch, answer)
    assert names(cam) == (True, [])
    assert call(cam, "DELETE", "/photo/old_1.jpg")[0] == 403
    assert (tmp_path / "old_1.jpg").exists()


def test_with_no_controller_at_all_the_real_query_fails_closed(cam, tmp_path):
    # Nothing stubbed: _ask_lock_state really tries the dead port.
    assert cam.phone_locked() is True
    assert names(cam) == (True, [])
    assert call(cam, "DELETE", "/photo/old_2.jpg")[0] == 403
    assert (tmp_path / "old_2.jpg").exists()


def _fake_session(monkeypatch, m):
    s = types.SimpleNamespace(frame=lambda: (JPG, 1), frame_after=lambda seq, t: JPG, error=None)
    monkeypatch.setattr(m, "_session", lambda which=None: s)


def test_a_shot_taken_while_locked_is_the_only_one_shown(cam, monkeypatch, tmp_path):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": True})
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
    say(cam, monkeypatch, {"locked": True})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    say(cam, monkeypatch, {"locked": False})
    assert names(cam) == (False, sorted([shot, "old_1.jpg", "old_2.jpg"]))
    say(cam, monkeypatch, {"locked": True})
    # Locked again: last session's shot is as hidden as the rest.
    assert names(cam) == (True, [])
    assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_a_shot_taken_unlocked_is_not_shown_once_locked(cam, monkeypatch):
    _fake_session(monkeypatch, cam)
    say(cam, monkeypatch, {"locked": False})
    shot = json.loads(call(cam, "POST", "/capture")[1])["name"]
    say(cam, monkeypatch, {"locked": True})
    assert names(cam) == (True, [])
    assert call(cam, "GET", "/photo/" + shot)[0] == 404


def test_a_shot_observed_before_a_session_reset_is_not_added_to_the_next_session(cam, monkeypatch):
    # The race, without threads: capture observes "locked", another request then
    # sees the phone unlocked (session over), the phone locks again, and only
    # then does the capture register its shot. That shot belongs to the old
    # session and must not appear in the new one.
    say(cam, monkeypatch, {"locked": True})
    locked, gen = cam.lock_observation()
    assert locked is True
    say(cam, monkeypatch, {"locked": False})
    assert cam.phone_locked() is False
    say(cam, monkeypatch, {"locked": True})
    cam.note_shot("x.jpg", locked, gen)
    assert cam.visible_while_locked("x.jpg") is False


def test_a_shot_with_no_reset_in_between_is_registered(cam, monkeypatch):
    say(cam, monkeypatch, {"locked": True})
    locked, gen = cam.lock_observation()
    say(cam, monkeypatch, {"locked": True})
    cam.note_shot("x.jpg", locked, gen)
    assert cam.visible_while_locked("x.jpg") is True


def test_a_reset_that_lands_inside_the_observation_is_not_missed(cam, monkeypatch):
    """Another request resets the session and the phone locks again while this
    capture is still asking: the shot must not join the new session."""
    answers = iter([{"locked": True}])

    def ask():
        # This capture's own query: before it answers, another request sees
        # the phone unlocked (a reset), and then the phone locks again.
        monkeypatch.setattr(cam, "_ask_lock_state", lambda: {"locked": False})
        cam.phone_locked()
        monkeypatch.setattr(cam, "_ask_lock_state", lambda: {"locked": True})
        return next(answers)

    monkeypatch.setattr(cam, "_ask_lock_state", ask)
    locked, gen = cam.lock_observation()
    assert locked is True
    cam.note_shot("y.jpg", locked, gen)
    assert cam.visible_while_locked("y.jpg") is False
