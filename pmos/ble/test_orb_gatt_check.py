"""Each verdict in orb-gatt-check.py fails on its own defect, and passes the real thing.

The good hello reply comes from the REAL PairResponder, so the pass arm is a true round trip,
not a hand-built dict. Needs a taOS checkout with tinyagentos/cluster/ble/proto.py:

    TAOS_SRC=~/Development/tinyagentos python3 -m pytest -q pmos/ble/test_orb_gatt_check.py
"""
from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SRC = os.environ.get("TAOS_SRC", os.path.expanduser("~/Development/tinyagentos"))
if not (Path(SRC) / "tinyagentos/cluster/ble/proto.py").exists():
    pytest.skip("TAOS_SRC has no cluster/ble/proto.py", allow_module_level=True)
sys.path.insert(0, SRC)
from tinyagentos.cluster.ble import proto  # noqa: E402

assert Path(proto.__file__).resolve().is_relative_to(Path(SRC).resolve()), proto.__file__


def _load():
    path = Path(__file__).with_name("orb-gatt-check.py")
    loader = importlib.machinery.SourceFileLoader("orb_gatt_check", str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


chk = _load()
GOOD_MFR = {proto.MFR_ID: proto.advert_mfr_data(False)}


def test_advert_pass():
    assert chk.check_advert(proto, "taOS-Orb-A1B2", GOOD_MFR)[0]


@pytest.mark.parametrize("name,mfr", [
    ("taOS-usb-A1B2", GOOD_MFR),                                         # not an Orb name
    ("taOS-Orb-A1B", GOOD_MFR),                                          # 3 hex
    ("taOS Orb", GOOD_MFR),                                              # spaced, no hex
    ("taOS_Orb_A1B2", GOOD_MFR),                                         # underscores
    ("taOS-Orb-A1B2", {}),                                               # no mfr data
    ("taOS-Orb-A1B2", {0x004C: proto.advert_mfr_data(False)}),           # wrong company
    ("taOS-Orb-A1B2", {proto.MFR_ID: b"TAOS\x02\x00"}),                  # wrong magic
    ("taOS-Orb-A1B2", {proto.MFR_ID: proto.MFR_MAGIC + b"\x01\x00"}),    # v1
    ("taOS-Orb-A1B2", {proto.MFR_ID: proto.advert_mfr_data(True)}),      # paired
])
def test_advert_each_defect_fails(name, mfr):
    assert not chk.check_advert(proto, name, mfr)[0]


def _info(**over):
    d = {"v": 1, "id": "a1b2", "name": "Orb", "caps": ["orb"], "state": "idle",
         "pairable": True, "bpub": "x"}
    d.update(over)
    return json.dumps(d).encode()


def test_info_pass_id_case_insensitive():
    assert chk.check_info(_info(), "A1B2")[0]


@pytest.mark.parametrize("raw", [
    b"\xff\xfe", b"not json", b"[1,2]",
    _info(id="ffff"), _info(caps=["agent"]), _info(caps=None),
])
def test_info_each_defect_fails(raw):
    assert not chk.check_info(raw, "A1B2")[0]


def test_info_frame_as_on_dev_today_fails_the_orb_check():
    # dev's info_frame hard-codes caps ["agent"]; an Orb built on it must NOT pass as an Orb.
    raw = proto.info_frame("a1b2", "Orb", "idle", True, proto.pub_bytes(proto.x25519_keypair()[1]))
    ok, why = chk.check_info(raw, "A1B2")
    assert not ok and "orb" in why


def _real_round():
    init = proto.PairInitiator("t", proto.x25519_keypair()[0])
    board = proto.PairResponder("a1b2", proto.x25519_keypair()[0])
    return init, board.handle_message(init.start_hello())


def test_hello_pass_with_real_responder():
    init, reply = _real_round()
    ok, why = chk.check_hello(init, "a1b2", reply)
    assert ok, why


def _mut(reply, **over):
    d = json.loads(reply)
    d.update(over)
    return json.dumps({k: v for k, v in d.items() if v is not None}).encode()


@pytest.mark.parametrize("over", [
    {"v": 1}, {"t": "nonce"}, {"commit": None},
    {"bpub": base64.b64encode(b"\x01" * 31).decode()},
])
def test_hello_each_defect_fails(over):
    init, reply = _real_round()
    assert not chk.check_hello(init, "a1b2", _mut(reply, **over))[0]


def test_hello_no_reply_and_error_frame_fail():
    init, _ = _real_round()
    assert not chk.check_hello(init, "a1b2", None)[0]
    init, _ = _real_round()
    assert not chk.check_hello(init, "a1b2", b'{"t":"error","why":"locked"}')[0]


def test_spaced_name_parses_but_is_not_the_spec_form():
    # firmware v0.5.0 advertises "taOS Orb XXXX"; it must parse (so the real Orb is checked) and must
    # NOT match the spec form (so the drift is reported as WARN, never passed silently).
    assert chk.check_advert(proto, "taOS Orb A1B2", GOOD_MFR)[0]
    assert chk.NAME_RE.match("taOS Orb A1B2").group(1) == "A1B2"
    assert not chk.SPEC_NAME_RE.match("taOS Orb A1B2")
    assert chk.SPEC_NAME_RE.match("taOS-Orb-A1B2")
