# Advisory status CLI closeout

## Scope
Worktree: `/Volumes/StudioExt/repos/agent-lb-worktrees/status-cli`.
Branch: `feat/status-cli`, based on `6f8f77d5`.
Main checkout and unrelated dirty work preserved.

## Runtime recovery observed
- Agent LB `/internal/drain/status` reported zero in-flight HTTP requests.
- Patched only the runtime Fable scoped threshold from 90 to 100 percent,
  preserving other runtime changes. Graceful SIGTERM via launchd after drain.
- `/health/ready` returned `ok` after reload.
- Active account `2c436b54-a7e2-4299-9d6b-689ad2dda8cb` changed from
  `fableEligible=false` to `true` at the same 90-percent Fable usage.
- Runtime settings backup:
  `/Users/aneyman/.agent-lb/backups/status-cli-20260921T193240Z/settings.py`.
- Actual upstream quota, auth, explicit spend settings, and other accounts unchanged.

## Verification so far
- Existing CLI suite: 11 passed.
- Existing targeted Fable balancer check: passed.
- PATH installer: shell syntax valid; isolated install and refusal-to-overwrite passed.
- No standalone provider inference probe was sent; live conversation recovery
  will supply provider evidence separately from status telemetry.

## Remaining acceptance
CLI focused tests, source review, PATH installation and live invocation;
message existing `w56:p2Z` and observe reply.

## Installed guard
The routing-only seat guard and its shell fallback are advisory, including Fable
model choice and missing/stale capacity snapshots. Unrelated security controls
were not changed. The installed policy symlink targets the foreign dirty main
checkout, so that prose was preserved; the exact owner override will be sent to
the resumed coordinator. The worktree contains the durable advisory hook and
updated policy for integration. Backup paths:
- `/Users/aneyman/.claude/hooks/seat-guard.py.pre-model-advisory-20260921T193454Z`
- `/Users/aneyman/.claude/settings.json.pre-seat-guard-advisory-20260921T193454Z`

No PR, merge, or public release has been performed.

## Guard acceptance
- Six focused hook regressions passed; ruff check passed.
- Parent independently exercised the installed shell fallback with an absent
  hook: exit 0 and advisory-only JSON, no permission decision.
- Settings diff changes only `/hooks/PreToolUse/0/hooks/0/command`.
- Installed hook matches tracked source SHA256
  `8ecf2b0db75bf20a3c8e5002215d049e073347afdf0d1d1299185fc659508779`
  (see actual hash receipt in acceptance output if this snapshot is superseded).

## CLI acceptance and install
- Parent combined focused suite: 29 passed in 6.42 seconds, then rerun after
  final formatting; Ruff clean. Six new acceptance regressions were observed
  failing before the correction.
- Source changes installed selectively into internal runtime `app/cli.py` and
  `app/status_cli.py`, with matching hashes in
  `/Users/aneyman/.agent-lb/backups/status-cli-20260921T193240Z/cli-manifest.json`.
- PATH entry: `/Users/aneyman/.local/bin/agent-lb`.
- Live human and JSON command
  `agent-lb status --provider anthropic --model claude-fable-5-1 --thinking --json`
  returned ready, 5 accounts, 1 usable, Fable thinking usable, 10% scoped remaining.
- Unreachable service check returned structured JSON and exit 2.
- Status CLI installation needed no further service restart.
- OpenSpec CLI is not installed here; formal validator not run. Proposal,
  scenarios, tasks, and this receipt are included for integration.

## Coordinator delivery
Sent one Herdr `agent prompt` to exact existing pane `w56:p2Z`, session
`834653fa-30a9-45a4-b11c-1265bfe11cb7`, after verifying its identity and idle state.
Readback shows the full message delivered and agent working (`Sublimating`).
The message carries the owner override, CLI command, and instructions to reconcile
existing jobs without duplicate writers; no iMessage action authorized.

## Live provider recovery proven
At `2026-09-21T19:45:19.704379Z`, Agent LB request logs recorded:
- session `834653fa-30a9-45a4-b11c-1265bfe11cb7` (the requested `w56:p2Z`)
- model `claude-fable-5-1`, status `ok`, no error
- account `2c436b54-a7e2-4299-9d6b-689ad2dda8cb`

Herdr readback shows the agent running the new CLI and reading its existing job
output. This is real provider/session evidence, separate from the 29 passing local
tests and GET-only status checks. No duplicate writer was created.

## Outcome
Requested local CLI and advisory routing recovery are installed and verified.
Remaining integration follow-up: this worktree is committed but not merged/pushed;
foreign main checkout policy prose was not modified. The resumed coordinator
received Alex's explicit overriding instruction. OpenSpec validator was unavailable.
