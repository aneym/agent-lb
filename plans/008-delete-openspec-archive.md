# Plan 008: Archived OpenSpec changes live only in git history

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- openspec/changes/archive scripts/validate-active-openspec-changes.sh`
> New archived changes since `63b62f18` are expected; delete them too.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs (legacy)
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

`openspec/changes/archive/` holds 793 files (about 22,000 lines) of finished
change proposals. Nothing reads them: the gate `openspec validate --specs`
reads `openspec/specs/`, and `scripts/validate-active-openspec-changes.sh`
skips the `archive` directory (`! -name archive`). They show up in every
repo-wide search agents run and bury the 55 live spec files. AGENTS.md rule 12
says "History stays in git", so git history is the archive.

## Current state

- `git ls-files openspec/changes/archive | wc -l` → 793 at `63b62f18`.
- Consumers: `scripts/validate-active-openspec-changes.sh` excludes it; the
  OpenSpec skills under `.agents/skills/openspec-archive-change/` and
  `.agents/commands/opsx/archive.md` move finished changes into it. The
  `openspec archive` command recreates the directory when it runs, so later
  archives still work; this plan only removes what is there today.
- `openspec/specs/**` and active `openspec/changes/<slug>/` stay.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-openspec-archive -b cull/openspec-archive origin/main` | exit 0 |
| Specs gate | `npx --yes @fission-ai/openspec@latest validate --specs` (or `openspec validate --specs` if installed) | same result as on `origin/main` |
| Refs | `git grep -n "changes/archive/" -- . ':!openspec/changes/archive' ':!CHANGELOG.md'` | only the OpenSpec skill and command docs, which describe the directory, not a file in it |

## Scope

**In scope**: delete `openspec/changes/archive/` (tracked files only).

**Out of scope**: `openspec/specs/**`, active change directories, the OpenSpec
skills and commands, `CHANGELOG.md`.

## Git workflow

- Branch `cull/openspec-archive`; one commit, `Drop archived OpenSpec changes; history stays in git`; `check:` line; `Seat:` trailer.
- Land: `git push origin HEAD:main`.

## Steps

### Step 1: Baseline the specs gate

Run Specs gate on the untouched worktree; record its output.

**Verify**: output recorded.

### Step 2: Find links into the archive

Run Refs. For any hit that links a specific archived file from a live spec,
README or AGENTS file, replace the link with the commit form
`git show 63b62f18:<path>` in plain text.

**Verify**: Refs prints only generic mentions of the directory.

### Step 3: Delete

`git rm -r -q openspec/changes/archive`.

**Verify**: `git ls-files openspec/changes/archive | wc -l` prints `0`; Specs gate gives the same result as Step 1.

## Test plan

None needed; the specs gate is the check.

## Done criteria

- [ ] `git ls-files openspec/changes/archive | wc -l` is `0`
- [ ] Specs gate unchanged
- [ ] `plans/README.md` row 008 updated

## STOP conditions

- The specs gate result changes after the deletion.
- A live spec (`openspec/specs/**`) includes or imports an archived file rather than linking it.

## Maintenance notes

- To read an old change: `git log --diff-filter=D --name-only -- openspec/changes/archive | head`, then `git show <sha>^:<path>`.
- Follow-up not in this plan: 229 change directories are still "active"; many are done. Archiving them merges their deltas into `openspec/specs/`, which needs judgment per change.

LOC removed (estimate): ~22,000 (docs).
