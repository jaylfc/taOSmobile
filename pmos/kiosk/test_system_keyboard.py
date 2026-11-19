"""The handset's own kiosk files carry what the system keyboard needs.

The keyboard lives in taOS's shared session layer; the handset installs its own
sway config and launcher, so these two must keep the hooks or the keyboard
silently never loads.

Run: python3 -m pytest -q pmos/kiosk/test_system_keyboard.py
"""
import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
CONF = (HERE / "etc" / "sway-kiosk.conf").read_text()
LAUNCH = (HERE / "bin" / "taos-kiosk-launch").read_text()
INSTALL = HERE / "install-keyboard.sh"


def test_sway_config_includes_the_drop_in_directory():
    # Control: we are reading the real kiosk config.
    assert "taos-kiosk-app.conf" in CONF
    assert re.search(r"^include /etc/taos/sway\.d/\*\.conf$", CONF, re.M)


def test_launcher_passes_both_ime_flags_to_chromium():
    assert "--kiosk" in LAUNCH  # control: this is the browser argument list
    assert "--enable-wayland-ime" in LAUNCH
    assert "--wayland-text-input-version=3" in LAUNCH


def test_installer_parses_and_never_runs_the_generic_session_installer():
    assert subprocess.run(["sh", "-n", str(INSTALL)]).returncode == 0
    code = "\n".join(l for l in INSTALL.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert "taos-session keyboard on" in code  # control: comments were stripped, code remains
    assert "install-session.sh" not in code
