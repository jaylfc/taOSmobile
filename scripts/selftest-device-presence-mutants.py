#!/usr/bin/env python3
"""Prove selftest-device-presence.sh can go RED -- tsk-wtc2zn, tsk-amhjqp.

Thirty green states read as thirty proofs. They are not: a state proves
something only if it FAILS when the defect it names is present. This script
breaks check-device-presence.sh on purpose, one defect at a time, and requires
the state that names each defect to go red.

Two of the mutants below had to be rewritten because their first versions were
INERT: they forced `carrier=yes` on one line while the NO-CARRIER and state
checks two lines down reset it, so the mutated code ran and changed nothing
and the harness printed a clean green. That is tsk-amhjqp exactly, met while
writing the file that cites it. A mutation is evidence only when it turns the
harness RED; a green one is an unproven mutation until you have checked that
the mutated line actually decides anything.

This is not hypothetical for this file. While it was being written, every stub
in the harness was inert -- `cat` was missing from the sealed PATH -- and 14
states printed PASS against a fixture the check never saw. Two of the defects
below are the exact ones tsk-wtc2zn was filed about, restored rather than
approximated: the substring `usb` match, and the missing carrier requirement.

Each mutation is applied by EXACT text replacement and read back out of the
file afterwards. A mutation that did not apply prints an ordinary green, which
is the same vacuous-pass defect one level up, so the check is not optional.

  exit 0  every mutant was caught by the state that names it
  exit 1  a mutant SURVIVED -- that state cannot fail, so it proves nothing
  exit 2  INCOMPLETE -- the baseline was not green, or a mutation did not
          apply, so nothing was measured

Usage:  ./selftest-device-presence-mutants.py [-v]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = "check-device-presence.sh"
HARNESS = "selftest-device-presence.sh"

VERBOSE = "-v" in sys.argv[1:]

# (name, why, [(old, new), ...], substring of the FAIL line that must appear)
#
# `edits` is a list because one defect is sometimes two lines. The original
# tsk-wtc2zn instrument had NO carrier requirement AND matched by substring;
# restoring only half of it would be a different, weaker bug than the one that
# actually shipped.
MUTANTS = [
    (
        "the original instrument, restored: substring match AND no carrier test",
        "`ip -o link | grep -ci \"usb\\|rndis\"` returned 3 on the Pi -- ptusb0 and "
        "two Incus bridges, all NO-CARRIER and DOWN. This is that check, put back.",
        [
            ('        for want in $RNDIS_NAMES; do [ "$name" = "$want" ] && matched=yes; done',
             '        case "$name" in *usb*|*rndis*) matched=yes ;; esac'),
            ('        carrier=no\n        case ",$flags," in *,LOWER_UP,*) carrier=yes ;; esac',
             '        carrier=yes'),
            ('        case ",$flags," in *NO-CARRIER*) carrier=no ;; esac',
             '        : # NO-CARRIER ignored'),
            ('        case "$state" in UP|UNKNOWN) ;; *) carrier=no ;; esac',
             '        : # link state ignored'),
        ],
        "the real Pi, phone in a drawer",
    ),
    (
        "interface names matched by substring",
        "The half of the original defect that survives a carrier check: ptusb0 "
        "can come up with a carrier, and then it is reported as the phone.",
        [('        for want in $RNDIS_NAMES; do [ "$name" = "$want" ] && matched=yes; done',
          '        case "$name" in *usb*|*rndis*) matched=yes ;; esac')],
        "ptusb0 UP with a carrier",
    ),
    (
        "interface names matched by suffix",
        "The plausible half-fix -- `grep 'usb0$'` instead of a substring. It "
        "still matches ptusb0, so it is the same bug wearing a tighter pattern.",
        [('        for want in $RNDIS_NAMES; do [ "$name" = "$want" ] && matched=yes; done',
          '        case "$name" in *usb0|*rndis0) matched=yes ;; esac')],
        "ptusb0 UP with a carrier",
    ),
    (
        "the carrier requirement dropped",
        "A DOWN interface is never evidence of a booted phone. Without this, an "
        "RNDIS interface left behind by an earlier session reports the phone.",
        [
            ('        carrier=no\n        case ",$flags," in *,LOWER_UP,*) carrier=yes ;; esac',
             '        carrier=yes'),
            ('        case ",$flags," in *NO-CARRIER*) carrier=no ;; esac',
             '        : # NO-CARRIER ignored'),
            ('        case "$state" in UP|UNKNOWN) ;; *) carrier=no ;; esac',
             '        : # link state ignored'),
        ],
        "usb0 exists but has NO CARRIER",
    ),
    (
        "link up with nothing answering is no longer flagged",
        "The link is the medium, not the device. Silently accepting it is how a "
        "half-finished boot gets reported as a successful flash.",
        [('[ "$L" = yes ] && [ "$P" != yes ] && \\',
          '[ "$L" = yes ] && [ "$P" != yes ] && false && \\')],
        "link up but nothing answers the ping",
    ),
    (
        "a ping with no link behind it is no longer flagged",
        "Something other than the phone owning 172.16.42.1 on the flash host is "
        "the false positive that looks most like success.",
        [('[ "$P" = yes ] && [ "$L" != yes ] && \\',
          '[ "$P" = yes ] && [ "$L" != yes ] && false && \\')],
        "the ping answers but no RNDIS link is up",
    ),
    (
        "booted and in-the-bootloader at once is no longer flagged",
        "One device cannot be in both states, so this is an instrument fault. "
        "Resolving it silently in favour of either one hides that.",
        [('{ [ "$L" = yes ] || [ "$P" = yes ]; } && { [ "$F" = yes ] || [ "$A" = yes ]; } && \\',
          '{ [ "$L" = yes ] || [ "$P" = yes ]; } && { [ "$F" = yes ] || [ "$A" = yes ]; } && false && \\')],
        "booted AND in the bootloader at once",
    ),
    (
        "fastboot and adb both claiming a device is no longer flagged",
        "Same family, and the one most likely to be a stale adb server rather "
        "than a real device.",
        [('[ "$F" = yes ] && [ "$A" = yes ] && \\',
          '[ "$F" = yes ] && [ "$A" = yes ] && false && \\')],
        "fastboot and adb both claim it",
    ),
    (
        "lsusb seeing a device that fastboot and adb cannot is no longer flagged",
        "This is what unprivileged `fastboot devices` looks like with the phone "
        "attached (udev permissions). Unflagged, it reads as ABSENT -- the phone "
        "is plugged in and the check says it is not there.",
        [('[ "$C" = yes ] && [ "$F" != yes ] && [ "$A" != yes ] && [ "$L" != yes ] && \\',
          '[ "$C" = yes ] && [ "$F" != yes ] && [ "$A" != yes ] && [ "$L" != yes ] && false && \\')],
        "lsusb sees a candidate that fastboot and adb do not",
    ),
    (
        "adb 'unauthorized' treated as absent",
        "Stock recovery on spacewar draws no authorisation prompt at all, so "
        "unauthorized is the state this device actually sits in. Dropping it "
        "hides the phone for the entire recovery route.",
        [("""ADB_LINES=$(printf '%s\\n' "$ADBOUT" | awk 'NF>=2 && $1 != "List" {print $1" "$2}')""",
          """ADB_LINES=$(printf '%s\\n' "$ADBOUT" | awk 'NF>=2 && $2 == "device" {print $1" "$2}')""")],
        "adb sees it as 'unauthorized'",
    ),
    (
        "a missing instrument reads as a measurement",
        "The defect this file cares about most. 'Nothing found' and 'nothing "
        "could look' are the same silence; folding them together reports an "
        "absent phone because fastboot is not installed.",
        [('if [ -n "$MISSING" ]; then', 'if false && [ -n "$MISSING" ]; then')],
        "no 'ip'",
    ),
    (
        "EDL is no longer reported",
        "05c6:9008 is the one unrecoverable failure mode on spacewar. Reported "
        "as ABSENT it invites exactly the retry that bricks the device.",
        [('if [ -n "$USB_EDL" ]; then', 'if false && [ -n "$USB_EDL" ]; then')],
        "05c6:9008 is emergency download mode",
    ),
]


def stage() -> Path:
    work = Path(tempfile.mkdtemp(prefix="mutants-device-presence."))
    for name in (CHECK, HARNESS):
        shutil.copy2(HERE / name, work / name)
        (work / name).chmod(0o755)
    return work


def run_harness(work: Path) -> tuple[int, str]:
    # Bounded: a mutant that makes the check hang would otherwise stall the run
    # forever, and a harness that stopped reporting is not a red.
    try:
        p = subprocess.run([str(work / HARNESS)], capture_output=True, text=True,
                           timeout=600)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "<the harness hung>"


def red_lines(output: str) -> list[str]:
    return [ln.strip()[len("FAIL"):].strip()
            for ln in output.splitlines() if ln.strip().startswith("FAIL ")]


def main() -> int:
    print("baseline: the unmutated harness must be green, or nothing below means anything")
    work = stage()
    rc, out = run_harness(work)
    shutil.rmtree(work, ignore_errors=True)
    if rc != 0:
        print(f"  INCOMPLETE: baseline harness exited {rc}, expected 0")
        print("\n".join("  | " + ln for ln in out.splitlines()[-25:]))
        return 2
    print("  ok: baseline green (exit 0)\n")

    caught = survived = unapplied = 0
    for name, why, edits, expect in MUTANTS:
        print(f"== mutant: {name} ==")
        print(f"   why: {why}")
        work = stage()
        target = work / CHECK
        text = target.read_text()

        applied = True
        for old, new in edits:
            if text.count(old) != 1:
                print(f"   INCOMPLETE: anchor appears {text.count(old)} times, not once."
                      " check-device-presence.sh has drifted from this mutant.")
                applied = False
                break
            text = text.replace(old, new)
        if applied:
            target.write_text(text)
            readback = target.read_text()
            for old, new in edits:
                if new not in readback or old in readback:
                    print("   INCOMPLETE: the mutation did not survive the write-back.")
                    applied = False
                    break
        if not applied:
            unapplied += 1
            shutil.rmtree(work, ignore_errors=True)
            print()
            continue

        rc, out = run_harness(work)
        shutil.rmtree(work, ignore_errors=True)
        reds = red_lines(out)
        hit = [s for s in reds if expect in s]
        if VERBOSE:
            print("\n".join("   | " + ln for ln in out.splitlines()))
        if rc == 1 and hit:
            others = [s for s in reds if expect not in s]
            print(f"   CAUGHT by: {hit[0]}")
            if others:
                print(f"   also red:  {len(others)} other state(s)")
            caught += 1
        elif rc == 2:
            print("   INCOMPLETE: harness exited 2 (it could not set itself up), so"
                  " this mutant was not measured.")
            unapplied += 1
        else:
            print(f"   SURVIVED: harness exit {rc}; red states: {reds or 'none'}")
            print(f"   The state matching '{expect}' did not fail, so it cannot fail"
                  " on this defect and proves nothing about it.")
            survived += 1
        print()

    total = len(MUTANTS)
    print(f"mutants: {total} defined, {caught} caught, {survived} survived,"
          f" {unapplied} not measured")
    if survived:
        print("VERDICT: FAIL -- a state in selftest-device-presence.sh cannot fail on"
              " the defect it names.")
        return 1
    if unapplied or caught != total:
        print("VERDICT: INCOMPLETE -- a mutant was not measured. This is not a pass.")
        return 2
    print("VERDICT: PASS -- every mutant was caught by the state that names it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
