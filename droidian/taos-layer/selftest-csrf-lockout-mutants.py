#!/usr/bin/env python3
"""Prove selftest-csrf-lockout.sh can go RED -- tsk-cupx7r.

A harness that only ever goes green is a sentence, not a proof, which is the
exact failure tsk-cupx7r was filed about one level down. So this script breaks
check-csrf-lockout.sh on purpose, one defect at a time, and requires the
harness to catch each one in the state that names it.

Every mutant below is a defect this repo has actually shipped or narrowly
avoided -- b0cc558's absent controller, d4a1935's 5xx-from-a-proxy, a `*)`
catch-all reporting ok, a skip folded into a pass. None are invented.

Each mutation is applied by EXACT text replacement and then read back out of
the file: a mutant that did not apply prints an ordinary green and would be
read as evidence. That has happened here twice (a shell-mangled newline; a
function name eaten by `except: pass`), so the check is not optional.

  exit 0  every mutant was caught by the state that names it
  exit 1  a mutant SURVIVED -- that state cannot fail, so it proves nothing
  exit 2  INCOMPLETE -- the baseline was not green, or a mutation did not
          apply, so nothing was measured

Usage:  ./selftest-csrf-lockout-mutants.py [-v]
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = "check-csrf-lockout.sh"
HARNESS = "selftest-csrf-lockout.sh"
STUB = "csrf-stub-controller.py"

VERBOSE = "-v" in sys.argv[1:]

# (name, why, old-text, new-text, substring of the state that must go red)
MUTANTS = [
    (
        "self-proof disabled",
        "b0cc558 added section 0 because the suite was reporting PASS against a "
        "controller that was not running. Everything else is gated on it.",
        'if [ "$sentinel" != "$UNREACHABLE" ]; then',
        'if false && [ "$sentinel" != "$UNREACHABLE" ]; then',
        "self-proof fires",
    ),
    (
        "5xx reads as 'the route exists'",
        "d4a1935. A reverse proxy up with the controller behind it down produced "
        "four green checks, two of them asserting the #2081 mitigation holds.",
        '        5*)  bad "$path answered $code. A 5xx is what a proxy returns when the controller"\n'
        '             cont "behind it is DOWN, so it does not show the route exists. Refusing to"\n'
        '             cont "measure #2081 against it."\n'
        '             routes_ok=0 ;;\n',
        '        5*)  ok  "$path exists (POST with no cookie -> $code)" ;;\n',
        "proxy up, controller down",
    ),
    (
        "unknown code reads as 'the route exists'",
        "The pre-d4a1935 catch-all. Same family as the 5xx arm: an unknown code "
        "is not evidence, and a `*)` that reports ok cannot fail on anything.",
        '        *)   bad "$path answered $code, which does not establish that the route exists"\n'
        '             cont "(405, say, means the path is not routed for POST). Refusing to measure"\n'
        '             cont "#2081 against it."\n'
        '             routes_ok=0 ;;\n',
        '        *)   ok  "$path exists (POST with no cookie -> $code)" ;;\n',
        "path not routed for POST",
    ),
    (
        "404 no longer stops the later sections",
        "routes_ok is the whole vacuous-pass gate. Drop it from the 404 arm and "
        "sections 2 and 3 measure #2081 against a route that is not there.",
        '        404) bad "$path returns 404. This check cannot measure #2081 against a route"\n'
        '             cont "that does not exist; do not read the rest as a clean bill of health."\n'
        '             routes_ok=0 ;;\n',
        '        404) bad "$path returns 404. This check cannot measure #2081 against a route"\n'
        '             cont "that does not exist; do not read the rest as a clean bill of health." ;;\n',
        "routes absent",
    ),
    (
        "the 403 that IS taOS#2081 stops being detected",
        "Section 2 is the reason the file exists. If its arm cannot match, a "
        "device with the live bug reports a clean CSRF section.",
        '        if [ "$code" = "403" ]; then',
        '        if [ "$code" = "4030" ]; then',
        "pre-#2543",
    ),
    (
        "a non-ephemeral kiosk profile is accepted",
        "The ephemeral /run profile IS the boot half of the mitigation: it is "
        "what stops a stale cookie surviving a reboot.",
        '    elif [[ "$profile" != /run/* ]]; then',
        '    elif [[ "$profile" != /* ]]; then',
        "a profile outside /run",
    ),
    (
        "a kiosk unit with no profile at all is accepted",
        "No --user-data-dir means Chromium's default profile under $HOME, which "
        "outlives every reboot.",
        '    if [ -z "$profile" ]; then',
        '    if false; then',
        "no --user-data-dir means",
    ),
    (
        "an uninstalled guard reports ok",
        "The running half. Without it a session that lapses while the kiosk is "
        "up has no recovery short of an SSH login, on a device with no keyboard.",
        '    bad "$GUARD_UNIT is not installed; a session that lapses while the kiosk is"\n'
        '    cont "running has no recovery short of an SSH login"\n',
        '    ok "$GUARD_UNIT is not installed"\n',
        "the guard is not installed",
    ),
    (
        "skipped checks fold into a PASS",
        "The header's central promise: a check that did not RUN must not be "
        "counted as one that passed.",
        'elif [ "$skip" -gt 0 ]; then',
        'elif [ "$skip" -gt 9999 ]; then',
        "credentials withheld",
    ),
]


def run_harness(workdir: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(workdir / HARNESS)],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def stage() -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="csrf-mutants-"))
    for name in (SUITE, HARNESS, STUB):
        shutil.copy2(HERE / name, workdir / name)
        (workdir / name).chmod(0o755)
    return workdir


def red_states(output: str) -> list[str]:
    """State headings whose block contains a FAIL line."""
    out, current = [], None
    for line in output.splitlines():
        if line.startswith("== state: "):
            current = line[len("== state: "):].rstrip(" =")
        elif current and line.startswith("    FAIL "):
            if current not in out:
                out.append(current)
    return out


def main() -> int:
    print("baseline: the unmutated suite must be green, or nothing below means anything")
    work = stage()
    rc, out = run_harness(work)
    shutil.rmtree(work, ignore_errors=True)
    if rc != 0:
        print(f"  INCOMPLETE: baseline harness exited {rc}, expected 0")
        print("\n".join("  | " + ln for ln in out.splitlines()[-25:]))
        return 2
    print("  ok: baseline green (exit 0)\n")

    caught = survived = unapplied = 0
    for name, why, old, new, expect in MUTANTS:
        print(f"== mutant: {name} ==")
        print(f"   why: {why}")
        work = stage()
        target = work / SUITE
        text = target.read_text()

        # A mutant that did not apply prints an ordinary green.
        if text.count(old) != 1:
            print(f"   INCOMPLETE: the anchor appears {text.count(old)} times, not once."
                  " The suite has drifted from this mutant.")
            unapplied += 1
            shutil.rmtree(work, ignore_errors=True)
            continue
        target.write_text(text.replace(old, new))
        readback = target.read_text()
        if new not in readback or old in readback:
            print("   INCOMPLETE: the mutation did not survive the write-back.")
            unapplied += 1
            shutil.rmtree(work, ignore_errors=True)
            continue

        rc, out = run_harness(work)
        shutil.rmtree(work, ignore_errors=True)
        reds = red_states(out)
        hit = [s for s in reds if expect in s]
        if VERBOSE:
            print("\n".join("   | " + ln for ln in out.splitlines()))
        if rc == 1 and hit:
            others = [s for s in reds if expect not in s]
            print(f"   CAUGHT by: {hit[0]}  (harness exit {rc})")
            if others:
                print(f"   also red:  {'; '.join(others)}")
            caught += 1
        elif rc == 2:
            print(f"   INCOMPLETE: harness exited 2 (a fixture was not in its claimed"
                  f" state), so this mutant was not measured.")
            unapplied += 1
        else:
            print(f"   SURVIVED: harness exit {rc}; states red: {reds or 'none'}")
            print(f"   The state matching '{expect}' did not fail, so it cannot fail"
                  " on this defect and proves nothing about it.")
            survived += 1
        print()

    total = len(MUTANTS)
    print(f"mutants: {total} defined, {caught} caught, {survived} survived,"
          f" {unapplied} not measured")
    if survived:
        print("VERDICT: FAIL -- a state in selftest-csrf-lockout.sh cannot fail on the"
              " defect it names.")
        return 1
    if unapplied or caught != total:
        print("VERDICT: INCOMPLETE -- a mutant was not measured. This is not a pass.")
        return 2
    print("VERDICT: PASS -- every mutant was caught by the state that names it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
