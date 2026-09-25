# {{NAME}} — Open Factory

**Goal:** {{GOAL}}  
**Path:** {{PATH}}  
**Date:** {{DATE}}

## How work is routed

Opus drives this session (`cc`). Each piece of work gets its seat from
`open-factory route --class <class> "<task>"`: the host lists the seats it can
run now (`route menu`: installed, served, logged in, with pool headroom), Jev
picks one or abstains, the host re-checks the pick and falls back to the
routing table's chain. Every decision is an `of_decision` row in
`~/.claude/logs/dispatch.jsonl`; `open-factory report` summarizes them.

## Loop

1. Plan; split into seams with owned files.
2. Route each seam, dispatch independent seats in one message (one worktree each).
3. Verify by running the work; a separate checker accepts.
4. Land merge-ready heads.
