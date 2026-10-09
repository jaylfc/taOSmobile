#!/usr/bin/env python3
"""Tests for taos-locationd -- keep modem GPS enabled and publish location fixes.

Red-first: run on origin/main (script missing) to capture the FAILED output.
After implementing, one defect is restored (print the fix in the transition
log line) and (f) goes red, then it is put back.

Run: python3 -m pytest -q -p no:cacheprovider pmos/kiosk/test_taos_locationd.py
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# Fixtures as string constants in the test
NOFIX = """modem.location.gps.nmea.length   : 2
modem.location.gps.nmea.value[1] : $GPGSV,1,1,0,1*54
modem.location.gps.nmea.value[2] : $GLGSV,1,1,01,255,,,30,1*48
modem.location.gps.utc           : --
modem.location.gps.longitude     : --
modem.location.gps.latitude      : --
modem.location.gps.altitude      : --"""

FIX = """modem.location.gps.nmea.length   : 2
modem.location.gps.nmea.value[1] : $GPGSV,1,1,0,1*54
modem.location.gps.nmea.value[2] : $GLGSV,1,1,01,255,,,30,1*48
modem.location.gps.utc           : 154530.00
modem.location.gps.longitude     : -0.120000
modem.location.gps.latitude      : 51.500000
modem.location.gps.altitude      : --
modem.location.gps.nmea.value[3] : $GPGGA,154530.00,5130.0000,N,00007.2000,W,1,08,1.2,30.0,M,47.0,M,,*4A"""

FIX_NO_GGA = """modem.location.gps.nmea.length   : 2
modem.location.gps.nmea.value[1] : $GPGSV,1,1,0,1*54
modem.location.gps.nmea.value[2] : $GLGSV,1,1,01,255,,,30,1*48
modem.location.gps.utc           : 154530.00
modem.location.gps.longitude     : -0.120000
modem.location.gps.latitude      : 51.500000
modem.location.gps.altitude      : --"""

OUT_OF_RANGE_LAT = """modem.location.gps.nmea.length   : 2
modem.location.gps.nmea.value[1] : $GPGSV,1,1,0,1*54
modem.location.gps.nmea.value[2] : $GLGSV,1,1,01,255,,,30,1*48
modem.location.gps.utc           : --
modem.location.gps.longitude     : --
modem.location.gps.latitude      : 95.0
modem.location.gps.altitude      : --"""


def _load_taos_locationd():
    """Load the taos-locationd script as a module."""
    path = Path(__file__).with_name("bin") / "taos-locationd"
    loader = importlib.machinery.SourceFileLoader("taos_locationd", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_parse_location_nofix():
    """parse_location(NOFIX) is None."""
    mod = _load_taos_locationd()
    assert mod.parse_location(NOFIX) is None


def test_parse_location_fix():
    """parse_location(FIX) == {"lat": 51.5, "lon": -0.12, "accuracy_m": 6, "gps_utc": "154530.00"}."""
    mod = _load_taos_locationd()
    result = mod.parse_location(FIX)
    assert result is not None
    assert result["lat"] == 51.5
    assert result["lon"] == -0.12
    assert result["accuracy_m"] == 6
    assert result["gps_utc"] == "154530.00"


def test_parse_location_fix_no_gga():
    """FIX without the GGA line gives accuracy_m None."""
    mod = _load_taos_locationd()
    result = mod.parse_location(FIX_NO_GGA)
    assert result is not None
    assert result["lat"] == 51.5
    assert result["lon"] == -0.12
    assert result["accuracy_m"] is None
    assert result["gps_utc"] == "154530.00"


def test_parse_location_out_of_range():
    """latitude 95.0 gives None."""
    mod = _load_taos_locationd()
    assert mod.parse_location(OUT_OF_RANGE_LAT) is None


def test_write_fix(tmp_path):
    """write_fix into tmp_path: file mode is 0o640, JSON has lat/lon/fix_utc/source,
    a second write replaces it, and the dir holds exactly one file afterwards.
    """
    mod = _load_taos_locationd()
    fix = {"lat": 51.5, "lon": -0.12, "accuracy_m": 6, "gps_utc": "154530.00"}

    # First write
    out_path = tmp_path / "location.json"
    mod.write_fix(out_path, fix)
    assert out_path.exists()

    # Check file mode
    st = out_path.stat()
    assert stat.S_IMODE(st.st_mode) == 0o640

    # Check content
    with open(out_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    assert content["lat"] == 51.5
    assert content["lon"] == -0.12
    assert "fix_utc" in content
    assert content["source"] == "modem-gnss"
    assert "gps_utc" in content

    # Second write (should replace)
    fix2 = {"lat": 1.0, "lon": 2.0, "accuracy_m": 5, "gps_utc": "123456.00"}
    mod.write_fix(out_path, fix2)

    # Check first write is gone
    with open(out_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    assert content["lat"] == 1.0
    assert content["lon"] == 2.0

    # Check directory holds exactly one file
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "location.json"


def test_run_script_once(tmp_path, monkeypatch):
    """Run the script as a subprocess with TAOS_LOCATIOND_ONCE=1,
    TAOS_LOCATION_OUT in tmp_path and TAOS_MMCLI pointing at a fake sh script
    that prints FIX for `--location-get` and nothing for the enable call:
    the output file has lat 51.5, AND the combined stdout+stderr contains
    "fix acquired" but NOT "51.5" and NOT "-0.12" (the leak check; the
    positive "fix acquired" control proves output was captured at all).
    """
    # Create fake mmcli script
    fake_mmcli = tmp_path / "fake-mmcli"
    fake_mmcli.write_text("""#!/bin/sh
