"""taos-kiosk-shellmode starts the nav bar only once a surface needs it.

Jay (2026-09-30): the boot logo showed the nav bar. sway paints the boot
wordmark while the server starts, and a bar started with the session sat over
it. The bar must stay down through boot, a loading kiosk window and the lock
screen, and come up for the signed-in desktop or a taOS app window.

Run: python3 -m pytest -q pmos/kiosk/test_taos_kiosk_shellmode.py
"""
from __future__ import annotations

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
    ([True, False], False),      # one glyph is not a bar
    ([None, True], None),        # could not read: no answer
])
def test_bar_painted_needs_both_glyphs(monkeypatch, spots, expected):
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


def _guard(monkeypatch, running=True, wanted=True, looks=(False, False)):
    m = _load()
    restarts = []
    it = iter(looks)
    monkeypatch.setattr(m, "bar_running", lambda: running)
    monkeypatch.setattr(m, "still_wants_bar", lambda: wanted)
    monkeypatch.setattr(m, "bar_painted", lambda: next(it))
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
])
def test_the_guard_does_not_restart_a_bar_it_cannot_fault(monkeypatch, kw):
    did, restarts = _guard(monkeypatch, **kw)
    assert did is False and restarts == []


def test_a_restart_and_an_ordinary_start_cannot_both_launch(monkeypatch):
    """The guard's thread restarts the bar while a window event asks for it:
    the second caller must see the bar the first one started, not the gap."""
    import threading
    import time as _time
    m = _load()
    state = {"running": True, "launches": 0}

    def launcher(argv, **kw):
        state["running"] = False          # the launcher kills the old bar first
        _time.sleep(0.2)                  # ... and the new pid is written later
        state["launches"] += 1
        state["running"] = True

    monkeypatch.setattr(m, "bar_running", lambda: state["running"])
    monkeypatch.setattr(m.subprocess, "run", launcher)
    t = threading.Thread(target=lambda: m.start_bar(replace=True))
    t.start()
    _time.sleep(0.05)                     # inside the gap
    assert state["running"] is False
    m.start_bar()                         # the main thread's ordinary start
    t.join()
    assert state["launches"] == 1
    # Control: with no bar at all, an ordinary start does launch.
    state.update(running=False, launches=0)
    m.start_bar()
    assert state["launches"] == 1
