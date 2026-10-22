# Muse Code research, 2026-10-13

## Status, 2026-10-22

This section supersedes the 2026-10-13 notes below where they disagree. The research below is kept as written.

- **Seat: live, eval-only.** `muse-seat` forwards to Studio-local `muse-seat-local` on `muse-latest`, which resolves to `muse-wonder-1.0`. Real runs go through agent-lb `/muse-api` and land in request logs with caller seat `muse-seat-local` (for example 2026-10-22 13:44Z and 14:16Z, all ok). `route pools` shows the `muse` pool with observed runs and tokens; remaining quota stays null because Muse reports none.
- **Version.** The seat accepts Muse Code 1.4.x at or above `1.4.9-R6297.1` (`MUSE_MIN`) and runs with `MUSE_NO_AUTO_UPDATE=1`. The Meta launcher still self-updates on any plain `muse` call without that variable; Studio moved to `1.4.12-R6909.1` that way on 2026-10-22. The 1.4.7 pin below is history.
- **Login.** Studio's CLI login lives in the login keychain. A seat run must start from a Studio-local session; over ssh the CLI fails with "Keychain is locked or requires approval". Boxes have no Muse login by design.
- **Routing.** Muse is an available seat only (routing 08 and 14). Its explore arms exist but `explore.enabled` is false, so no class ladder picks Muse automatically. A ladder change needs an Opus routing ruling.
- **Evaluation.** Muse Wonder high vs Sol medium, 2026-10-22 (provisional, 6 pairs): both 6/6 accepted; Sol was about 6x cheaper ($0.135 vs $0.803 per accepted) and about 3x faster (119 s vs 374 s). Decision: Muse stays eval-only. Evidence: `~/.open-factory/erun/muse-wonder-vs-sol-2026-10-22/SUMMARY.md` on Studio.
- **Provider `/muse/v1`** (Pi and Sunday, change `muse-provider`). Built and deployed. It reads a Meta Model API key from `~/.agent-lb/secrets/muse-credential.json` (mode 0600) and never the keychain. That file does not exist yet, so every call returns 503 `muse_credential_unavailable`. It is waiting on Alex to create the key.
- **Not done, by decision.** Raise (Muse) was dropped by Alex on 2026-10-16. Box placement stays parked until a scoped credential exists.
- **Follow-ups.** Muse request-log rows carry no `costUsd`; agent-lb should read Muse's own cost ledger. open-factory `muse-install --check` matches only 1.4.9 exactly. It is never called automatically and downgrades nothing without an explicit `--sha256`.

Meta publishes an official Muse Code CLI. Studio installation and login are complete according to team-lead on 2026-10-13 at about 04:22Z. This continuation runs in a Linux box on ax42. It prepares the account adapter, pool and factory launcher; a real Studio run remains unverified.

All web sources below were accessed on 2026-10-13. Publication dates appear where the publisher supplies one. This document is at the path the brief explicitly requested; implementation requirements remain in `openspec/changes/add-muse-code-seat/`.

## Product and source

Muse Code is Meta's terminal and CI coding agent. It edits files and runs commands in a project. Its executable is `muse`, distributed as a native binary for macOS, Linux and Windows. Meta's documented Unix installation command is:

```sh
curl -fsSL https://dev.meta.ai/install.sh | sh
muse --version
```

