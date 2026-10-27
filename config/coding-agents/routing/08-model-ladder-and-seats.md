## Model ladder and seats (2026-09-30; updated 2026-10-13)

Best first, and only Claude is precious; use family aliases (`route resolve <alias>`). Alex, 2026-09-30: "use things up that are best always first, using fallbacks when we need to ... the only precious thing i think is claude usage".

| Job | Seat | Model, effort |
|---|---|---|
| Code to a spec | gpt-implementer; grok-implementer; devin-seat; sonnet-implementer | sol-latest medium; grok-latest medium when Codex is short (2026-10-20); sonnet-implementer off while quota is tight (2026-10-13) |
| Fixing a review's numbered must-fix list (trial, lead picks it by hand) | terra-implementer | terra-latest high; never medium, never the default route (terra-eval, 2026-10-27) |
| Opus-written stories: browser steps and mechanical loops | story-driver | deepseek-flash; luna-latest low when OpenRouter has no headroom |
| Mechanical sweeps | gpt-implementer; grok-implementer; devin-seat | sol-latest low; grok-latest-low when Codex is short (2026-10-20) |
| Judgment code, or work a seat failed twice | opus-seat | opus-latest; overrides the 2026-10-13 off-while-tight note (Opus architects, 2026-10-14) |
| UI-critical, contract and trust-boundary code | opus-seat | opus-latest; overrides the 2026-10-13 off-while-tight note (Opus architects, 2026-10-14) |
| Read-only exploration | gpt-explorer | sol-latest low |
| Review, on request only (non-Claude author) | verifier (Sonnet) | sonnet-latest high (2026-10-16, E15) |
| Review, on request only (Claude author) | codex-verifier | sol-latest high; money path keeps xhigh (2026-10-16) |
| Plan and architecture second opinion | astra-consult (one per plan, brief under 2k tokens) | astra-latest high; never sol-consult (2026-10-14) |
| Plans, specs, verdicts, design | Opus (planner, plan-reviewer) | opus-latest medium, never Sol (2026-10-16, E19); under the Claude reserve these wait |
| Tabs Alex talks to, leads, orchestrators | Opus tab; full-tier ultracode on; workflow helpers on Sol | opus-latest medium for top-level tabs and workflow orchestrators; sol-latest for helpers |
| Remote-machine relay (batch templates) | host-relay | sol-latest low |

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

