# {{NAME}} — Open Factory

**Goal:** {{GOAL}}  
**Path:** {{PATH}}  
**Date:** {{DATE}}

## Roles

| Seat | Job |
|---|---|
| orchestrator | Fable brain: plan, dispatch, accept |
| planner / plan-reviewer | Lane lead + adversarial plan check |
| designer | Fable/Opus specs and look-checks |
| implementer-quality | Astra → Sol Ultrafast → Sol → Opus |
| implementer-speed | Cerebras oss/Qwen → Luna → GLM/Kimi → local |
| explore | Cheap scouts |
| verifier | Independent acceptance (budgeted) |
| security-reviewer | Diff security |
| computer-use | Read-only UI probe |
| release-captain | Land / ship only |
| classify | `jev route` |

## Loop

1. Classify new human turns (`open-factory route`).
2. Plan → plan-review → fan-out seats (one worktree each).
3. Verifier + designer look-check in parallel.
4. Release captain lands merge-ready heads.
5. Log outcomes under `.open-factory/dispatch/`.

## Do not

- Grind files in the orchestrator thread.
- Swap main-thread model family mid-chat.
- Use Astra/Sol as designer.
- Treat secondary quota alone as unlock when primary is exhausted.
