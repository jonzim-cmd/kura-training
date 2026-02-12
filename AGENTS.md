# Agent Instructions

This project uses **bd** (beads) for issue tracking.

## Issue Tracking

Run `bd prime` for workflow context, or install hooks (`bd hooks install`) for auto-injection.
For full workflow details: `bd prime`.
When working in a git worktree, use `scripts/bd-safe.sh` (auto-sets `BEADS_NO_DAEMON=1`).

## Collaboration Principle

This product is co-created by humans and agents as equal partners.
Agents should actively shape architecture and product decisions, not just execute tasks.
All collaboration should align with the partnership and integrity principles in `CLAUDE.md`:
clear reasoning, honest uncertainty, and shared ownership of quality.

## Quick Reference

```bash
scripts/bd-safe.sh ready              # Find available work
scripts/bd-safe.sh show <id>          # View issue details
scripts/bd-safe.sh update <id> --status in_progress  # Claim work
scripts/bd-safe.sh close <id>         # Complete work
scripts/bd-safe.sh sync               # Sync with git
```

## Environment Setup

**MANDATORY**: Before running any commands, load the project environment:
```bash
set -a && source .env && set +a
```
This sets `DATABASE_URL` and other required variables from the project `.env` file.

## Quality Gates

Before completing any task that changed code, run ALL applicable gates:
```bash
# Load environment first
set -a && source .env && set +a

# Python lint
ruff check workers/src/ workers/tests/

# Python unit tests (no DB needed)
PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/ -q --ignore=workers/tests/test_integration.py

# Python integration tests (needs DATABASE_URL + running PostgreSQL)
PYTHONPATH=workers/src uv run --project workers python -m pytest workers/tests/test_integration.py -q

# Rust tests
cargo test --workspace
```

If integration tests show "skipped", `DATABASE_URL` is not set. Re-run `set -a && source .env && set +a` and retry.

**CI/sandbox environments** without a running PostgreSQL can use `bash scripts/codex-setup.sh` to install and configure one.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   scripts/bd-safe.sh sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
