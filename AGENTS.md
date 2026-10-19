# agent-lb

Local load balancer for Claude Code, Codex and other agent clients; used by its operator and teammates.
Pools subscription accounts behind one endpoint and provides an operational dashboard.

rules: agent-plugin-kit@055d550

## Read first
1. This file.
2. `features/README.md`: what the repo does, by feature.
3. `PRODUCT.md`.
4. The `docs/agents/<topic>.md` page named below for the area you touch.

## Check
`./check` runs the whole fast loop. `./check --staged` is the pre-commit hook.

## Land
Fast-forward push to `origin/main`.
The commit body carries a `check:` line: one bare shell command the gate runs.

## Worktrees
The main checkout `/Volumes/StudioExt/repos/agent-lb` stays on `main`.
Other branches: `git worktree add ../agent-lb-wt/<topic> -b <branch> origin/main`.

## Feature map
Index: `features/README.md` (generated; do not edit by hand).
Feature files: `features/`. Product manifest: `products.json`.
Validate with `feature-map check`.

## Verify
`.agents/skills/verify-agent-lb/SKILL.md`: launch, doctor, drive, evidence, cleanup.

## Topics
- Onboarding and account operations: `.agents/conventions/agents-long-form.md`.
- Billing marker and system-block safety: `.agents/conventions/agents-long-form.md`.
- Restart and deploy (lb-restart only, never launchctl kickstart/bootout): `.agents/conventions/agents-long-form.md`.
- Money-path, incident fixes and rule changes: `.agents/conventions/factory-rules.md` (record rule changes in `DECISIONS.md`).
- File-size cap: `CODING_STANDARDS.md`; keep authored files under 20,000 bytes.
- Repo-specific coding standards: `CODING_STANDARDS.md`.

## Repo facts
- The serving machine runs the launchd service on `http://127.0.0.1:2455`; see `scripts/install-service.sh`.
- Worktree setup: `sh scripts/worktree-setup.sh`; use `.venv/bin/python` or `uv run --no-sync`, not `uv sync`.
- New machine setup: `GETTING-STARTED.md`; account operations: `.agents/skills/agent-lb-account-operator/SKILL.md`.

- No unit tests (Alex, 2026-10-19). Prove behavior through the running app, HTTP, a CLI subprocess, MCP transport or a real install. Keep kept-floor guards (outbound approval, spend caps, destructive-data approval, secret redaction, Alex’s credentials) and never thin them. Delete unit tests you touch.