Source: [Meta CLI overview](https://dev.meta.ai/docs/muse-code), accessed 2026-10-13. The product announcement is [Meta's Muse Code launch post](https://dev.meta.ai/resources/blog/build-with-muse-code), published 2026-08-05, accessed 2026-10-13.

The official public repository is [meta-models/muse-code-sdk](https://github.com/meta-models/muse-code-sdk), accessed 2026-10-13. It contains the SDK and protocol declarations; its README says the native host binary is absent. No public CLI implementation repository was identified. `meta-models/muse-code` returned 404. The npm package `@muse-code/sdk` controls an installed host and does not install the CLI. Its [source package manifest](https://raw.githubusercontent.com/meta-models/muse-code-sdk/main/clients/sdk-ts/package.json) currently says SDK version `1.4.7`, accessed 2026-10-13. That is distinct from the native CLI version.

## Version and install pin

Team-lead supplied the Studio executable path `/Users/aneyman/.local/bin/muse`
and its output `Muse Code 1.4.7 (1.4.7-R6101.1)` on 2026-10-13. The required
native build pin is `1.4.7-R6101.1`; it replaces the previous proposed 1.4.6 pin.
The supplied, ANSI-stripped help is committed in [muse-help.txt](muse-help.txt).
No binary checksum for this build was supplied or measured here.

The prepared open-factory installer writes the native binary to
`~/.agent-rails/tools/muse/1.4.7-R6101.1/muse` and links
`~/.agent-rails/tools/bin/muse`. Its `box-tools install-muse` command requires an
independently verified platform SHA-256 and checks the exact version before
publishing the executable. An offline artifact is supported. No host was installed.

Meta's download template comes from the [Homebrew manifest](https://raw.githubusercontent.com/Homebrew/homebrew-cask/master/Casks/m/muse-code.rb), accessed 2026-10-13. The prepared installer uses the build-specific URL with `muse-x86-linux`, `muse-aarch64-linux`, `muse-x86-macos` or `muse-aarch64-macos`. The new build's download has not been tested. A 1.4.6 checksum must never be reused for 1.4.7.

## Authentication and billing

Muse supports browser approval through a Meta account or an API key. Headless runs use `META_API_KEY`. Priority is the environment key, stored key, then stored browser session. `muse auth set` stores a key. The documented browser flow does not specify its OAuth grant or custody format; OAuth 2.1 in the MCP guide concerns remote MCP servers, not Meta model authentication. Source: [Meta authentication and billing](https://dev.meta.ai/docs/muse-code/auth), accessed 2026-10-13.

A subscription requires a Meta Account and applies to the key connected during Muse CLI onboarding. Additional API keys are pay-as-you-go. Subscriptions default to Contributor mode, which permits training on submitted content; Standard mode disables that use for future content. Provisioning must preserve the subscription credential and select Standard mode before repository content is sent. Source: [Meta subscriptions](https://dev.meta.ai/docs/muse-code/subscriptions), accessed 2026-10-13.

Alex reports Studio login complete. No owner.ask is needed for Studio, and no
credential or Muse settings file was opened. This box has no Muse executable;
the inherited credential ask is retained only for box provisioning. It is an
unfiled payload, not a ticket. Boxes remain not-yet until their secret store
provides `META_API_KEY`; the Studio browser session is never copied or leased.

The adapter registers one Muse account per host. On macOS it uses the existing
login and removes `META_API_KEY` so that an environment key cannot override the
session. On Linux registration requires a provisioned environment key, checked
for presence only. There is no supported auth-status subcommand in the supplied
help; `seat probe --vendor muse` reports unknown. Successful work establishes
observed account health without opening credentials.

## Models and endpoints

The current Meta catalog lists `muse-wonder-1.0` for Muse Code and API access. Muse Wonder Full is available only in Muse Code on Power Usage and Maximum Usage subscriptions; its CLI model ID is not established by the catalog. Muse Code also supports Spark 1.3 and 1.2, including their Contributor variants; Spark 1.1 is API-only. This change proposes `muse-latest` resolving to `muse-wonder-1.0`. Resolution does not prove account eligibility. Source: [Meta model catalog](https://dev.meta.ai/docs/models), accessed 2026-10-13.

The docs still name `muse-spark-1.2` as the CLI default. `--model` overrides it. Both headless and interactive runs accept `--workspace`, `--reasoning-effort` and `--trust-workspace`. Configuration lives at `~/.config/muse/settings.json`. Source: [Meta CLI configuration](https://dev.meta.ai/docs/muse-code/configuration), accessed 2026-10-13.

Meta's API supports Responses and Chat Completions at `https://api.meta.ai/v1`, and Anthropic-format Messages at `https://api.meta.ai`. Third-party agent examples use `MODEL_API_KEY`; the native Muse CLI documents `META_API_KEY`. Source: [Meta coding-agent guide](https://dev.meta.ai/docs/coding-agents), accessed 2026-10-13. The supplied 1.4.7 help confirms `--base-url`; no auth-status subcommand appears. The adapter uses the native backend under the existing session, following the Cursor and Devin CLI-seat precedent. This is not an HTTP model proxy.

## Headless execution and returned diffs

`muse exec` runs one prompt to completion. `--prompt-file` reads the contract; `--json` emits JSONL. `--disable-approval` retains the sandbox; `--yolo` also disables it. `--max-model-steps` caps work. A session can resume with `exec --session-id`, and `muse export --session <uuid> --out run.json` exports its log. Exit 0 means the turn ended, not that its checks passed. Source: [Meta automation guide](https://dev.meta.ai/docs/muse-code/extending), accessed 2026-10-13.

A documented launch shape is:

```sh
muse exec --workspace <scratch-repo> --model muse-wonder-1.0 \
  --trust-workspace --disable-approval --max-model-steps 8 \
  --json --prompt-file <contract>
```

This command has not been executed against the real Muse binary here. The prepared adapter uses `--approval-mode never`, retains sandboxing, sets `--contributor-mode off`, and assigns `--session-id <uuid>`. Read-only mode additionally disables writes and shell calls. The docs establish file edits, not a standalone patch-return flag. The factory runner must collect the working-tree diff, including a newly created file. Capture a real redacted JSONL run before writing its parser; the SDK protocol's token notifications do not establish the CLI JSONL format.

## Usage and limits

The API returns request and token limit/remaining headers on successful token responses. Limits apply per team and model, not per API key. Current standard Wonder and Spark 1.3 API limits are 3,000 requests/minute and 4,000,000 tokens/minute. They are not subscription prompt budgets. No supported machine-readable subscription quota endpoint was established. Unmeasured pool values must remain unknown. Source: [Meta pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits), accessed 2026-10-13.

## Local evidence and remaining work

The previous agent-lb commit `b8af7d2a4fe1a53bd31ef9e72aa61b44c7603278` is the
starting HEAD of this continuation. It already contains the resolver and guard.
The supplied Studio path is absent, and `uname` reports Linux on ax42.
`ssh -o BatchMode=yes -o ConnectTimeout=8 studio` failed with
`socket: Operation not permitted` on 2026-10-13. This environment cannot finish
Studio execution, restart its LB, or create its Herdr tab.

A separate open-factory checkout was prepared under this worktree's `tmp/` from
local `origin/main` at `f7b028462`. The explicitly named Studio worktree is
absent; using the writable local checkout is a recorded deviation. Its commit
and git bundle can be transferred to Studio without pushing. No main checkout,
lane, runtime tool install or pinned-tab file was changed.

`clients/seat` invokes pinned plain `muse exec`, records the assigned session UUID
and result, counts runs, and applies vendor-limit cooldowns. `/api/pools` exposes
`muse` when registered. Remaining percentages and token totals stay null.
JSONL parsing is deferred until a real 1.4.7 transcript establishes the event
format. The seat ledger is `dispatch.jsonl`, the same dispatch/closeout path as
Cursor and Devin. This is not yet a verified LB HTTP request-log row.

`muse-seat` and `muse-seat-local` forward the scoped brief to `seat-exec`, run
in place on Studio. Studio registration is `seat add muse-studio
--vendor muse`; subsequent runs use `muse-latest`. Muse is optional capacity;
default class ladders are unchanged. Factory `seat-exec` delegates to agent-lb's
`seat run`, so account settlement stays in agent-lb. The local seat refuses Linux.

The new subprocess checks exercise pinned installation, checksum and version
rejection, the factory launcher, real registry/state/ledger files, plain-output
settlement, model resolution and vendor cooldown. Their Muse executable is a
stand-in. These checks do not prove the real native CLI or existing login works.

Phase 4 remains parked. On Studio, after the real scratch task passes, inspect
Raise's agent package and launch configuration without exposing values in MCP
headers or environment fields. Create `~/.agent-rails/agents/raise-muse` beside
Raise, share memory when supported, preserve its tools and MCP names, add its tab
to `pin-keep.txt`, then capture a status answer and the live MCP inventory.
No Raise package or pin file exists at the supplied paths in this environment;
no tab was created and no tab ID is available.

Official web pages were rechecked using the browser tool on 2026-10-13.
Agent Reach's GitHub fallback `gh search repos muse-code-sdk --limit 2` failed
at the network edge. Agent Reach and mcporter are absent. The browser tool
reached Meta documentation and the SDK manifest; shell downloads and SSH remain
unavailable.
