"""The patched usb-signaller APKBUILD ships the patch it carries, with a matching checksum.

abuild only applies a patch named in source=, and refuses a source whose sha512 is missing or
stale. Both mistakes are silent until a pmbootstrap build on another host, so check here.

Run: python3 -m pytest -q -p no:cacheprovider pmos/usb-signaller/test_apkbuild.py
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _var(text: str, name: str) -> list[str]:
    m = re.search(rf'^{name}="(.*?)"', text, re.S | re.M)
    assert m, f"{name} missing from APKBUILD"
    return m.group(1).split()


def _local_sources() -> list[str]:
    text = (HERE / "APKBUILD").read_text()
    return [s for s in _var(text, "source") if "::" not in s and "://" not in s]


def test_every_patch_here_is_in_source():
    patches = sorted(p.name for p in HERE.glob("*.patch"))
    assert patches, "no patch in pmos/usb-signaller"
    assert sorted(s for s in _local_sources() if s.endswith(".patch")) == patches


def test_local_sources_have_current_sha512():
    text = (HERE / "APKBUILD").read_text()
    sums = _var(text, "sha512sums")
    recorded = dict(zip(sums[1::2], sums[0::2]))
    for name in _local_sources():
        actual = hashlib.sha512((HERE / name).read_bytes()).hexdigest()
        assert recorded.get(name) == actual, f"{name}: run pmbootstrap checksum"


def test_pkgrel_upgrades_stock_package():
    text = (HERE / "APKBUILD").read_text()
    assert re.search(r"^pkgrel=([1-9]\d*)$", text, re.M), "pkgrel must be above Alpine's 0"
