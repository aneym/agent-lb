# Open Factory program log

[Program](PROGRAM.md)

- 2026-09-25: plan written. Keel/Jev digest at `~/.agent-lb/of/keel-jev-digest.md`.
- 2026-09-25: eval moved onto Harbor (D5-D9) at Alex's request via the fixer.
- 2026-09-25: M3 Harbor gate passed (`~/.agent-lb/of/harbor-feasibility.md`). OrbStack
  containers reach agent-lb at `host.docker.internal:2455` (loopback-forwarded, so agent-lb
  treats them as local and keyless; attribution is by sessionId via /api/request-logs).
  claude-code/claude-sonnet-5 reward 1.0, 69,967 in / 34,904 cache-read, one Max account,
  second request cache-read (LB cost $0.088 -> $0.0076); codex/gpt-6-luna reward 1.0 on one
  Pro account. Flags: `--ae ANTHROPIC_BASE_URL=... --ae ANTHROPIC_AUTH_TOKEN=...` /
  `--ae OPENAI_BASE_URL=.../v1`; full model ids. Agent install is 3-6 min of each trial, so
  CLIs get pre-baked into images and install-skipping agent subclasses before any sweep.
  The `of-harbor` key (7d6b0004) exists for non-local paths (tailnet, apple-container);
  lb-harbor-access (fixer) owns it. Old of-wt-harbor adapter is dead; A3 subclasses the
  built-in ClaudeCode agent. Public dataset: terminal-bench@2.0 (89 tasks; published
  Claude Code and Codex numbers exist), terminal-bench-sample@2.0 (10) for iteration.
  cursor-cli and devin cannot route through agent-lb and need CURSOR_API_KEY/DEVIN_API_KEY;
  grok-build can run Claude/GPT through agent-lb, native Grok needs an xAI key. These
  keys are requested from Alex only when A5/A6 are next.
- 2026-09-25: fixer landed dynamic GPT aliases (agent-lb 3f80283f): `/v1/messages` takes any
  served `gpt-*` plus `sol-latest`/`luna-latest` with optional effort suffix; unserved names
  get a 400 before upstream. Retired slugs still route when named, so `route` keeps its
  retirement check. Effort (Q2) is now a per-call model-name suffix for GPT seats.
- 2026-09-25: fixer's lb-harbor-access landed agent-lb 561e12eb: sk-clb keys are honored
  from loopback/containers (x-api-key too), so every trial sends `of-harbor`
  (`~/.agent-lb/of/of-harbor.key`). Plain base URL, no launcher: Max pool, one account per
  session, 100% cache-read after the first call, host and OrbStack; gpt-6-sol-low through
  the bridge 99% cached. Receipts: `~/.agent-lb/runtime/agent-lb/scripts/request_log_query.py
  --session <id> | --key of-harbor --json` (account, pool, model, tokens, cache, latency).
- 2026-09-25: in-session GPT seats `gpt-explorer` (gpt-6-luna) and `gpt-implementer`
  (gpt-6-sol) appeared; they join Q1 as seats.
- 2026-09-25: fixer landed the GPT bridge fix (444b5a66, ccgpt e2e PASS: Sol/Luna 13 s,
  Sonnet driver + gpt-6-sol subagent 15 s). GPT-inside-Claude-Code is unblocked: A3 gains
  in-session GPT seats (Agent/`agent()` with `model: gpt-6-*`) beside the codex-sol
  forwarder, measured as separate seats since their startup cost differs (D12).
- 2026-09-25: standing Workflow opt-in recorded. Planned uses: M4 fan-out converting dev
  candidates into Harbor task dirs (sonnet build + haiku oracle-run check per task), M5/M7
  arm batches, D9 replay sweeps.
- 2026-09-25: routing questions become eval arms (Q1-Q10); startup token cost added (D12).
- 2026-09-25: milestone 2 landed. `open-factory route` measured live: Jev 370-750 ms,
  ~$0.00005 per pick. Observation for the eval: the routing table still puts `codex-sol`
  first for explore/research although ROUTING.md (dfe1ea2d) made Claude the default
  worker; A3's static sub-arm uses the table as it is, and the eval decides.
