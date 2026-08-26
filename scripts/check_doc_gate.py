#!/usr/bin/env python3
"""Documentation-drift gate.

Blocks commits/PRs that change feature code without a matching doc update,
unless the change carries an explicit "Docs-Reviewed: <why>" trailer.

Three layers:
  liveness    -- Layer A0: refuse to pass vacuously. Config that names a tree,
                 doc or rule target this repo does not have would make the
                 gate green by measuring nothing; that is reported as an
                 error rather than as coverage.
  invariants  -- deterministic sanity checks (Layer A). Currently: every
                 path mentioned in the configured doc set, under one of the
                 repo trees named by `[invariants] path_prefixes`, actually
                 exists on disk. `ignore_paths` globs are exempt (build
                 artifacts that are gitignored and absent from a fresh clone).
  diff-gate   -- path -> doc rule engine (Layer B). A configured rule fires
                 when a *structural* change (a file added or deleted, never a
                 plain modification) matches one of its `when_changed` globs.
                 A fired rule is satisfied by either editing one of its
                 `require_doc` files in the same changeset, or by a commit
                 message trailer line starting with the configured trailer
                 (default "Docs-Reviewed:") with non-empty text after it.

Config lives in docs/doc-gate.toml. Rules are data, not code: add more by
editing the TOML, no changes to this file required.

Usage:
    python scripts/check_doc_gate.py invariants
    python scripts/check_doc_gate.py diff-gate --staged
    python scripts/check_doc_gate.py diff-gate --base origin/dev
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "docs" / "doc-gate.toml"
DEFAULT_TRAILER = "Docs-Reviewed:"

# The repo trees whose paths Layer A asserts must exist. Overridable per repo
# via `[invariants] path_prefixes` in the TOML -- taOSmobile has no
# `tinyagentos/` or `desktop/`, and hardcoding another repo's trees here would
# make the whole layer silently dead config.
DEFAULT_PATH_PREFIXES = ["scripts", "tinyagentos", "docs", "desktop"]


def build_token_re(prefixes: list[str]) -> re.Pattern:
    """A path-like token: one of the repo's known prefixes followed by a run of
    non-whitespace / non-quoting characters. The negative lookbehind stops us
    matching a prefix that is actually embedded inside a larger path (e.g. the
    "docs/" inside "/home/<user>/notes/docs/" in a host-layout table), which
    would otherwise falsely flag paths that never exist in the repo itself.

    A preceding "." is excluded for the same reason: an Android component name
    like `com.taos.kiosk/.AdminReceiver` is not a path, and the dotted package
    prefix is the only thing that distinguishes it from one.

    A preceding "-" likewise: a prefix that starts mid-identifier is part of a
    longer name, not a repo path. `/run/taos-kiosk/profile` is the case that hit
    -- the one-character lookbehind saw the hyphen, not the leading slash, and
    reported `kiosk/profile` as a missing repo path. Widening this is a
    precision fix and does not weaken Layer A: a real repo path is never written
    with a hyphen jammed against its first segment."""
    alternation = "|".join(re.escape(p) for p in prefixes)
    return re.compile(r"(?<![\w/.-])(?:" + alternation + r")/[^\s`\"'|]+")


_TOKEN_RE = build_token_re(DEFAULT_PATH_PREFIXES)

# Chars that mark a token as a glob pattern or a <placeholder> rather than a
# concrete repo path -- these are never asserted to exist.
_GLOB_OR_PLACEHOLDER_CHARS = set("*?[]{}<>$~")

# Trailing punctuation that is prose/markdown decoration, not part of the path.
_TRAILING_PUNCT = ".,;:!?)]}'\"`"


def _clean_token(raw: str) -> str | None:
    """Normalize a raw regex match into a bare repo-relative path, or None
    if it should be ignored (glob, placeholder, anchor/query fragment)."""
    token = raw
    for sep in ("#", "?"):
        if sep in token:
            token = token.split(sep, 1)[0]
    while token and token[-1] in _TRAILING_PUNCT:
        token = token[:-1]
    if not token:
        return None
    if any(c in _GLOB_OR_PLACEHOLDER_CHARS for c in token):
        return None
    return token


def extract_path_tokens(text: str, token_re: re.Pattern | None = None) -> list[str]:
    """Pull candidate repo-relative path tokens out of doc prose."""
    token_re = token_re or _TOKEN_RE
    tokens = []
    for m in token_re.finditer(text):
        cleaned = _clean_token(m.group(0))
        if cleaned:
            tokens.append(cleaned)
    return tokens


def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def check_config_is_live(repo_root: Path, files_to_scan: list[str], config: dict) -> list[str]:
    """Layer A0: the gate must not be able to pass by measuring nothing.

    Every check here targets a way this gate can report "clean" while actually
    inspecting an empty set:

    - a `path_prefixes` entry naming a tree the repo does not have. The token
      regex then matches nothing, every scanned doc yields zero tokens, and
      Layer A is green forever. This is not hypothetical: the taOS original
      hardcoded `tinyagentos|desktop`, and porting it here unchanged would have
      produced exactly that.
    - a `referenced_paths_scan` entry that does not exist. Missing scan targets
      are skipped by design (a gitignored local-only doc is legitimate), but a
      typo is indistinguishable from that and silently shrinks coverage, so
      anything skipped must be declared in `optional_scan_paths`.
    - a rule whose `when_changed` globs are all rooted in a nonexistent tree, or
      whose `require_doc` names a file that does not exist. Either way the rule
      can never fire or can never be satisfied.

    The failure mode this guards against is the one that cost taOS five weeks on
    #2081: an autouse fixture no-oped `verify_csrf` for the whole auth suite, so
    the tests were green because they were measuring the fixture rather than the
    system. A gate that cannot fail is worse than no gate, because it is
    reported as coverage.
    """
    invariants = config.get("invariants", {})
    optional = invariants.get("optional_scan_paths", [])
    failures: list[str] = []

    for prefix in invariants.get("path_prefixes", DEFAULT_PATH_PREFIXES):
        if not (repo_root / prefix).is_dir():
            failures.append(
                f"config: path_prefixes names '{prefix}/', which is not a directory in this "
                f"repo -- Layer A would scan for a tree that does not exist and pass vacuously"
            )

    for rel in files_to_scan:
        if not (repo_root / rel).is_file() and not _match_any(rel, optional):
            failures.append(
                f"config: referenced_paths_scan names '{rel}', which does not exist -- it "
                f"would be skipped silently; fix the path or declare it in optional_scan_paths"
            )

    for rule in config.get("rules", []):
        name = rule.get("name", "?")
        for pattern in rule.get("when_changed", []):
            root = pattern.split("/", 1)[0]
            if not any(ch in root for ch in "*?[") and not (repo_root / root).exists():
                failures.append(
                    f"config: rule '{name}' watches '{pattern}', but '{root}' does not exist "
                    f"in this repo -- the rule can never fire"
                )
        for doc in rule.get("require_doc", []):
            if any(ch in doc for ch in "*?[") :
                continue
            if not (repo_root / doc).exists():
                failures.append(
                    f"config: rule '{name}' requires '{doc}', which does not exist -- the rule "
                    f"could fire but never be satisfied by a doc edit"
                )
    return failures


def check_referenced_paths(repo_root: Path, files_to_scan: list[str], config: dict) -> list[str]:
    """Layer A: every path token mentioned in the configured doc set, under one
    of the repo trees named by `path_prefixes`, must exist on disk. A scan-target
    file that itself does not exist is skipped here, but Layer A0 above refuses
    to let that happen silently."""
    prefixes = config.get("invariants", {}).get("path_prefixes", DEFAULT_PATH_PREFIXES)
    token_re = build_token_re(prefixes)
    ignore = config.get("invariants", {}).get("ignore_paths", [])
    failures: list[str] = []
    for rel in files_to_scan:
        doc_path = repo_root / rel
        if not doc_path.is_file():
            continue
        text = doc_path.read_text(encoding="utf-8", errors="ignore")
        for token in extract_path_tokens(text, token_re):
            if _match_any(token, ignore):
                continue
            if not (repo_root / token).exists():
                failures.append(f"{rel} references '{token}' which does not exist in the repo")
    return failures


def _glob_match(path: str, pattern: str) -> bool:
    """Path-segment-aware glob match, unlike fnmatch (where `*` crosses `/`).

    `**` matches zero or more path segments (so a trailing `/**` also matches
    the bare parent, e.g. `a/**` matches `a`), a single `*` matches within one
    path segment only (`[^/]*`), `?` matches one non-separator character
    (`[^/]`), and every other character is matched literally.
    """
    regex_parts = []
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == "*":
            if i + 1 < length and pattern[i + 1] == "*":
                # A trailing `/**` should also match the bare parent path, so
                # fold the preceding literal `/` into an optional group.
                if regex_parts and regex_parts[-1] == "/" and i + 2 == length:
                    regex_parts[-1] = "(?:/.*)?"
                else:
                    regex_parts.append(".*")
                i += 2
            else:
                regex_parts.append("[^/]*")
                i += 1
        elif char == "?":
            regex_parts.append("[^/]")
            i += 1
        else:
            regex_parts.append(re.escape(char))
            i += 1
    return re.fullmatch("".join(regex_parts), path) is not None


def _match_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_match(path, pat) for pat in patterns)


def _is_test_path(path: str) -> bool:
    """A test file is never a structural feature change (a new app, route,
    etc.), so adding or removing one must not trip a doc-gate structural rule.
    Covers frontend co-located tests and Python test modules (#171)."""
    base = path.rsplit("/", 1)[-1]
    if "/__tests__/" in path:
        return True
    if base.startswith("test_") and base.endswith(".py"):
        return True
    return base.endswith(
        (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts")
    )


def evaluate_rules(
    changed_status: list[tuple[str, str]],
    commit_messages: list[str],
    config: dict,
) -> list[str]:
    """Layer B: run every configured rule against a changeset.

    changed_status: list of (status, path) pairs as from `git diff
    --name-status`, e.g. [("A", "desktop/src/apps/Foo/Foo.tsx"), ("M", "x")].
    Only status "A" (added) or "D" (deleted) files count as structural change
    for the purposes of *triggering* a rule; plain modifications ("M") are
    ignored to keep the gate precise. Any changed file (any status) can
    *satisfy* a rule if it matches a require_doc glob.
    commit_messages: full text of each commit message in range (empty list
    when there is no finalized commit yet, i.e. --staged mode).
    """
    trailer = get_trailer(config)
    rules = config.get("rules", [])

    all_paths = [path for _status, path in changed_status]
    structural_paths = [
        path for status, path in changed_status
        if status in ("A", "D") and not _is_test_path(path)
    ]

    trailer_present = any(
        line.strip().startswith(trailer) and line.strip()[len(trailer):].strip()
        for message in commit_messages
        for line in message.splitlines()
    )

    failures: list[str] = []
    for rule in rules:
        name = rule.get("name", "?")
        when_changed = rule.get("when_changed", [])
        require_doc = rule.get("require_doc", [])
        hint = rule.get("hint", "")

        triggered = any(_match_any(p, when_changed) for p in structural_paths)
        if not triggered:
            continue

        doc_edited = any(_match_any(p, require_doc) for p in all_paths)
        if doc_edited or trailer_present:
            continue

        failures.append(
            f"{name} -- {hint} (edit one of: {', '.join(require_doc)}, "
            f"or add a 'Docs-Reviewed: <why>' trailer)"
        )
    return failures


def _run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout


def _parse_name_status(output: str) -> list[tuple[str, str]]:
    changed: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        # Renames/copies (R100, C100, ...) carry old + new path; the new path
        # is what matters for both triggering and satisfying a rule.
        path = parts[-1]
        changed.append((status[0], path))
    return changed


def _git_changed_staged() -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--cached", "--name-status"]))


def _git_changed_base(base_ref: str) -> list[tuple[str, str]]:
    return _parse_name_status(_run_git(["diff", "--name-status", f"{base_ref}...HEAD"]))


def _git_commit_messages(base_ref: str) -> list[str]:
    out = _run_git(["log", f"{base_ref}..HEAD", "--format=%B%x00"])
    return [m for m in out.split("\x00") if m.strip()]


def get_trailer(config: dict) -> str:
    """Single source of truth for the commit-message trailer prefix, shared
    by the diff-gate check and the hooks (via the print-trailer command)."""
    return config.get("gate", {}).get("trailer", DEFAULT_TRAILER)


def _report(failures: list[str]) -> int:
    if not failures:
        print("doc-gate: clean")
        return 0
    for failure in failures:
        print(f"DOC-GATE FAIL: {failure}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("invariants", help="Run Layer-A deterministic checks")
    sub.add_parser("print-trailer", help="Print the configured commit trailer prefix")

    diff_parser = sub.add_parser("diff-gate", help="Run Layer-B path->doc rule engine")
    group = diff_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="Check the git index (pre-commit)")
    group.add_argument("--base", help="Compare <base>...HEAD (CI / commit-msg)")

    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "invariants":
        files_to_scan = config.get("invariants", {}).get("referenced_paths_scan", [])
        failures = check_config_is_live(REPO_ROOT, files_to_scan, config)
        failures += check_referenced_paths(REPO_ROOT, files_to_scan, config)
        return _report(failures)

    if args.command == "print-trailer":
        print(get_trailer(config))
        return 0

    # diff-gate
    if args.staged:
        changed = _git_changed_staged()
        commit_messages: list[str] = []
    else:
        changed = _git_changed_base(args.base)
        commit_messages = _git_commit_messages(args.base)

    failures = evaluate_rules(changed, commit_messages, config)
    return _report(failures)


if __name__ == "__main__":
    sys.exit(main())
