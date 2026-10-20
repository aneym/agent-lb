## Routing standard (2026-10-16)

Alex, 2026-10-16: "we should explore using haiku and sonnet more as well, and see what benchmarks we have already so we can set the new routing standards. all of this should be elegantly baked into factory so as we adjust things, we're learning and using the new selection of models with all new dispatches." And: "really need the claude smarts but efficient to drive other models or lower models like sonnet and haiku."

Claude smarts drive at Opus medium. Sonnet and Haiku take only bounded decisions under an Opus contract. A class is set by its authority, not by its script name: verdict, architecture and spec authority stay on Opus. Sol, Muse and Grok do volume. Fable is off by default and runs only when a dispatch names it, logged; fable-orchestrator is no longer readmitted.

`routing-table.json` is one versioned policy (`policy_rev`, `table_sha256`). `route dispatch <class> --job <id> --json` makes every decision and logs one row to `~/.claude/logs/route-decisions.jsonl` (schema `schemas/route-decision.v1.json`). `route policy snapshot --json` is the one source seat files, `effortLevel`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` and quota tab defaults are generated from.

Precedence, in order:
1. **Explicit** seat, model or effort flags win and are logged as arm `explicit`.
2. **Constraints** only narrow eligibility: never-explore classes, closed gates, exploration off, the Anthropic reserve and tight mode for Claude arms, daily caps counted in jobs, paused arms. An Opus-authority class under the reserve returns exit 3 `wait`; no constraint ever substitutes another model.
3. **Sticky:** a job keeps its first arm through retries, fix rounds and box forwarding; nested wrappers never redraw.
4. **Explore** draws a seeded arm among the eligible ones and logs its propensity.
5. **Standard** otherwise.

| Class | Standard | Explore arm (phase 3 only) | Evidence |
|---|---|---|---|
| Tabs, leads, workflow orchestrators | Opus tab, opus-latest medium | none | 2026-10-14 |
| plan, review, design, audit, judge, synthesis | planner, plan-reviewer, frontend-designer, Opus stage; opus-latest medium | none | E19 Opus medium 9/20 = high 9/20, $0.78 vs $1.03 per accepted |
| implement | gpt-implementer, sol-latest medium | muse-seat 10%; cursor-seat grok-latest 10% (gated until the 2026-10-30 Cursor probe) | E17 Sol 19/20 at $0.64 per accepted, Grok 20/20 |
| mechanical | gpt-implementer, sol-latest low | muse-seat 10%; luna-latest low 10% (gated on Alex) | E10, E12 |
| explore | gpt-explorer, sol-latest low | Explore on haiku-latest 20% | Haiku unmeasured |
| research | codex-sol, sol-latest high | Explore on sonnet-latest 10% | |
| verify | verifier, sonnet-latest high; codex-verifier sol-latest high for Claude authors | sol-latest xhigh 10% for Claude authors | E15 Sonnet 16/16 at $0.101 vs $0.216; DeepSWE low 16/20 vs xhigh 17/20 at a third of the cost |
| decide (factory decider ticks, lookback scoring) | decider, sonnet-latest high (was Fable every 30 min on ax42) | haiku-latest 10% | E20 Sonnet high 12/20, Sol 12/20, Opus medium 11/20, exploration evidence only; a routing failure skips the tick |
| digest | sonnet-latest medium | haiku-latest 20% | no decision authority |
| relay (forwarding wrappers) | sonnet-latest low, full ids in seat files | haiku-latest low 30% | |
| classify | Jev first; haiku-latest low when a model is needed | none | |
| computer, council | computer-use sol-latest medium; codex-sol sol-latest high | none | |

The background model stays `claude-sonnet-5-5` until Opus promotes the relay Haiku arm.

Guardrails: exploration ships off (`explore.enabled` false). When on, arms take at most 30% of a class, 40 jobs per arm per day and 60 Claude jobs per day; three non-accepts in a row pause an arm. Never explore plan, review, spec, contract, verdict, judge, design, audit, UI-critical, trust-boundary, money-path or fix work.

Success: an arm succeeds when its accepted rate is within the standard's at no worse cost per accepted job, over at least 40 jobs per arm, read from decision rows joined to outcomes. Promotion is manual: Opus edits `standards`, bumps `policy_rev` and adds a dated evidence line here. No automatic promotion.

