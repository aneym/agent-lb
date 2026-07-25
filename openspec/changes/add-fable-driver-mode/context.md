# add-fable-driver-mode — context

## Why a driver-swap and not a new stack

The shape is copied deliberately from Kimi mode. The retired `kimi` function
pointed at Moonshot and flattened opus, sonnet, haiku, AND subagent to one
model, which silently destroyed the canonical seat lineup — sol-alias seats
cannot be served when the base URL is not the LB, and sonnet catch-alls
stopped being cheap. `fable` therefore changes exactly three environment
slots and nothing else, and runs through `claude-lb-launch` rather than
`claude` directly so LB probing, limit waiting, the account banner, session
ids, and the resilient shim are identical to `cc`.

The seat slots are named in `clients/fable` as `SEAT_SLOTS` even though the
client never writes them: the constant exists so the omission is visible to a
reader and checkable by `verify-routing`. A future edit that starts writing
those slots fails verification instead of quietly costing the lineup.

## Why the plan reviewer is a Fable seat

Judging a plan is the same brain work as writing one, so it is
capability-bound rather than volume — the same argument that made
frontend-designer a sanctioned Opus seat and the planner a sanctioned Fable
one. It is kept honest by construction: read-only tools, no rewriting of the
plan, a bounded report, and a requirement that every finding cite `file:line`
or a plan section. Findings without evidence are opinions and do not count.

It uses `claude-planner` rather than a literal `claude-fable-5` so it inherits
the alias's documented degradation: Fable 5 while any otherwise-routable
account is outside authoritative Fable-scoped exhaustion, Opus 5 only when
every one of them carries a fresh, future-reset scoped marker. A literal model
id would fail the dispatch outright in that state.

## The failure mode it targets

Plans that are internally coherent and factually wrong about the codebase.
Every seat dispatched against such a plan builds toward files, signatures, or
schemas that do not exist, and the error surfaces only at integration. Hence
step 2 of the reviewer's procedure — fact-check every repo claim before
judging anything else — is the reason the seat exists, not a preliminary.

## Interaction with existing enforcement

`seat-guard.py` denies expensive ad-hoc dispatches: an explicit `fable`/`opus`
model override on an `Agent` call, or a catch-all subagent type inheriting the
driver model. Named agent types manage their own pinned models, so dispatching
`plan-reviewer` is allowed without any guard change. Inside `fable` mode the
guard is if anything more important: catch-alls would otherwise inherit a
Fable driver, and the guard already denies exactly that.

## Verification performed

- `config/coding-agents/install-policy.py` converged; `verify-routing` all
  green including the new plan-reviewer and fable-client checks.
- `CLAUDE_LB_DRY_RUN=1 fable` prints the launcher's Claude Code command and
  confirms the driver model and that no seat slot is exported.
- `ruff check app clients`, `python -m py_compile clients/fable`, and strict
  OpenSpec validation.