if [ "$*" = "-m any --location-enable-gps-nmea --location-enable-gps-raw" ]; then
    exit 0
fi
if [ "$*" = "-m any --location-get --output-keyvalue" ]; then
    echo "modem.location.gps.nmea.length   : 2"
    echo "modem.location.gps.nmea.value[1] : $GPGSV,1,1,0,1*54"
    echo "modem.location.gps.nmea.value[2] : $GLGSV,1,1,01,255,,,30,1*48"
    echo "modem.location.gps.utc           : 154530.00"
    echo "modem.location.gps.longitude     : -0.120000"
    echo "modem.location.gps.latitude      : 51.500000"
    echo "modem.location.gps.altitude      : --"
    echo "modem.location.gps.nmea.value[3] : $GPGGA,154530.00,5130.0000,N,00007.2000,W,1,08,1.2,30.0,M,47.0,M,,*4A"
    exit 0
fi
exit 1
""")
    fake_mmcli.chmod(0o755)

    # Set up environment
    monkeypatch.setenv("TAOS_LOCATIOND_ONCE", "1")
    monkeypatch.setenv("TAOS_LOCATION_OUT", str(tmp_path / "location.json"))
    monkeypatch.setenv("TAOS_MMCLI", str(fake_mmcli))

    # Run the script
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.util, pathlib, sys
loader = importlib.machinery.SourceFileLoader('taos_locationd', r'%s')
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
sys.exit(0 if not hasattr(mod, 'main') else mod.main())
""" % str(Path(__file__).with_name("bin") / "taos-locationd")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    # Check that the output file has the correct location
    out_path = tmp_path / "location.json"
    assert out_path.exists()
    with open(out_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    assert content["lat"] == 51.5
    assert content["lon"] == -0.12

    # Check combined stdout+stderr contains "fix acquired" but NOT coordinates
    combined = result.stdout + result.stderr
    assert "fix acquired" in combined
    assert "51.5" not in combined
    assert "-0.12" not in combined
    
    # Check script exited with code 0
    assert result.returncode == 0

def _run_once(tmp_path, monkeypatch, fake_body):
    fake_mmcli = tmp_path / "fake-mmcli"
    fake_mmcli.write_text("#!/bin/sh\n" + fake_body)
    fake_mmcli.chmod(0o755)
    monkeypatch.setenv("TAOS_LOCATIOND_ONCE", "1")
    monkeypatch.setenv("TAOS_LOCATION_OUT", str(tmp_path / "location.json"))
    monkeypatch.setenv("TAOS_MMCLI", str(fake_mmcli))
    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("bin") / "taos-locationd")],
        capture_output=True, text=True, cwd=tmp_path, timeout=30,
    )


def test_failed_location_read_is_logged_not_a_crash(tmp_path, monkeypatch):
    """mmcli failing the read (modem gone, ModemManager restarting) must log one
    line and carry on, not kill the daemon with a traceback."""
    result = _run_once(tmp_path, monkeypatch, (
        'case "$*" in *--location-get*) echo "error: no modems were found" >&2; exit 1;; esac\n'
        "exit 0\n"
    ))
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "taos-locationd: get location failed: error: no modems were found" in result.stderr
    assert not (tmp_path / "location.json").exists()


def test_missing_mmcli_is_logged_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_LOCATIOND_ONCE", "1")
    monkeypatch.setenv("TAOS_LOCATION_OUT", str(tmp_path / "location.json"))
    monkeypatch.setenv("TAOS_MMCLI", str(tmp_path / "no-such-mmcli"))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("bin") / "taos-locationd")],
        capture_output=True, text=True, cwd=tmp_path, timeout=30,
    )
    assert "Traceback" not in result.stderr
    assert "taos-locationd: enable failed" in result.stderr


def test_gga_from_any_gnss_talker_gives_accuracy():
    """$GNGGA (multi-constellation) is what a Qualcomm engine usually emits."""
    text = FIX.replace("$GPGGA", "$GNGGA")
    assert "$GNGGA" in text
    assert _load_taos_locationd().parse_location(text)["accuracy_m"] == 6
