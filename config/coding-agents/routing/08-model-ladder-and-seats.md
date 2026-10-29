## Model picks (2026-09-30; updated 2026-10-29)

Best first, and only Claude is precious; `route` picks the model and, for another machine, the box. Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".

How a child starts:
- **Local, Claude model:** Agent tool with `model: opus|sonnet|haiku` (or Workflow), in-process. T3 Claude seats remap the `sonnet` and `haiku` aliases and the default subagent model to `sol-latest`; when the vendor matters, pass the explicit id (`claude-sonnet-5-5`) and check `requestedModel` in the agent transcript (2026-10-29).
- **Local, other provider or work outliving this turn:** `delegate_task` with `target.providerInstanceId` + `model`, effort in `target.options` (`reasoningEffort` for codex, `effort` for claudeAgent), `isolation: "worktree"` when it writes.
- **Another machine, read-only:** `delegate_task` with the `environmentId` of the box `route` picked (from `t3_environment_links`), no worktree.
- **Another machine, writes or runs plain commands:** the seat-run forwarding agent for the job (opus-seat, gpt-implementer, verifier and the like). T3 refuses a worktree child on a linked environment (invalid_request, 2026-10-29).

| Job | Local | Another machine (writes) | Model, effort |
|---|---|---|---|
| Code to a spec | `delegate_task` `codex`/`gpt-6.1-sol`, worktree; or `claudeAgent`/`k3.1-pro` (Kimi) | gpt-implementer; sonnet-implementer | sol-latest medium; k3.1-pro spreads load; Sonnet high only when Codex is out (2026-10-13) |
| Fixing a review's numbered must-fix list (trial, lead picks it by hand) | `delegate_task` `codex`/`gpt-6.1-terra`, worktree | terra-implementer | terra-latest high; never medium, never the default route (terra-eval, 2026-10-27) |
| Mechanical sweeps | `delegate_task` `codex`/`gpt-6.1-sol` or Kimi, worktree | gpt-implementer; grok-implementer | sol-latest low; grok-latest-low when Codex is short (2026-10-20) |
| Judgment code, or work a child failed twice | Agent `model: opus` | opus-seat | opus-latest (Opus architects, 2026-10-14) |
| UI-critical, contract and trust-boundary code | Agent `model: opus` | opus-seat | opus-latest (Opus architects, 2026-10-14) |
| Read-only exploration | Agent Explore; `delegate_task` `codex`/`gpt-6.1-sol` | `delegate_task` with environmentId | sol-latest low |
| Research | `delegate_task` `codex`/`gpt-6.1-sol` | codex-sol | sol-latest high |
| Review, on request only (non-Claude author) | `delegate_task` `claudeAgent`/`claude-sonnet-5-5`, read-only; or Workflow `model: claude-sonnet-5-5`, never the `sonnet` alias (it resolves to Sol in T3 seats, the author's vendor) | verifier | sonnet-latest high (2026-10-16, E15) |
| Review, on request only (Claude author) | `delegate_task` `codex`/`gpt-6.1-sol`, read-only | codex-verifier | sol-latest high; money path keeps xhigh (2026-10-16) |
| A suite that must run for a review | `delegate_task` `codex`, worktree at the PR head | codex-test-runner | sol-latest |
| Plan and architecture second opinion (one per plan, brief under 2k tokens) | astra-consult, or `delegate_task` `codex`/`gpt-6-astra` read-only | `delegate_task` with environmentId | astra-latest high; never sol-consult (2026-10-14) |
| Plans, specs, verdicts, design | Agent planner, plan-reviewer, frontend-designer | opus-seat | opus-latest medium, never Sol (2026-10-16, E19); under the Claude reserve these wait |
| Tabs Alex talks to, leads, orchestrators | the thread itself | | opus-latest medium; workflow helpers on Sol |
| Remote-machine relay (batch templates) | host-relay | host-relay | sol-latest low |
| Mac computer use and video editing (HyperFrames Studio and other Mac apps); never code | on Studio: `delegate_task` `codex`/`gpt-6.1-terra`, serviceTier priority | computer-use | terra-latest medium, service tier fast (Alex, 2026-10-28: "terra isnt for coding though") |

Available optional seat: `muse-seat` via `muse-seat-local` on Studio, Muse Code `1.4.9-R6297.1` (re-pinned 2026-10-16 after launcher auto-update; seat runs set `MUSE_NO_AUTO_UPDATE=1`), `muse-latest` pins `muse-wonder-1.0`. Alex reports login complete on 2026-10-13; a trivial brief passed on 2026-10-16. Boxes are not yet provisioned. No ladder promotion before interleaved evals.

- **Quota rule (2026-10-14): tabs run `opus-latest` medium; full-tier ultracode stays on.** This replaces the 2026-10-10 Fable exception and the 2026-10-12 Claude lane defaults. Alex, 2026-10-13 ~03:00Z: "okay we ahve very limited claude quota left. we need to slow thigns down a lot until we're ready - ensure everything runs on opus medium at the top level as well."
  Alex, 2026-10-13 ~03:10Z: "token allocation should be to raise and recruiter and that's basically it i think. since those are our priorities".
  Claude quota goes to the Raise and Recruiter tabs only, both at tier `full`. Every other tab (`cos`, `factory`, `rails`, `content`, `home`, `health`, `league-coach`, `frank`, `shelf`, `poker-trainer`, `internet`) is `wake-only`: no scheduled loops, timers, 2h summaries or per-bulletin wakes. These tabs wake only for prod down, a security incident, or Alex typing.
  Lane and workflow helpers run Sol (`sol-latest`). Top-level tabs and workflow orchestrators run `opus-latest` medium; Opus judge and synthesis stages also run at medium. Tonight's normal-mode full tier puts Rails first (Alex, 2026-10-14 ~03:33Z: "full fan out max usage fix absolute everything"); tight-mode defaults resume when a 5h window passes 70%. This quota rule overrides the ladder and older planning, wake and model defaults while quota is tight. Status goes to state files, not bulletins. The quota allocation lives in `~/.config/open-factory/quota.toml` and is managed by `factory quota`, which also emits the workflow policy.
- **Grok is the hands on the X subscription pool (2026-10-20).** grok-implementer takes implement and mechanical work when Codex is low or out, its reservation cap is full, or codex-swap spreads non-critical volume; never UI-critical, contract, trust-boundary, money-path or fix-after-two-fails work, and never past 80% of the weekly `grok` pool. Rules in 07-routing-standard.md.
- **While Claude is on its last account or a 5h window is under 40%,** Claude runs only tabs, plans and specs (2026-09-29).
- **Retired models run when asked** (Alex, 2026-10-05 19:55 ET: "we shouldnt just block model usage, we shoudl allow them if we request or we want t escalate things"). Each use is logged; only a model gone upstream is refused.
- **Grok and Composer never review.** Keep the Haiku background pin on Sonnet (2026-09-30).
- **Move a seat or effort level only on interleaved evals,** about 20 slices per condition, ranked by cost per accepted slice from agent-lb request logs (2026-09-28).