- 2026-09-25: of-work v0 frozen. 59 verified FAIL_TO_PASS tasks.
  - Dev: 34, at `~/.agent-lb/of/dataset/v0/dev.json`, read-only, sha256 `8268fdd59dca2cf0f21c246e21ea0ea7acc43cdb40f898defdf1c7e9f35fa0e0`. agent-lb has 21: 15 bugfix, 5 feature, 1 mechanical. agent-rails has 13: 5 bugfix, 8 feature. Difficulty 1:3, 2:15, 3:12, 4:4.
  - Held-out: 25, sealed in `~/.agent-lb/of/heldout/frozen-v0/` and never read by the orchestrator. `candidates.json` sha256 `9cf074686834cacb843b426c73e35d3538de2f008f2317b8344290ce5e2bbcef`. `MANIFEST.sha256` sha256 `4fdaed5431dedbe9f470b16dc2f0b524402caf1a2a308da422e44aa05d286fd0`. A different hash at M8 means the seal was broken. These supersede the b5ac6019 hashes, which were taken while mining lanes were still racing.
  - Checks: split rule holds on both sides, no overlap, complete schema.
  - Verification is uneven: the miner re-ran only 2 of 59 end-to-end and the rest rest on logged subagent runs. So M4's oracle stage re-verifies every dev task in its container (no solution means FAIL_TO_PASS fails; the fix makes all pass), and M8 does the same for held-out through a sealed seat before scoring.
  - No refactor class survived verification. Q1's per-class answers cover bugfix/feature only in v0.
  - Leak rule: agent-lb (and codex-lb) is PUBLIC on GitHub. A trial with open egress could fetch the fix. Trials get egress only to agent-lb, or else every trial's agent log is scanned for fetches of the repo, and a hit marks that trial contaminated and excludes it from scoring.
  - Process note: the miner fanned out to forks, which inherit the whole brief. Several acted as the whole task and raced on shared files. Future fan-outs use fresh subagents with a slice-only brief and a private output path, never forks.
- 2026-09-25: `route --class implement` picked sonnet-implementer while Codex had room. The menu was right, with gpt-implementer at its head. The cause was the capability blurbs: Jev rated GPT Sol's fit at 0.60-0.69 for plain implementation tasks ("strong coding" versus Sonnet's "lower cost").
  - Fix (decider policy v3): the blurbs now state the seat ladder from models.md, and the chain head is the house default, which the host keeps whenever the decider's fit for it is ≥ 0.8.
  - Live eval `clients/open-factory/evals/decider/` (10 cases: 6 implement, 4 mechanical). Before: 4/10. After: 10/10 in 3 runs, where Jev picked directly in 6-8 of 10 and abstained to the chain head in the rest.
- 2026-09-25: M4 done. `of-work` v0 dev = 33 Harbor tasks (34 frozen; agent-lb-ba15f0a0 dropped because no test fails without its fix inside the image). Pipeline in `evals/of-work/`:
  - `build_tasks.py` writes each task from `dev.json`: the base commit via `of-checkout` on a pinned base image, egress allowlisted to agent-lb, the fix's test diff applied at grading, and reward 1 only if every required test passes.
  - `calibrate.py` re-derives FAIL_TO_PASS/PASS_TO_PASS inside the image from one oracle and one nop pass, SWE-bench style. The miner's host lists did not all hold in the image (host python paths in parametrized ids, tests that need host tools). One task was dropped; three had their lists narrowed.
  - `prebuild.py` builds each task image once under a content-hashed tag. Harbor otherwise rebuilt and deleted it on every trial. Median env setup fell from about 25 s to 7 s, max from 124 s to 41 s.
  - Oracle on the calibrated, prebuilt set: 33/33 at reward 1.0 in 6.5 min (-n 4). Egress sidecars were up during the run.
  - `run_arms.py` + `arms.json` run the M5 arms. `summarize.py` reports cluster-bootstrap CIs, paired differences and agent-lb receipts joined per trial by session id.
  - Egress: Harbor allowlist to host.docker.internal, which the agents narrow to :2455, with web tools off. Probe trials show GitHub, PyPI, git ls-remote and other host ports blocked. `harbor/scan_trials.py` is the second layer.
  - Base images: of-work/agent-lb-base:328b33b849ec and agent-rails-base:7adf4749e52f. They are never deleted or retagged, because a mid-run rebuild that deleted the pinned tags voided the first calibration.
