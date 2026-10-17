# Plan 006: install-policy derives each managed file's paths from its template name

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- config/coding-agents/install-policy.py`
> This file is the most-changed file in the repo (51 of the last 300 commits),
> so drift is likely. Drift outside lines 36-349 (`MANAGED_AGENTS`) is fine.
> If `MANAGED_AGENTS` gained or lost entries, include them in the new lists;
> if any new entry breaks the naming pattern described below, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED (seat routing install path is on the money path, AGENTS.md rule 8)
- **Depends on**: plans/003 (unit tests that import `MANAGED_AGENTS` by shape are gone)
- **Category**: tech-debt (architecture: deepening)
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

`config/coding-agents/install-policy.py` installs the routing policy, seat
definitions and hooks into a home directory. Its `MANAGED_AGENTS` table
(lines 36-349, about 314 lines) is 50 hand-written 4-tuples. Every tuple
follows one pattern, so each new seat or hook means copying four lines and
editing the name in four places; a typo in one silently installs to the wrong
place or writes the wrong owner marker. Deriving three of the four fields from
the template path makes the table a list of names, and the pattern lives in
one function.

## Current state

```python
# config/coding-agents/install-policy.py:36-48
MANAGED_AGENTS = (
    (
        Path(".agent-lb/managed/coding-agents/codex-seat-home.py"),
        Path(".agent-lb/managed/coding-agents/codex-seat-home"),
        "agent-lb:codex-seat-home:v1\n",
        Path("codex-seat-home.py"),
    ),
    ...
    (
        Path(".claude/agents/planner.md"),
        Path(".agent-lb/managed/coding-agents/planner"),
        "agent-lb:planner:v1\n",
        Path("agents/planner.md"),
    ),
```

Each tuple is `(target, owner, marker, template)`. At `63b62f18`, 49 of the 50 obey
the rules below. The one exception is `codex/port-kill.rules`, whose owner is
`.agent-lb/managed/coding-agents/codex-port-kill-rules` and marker
`agent-lb:codex-port-kill-rules:v1\n` (name `codex-port-kill-rules`, not the stem `port-kill`). The rules:

- `name = owner.name`, and `owner == Path(".agent-lb/managed/coding-agents") / name`
- `marker == f"agent-lb:{name}:v1\n"`
- `target.name == template.name`, `name == template.stem`
- `target.parent` is one of `.agent-lb/managed/coding-agents` (1 entry: `codex-seat-home.py`), `.codex/rules` (1 entry: `codex/port-kill.rules`), `.claude/agents` (32 entries: `agents/*.md`), `.claude/hooks` (16 entries: `hooks/*`).

Order today (keep it): `codex-seat-home.py`, `codex/port-kill.rules`, then
agents `computer-use, frontend-designer, planner, plan-reviewer, codex-sol,
Explore, opus-seat, opus-seat-full, cursor-seat, devin-seat, muse-seat,
muse-seat-local, pi-durable-seat, codex-verifier, codex-test-runner, verifier,
gpt-implementer, gpt-explorer, sol-consult, astra-consult, fable-orchestrator,
sonnet-implementer, effort-xhigh, host-relay, gpt-implementer-local,
gpt-explorer-local, sonnet-implementer-local, verifier-local, opus-seat-local,
codex-sol-local, codex-test-runner-local, computer-use-local`, then hooks
`subagent-closeout.py, seat-guard.py, workflow-seat-guard.py,
workflow-readback.py, factory-context.py, compact-checkpoint.py,
wide-scan-guard.sh, agent-lb-bootout-guard.sh, railway-vars-guard.sh,
dangerous-command-guard.sh, link-cli-guard.sh, workflow-relay-guard.py,
plutil-guard.sh, kill-scope-guard.py, hook-dispatch.py, hook-dispatch-parity.py`.
Confirm the order by printing it (Step 1); the printed order wins over this list.

Not every file in `agents/` or `hooks/` is managed (for example `agents/decider.md`,
`agents/jev.md`, `hooks/codex-local.sh` are not), so the lists must stay
explicit names, not a directory glob.

Consumers read the tuple positionally (`install-policy.py:1159`, `:1327`, `:1968`);
they do not change.

## Target interface

```python
MANAGED_DIR = Path(".agent-lb/managed/coding-agents")

def managed(target_dir: str, template: str, name: str | None = None) -> tuple[Path, Path, str, Path]:
    source = Path(template)
    owner = name or source.stem
    return (Path(target_dir) / source.name, MANAGED_DIR / owner,
            f"agent-lb:{owner}:v1\n", source)

MANAGED_SEATS = ("computer-use", "frontend-designer", ...)
MANAGED_HOOKS = ("subagent-closeout.py", "seat-guard.py", ...)

MANAGED_AGENTS = (
    managed(".agent-lb/managed/coding-agents", "codex-seat-home.py"),
    managed(".codex/rules", "codex/port-kill.rules", "codex-port-kill-rules"),
    *(managed(".claude/agents", f"agents/{name}.md") for name in MANAGED_SEATS),
    *(managed(".claude/hooks", f"hooks/{name}") for name in MANAGED_HOOKS),
)
```

Name `MANAGED_DIR` differently if that name already exists in the file
(`grep -n "^MANAGED_DIR" config/coding-agents/install-policy.py`). No comments or docstrings.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/managed-files -b arch/managed-files origin/main` | exit 0 |
| Dump | `python3 -c "import importlib.util,json; s=importlib.util.spec_from_file_location('ip','config/coding-agents/install-policy.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps([[str(a),str(b),c,str(d)] for a,b,c,d in m.MANAGED_AGENTS], indent=0))"` | prints the table as JSON |
| Policy check | `python3 config/coding-agents/install-policy.py --check` | same exit code and output as on `origin/main` |
| Routing verify | `python3 config/coding-agents/verify-routing --source-only` | exit 0 |
| Lint | `uv run --no-sync ruff check config && uv run --no-sync ruff format --check config` | exit 0 (or same as `origin/main` if `config/` is not ruff-clean today) |

## Scope

**In scope**: `config/coding-agents/install-policy.py`, lines defining `MANAGED_AGENTS` only.

**Out of scope**: every consumer of `MANAGED_AGENTS`; `ADOPTED_KEEP`,
`RETIRED_AGENTS` and the rest of the file; the agent and hook templates.

## Git workflow

- Branch `arch/managed-files`; one commit, e.g. `install-policy: derive managed file paths from template names`; `check:` line (Dump diff empty, Policy check unchanged) and `Seat:` trailer.
- Land: `git push origin HEAD:main`. Then run the installer the normal way once on Studio (`python3 config/coding-agents/install-policy.py` from the main checkout after `git pull --ff-only`), and confirm Policy check passes.

## Steps

### Step 1: Snapshot the table

Run Dump on the untouched worktree: `... > /tmp/006-before.json`. Run Policy
check and save its output and exit code to `/tmp/006-check-before.txt`.

**Verify**: `/tmp/006-before.json` holds 50 entries (`python3 -c "import json;print(len(json.load(open('/tmp/006-before.json'))))"`), or the drifted count.

### Step 2: Check the pattern on today's table

Run:
`python3 -c "import json; t=json.load(open('/tmp/006-before.json')); from pathlib import Path; bad=[r for r in t if not (Path(r[1]).name==Path(r[3]).stem and r[2]==f'agent-lb:{Path(r[3]).stem}:v1\n' and Path(r[0]).name==Path(r[3]).name and str(Path(r[1]).parent)=='.agent-lb/managed/coding-agents')]; print(bad)"`

**Verify**: prints exactly one entry, the `codex/port-kill.rules` row with owner name `codex-port-kill-rules`. Any other entry is a STOP.

### Step 3: Replace the table

Write `managed`, `MANAGED_SEATS`, `MANAGED_HOOKS` and the new `MANAGED_AGENTS`
in place of lines 36-349, keeping the printed order.

**Verify**: Dump `> /tmp/006-after.json`; `diff /tmp/006-before.json /tmp/006-after.json` prints nothing.

### Step 4: Behavior unchanged

**Verify**: Policy check gives the same output and exit code as `/tmp/006-check-before.txt`; Routing verify exits 0; Lint as expected.

## Test plan

No new tests. The Dump diff is the proof that the data is identical; the
installer's own `--check` and `verify-routing` cover the install path.

## Done criteria

- [ ] `diff /tmp/006-before.json /tmp/006-after.json` empty
- [ ] Policy check unchanged; Routing verify exits 0
- [ ] `grep -c '"agent-lb:' config/coding-agents/install-policy.py` is at most 2 (only the f-string remains, plus any unrelated marker)
- [ ] `plans/README.md` row 006 updated

## STOP conditions

- Step 2 finds an entry that breaks the pattern.
- The Dump diff is not empty after Step 3.
- `--check` output changes.

## Maintenance notes

- Adding a seat is now one name in `MANAGED_SEATS`; adding a hook is one name in `MANAGED_HOOKS`.
- A file that must install somewhere outside the pattern gets its own explicit `managed(...)` line or, if the owner/marker must differ, an explicit 4-tuple; do not bend `managed`.

LOC removed (estimate): ~250.
