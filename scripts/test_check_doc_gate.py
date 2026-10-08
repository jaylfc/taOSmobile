#!/usr/bin/env python3
"""Tests for check_doc_gate.py invariants check."""

import tempfile
import subprocess
from pathlib import Path
import shutil


def run_invariants_check(repo_root: Path, config_content: str) -> tuple[int, str]:
    """Helper to run the invariants check in a temporary repo."""
    # Write config
    config = repo_root / "docs" / "doc-gate.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(config_content)
    
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
path_prefixes = ["scripts", "docs", "droidian", "kiosk", "bridge", "pmos"]
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
        if returncode != 0:
            # Check if the failure is about .pytest_cache/README.md
            if ".pytest_cache/README.md" in output:
                print(f"FAILED: .pytest_cache/README.md caused DOC-GATE FAIL (bug still present)")
                print(f"Output: {output}")
                return False
        
        print(f"PASSED: .pytest_cache/README.md did not cause failure (fix working)")
        return True


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
path_prefixes = ["scripts", "docs", "droidian", "kiosk", "bridge", "pmos"]
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
        if returncode != 0 and "docs/new.md" in output and "neither referenced_paths_scan nor unscanned_paths" in output:
            print(f"PASSED: docs/new.md correctly caused DOC-GATE FAIL")
            return True
        else:
            print(f"FAILED: docs/new.md should have caused DOC-GATE FAIL but didn't")
            print(f"Output: {output}")
            return False


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
path_prefixes = ["scripts", "docs", "droidian", "kiosk", "bridge", "pmos"]
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
        if returncode != 0 and "docs/tracked.md" in output and "neither referenced_paths_scan nor unscanned_paths" in output:
            print(f"PASSED: docs/tracked.md correctly caused DOC-GATE FAIL")
            return True
        else:
            print(f"FAILED: docs/tracked.md should have caused DOC-GATE FAIL but didn't")
            print(f"Output: {output}")
            return False


if __name__ == "__main__":
    print("Running tests for check_doc_gate.py fix...")
    print("=" * 60)
    
    # Run test (a) - the buggy one
    print("\nTest (a): Ignored .pytest_cache/README.md should NOT fail")
    print("-" * 60)
    test_a_passed = test_ignored_pytest_cache_md_does_not_fail()
    
    # Run test (b) - control test 1
    print("\nTest (b): Untracked docs/new.md should fail")
    print("-" * 60)
    test_b_passed = test_untracked_docs_new_md_fails()
    
    # Run test (c) - control test 2  
    print("\nTest (c): Tracked docs/tracked.md not in config should fail")
    print("-" * 60)
    test_c_passed = test_tracked_md_not_in_config_fails()
    
    print("\n" + "=" * 60)
    print(f"Summary: Test (a) {'PASSED' if test_a_passed else 'FAILED'}, Test (b) {'PASSED' if test_b_passed else 'FAILED'}, Test (c) {'PASSED' if test_c_passed else 'FAILED'}")
    
    if test_a_passed and test_b_passed and test_c_passed:
        print("All tests PASSED!")
        exit(0)
    else:
        print("Some tests FAILED!")
        exit(1)