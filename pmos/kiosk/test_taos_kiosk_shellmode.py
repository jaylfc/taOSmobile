"""taos-kiosk-shellmode starts the nav bar only once a surface needs it.

Jay (2026-09-30): the boot logo showed the nav bar. sway paints the boot
wordmark while the server starts, and a bar started with the session sat over
it. The bar must stay down through boot, a loading kiosk window and the lock
screen, and come up for the signed-in desktop or a taOS app window.

Run: python3 -m pytest -q pmos/kiosk/test_taos_kiosk_shellmode.py
"""
from __future__ import annotations
import json
import types

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

KIOSK = "chrome-127.0.0.1__-Default"
APP = "chrome-127.0.0.1__app.html-Default"


def _load():
    path = Path(__file__).with_name("bin") / "taos-kiosk-shellmode"
    loader = importlib.machinery.SourceFileLoader("taos_kiosk_shellmode", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def sm(monkeypatch, tmp_path):
    mod = _load()
    calls = {"bar": 0, "sway": []}
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    def fake_run(argv, **kw):
        if argv == [mod.BAR]:
            calls["bar"] += 1
        return None

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "swaymsg", lambda *a: calls["sway"].append(a))
    mod.calls = calls
    return mod


def con(app_id, title, fs=0, cid=7):
    return {"id": cid, "app_id": app_id, "name": title, "fullscreen_mode": fs}


def event(sm, c):
    """What main() does with one window event."""
    if sm.apply(c):
        sm.start_bar()


# Titles a kiosk window can carry before the page sets its own. The control
# ("taOS") is the signed-in desktop and MUST start the bar, so a loading()
# that swallowed every title would fail below rather than pass here.
@pytest.mark.parametrize("title", ["", "  ", "127.0.0.1:6969/", "http://127.0.0.1:6969/",
                                   "about:blank", "localhost:6969/auth/login"])
def test_a_loading_kiosk_window_starts_no_bar_and_is_not_touched(sm, title):
    event(sm, con(KIOSK, title, fs=1))
    assert sm.calls["bar"] == 0
    assert sm.calls["sway"] == []


def test_the_lock_screen_starts_no_bar(sm):
    event(sm, con(KIOSK, "Sign in — taOS", fs=1))
    assert sm.calls["bar"] == 0


def test_the_signed_in_desktop_starts_the_bar(sm):
    event(sm, con(KIOSK, "taOS", fs=1))
    assert sm.calls["bar"] == 1
    assert sm.calls["sway"] == [("[con_id=7] fullscreen disable",)]


def test_a_taos_app_window_starts_the_bar(sm):
    event(sm, con(APP, "Chat", fs=1))
    assert sm.calls["bar"] == 1


def test_boot_sequence_bar_appears_only_after_sign_in(sm):
    seq = [con(KIOSK, ""), con(KIOSK, "127.0.0.1:6969/"),
           con(KIOSK, "Sign in — taOS", fs=1)]
    for c in seq:
        event(sm, c)
    assert sm.calls["bar"] == 0
    event(sm, con(KIOSK, "taOS", fs=1))
    assert sm.calls["bar"] == 1


def test_a_running_bar_is_not_restarted_on_every_event(sm, monkeypatch):
    monkeypatch.setattr(sm, "bar_running", lambda: True)
    event(sm, con(KIOSK, "taOS"))
    event(sm, con(APP, "Chat"))
    assert sm.calls["bar"] == 0


