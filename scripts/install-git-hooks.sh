#!/usr/bin/env bash
# Install the local doc-gate hooks.
#
# CI is authoritative (.github/workflows/doc-gate.yml) and cannot be bypassed,
# but it only tells you after the commit exists. These hooks tell you before.
#
#   pre-commit  -- Layer A invariants + Layer B against the staged index
#
# `git commit --no-verify` skips them; CI will still catch it.
set -euo pipefail

repo_root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
hooks_dir="$(git -C "$repo_root" rev-parse --git-path hooks)"
case "$hooks_dir" in /*) ;; *) hooks_dir="$repo_root/$hooks_dir" ;; esac
mkdir -p "$hooks_dir"

cat > "$hooks_dir/pre-commit" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
python3 "$root/scripts/check_doc_gate.py" invariants
python3 "$root/scripts/check_doc_gate.py" diff-gate --staged
HOOK
chmod +x "$hooks_dir/pre-commit"

echo "installed: $hooks_dir/pre-commit"
