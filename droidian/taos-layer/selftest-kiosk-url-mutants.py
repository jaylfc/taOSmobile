#!/usr/bin/env python3
"""Prove check-kiosk-url.sh can go RED -- tsk-wcku6n, tsk-amhjqp.

Forty-two green checks read as forty-two proofs. They are not: a check proves
something only if it FAILS when the defect it names is present. This script
breaks taos-kiosk-launch.sh and taos-kiosk.service on purpose, one defect at a
time, and requires the check that names each defect to go red.

WHY THIS FILE AND NOT ANOTHER SWEEP
-----------------------------------
check-kiosk-url.sh has already been swept per-assertion (tsk-nvud5k: 7215fd1,
6995556, b5192ea) and every vacuous arm those passes found is fixed, with the
measurement written beside it. What was never done is the other direction:
nothing has ever demonstrated that the file as a whole reddens on the defect
tsk-wcku6n was filed about. A suite that has only ever been observed green is
an instrument with no calibration, however carefully each arm reads.

CATCHING BY CONTROL COUNTS, AND IS NOT THE SAME AS "NOT MEASURED"
-----------------------------------------------------------------
check-kiosk-url.sh refuses rather than reports when a positive control fails:
give_up() exits 2. So a launcher that ignores shell.conf entirely does not
produce a FAIL line -- it produces exit 2 and a named control message, which is
the file correctly declining to certify forty fallback checks that would all
have passed on a constant. That is a catch, and mutants 1 and 2 below exist to
prove it: they break resolution in OPPOSITE directions (always :6969, always
the helper) and control 1 is the only thing in the file that stops either from
reading as a clean green.

But exit 2 also means "python3 is missing" or "the launcher is not executable".
Those are genuinely unmeasured. So a mutant is caught by a control only when the
INCOMPLETE line contains the text that mutant names -- never on the exit code
alone. Reading exit 2 as a catch by itself would be the same defect this file
exists to find, one level up.

A MUTATION THAT DID NOT APPLY IS NOT A GREEN
--------------------------------------------
Each mutation is applied by EXACT text replacement, the anchor is required to
appear exactly once, and the result is read back out of the file. A mutant that
silently failed to apply produces an ordinary green, which is indistinguishable
from a survived mutant unless you check. Two mutants in the sibling
selftest-device-presence-mutants.py were inert on their first writing.

ONE MUTANT DEPENDS ON THIS HOST
-------------------------------
"the gate is blind to one loopback spelling" can only be measured where nothing
is listening on 127.0.0.1:6969, because the check that catches it distinguishes
"the gate was removed" from "the controller is genuinely up" by asking :6969
directly. Where :6969 is up, that mutant is reported NOT MEASURED rather than
counted as caught -- a pass arm that can mean "unmeasured" is the defect, not
the reporting of it.

The FAIL verdict is reachable and was OBSERVED, not merely written: the first
run of this file exited 1 on the insecure-origin mutant, because the expectation
named the text of the arm that PASSES ("...names the ORIGIN, not the URL") and
no FAIL line can ever contain that. The suite was red on the right check the
whole time. Worth keeping in mind when adding a mutant: the string to expect is
the wording of the bad() call, never the ok() one.

  exit 0  every mutant was caught by the check or control that names it
  exit 1  a mutant SURVIVED -- that check cannot fail, so it proves nothing
  exit 2  INCOMPLETE -- the baseline was not green, a mutation did not apply,
          or a mutant could not be measured on this host

Usage:  ./selftest-kiosk-url-mutants.py [-v]
"""
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = "check-kiosk-url.sh"
LAUNCH = "taos-kiosk-launch.sh"
UNIT = "taos-kiosk.service"

VERBOSE = "-v" in sys.argv[1:]

