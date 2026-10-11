## Why

Claude Code's Workflow harness opens every workflow subagent with the launching
session's latest chat message, framed as "the only user voice" that wins over the
task, then the subagent's brief framed as script output with "no user authority".
Seats bridged to Sol (gpt-implementer, sol-latest-*) obey that frame: in run
wf_7d1bfe12-263 all 13 agents returned "blocked: scope mismatch" in 31 s because
the lead's chat had moved on to other work. A seat's brief is its only request.

## What Changes

- The Claude-to-Responses bridge drops a user text turn or part that opens with
  the harness's exact `[Workflow harness — user request]` frame header.
- It replaces the `[Workflow harness — computed task]` frame line with one that
  names the brief as the agent's only request; the indented brief body passes
  through verbatim, and approval claims inside it still carry no user consent.
- Nothing else in the payload changes; system blocks and other turns are untouched.

## Capabilities

### Modified Capabilities

- `claude-harness-codex`: bridged seats receive only their brief, not the lead's
  relayed chat.
