# add-fable-driver-mode

## Why
Some sessions are brain work end to end — architecture, spec and canon
authoring, plan review — and the operator wants those to run on Fable without
hand-editing model flags or, worse, improvising a launcher that flattens every
model slot the way the retired `kimi` function did. There is no sanctioned way
to start a Claude Code session on Fable today: `cc` hardcodes Opus 5, and any
ad-hoc `--model claude-fable-5` invocation loses the launcher's LB probing,
limit waiting, banner, and session identity.

The canonical seats also have no reviewer for plans. The verifier seat checks
that an implementation matches its brief, but nothing checks the brief itself
before seats are dispatched against it. The expensive failure mode observed in
practice is a plan that is internally coherent and factually wrong about the
codebase: every downstream seat then builds against files, signatures, or
schemas that do not exist.

## What Changes
- Add a `fable` client (`clients/fable`) that execs the canonical
  `claude-lb-launch` with the driver and the opus/fable default slots set to
  `claude-fable-5`, leaving the sonnet, haiku, and subagent slots unset so the
  canonical seats keep resolving through the LB. `AGENT_LB_FABLE_MODEL`
  overrides the driver model; an explicit `--model` still wins.
- Install `fable` alongside `cc` in `scripts/install-claude-clients.sh`, with
  the same non-symlink backup and uninstall-by-ownership behavior.
- Add a canonical `plan-reviewer` seat: read-only (no Edit/Write), on the
  Fable-primary `claude-planner` route alias at high effort, which fact-checks
  a plan's repo claims against the codebase and judges assumptions, acceptance
  criteria, decomposition, and blast radius, returning a bounded verdict with
  `file:line` evidence.
- Manage the `plan-reviewer` definition through the policy installer
  (checkpoint, ownership marker, idempotent convergence, ownership-gated
  uninstall) exactly as the planner and frontend-designer definitions are.
- Document both in the canonical routing policy (seat table row + a "Fable
  driver mode" section) and extend `verify-routing` to prove them.

## Impact
- Operators get a sanctioned Fable-driver session shape that preserves the
  hands-vs-brain seat lineup instead of an ad-hoc launcher that destroys it.
- Plans can be adversarially validated before implementation fan-out spends
  seat tokens against wrong premises.
- `cc`, the seat lineup, provider routing, and account selection are
  unchanged; the LB serves `claude-fable-5` and `claude-planner` through the
  existing Anthropic route.
