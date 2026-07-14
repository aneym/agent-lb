# Claude harness and Codex routing context

## Purpose

Agent-lb supports two deliberately different Claude Code entry points. Normal
`cc` is a Fable-led planning and orchestration environment that sends hands-on
work to GPT through the direct CCDEX worker. `ccdex` is a compatibility host in
which GPT owns the entire loop.

## Decisions and constraints

- Parent-mode selection is a launcher property, not a prompt convention.
- CCDEX fails closed on a noncanonical Messages model before selecting an
  Anthropic account. A hook provides earlier feedback but is not the security
  boundary.
- Claude `Workflow` does not provide a trustworthy inherited worker model;
  every embedded phase or agent can name its own model. Therefore CCDEX does
  not use Claude Agent or Workflow delegation.
- The worker transport is durable and resumable, but its read-only mode is a
  Claude Code tool restriction rather than an operating-system sandbox.
- Machine-wide routing policy is versioned in `config/coding-agents` and
  installed at `~/.agents/policy/coding-agents`.

## Failure modes

- A Fable quota denial in normal `cc` is reported as unavailable capacity; it
  must not trigger a task-based model substitution.
- An unavailable GPT worker is a blocker for delegated implementation; normal
  Claude Code must not silently replace it with a cheaper Claude worker.
- A stale shell wrapper or global Claude setting cannot alter the default
  launch profile because the launcher injects the defaults itself.

## Example

For a normal Claude Code feature request, Fable writes a bounded work order and
acceptance command, starts `ccdex-worker`, reads its bounded result and diff,
and reruns the acceptance command. The same request launched through `ccdex`
is planned and executed by GPT; any attempted Claude-model child request is
rejected locally.
