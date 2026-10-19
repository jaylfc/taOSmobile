"""The flash host reaches the handset over USB without a hand-typed address.

Jay 2026-10-19: the Mac mini goes, so the phone's USB fallback (ssh
jay@172.16.42.1 when wifi/tailnet is down) moves to omarchy with a persistent
NetworkManager profile for 172.16.42.2/24. The phone runs no DHCP on usb0
(measured 2026-09-17), so without this the host self-assigns a 169.254
link-local and the phone looks dead.

NM cannot match a device by USB serial, and the taOSusb Pi is also an NCM
gadget, so a udev rule names ONLY the phone's interface and the profile binds
to that name. These tests hold the three files (rule, profile, presence
check) to one name and one subnet.

Run: python3 -m pytest -q -p no:cacheprovider pmos/host/test_handset_usb_host.py
"""
from __future__ import annotations

import configparser
import ipaddress
import re
from pathlib import Path

HERE = Path(__file__).parent
RULE = HERE / "70-taos-handset-usb.rules"
PROFILE = HERE / "taos-handset-usb.nmconnection"
PRESENCE = HERE.parent.parent / "scripts" / "check-device-presence.sh"


def _rule_lines() -> list[str]:
    return [l for l in RULE.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def _rule_name() -> str:
    (line,) = _rule_lines()
    m = re.search(r'\bNAME="([^"]+)"', line)
    assert m, line
    return m.group(1)


def _profile() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(PROFILE.read_text())
    return cp


def test_rule_renames_only_the_phone():
    (line,) = _rule_lines()
    assert 'SUBSYSTEM=="net"' in line
    assert 'ACTION=="add"' in line
    # The phone's gadget serial, as enumerated on omarchy (1d6b:0103).
    assert 'ATTRS{serial}=="nothing,spacewar"' in line
    assert 'ATTRS{idVendor}=="1d6b"' in line


def test_rule_name_is_a_valid_interface_name():
    name = _rule_name()
    assert re.fullmatch(r"[a-z][a-z0-9]{0,14}", name), name


def test_profile_binds_to_the_rule_name():
    cp = _profile()
    assert cp["connection"]["type"] == "ethernet"
    assert cp["connection"]["interface-name"] == _rule_name()
    assert cp["connection"]["autoconnect"] == "true"


def test_profile_address_reaches_the_phone():
    cp = _profile()
    assert cp["ipv4"]["method"] == "manual"
    iface = ipaddress.ip_interface(cp["ipv4"]["address1"])
    assert str(iface) == "172.16.42.2/24"
    phone = re.search(r'^PHONE_IP="([0-9.]+)"', PRESENCE.read_text(), re.M).group(1)
    assert ipaddress.ip_address(phone) in iface.network
    assert iface.ip != ipaddress.ip_address(phone)


def test_profile_never_takes_the_default_route_or_dns():
    # The host's real uplink must survive the phone being plugged in.
    cp = _profile()
    assert cp["ipv4"]["never-default"] == "true"
    assert cp["ipv4"]["ignore-auto-dns"] == "true"
    assert cp["ipv6"]["method"] == "disabled"


def test_presence_check_accepts_the_rule_name():
    names = re.search(r'^RNDIS_NAMES="([^"]*)"', PRESENCE.read_text(), re.M).group(1).split()
    assert _rule_name() in names