# (name, why, target, [(old, new), ...], expected text, needs_dead_6969)
#
# `target` is the file the defect is planted in: the launcher or the unit. Both
# are staged, because half the card's deliverable is the ExecStart change and a
# suite that only ever reads the script would not notice the unit reverting.
#
# `expected text` must appear in a FAIL line, or in the INCOMPLETE line when a
# positive control is what refuses. It names the check that is supposed to own
# this defect -- not merely "something went red".
MUTANTS = [
    (
        "the original defect, restored: the URL is hardcoded to :6969",
        "This is tsk-wcku6n's premise put back. On a fresh device and in remote "
        "mode nothing listens there, so the kiosk opens a dead page and a "
        "keyboard-less user has no route to the helper on :6970.",
        LAUNCH,
        [('URL="$(resolve_url)"', 'URL="$LOCAL_URL"')],
        "launcher ignores a valid remote config",
        False,
    ),
    (
        "the blind fallback: the helper is returned unconditionally",
        "The opposite failure, and the one this suite is most exposed to: most "
        "expected answers in it ARE the helper, so a launcher that never reads "
        "shell.conf would pass roughly forty of its checks. Control 1 is the "
        "only thing standing in front of that.",
        LAUNCH,
        [('URL="$(resolve_url)"', 'URL="$FIRSTRUN_URL"')],
        "launcher ignores a valid remote config",
        False,
    ),
    (
        "a missing config falls back to the controller",
        "The fresh-device case in the card title. Nothing is listening on :6969 "
        "on a device that has never been configured.",
        LAUNCH,
        [('        log "no readable config at $CONF -- first run"\n'
          '        printf \'%s\\n\' "$FIRSTRUN_URL"; return',
          '        log "no readable config at $CONF -- first run"\n'
          '        printf \'%s\\n\' "$LOCAL_URL"; return')],
        "missing file -> helper",
        False,
    ),
    (
        "a config with no mode set falls back to the controller",
        "The literal title of tsk-wcku6n. The helper writes the file before it "
        "writes a mode, so this state is reachable on a real device.",
        LAUNCH,
        [('        "")\n'
          '            log "config present but no mode set -- first run"\n'
          '            printf \'%s\\n\' "$FIRSTRUN_URL"\n'
          '            ;;',
          '        "")\n'
          '            log "config present but no mode set -- first run"\n'
          '            printf \'%s\\n\' "$LOCAL_URL"\n'
          '            ;;')],
        "empty mode -> helper",
        False,
    ),
    (
        "an unknown mode falls through to the controller",
        "The case an edited or partially-written config produces. A catch-all "
        "that resolves to :6969 is the dead screen again, reached by the arm "
        "nobody tests.",
        LAUNCH,
        [('            log "unknown mode \'$mode\' -- first-run helper"\n'
          '            printf \'%s\\n\' "$FIRSTRUN_URL"',
          '            log "unknown mode \'$mode\' -- first-run helper"\n'
          '            printf \'%s\\n\' "$LOCAL_URL"')],
        "unknown mode -> helper",
        False,
    ),
    (
        "remote mode with a bad url guesses the controller",
        "There is no default remote controller, so guessing :6969 in this "
        "branch is a dead end. The helper is the only screen that can fix it.",
        LAUNCH,
        [('                log "mode=remote but url is missing or malformed -- first-run helper"\n'
          '                printf \'%s\\n\' "$FIRSTRUN_URL"',
          '                log "mode=remote but url is missing or malformed -- first-run helper"\n'
          '                printf \'%s\\n\' "$LOCAL_URL"')],
        "remote without url -> helper",
        False,
    ),
    (
        "url validation reduced to a non-empty test",
        "--unsafely-treat-insecure-origin-as-secure takes a COMMA-SEPARATED "
        "list, so a comma in the url grants a secure context to an origin "
        "nobody chose. A non-empty test is the plausible weakening.",
        LAUNCH,
        [('valid_url() { [[ "$1" =~ $URL_RE ]]; }',
          'valid_url() { [ -n "$1" ]; }')],
        "comma in url refused -> helper",
        False,
    ),
    (
        "the insecure-origin flag hardcoded back to :6969",
        "The drift the launcher header names: the flag stops tracking the "
        "resolved URL, so a remote http controller is NOT granted a secure "
        "context while localhost -- which chromium already trusts -- is.",
        LAUNCH,
        [('        http://*) origin_flag=("--unsafely-treat-insecure-origin-as-secure=$ORIGIN") ;;',
          '        http://*) origin_flag=("--unsafely-treat-insecure-origin-as-secure=http://localhost:6969") ;;')],
        # The FAIL label, not the ok label. Naming the ok text ("...names the
        # ORIGIN, not the URL") made this read as SURVIVED while the suite was
        # in fact red on the right check: no FAIL line can ever contain the
        # wording of the arm that did not run.
        "insecure-origin flag wrong",
        False,
    ),
    (
        "--app stops following the resolved URL",
        "Resolution and launch live in one file so they cannot drift. This "
        "plants the drift anyway: --print-url would still be right and the "
        "screen would still be wrong.",
        LAUNCH,
        [('        "--app=$URL"', '        "--app=http://localhost:6969/"')],
        "--app does not carry the resolved URL",
        False,
    ),
    (
        "the loopback readiness gate is removed",
        "A kiosk showing 'site can't be reached' is a SUCCESSFUL start, so "
        "Restart= and OnFailure= never fire and the display is stuck. Exiting "
        "non-zero is what hands the screen back to Phosh.",
        LAUNCH,
        [('if is_loopback_origin "$ORIGIN"; then',
          'if false && is_loopback_origin "$ORIGIN"; then')],
        "dead helper -> exit 3",
        False,
    ),
    (
        "the gate is blind to one loopback spelling",
        "The defect measured on 2026-08-30: with 'localhost' dropped and 127.* "
        "still gated, the dead-helper check stays green because it resolves to "
        "127.0.0.1, and mode=local becomes completely ungated. The file "
        "reported 42/0 while :6969 was refusing connections.",
        LAUNCH,
        [('        *://localhost|*://localhost:*|*://127.*|*://\\[::1\\]|*://\\[::1\\]:*) return 0 ;;',
          '        *://127.*|*://\\[::1\\]|*://\\[::1\\]:*) return 0 ;;')],
        "local mode is NOT gated",
        True,
    ),
    (
        "remote targets become gated too",
        "A phone in remote mode is a surface whose network comes and goes. "
        "Gating it hands the display back to Phosh on a blip, which is worse "
        "than the unreachable-controller state the UI already shows.",
        LAUNCH,
        [('        *) return 1 ;;\n    esac\n}',
          '        *) return 0 ;;\n    esac\n}')],
        "remote must not be gated",
        False,
    ),
    (
        "the setup sentinel is never consumed",
        "The sentinel is a ONE-START override. Left in place it sends every "
        "subsequent boot to the setup screen -- a one-way door in the other "
        "direction, and requirement 1 forbids both.",
        LAUNCH,
        [('    if rm -f "$SENTINEL" 2>/dev/null && [ ! -e "$SENTINEL" ]; then',
          '    if [ -e "$SENTINEL" ]; then')],
        "the sentinel survived resolution",
        False,
    ),
    (
        "the setup sentinel is ignored entirely",
        "Restores the one-way door requirement 1 forbids: a well-formed but "
        "STALE config resolves cleanly to somewhere unreachable, every boot, "
        "with no way back on a device with no keyboard.",
        LAUNCH,
        [('    if take_sentinel; then', '    if false && take_sentinel; then')],
        "sentinel beats a well-formed remote config",
        False,
    ),
    (
        "the unit reverts to a hardcoded ExecStart",
        "Half of this card's deliverable is the unit change. A suite that only "
        "read the script would not notice the unit going back.",
        UNIT,
        [('ExecStart=/usr/local/lib/taos/taos-kiosk-launch.sh',
          'ExecStart=/usr/bin/cage -- /usr/bin/chromium --kiosk --app=http://localhost:6969/')],
        "unit ExecStart does not call the launcher",
        False,
    ),
    (
        "an --app URL smuggled into ExecStartPre",
        "The stated-wider-than-real defect measured on 2026-08-30: the check "
        "matched one literal while claiming the unit hardcodes no app URL at "
        "all. It asserts every spelling now; this proves that widening works.",
        UNIT,
        [('ExecStartPre=/bin/rm -rf /run/taos-kiosk/profile',
          'ExecStartPre=/bin/rm -rf /run/taos-kiosk/profile\n'
          'ExecStartPre=/bin/echo --app=http://127.0.0.1:6969')],
        "unit hardcodes an app URL",
        False,
    ),
    (
        "RestartPreventExitStatus is dropped",
        "Restart=on-failure parks a unit in auto-restart, not failed, and "
        "OnFailure= fires only on failed. Without this the kiosk flaps forever "
        "and never hands the display back.",
        UNIT,
        [('RestartPreventExitStatus=3 64', '# RestartPreventExitStatus removed')],
        "RestartPreventExitStatus missing",
        False,
    ),
    (
        "the unit stops wanting the first-run helper",
        "On the very first boot the launcher resolves to the helper, so the "
        "helper has to exist for that boot to show anything at all.",
        UNIT,
        [('Wants=taos-firstrun.service', '# Wants removed')],
        "unit does not want the first-run helper",
        False,
    ),
]


