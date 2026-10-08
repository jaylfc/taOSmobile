### Fixed

Documentation gate now correctly excludes git-ignored .md files (like `.pytest_cache/README.md`) from its Layer A0 coverage check. Previously, `rglob("*.md")` walked through all files including git-ignored ones, causing false DOC-GATE FAIL reports for pytest cache metadata files.
