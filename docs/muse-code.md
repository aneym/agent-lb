# Muse Code research, 2026-10-13

Meta publishes an official Muse Code CLI. Research is complete; installation and end-to-end integration are blocked on this Linux worker. The local changes provide the model resolver and personal-login guard only.

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

The latest heading in the [official CLI changelog](https://dev.meta.ai/docs/muse-code/changelog) is `1.4.6`, accessed 2026-10-13; it supplies no release date for that heading. The [Homebrew distribution manifest](https://raw.githubusercontent.com/Homebrew/homebrew-cask/master/Casks/m/muse-code.rb), accessed 2026-10-13, identifies build `1.4.6-R6000.1` and Meta's download URL template:

```text
https://lookaside.facebook.com/lookaside/muse/download/?channel=muse&version=1.4.6-R6000.1&file=muse-<arch>-<os>
```

The manifest's platform checksums are:

| Artifact | SHA-256 |
|---|---|
| muse-aarch64-macos | c713165a287d64afb8816f8351a240b457a23524f4ade06bca0e41dfde410460 |
| muse-x86-macos | 2a4ecde03c43bd81c01d7dbd978c894db940c5f6e235e3e3da503376bd0fb45f |
| muse-aarch64-linux | fdbb7f1a85d5fa0727253726785a940e86bdbb5f3885bb0ed182657781f5aa93 |
| muse-x86-linux | 82a495542965a5b6256f7acdb81693451c28640a419d5528ad8340ea93f36f6f |

These are reported checksums, not hashes measured from downloaded artifacts. The stable channel endpoint named in that manifest, `https://api.meta.ai/muse-code/channels/muse-stable`, could not be read. The build is a proposed reproducible pin; it has not been installed or executed here. The unversioned one-line installer alone does not establish a durable pin.

## Authentication and billing

Muse supports browser approval through a Meta account or an API key. Headless runs use `META_API_KEY`. Priority is the environment key, stored key, then stored browser session. `muse auth set` stores a key. The documented browser flow does not specify its OAuth grant or custody format; OAuth 2.1 in the MCP guide concerns remote MCP servers, not Meta model authentication. Source: [Meta authentication and billing](https://dev.meta.ai/docs/muse-code/auth), accessed 2026-10-13.

A subscription requires a Meta Account and applies to the key connected during Muse CLI onboarding. Additional API keys are pay-as-you-go. Subscriptions default to Contributor mode, which permits training on submitted content; Standard mode disables that use for future content. Provisioning must preserve the subscription credential and select Standard mode before repository content is sent. Source: [Meta subscriptions](https://dev.meta.ai/docs/muse-code/subscriptions), accessed 2026-10-13.

Auth status here is unverified. No credential file was opened and no sign-in was attempted. `muse` is absent from PATH. The credential request in `docs/muse-code-ask.json` is an unfiled owner.ask arguments document, not a ticket or approval.

## Models and endpoints

The current Meta catalog lists `muse-wonder-1.0` for Muse Code and API access. Muse Wonder Full is available only in Muse Code on Power Usage and Maximum Usage subscriptions; its CLI model ID is not established by the catalog. Muse Code also supports Spark 1.3 and 1.2, including their Contributor variants; Spark 1.1 is API-only. This change proposes `muse-latest` resolving to `muse-wonder-1.0`. Resolution does not prove account eligibility. Source: [Meta model catalog](https://dev.meta.ai/docs/models), accessed 2026-10-13.

The docs still name `muse-spark-1.2` as the CLI default. `--model` overrides it. Both headless and interactive runs accept `--workspace`, `--reasoning-effort` and `--trust-workspace`. Configuration lives at `~/.config/muse/settings.json`. Source: [Meta CLI configuration](https://dev.meta.ai/docs/muse-code/configuration), accessed 2026-10-13.

Meta's API supports Responses and Chat Completions at `https://api.meta.ai/v1`, and Anthropic-format Messages at `https://api.meta.ai`. Third-party agent examples use `MODEL_API_KEY`; the native Muse CLI documents `META_API_KEY`. Source: [Meta coding-agent guide](https://dev.meta.ai/docs/coding-agents), accessed 2026-10-13. No official custom-endpoint or auth-status CLI contract was established. Proxy configuration and credential forwarding must be checked against the pinned executable before implementation.

## Headless execution and returned diffs

`muse exec` runs one prompt to completion. `--prompt-file` reads the contract; `--json` emits JSONL. `--disable-approval` retains the sandbox; `--yolo` also disables it. `--max-model-steps` caps work. A session can resume with `exec --session-id`, and `muse export --session <uuid> --out run.json` exports its log. Exit 0 means the turn ended, not that its checks passed. Source: [Meta automation guide](https://dev.meta.ai/docs/muse-code/extending), accessed 2026-10-13.

A documented launch shape is:

```sh
muse exec --workspace <scratch-repo> --model muse-wonder-1.0 \
  --trust-workspace --disable-approval --max-model-steps 8 \
  --json --prompt-file <contract>
```

This command has not been executed. The docs establish file edits, not a standalone patch-return flag. The factory runner must collect the working-tree diff, including a newly created file. Capture a real redacted JSONL run before writing its parser; the SDK protocol's token notifications do not establish the CLI JSONL format.

## Usage and limits

The API returns request and token limit/remaining headers on successful token responses. Limits apply per team and model, not per API key. Current standard Wonder and Spark 1.3 API limits are 3,000 requests/minute and 4,000,000 tokens/minute. They are not subscription prompt budgets. No supported machine-readable subscription quota endpoint was established. Unmeasured pool values must remain unknown. Source: [Meta pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits), accessed 2026-10-13.

## Local evidence and remaining work

The authorized Studio open-factory worktree is absent on this host. Read-only inspection used local `origin/main` at `5a5c771ac99ae0881ca7e8ecd983064c3e160096`. Its `engine/bin/box-tools` checks durable tools outside factory-runtime; `engine/bin/seat-exec` maps seats to harnesses and collects worktree changes. Those files must be extended in the authorized checkout, not installed by hand into factory-runtime. Local git evidence inspected 2026-10-13.

Shell requests failed with `Could not resolve host: dev.meta.ai` and `Could not resolve host: github.com`. The browser research tool could read documentation, but did not provide an executable download. agent-reach and mcporter are absent; the agent-reach GitHub fallback `gh search repos` also failed at the network edge. Fresh origin commits could not be fetched. These observations date to 2026-10-13.

Pending work is listed in `openspec/changes/add-muse-code-seat/tasks.md`: durable installation, authentication, account adapter and failover, usage and limit readback, pool exposure, seat-run forwarding, policy installation, ROUTING.md availability, real scratch diff and request-log receipt, and the open-factory commit. No Muse seat is advertised as available yet. The current resolver and guard checks use subprocess boundaries and do not claim live Muse proof.

Searches included `Meta "Muse Code" CLI`, `Meta "muse-code" github`, Meta Muse coding agent CLI official, official auth/status and quota queries, and the official SDK repository. They established the real Meta product, so the brief's nonexistent-product stop condition does not apply.
