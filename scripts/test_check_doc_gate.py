#!/usr/bin/env python3
"""Tests for check_doc_gate.py invariants check."""

import tempfile
import subprocess
from pathlib import Path


def run_invariants_check(repo_root: Path, config_content: str) -> int:
    """Helper to run the invariants check in a temporary repo."""
    # Write config
    config = repo_root / "docs" / "doc-gate.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(config_content)

    # Write the check_doc_gate.py script
    script_dir = repo_root / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "check_doc_gate.py"
    
    # Copy the script from the project root
    with open(Path.cwd() / "scripts" / "check_doc_gate.py", "rb") as src:
        with open(script_path, "wb") as dst:
            dst.write(src.read())

    # Run the check
    result = subprocess.run(
        ["python3", "scripts/check_doc_gate.py", "invariants"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


def test_ignored_pytest_cache_md_does_not_fail():
    """Test (a) that git-ignored .pytest_cache/README.md doesn't cause failure.

    This reproduces the bug: pytest creates .pytest_cache/README.md (which git
    ignores because pytest writes its own .gitignore inside that directory), and
    the current implementation walked through git-ignored files.

    After the fix, this should NOT fail.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, capture_output=True)

        # Create .pytest_cache/README.md (the file that triggers the bug)
        pytest_cache_dir = repo_root / ".pytest_cache"
        pytest_cache_dir.mkdir()
        (pytest_cache_dir / "README.md").write_text("pytest cache metadata")

        # Create .pytest_cache/.gitignore that ignores everything
        (pytest_cache_dir / ".gitignore").write_text("*")

        # Create minimal doc-gate.toml (copied from real one)
        config_content = """
[gate]
trailer = "Docs-Reviewed:"

[invariants]
path_prefixes = ["scripts", "docs"]
referenced_paths_scan = [
  "README.md",
]

unscanned_paths = []

ignore_paths = []

[[rules]]
name = "doc-index"
when_changed = ["docs/*.md"]
require_doc = ["README.md"]
hint = "a doc was added or removed; README indexes the doc set"
"""

        # Create README.md (scanned path)
        (repo_root / "README.md").write_text("# Test")

        # Run the invariants check
        returncode, output = run_invariants_check(repo_root, config_content)

        # After fix, this should pass (returncode 0) and NOT show DOC-GATE FAIL for .pytest_cache/README.md
        # Before fix, it would fail with DOC-GATE FAIL for .pytest_cache/README.md
        assert returncode == 0, f"Expected returncode 0, got {returncode}"
        assert ".pytest_cache/README.md" not in output, f"Git-ignored file should not appear in output: {output}"


def test_untracked_docs_new_md_fails():
    """Test (b) that untracked, NOT ignored docs/new.md not in config fails.

    Control test: a brand-new doc not yet staged must still be caught by the gate.
    This test should pass both before and after the fix.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, capture_output=True)

        # Create README.md (scanned path)
        (repo_root / "README.md").write_text("# Test")

        # Create docs/new.md (untracked, NOT in config, not gitignored)
        new_doc_dir = repo_root / "docs"
        new_doc_dir.mkdir()
        (new_doc_dir / "new.md").write_text("# New doc")

        # Create minimal doc-gate.toml
        config_content = """
[gate]
trailer = "Docs-Reviewed:"

[invariants]
path_prefixes = ["scripts", "docs"]
referenced_paths_scan = [
  "README.md",
]

unscanned_paths = []

ignore_paths = []

[[rules]]
name = "doc-index"
when_changed = ["docs/*.md"]
require_doc = ["README.md"]
hint = "a doc was added or removed; README indexes the doc set"
"""

        # Run the invariants check
        returncode, output = run_invariants_check(repo_root, config_content)

        # This should fail because docs/new.md is not in referenced_paths_scan or unscanned_paths
        assert returncode != 0, "Expected DOC-GATE FAIL for docs/new.md"
        assert "docs/new.md" in output, f"Expected docs/new.md in output: {output}"
        assert "neither referenced_paths_scan nor unscanned_paths" in output, f"Expected appropriate failure message: {output}"


def test_tracked_md_not_in_config_fails():
    """Test (c) that tracked (git add + commit) .md that is not listed still fails.

    Control test: a tracked .md file not in the config must still be caught.
    This test should pass both before and after the fix.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, capture_output=True)

        # Create README.md (scanned path)
        (repo_root / "README.md").write_text("# Test")

        # Create docs/tracked.md (tracked in git, not in config)
        tracked_doc_dir = repo_root / "docs"
        tracked_doc_dir.mkdir()
        (tracked_doc_dir / "tracked.md").write_text("# Tracked doc")

        # Add and commit
        subprocess.run(["git", "add", "README.md", "docs/tracked.md"], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_root, capture_output=True)

        # Create minimal doc-gate.toml (without docs/tracked.md)
        config_content = """
[gate]
trailer = "Docs-Reviewed:"

[invariants]
path_prefixes = ["scripts", "docs"]
referenced_paths_scan = [
  "README.md",
]

unscanned_paths = []

ignore_paths = []

[[rules]]
name = "doc-index"
when_changed = ["docs/*.md"]
require_doc = ["README.md"]
hint = "a doc was added or removed; README indexes the doc set"
"""

        # Run the invariants check
        returncode, output = run_invariants_check(repo_root, config_content)

        # This should fail because docs/tracked.md is a tracked .md not in referenced_paths_scan or unscanned_paths
        assert returncode != 0, "Expected DOC-GATE FAIL for docs/tracked.md"
        assert "docs/tracked.md" in output, f"Expected docs/tracked.md in output: {output}"
        assert "neither referenced_paths_scan nor unscanned_paths" in output, f"Expected appropriate failure message: {output}"


if __name__ == "__main__":
    import sys
    print("Running tests for check_doc_gate.py fix...")
    print("=" * 60)

    # Run test (a) - the buggy one
    print("\nTest (a): Ignored .pytest_cache/README.md should NOT fail")
    print("-" * 60)
    try:
        test_ignored_pytest_cache_md_does_not_fail()
        print("Test (a) PASSED")
    except AssertionError as e:
        print(f"Test (a) FAILED: {e}")
        sys.exit(1)

    # Run test (b) - control test 1
    print("\nTest (b): Untracked docs/new.md should fail")
    print("-" * 60)
    try:
        test_untracked_docs_new_md_fails()
        print("Test (b) PASSED")
    except AssertionError as e:
        print(f"Test (b) FAILED: {e}")
        sys.exit(1)

    # Run test (c) - control test 2  
    print("\nTest (c): Tracked docs/tracked.md not in config should fail")
    print("-" * 60)
    try:
        test_tracked_md_not_in_config_fails()
        print("Test (c) PASSED")
    except AssertionError as e:
        print(f"Test (c) FAILED: {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All tests PASSED!")