def local_6969_up() -> bool:
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", 6969))
        return True
    except OSError:
        return False
    finally:
        s.close()


def stage() -> Path:
    work = Path(tempfile.mkdtemp(prefix="mutants-kiosk-url."))
    for name in (SUITE, LAUNCH, UNIT):
        shutil.copy2(HERE / name, work / name)
    (work / SUITE).chmod(0o755)
    (work / LAUNCH).chmod(0o755)
    return work


def run_suite(work: Path) -> tuple[int, str]:
    # Bounded. A mutant that makes the launcher hang would otherwise stall this
    # run forever, and a suite that stopped reporting is not a red.
    try:
        p = subprocess.run([str(work / SUITE)], capture_output=True, text=True,
                           timeout=600)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "<the suite hung>"


def red_lines(output: str) -> list[str]:
    return [ln.strip()[len("FAIL"):].strip()
            for ln in output.splitlines() if ln.strip().startswith("FAIL ")]


def incomplete_line(output: str) -> str:
    for ln in output.splitlines():
        if ln.strip().startswith("INCOMPLETE:"):
            return ln.strip()
    return ""


def main() -> int:
    print("baseline: the unmutated suite must be green, or nothing below means anything")
    work = stage()
    rc, out = run_suite(work)
    shutil.rmtree(work, ignore_errors=True)
    if rc != 0:
        print(f"  INCOMPLETE: baseline suite exited {rc}, expected 0")
        print("\n".join("  | " + ln for ln in out.splitlines()[-25:]))
        return 2
    print("  ok: baseline green (exit 0)\n")

    # NEGATIVE CONTROL. Eighteen catches below are only attributable to the
    # eighteen defects if the suite does NOT redden on an edit that changes no
    # behaviour at all. Without this, "every mutant was caught" is also what you
    # would see from a suite that fails whenever the launcher is rewritten, its
    # mtime moves, or it is run from a staging directory -- and all three happen
    # to every mutant here. An inert edit must stay green.
    print("negative control: an edit that changes no behaviour must NOT redden the suite")
    work = stage()
    inert_path = work / LAUNCH
    inert_old = "# See docs/first-run-controller-choice.md and the layer README."
    inert_text = inert_path.read_text()
    if inert_text.count(inert_old) != 1:
        print("  INCOMPLETE: the inert anchor is not unique; the control cannot run.")
        shutil.rmtree(work, ignore_errors=True)
        return 2
    inert_path.write_text(inert_text.replace(
        inert_old, inert_old + "\n# (inert edit: negative control for this harness)"))
    rc, out = run_suite(work)
    shutil.rmtree(work, ignore_errors=True)
    if rc != 0 or red_lines(out):
        print(f"  INCOMPLETE: the suite exited {rc} on a COMMENT change"
              f" (red: {red_lines(out) or 'none'}).")
        print("  It is reacting to the file being edited, not to the defects, so"
              " every catch below would be uninterpretable.")
        return 2
    print("  ok: inert edit stays green, so a red below is attributable to the defect\n")

    six969 = local_6969_up()
    print(f"host: 127.0.0.1:6969 is {'UP' if six969 else 'down'}"
          f"{' -- one mutant cannot be measured here' if six969 else ''}\n")

    caught = survived = unmeasured = 0
    for name, why, target, edits, expect, needs_dead in MUTANTS:
        print(f"== mutant: {name} ==")
        print(f"   why: {why}")

        if needs_dead and six969:
            print("   NOT MEASURED: this defect is caught by asking :6969 directly,"
                  " and something is listening there. A green here would mean"
                  " 'unmeasured', not 'caught'.")
            unmeasured += 1
            print()
            continue

        work = stage()
        path = work / target
        text = path.read_text()

        applied = True
        for old, new in edits:
            if text.count(old) != 1:
                print(f"   INCOMPLETE: anchor appears {text.count(old)} times in"
                      f" {target}, not once. It has drifted from this mutant.")
                applied = False
                break
            text = text.replace(old, new)
        if applied:
            path.write_text(text)
            readback = path.read_text()
            for old, new in edits:
                # "the old text is gone" is only a valid post-condition when the
                # replacement does not itself CONTAIN the old text. An additive
                # mutant (keep the line, add one after it) legitimately leaves
                # the anchor in place, and demanding its absence reported a
                # correctly-applied mutation as INCOMPLETE -- a false unmeasured,
                # which hides a real result just as effectively as a false green.
                gone_required = old not in new
                if new not in readback or (gone_required and old in readback):
                    print("   INCOMPLETE: the mutation did not survive the write-back.")
                    applied = False
                    break
        if applied and target == LAUNCH:
            # A mutant that does not parse makes the suite give_up at its own
            # syntax check, which would read as a catch on any expectation.
            syn = subprocess.run(["bash", "-n", str(path)],
                                 capture_output=True, text=True)
            if syn.returncode != 0:
                print(f"   INCOMPLETE: the mutated launcher does not parse"
                      f" ({syn.stderr.strip()}), so the suite would refuse at its"
                      " syntax gate rather than on this defect.")
                applied = False
        if not applied:
            unmeasured += 1
            shutil.rmtree(work, ignore_errors=True)
            print()
            continue

        rc, out = run_suite(work)
        shutil.rmtree(work, ignore_errors=True)
        reds = red_lines(out)
        inc = incomplete_line(out)
        if VERBOSE:
            print("\n".join("   | " + ln for ln in out.splitlines()))

        hit = [s for s in reds if expect in s]
        # A matching FAIL line is a catch whatever the final exit code, and the
        # code alone is not enough to tell. Measured while writing this file:
        # the missing-config mutant reddened "missing file -> helper" in section
        # 2 and THEN tripped control 2 in section 6, so the suite exited 2 --
        # and reading the exit code first filed a genuine catch as NOT MEASURED.
        # The suite reports what it measured before it refuses; read that first.
        if rc in (1, 2) and hit:
            others = [s for s in reds if expect not in s]
            print(f"   CAUGHT by: {hit[0]}")
            if others:
                print(f"   also red:  {len(others)} other check(s)")
            caught += 1
        elif rc == 2 and expect in inc:
            # A control refusing to certify. Matched on the TEXT, never on the
            # exit code: exit 2 also means "python3 missing".
            print(f"   CAUGHT by control: {inc}")
            caught += 1
        elif rc == 2:
            print(f"   NOT MEASURED: suite exited 2 for an unrelated reason:"
                  f" {inc or '<no INCOMPLETE line>'}")
            unmeasured += 1
        elif rc == 124:
            print("   NOT MEASURED: the suite HUNG. A hang is not a red -- it is"
                  " an instrument that stopped reporting.")
            unmeasured += 1
        elif rc == 1:
            print(f"   SURVIVED (wrong catcher): the suite went red, but no check"
                  f" matching '{expect}' failed. Red: {reds}")
            print("   The check that names this defect did not fail on it, so it"
                  " proves nothing about it.")
            survived += 1
        else:
            print(f"   SURVIVED: suite exit {rc}; red checks: {reds or 'none'}")
            print(f"   The check matching '{expect}' did not fail, so it cannot"
                  " fail on this defect.")
            survived += 1
        print()

    total = len(MUTANTS)
    print(f"mutants: {total} defined, {caught} caught, {survived} survived,"
          f" {unmeasured} not measured")
    if survived:
        print("VERDICT: FAIL -- a check in check-kiosk-url.sh cannot fail on the"
              " defect it names.")
        return 1
    if unmeasured or caught != total:
        print("VERDICT: INCOMPLETE -- a mutant was not measured. This is not a pass.")
        return 2
    print("VERDICT: PASS -- every mutant was caught by the check or control that"
          " names it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
