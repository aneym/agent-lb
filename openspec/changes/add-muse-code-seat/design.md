## Context

Alex reports Studio Muse installed and logged in on 2026-10-13 at about 04:22Z.
The supplied native help and version pin establish the exec options. This run
is a Linux box on ax42, with Studio SSH blocked by the sandbox. The earlier
resolver/guard commit is present. Research remains at the explicitly requested
`docs/muse-code.md` path.

## Decisions

Extend `clients/seat` and the CLI-seat pool reader, following Cursor and Devin.
Do not guess JSONL fields. Plain exec has a documented exit contract; assign a
session UUID and retain null tokens pending a real transcript. Count observed
runs and process vendor-limit errors through existing cooldown settlement.
Muse percentages remain unknown; no API rate limit becomes a subscription quota.

Use Alex's existing macOS login without reading settings or credentials. Clear
the environment API key for a login account. Linux needs a provisioned key and
never receives a browser session lease. Registration does not claim an auth
probe, since the pinned help has no auth-status command. Runs check the exact
build before launch, trust the assigned workspace, retain the sandbox and
select Contributor mode off. Read-only mode disables writes and shell calls.

Add optional seat definitions and policy installation. Do not change class
ladders. Seat forwarding uses `seat-run --box studio` followed by `seat-exec`,
which calls agent-lb's account runner. With no Muse ladder, do not pass --class.

Prepare open-factory in an isolated checkout under the writable workspace,
since the named Studio worktree is absent. The installer requires a verified
platform checksum and exact version before replacing the durable executable.
This pin has no measured real artifact checksum yet, so no installation occurs.

## Validation and remaining work

Subprocess tests prove installation rejection, launch forwarding, settlement,
model resolution and cooldowns with a vendor stand-in. The real native run,
returned scratch diff, LB request-log receipt and Studio endpoint health remain
pending. The CLI ledger is distinct from the HTTP request log. Review remains
pending because the brief prohibits agent messages and pushes.

Phase 4 follows the real seat run. Read Raise's package and launch configuration,
share its memory if supported, create Raise (Muse) beside the existing tab and
capture its memory answer and MCP tool list. This box cannot access those files
or the Herdr runtime. Never fabricate a tab ID or claim the phase complete.
