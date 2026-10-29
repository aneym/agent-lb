# agent-lb

Local load balancer for Claude Code, Codex and other agent clients; used by its operator and teammates.
Pools subscription accounts behind one endpoint and provides an operational dashboard.

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

## Validate
A feature lane writes its user stories in plain words. The building agent proves each browser-visible story with `story-run <story.yaml> --agent <its id>` on prod under its own test account, then passes the passing `verdict.json` and `video.mp4` to `prod-evidence record --artifact`. The 30-60 s video is the default report to Alex. Story runs stay out of CI; only a story that guards something critical becomes a Playwright gate check.

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
- Worktree setup: T3 runs `python3 scripts/t3_setup_worktree.py`; use `.venv/bin/python` or `uv run --no-sync`, not `uv sync`.
- New machine setup: `GETTING-STARTED.md`; account operations: `.agents/skills/agent-lb-account-operator/SKILL.md`.

Floors here: outbound approval, spend caps, destructive-data approval, secret redaction and Alex's credentials.
