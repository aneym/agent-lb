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
Onboarding and account operations: `.agents/conventions/agents-long-form.md`.

## Repo facts
The serving machine runs the launchd service on `http://127.0.0.1:2455`; see `scripts/install-service.sh`.
Worktree setup: `sh scripts/worktree-setup.sh`; use `.venv/bin/python` or `uv run --no-sync`, not `uv sync`.
New machine setup: `GETTING-STARTED.md`; account operations: `.agents/skills/agent-lb-account-operator/SKILL.md`.
