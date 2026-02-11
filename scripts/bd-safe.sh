#!/usr/bin/env bash
set -euo pipefail

# In git worktrees, force beads direct mode to avoid cross-branch daemon commits.
git_dir="$(git rev-parse --git-dir 2>/dev/null || true)"
if [[ -n "${git_dir}" && "${git_dir}" == *"/worktrees/"* ]]; then
  export BEADS_NO_DAEMON=1
fi

exec bd "$@"
