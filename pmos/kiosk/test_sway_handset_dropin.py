"""Every helper the handset sway drop-in execs ships in this repo and is installed.

A bindsym's exec output goes nowhere, so a helper the drop-in names but the
installer never copies is a power key that silently does nothing.

Run: python3 -m pytest -q pmos/kiosk/test_sway_handset_dropin.py
"""
import re
from pathlib import Path

KIOSK = Path(__file__).resolve().parent
DROPIN = KIOSK / "etc" / "sway.d" / "50-handset.conf"
INSTALLER = KIOSK / "install-kiosk-continuity.sh"


def _named_helpers():
    return sorted(set(re.findall(r"/usr/local/bin/(taos-[a-z0-9-]+)", DROPIN.read_text())))


def _installed_helpers():
    m = re.search(r"for helper in (.*?); do", INSTALLER.read_text(), re.S)
    assert m, "installer helper loop not found"
    return set(m.group(1).replace("\\\n", " ").split())


def test_drop_in_names_the_handset_helpers():
    # Control: the regex must find the helpers, or the checks below see nothing.
    assert {"taos-kiosk-dt2w-run", "taos-kiosk-power-hold", "taos-kiosk-volume"} <= set(_named_helpers())


def test_every_named_helper_ships_and_is_installed():
    installed = _installed_helpers()
    for name in _named_helpers():
        assert (KIOSK / "bin" / name).is_file(), name + " is not in pmos/kiosk/bin"
        assert name in installed, name + " is not in the installer's helper loop"
