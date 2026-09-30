#!/usr/bin/env python3
"""The handset's first BLE connect/GATT test: the phone (taOS host) against a taOS Orb.

Jay (2026-09-30): the host is the taOSmobile handset's own taOS; test against the Orb first
(taOSusb on the Pi Zero later). Target spec from taOSc (addendum r3/r4), built to #3180.

Runs ON THE HANDSET as the taos user, importing the REAL protocol code from the installed taOS
(PYTHONPATH=/root/tinyagentos), so the phone's own code paths are what get exercised:

    PYTHONPATH=/root/tinyagentos /root/tinyagentos/.venv/bin/python orb-gatt-check.py [--no-pair]

Checks, each reported on its own line:
  advert  manufacturer data parses as taOS v2, unpaired; name is taOS-Orb-XXXX (4 hex)
  info    CHAR_INFO reads as UTF-8 JSON; "id" equals the name's XXXX; caps contains "orb"
  hello   a fragment()-framed PairInitiator hello on CHAR_PAIR gets a notify that reassembles
          to the board's hello, and PairInitiator.on_hello_commit() ACCEPTS it (real parser:
          version, 32-byte bpub/epub/commit)
Exit: 0 all PASS; 1 any FAIL; 3 no Orb found (NOT a pass: nothing was measured).
--no-pair reports hello as SKIP (never PASS) for a firmware stage without step 2.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

# Spec form is taOS-Orb-XXXX; firmware v0.5.0 advertises "taOS Orb XXXX" (spaces). Both parse; the
# spaced form is reported as a WARN line (a spec drift for taOSc to settle), never silently.
NAME_RE = re.compile(r"^taOS[- ]Orb[- ]([0-9A-Fa-f]{4})$")
SPEC_NAME_RE = re.compile(r"^taOS-Orb-[0-9A-Fa-f]{4}$")


def check_advert(proto, name, mfr):
    """-> (ok, detail). mfr is the advert's manufacturer_data dict {company_id: bytes}."""
    m = NAME_RE.match(name or "")
    if not m:
        return False, "name %r is not taOS-Orb-XXXX" % (name,)
    if proto.MFR_ID not in (mfr or {}):
        return False, "no manufacturer data under 0x%04X" % proto.MFR_ID
    parsed = proto.parse_advert_mfr(mfr[proto.MFR_ID])
    if parsed is None:
        return False, "manufacturer data %r is not a taOS advert" % (bytes(mfr[proto.MFR_ID]),)
    if parsed["v"] != proto.PROTO_VERSION:
        return False, "advert v%s, phone speaks v%d" % (parsed["v"], proto.PROTO_VERSION)
    if parsed["paired"]:
        return False, "advert says paired; the test expects an unpaired Orb"
    return True, "v%d unpaired, board %s" % (parsed["v"], m.group(1))


def check_info(raw, alphabet):
    """-> (ok, detail, board_id). The board id is random from proto.ID_ALPHABET and independent of
    the advert name (the real Orb: name 4F9A, id KLGK); the server binds THIS id into the handshake.
    state/pairable mirror the server's pair/start gate (409 "board is not pairable" otherwise)."""
    try:
        info = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return False, "info is not UTF-8 JSON: %s" % e, None
    if not isinstance(info, dict):
        return False, "info is JSON but not an object", None
    bid = info.get("id")
    if not isinstance(bid, str) or not bid or any(ch not in alphabet for ch in bid):
        return False, "info id %r is not a board id over the proto alphabet" % (bid,), None
    if info.get("state") != "unpaired" or info.get("pairable") is not True:
        return False, "state %r pairable %r: server would refuse (409)" % (
            info.get("state"), info.get("pairable")), None
    if "orb" not in (info.get("caps") or []):
        return False, "caps %r has no \"orb\"" % (info.get("caps"),), None
    return True, "id %s caps %s" % (bid, info["caps"]), bid


def check_hello(initiator, board_id, reply):
    if reply is None:
        return False, "no complete notify on CHAR_PAIR"
    try:
        nxt = initiator.on_hello_commit(board_id, bytes(reply))
    except Exception as e:  # noqa: BLE001 - any rejection by the real parser is the verdict
        return False, "on_hello_commit rejected the reply: %s: %s" % (type(e).__name__, e)
    if json.loads(nxt.decode("utf-8")).get("t") != "nonce":
        return False, "initiator did not advance to the nonce step"
    return True, "board hello accepted (bpub/epub/commit), next = nonce"


async def run(no_pair):
    from bleak import BleakClient, BleakScanner
    from tinyagentos.cluster.ble import proto

    found = await BleakScanner.discover(timeout=10.0, return_adv=True)
    # Select by the taOS manufacturer data, not the name: the name rides in the scan response.
    orbs = [(d, a) for d, a in found.values()
            if proto.MFR_ID in (a.manufacturer_data or {})
            and proto.parse_advert_mfr(a.manufacturer_data[proto.MFR_ID]) is not None]
    print("scan    %d devices, %d with taOS manufacturer data" % (len(found), len(orbs)))
    if not orbs:
        print("RESULT  NO ORB FOUND: nothing measured")
        return 3
    dev, adv = max(orbs, key=lambda da: da[1].rssi)
    results = []
    m = NAME_RE.match(adv.local_name or "")
    if not m:
        print("advert  FAIL  taOS advert but name %r is not taOS Orb XXXX (rssi %d)" % (adv.local_name, adv.rssi))
        print("RESULT  FAIL")
        return 1
    name_hex = m.group(1)
    if not SPEC_NAME_RE.match(adv.local_name):
        print("advert  WARN  name %r differs from the spec form taOS-Orb-XXXX" % adv.local_name)

    ok, why = check_advert(proto, adv.local_name, adv.manufacturer_data)
    results.append(ok)
    print("advert  %s  %s (rssi %d)" % ("PASS" if ok else "FAIL", why, adv.rssi))

    async with BleakClient(dev) as client:
        ok, why, board_id = check_info(await client.read_gatt_char(proto.CHAR_INFO_UUID), proto.ID_ALPHABET)
        results.append(ok)
        print("info    %s  %s (advert name hex %s)" % ("PASS" if ok else "FAIL", why, name_hex))
        if not ok:
            print("RESULT  FAIL")
            return 1

        if no_pair:
            print("hello   SKIP  --no-pair (NOT a pass)")
        else:
            initiator = proto.PairInitiator("taosmobile-gatt-check", proto.x25519_keypair()[0])
            reasm = proto.Reassembler()
            done = asyncio.get_running_loop().create_future()

            def on_notify(_char, data):
                msg = reasm.feed(bytes(data))
                if msg is not None and not done.done():
                    done.set_result(msg)

            await client.start_notify(proto.CHAR_PAIR_UUID, on_notify)
            chunk = max(1, client.mtu_size - 5)   # MTU - 3 ATT - 2 fragment header
            for frag in proto.fragment(initiator.start_hello(), 1, chunk):
                await client.write_gatt_char(proto.CHAR_PAIR_UUID, frag, response=True)
            try:
                reply = await asyncio.wait_for(done, 10.0)
            except asyncio.TimeoutError:
                reply = None
            ok, why = check_hello(initiator, board_id, reply)
            results.append(ok)
            print("hello   %s  %s (mtu %d)" % ("PASS" if ok else "FAIL", why, client.mtu_size))

    print("RESULT  %s" % ("PASS" if all(results) else "FAIL"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run("--no-pair" in sys.argv[1:])))