def test_the_session_config_no_longer_starts_the_bar():
    conf = (Path(__file__).parent / "etc" / "sway-kiosk.conf").read_text()
    live = [ln for ln in conf.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("taos-kiosk-bar" in ln for ln in live)
    assert any("exec_always /usr/local/bin/taos-kiosk-shellmode" in ln for ln in live)


# --- the bar can be alive and paint nothing --------------------------------
# Measured 2026-11-17: waybar up 14 h, strip reserved, nothing drawn; the strip
# showed stale lock-screen pixels and there was no back, home or recents.

def _ppm(sample, w=4, h=4):
    return b"P6\n%d %d\n255\n" % (w, h) + bytes([sample]) * (w * h * 3)


def test_ppm_has_bright_reads_the_samples_not_the_header():
    m = _load()
    assert m.ppm_has_bright(_ppm(255)) is True
    assert m.ppm_has_bright(_ppm(8)) is False        # the bar's own near-black
    assert m.ppm_has_bright(_ppm(150)) is False       # at the threshold is not above it
    assert m.ppm_has_bright(b"") is False
    assert m.ppm_has_bright(b"not an image") is False
    assert m.ppm_has_bright(b"P6\n4 4\n255\n") is False   # header only


def test_bar_painted_looks_where_back_and_recents_are(monkeypatch):
    m = _load()
    seen = []
    monkeypatch.setattr(m, "lit_output", lambda: (540, 1200))
    monkeypatch.setattr(m, "region_bright", lambda x, y, w, h: seen.append((x, y, w, h)) or True)
    assert m.bar_painted() is True
    # 120 px either side of the centre, inside the bottom 44 px.
    assert seen == [(138, 1163, 24, 30), (378, 1163, 24, 30)]
    for x, y, w, h in seen:
        assert y >= 1200 - 44 and y + h <= 1200


@pytest.mark.parametrize("spots,expected", [
    ([True, True], True),
    ([False, False], False),     # nothing drawn in the strip
    ([True, False], True),       # one spot covered is not a blank bar
    ([False, True], True),
    ([None, True], None),        # could not read: no answer
])
def test_bar_painted_is_false_only_when_both_spots_are_dark(monkeypatch, spots, expected):
    m = _load()
    it = iter(spots)
    monkeypatch.setattr(m, "lit_output", lambda: (540, 1200))
    monkeypatch.setattr(m, "region_bright", lambda *a: next(it))
    assert m.bar_painted() is expected


def test_bar_painted_gives_no_answer_on_a_dark_panel(monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "lit_output", lambda: None)
    monkeypatch.setattr(m, "region_bright", lambda *a: pytest.fail("grim must not run on a dark panel"))
    assert m.bar_painted() is None


def _guard(monkeypatch, running=True, wanted=True, looks=(False, False), keyboard=False,
           wanted_after=None, keyboard_after=None):
    """wanted_after / keyboard_after: the answer once the second look has been taken."""
    m = _load()
    restarts = []
    it = iter(looks)
    seen = {"looks": 0}

    def look():
        seen["looks"] += 1
        return next(it)

    def later(first, after):
        return lambda: after if (after is not None and seen["looks"] >= 2) else first

    monkeypatch.setattr(m, "bar_running", lambda: running)
    monkeypatch.setattr(m, "still_wants_bar", later(wanted, wanted_after))
    monkeypatch.setattr(m, "keyboard_visible", later(keyboard, keyboard_after))
    monkeypatch.setattr(m, "bar_painted", look)
    monkeypatch.setattr(m, "start_bar", lambda replace=False: restarts.append(replace))
    return m.check_bar_painted(sleep=lambda s: None), restarts


def test_a_running_bar_that_is_not_on_the_glass_is_restarted(monkeypatch):
    did, restarts = _guard(monkeypatch)
    assert did is True and restarts == [True]


@pytest.mark.parametrize("kw", [
    dict(looks=(True,)),              # it is painted: leave it alone
    dict(looks=(False, True)),        # one bad frame, then fine: not broken
    dict(looks=(None,)),              # could not read the glass
    dict(looks=(False, None)),        # second look unreadable
    dict(wanted=False),               # locked again: fullscreen covers the bar by design
    dict(running=False),              # not started yet: start_bar's job, not this one's
    dict(keyboard=True),              # the keyboard covers the strip by design
    dict(wanted_after=False),         # locked while the second look was taken
    dict(keyboard_after=True),        # keyboard came up during the second look
])
def test_the_guard_does_not_restart_a_bar_it_cannot_fault(monkeypatch, kw):
    did, restarts = _guard(monkeypatch, **kw)
    assert did is False and restarts == []


def test_a_restart_and_an_ordinary_start_cannot_both_launch(monkeypatch):
    """The guard's thread restarts the bar while a window event asks for it:
    the second caller must wait for the first launcher, not see its gap.

    Event driven: the restart is held inside the gap until the test has given
    the ordinary start every chance to run into it."""
    import threading
    m = _load()
    state = {"running": True, "launches": 0}
    in_gap, release = threading.Event(), threading.Event()
    second_called_launcher = threading.Event()

    def launcher(argv, **kw):
        state["launches"] += 1
        if state["launches"] == 1:
            state["running"] = False      # the launcher kills the old bar first
            in_gap.set()
            release.wait(5)               # ... and writes the new pid later
            state["running"] = True
        else:
            second_called_launcher.set()

    monkeypatch.setattr(m, "bar_running", lambda: state["running"])
    monkeypatch.setattr(m.subprocess, "run", launcher)
    first = threading.Thread(target=lambda: m.start_bar(replace=True))
    first.start()
    assert in_gap.wait(5)
    second = threading.Thread(target=m.start_bar)
    second.start()
    # Without the lock the second start reaches the launcher here, in the gap.
    reached = second_called_launcher.wait(1.0)
    release.set()
    first.join(5)
    second.join(5)
    assert not reached, "an ordinary start launched a bar inside a restart"
    assert state["launches"] == 1
    # Control: with no bar at all, an ordinary start does launch.
    state.update(running=False, launches=0)
    m.start_bar()
    assert state["launches"] == 1


def test_a_taos_app_window_counts_as_wanting_the_bar(monkeypatch):
    """Checks are scheduled for app windows too; the restart condition must admit them."""
    m = _load()
    tree = {"nodes": [{"app_id": m.APP_WINDOW_PREFIX + "-Default", "visible": True,
                       "fullscreen_mode": 0, "name": "Chat"}]}
    monkeypatch.setattr(m, "swaymsg", lambda *a: types.SimpleNamespace(stdout=json.dumps(tree)))
    assert m.still_wants_bar() is True
    tree["nodes"][0]["visible"] = False
    assert m.still_wants_bar() is False
    tree["nodes"][0].update(visible=True, fullscreen_mode=1)
    assert m.still_wants_bar() is False
    # Control: the lock screen (fullscreen kiosk) does not want it.
    tree["nodes"] = [{"app_id": m.KIOSK_APP_ID, "visible": True, "fullscreen_mode": 1,
                      "name": "Sign in \u2014 taOS"}]
    assert m.still_wants_bar() is False
    tree["nodes"][0].update(fullscreen_mode=0, name="taOS")
    assert m.still_wants_bar() is True


@pytest.mark.parametrize("out,rc,expected", [
    ("b true\n", 0, True), ("b false\n", 0, False), ("", 1, False), ("garbage", 0, False)])
def test_keyboard_visible_is_true_only_when_squeekboard_says_so(monkeypatch, out, rc, expected):
    m = _load()
    monkeypatch.setattr(m.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=out, returncode=rc))
    assert m.keyboard_visible() is expected
