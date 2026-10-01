"""taos-usb-mode switches the USB-C port through usb-signaller and reads it back.

Jay (2026-10-01): a way to switch USB modes by CLI and from the lock-screen
pull-down, kept across reboots, defaulting to the network link.

The fake below stands in for usb-signaller AND for the measured bug in it:
set_mode binds the gadget in a fake configfs, but usb0 only gets its address
back when the developer-mode helper is RESTARTED -- exactly what was measured
on the handset on 2026-10-01 (charging -> mtp -> developer left usb0 down).

Run: python3 -m pytest -q -p no:cacheprovider pmos/kiosk/test_taos_usb_mode.py
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).with_name("bin") / "taos-usb-mode"
    loader = importlib.machinery.SourceFileLoader("taos_usb_mode", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


GADGETS = ("usb-signaller-developer", "usb-signaller-mtp", "usb-signaller-tethering")


class FakeSignaller:
    """usb-signaller as measured, including its developer-mode restart bug."""

    def __init__(self, mod, root: Path):
        self.mod, self.root = mod, root
        self.calls: list[list[str]] = []
        self.address = True          # usb0 holds 172.16.42.1 right now
        self.binds = True            # set_mode actually binds a gadget
        self.restart_fixes = True    # restarting the helper restores the address
        for g in GADGETS:
            (root / "gadget" / g).mkdir(parents=True)
            (root / "gadget" / g / "UDC").write_text("")
        (root / "udc" / "a600000.usb").mkdir(parents=True)
        (root / "udc" / "a600000.usb" / "state").write_text("configured\n")
        self.bind("usb-signaller-developer")

    def bind(self, gadget):
        for g in GADGETS:
            (self.root / "gadget" / g / "UDC").write_text(
                "a600000.usb\n" if g == gadget else "")

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[:1] == ["busctl"] and "set_mode" in argv:
            sig = argv[-1]
            if self.binds:
                gadget = {"developer_mode": "usb-signaller-developer",
                          "mtp_ffs_mode": "usb-signaller-mtp",
                          "charging_only": None}[sig]
                self.bind(gadget)
                # Leaving or re-entering developer mode drops the address; the
                # helper is NOT re-run by usb-signaller (the bug).
                self.address = False
            return subprocess.CompletedProcess(argv, 0, 's "%s"\n' % sig, "")
        if argv[:2] == ["systemctl", "restart"]:
            if self.restart_fixes:
                self.address = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:1] == ["ip"]:
            out = ("5: usb0    inet 172.16.42.1/16 brd 172.16.255.255 scope global usb0\n"
                   if self.address else "")
            return subprocess.CompletedProcess(argv, 0, out, "")
        raise AssertionError("unexpected command %r" % (argv,))

    def set_modes(self):
        return [c[-1] for c in self.calls if "set_mode" in c]


@pytest.fixture()
def um(monkeypatch, tmp_path):
    mod = _load()
    fake = FakeSignaller(mod, tmp_path)
    monkeypatch.setattr(mod, "CONFIGFS", str(tmp_path / "gadget"))
    monkeypatch.setattr(mod, "UDC_CLASS", str(tmp_path / "udc"))
    monkeypatch.setattr(mod, "TOML", str(tmp_path / "etc" / "usb-signaller.toml"))
    monkeypatch.setattr(mod, "SETTLE_S", 0.6)
    monkeypatch.setattr(mod.subprocess, "run", fake.run)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/umtprd")
    mod.fake = fake
    return mod


def saved_text(um):
    return Path(um.TOML).read_text() if os.path.exists(um.TOML) else None


# ---- malformed and refused requests change NOTHING -------------------------

@pytest.mark.parametrize("argv", [
    ["set"], ["set", ""], ["set", "NCM"], ["set", "usb"], ["set", "ncm", "--save"],
    ["set", "ncm", "--now", "extra"], ["switch", "ncm"], ["set", "--now"],
])
def test_malformed_is_refused_and_touches_nothing(um, argv, capsys):
    assert um.main(argv) == 2
    assert um.fake.calls == []
    assert saved_text(um) is None


@pytest.mark.parametrize("mode", ["rndis", "adb"])
def test_rndis_and_adb_are_refused_with_the_reason(um, mode, capsys):
    assert um.main(["set", mode]) == 2
    err = capsys.readouterr().err
    assert "usb-signaller" in err and "ncm" in err
    assert um.fake.calls == [] and saved_text(um) is None


def test_mtp_refused_when_umtprd_is_missing(um, monkeypatch):
    monkeypatch.setattr(um.shutil, "which", lambda name: None)
    assert um.main(["set", "mtp"]) == 2
    assert um.fake.calls == [] and saved_text(um) is None
    assert "mtp" not in um.available()


def test_non_root_cannot_set(um, monkeypatch):
    monkeypatch.setattr(um.os, "geteuid", lambda: 1000)
    assert um.main(["set", "charging"]) == 1
    assert um.fake.calls == [] and saved_text(um) is None


# ---- switching, saving and the read-back -----------------------------------

def test_charging_unbinds_and_is_saved(um):
    assert um.main(["set", "charging"]) == 0
    assert um.fake.set_modes() == ["charging_only"]
    assert um.current() == "charging"
    assert 'default_mode="charging_only"' in saved_text(um)
    assert um.saved() == "charging"


def test_ncm_restarts_the_helper_so_usb0_gets_its_address_back(um):
    um.main(["set", "charging"])
    assert um.main(["set", "ncm"]) == 0
    assert ["systemctl", "restart", um.DEV_HELPER] in um.fake.calls
    assert um.fake.address is True
    assert um.saved() == "ncm"


def test_ncm_with_the_gadget_bound_but_no_address_is_a_failure(um, capsys):
    # The measured failure: developer gadget bound, usb0 empty. Without the
    # address check current() alone reads "ncm" and this would exit 0.
    um.main(["set", "charging"])
    um.fake.restart_fixes = False
    assert um.main(["set", "ncm"]) == 1
    assert um.current() == "ncm"          # the gadget DID bind ...
    assert "ncm-without-address" in capsys.readouterr().err   # ... and it is still a failure


def test_a_switch_that_does_not_land_exits_nonzero(um, capsys):
    um.fake.binds = False                 # usb-signaller says yes and does nothing
    assert um.main(["set", "mtp"]) == 1
    assert "reads ncm" in capsys.readouterr().err


def test_now_switches_without_saving(um):
    assert um.main(["set", "mtp", "--now"]) == 0
    assert um.current() == "mtp"
    assert saved_text(um) is None and um.saved() == "ncm"


def test_mode_is_saved_before_the_switch_is_tried(um):
    um.fake.binds = False
    um.main(["set", "charging"])
    assert um.saved() == "charging"


def test_save_replaces_the_whole_file_atomically(um):
    um.main(["set", "mtp"])
    um.main(["set", "charging"])
    text = saved_text(um)
    assert text.count("default_mode") == 1 and "[main]" in text
    leftovers = [p for p in os.listdir(os.path.dirname(um.TOML)) if p.startswith(".")]
    assert leftovers == []


# ---- status -----------------------------------------------------------------

def test_status_names_each_bound_gadget(um):
    assert um.current() == "ncm"
    um.fake.bind("usb-signaller-mtp")
    assert um.current() == "mtp"
    um.fake.bind("usb-signaller-tethering")
    assert um.current() == "tethering"
    um.fake.bind(None)
    assert um.current() == "charging"


def test_saved_defaults_to_ncm_and_reads_the_override(um):
    assert um.saved() == "ncm"
    Path(um.TOML).parent.mkdir(parents=True, exist_ok=True)
    Path(um.TOML).write_text('[main]\ndefault_mode = "mtp_ffs_mode"\n')
    assert um.saved() == "mtp"


def test_status_reports_a_missing_address(um, capsys):
    um.fake.address = False
    assert um.main([]) == 0
    assert "NONE on usb0" in capsys.readouterr().out
