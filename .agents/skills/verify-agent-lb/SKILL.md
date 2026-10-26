---
name: verify-agent-lb
description: Verify the agent-lb operator's three primary journeys with isolated identities and SHA-keyed evidence; report unavailable isolation as blocked.
---

# Verify agent-lb

## Launch

Work from the checked-out candidate repo. Read `AGENTS.md`, `PRODUCT.md`,
`products.json` and `features/README.md`. Resolve `git rev-parse HEAD` before
collecting evidence. Use the installed factory tools:

```sh
BIN=~/.agent-rails/factory-runtime/bin
"$BIN/feature-map" check --repo .
```

Do not launch or restart the operator's service, open its dashboard session,
import subscription credentials, or use `~/.agent-lb/store.db`. Ordinary service
installation instructions in GETTING-STARTED.md are not proof setup instructions.
Journeys run on the configured non-Studio proof host through `feature-map run`.
There is currently no isolated agent-lb launcher/seed backend. No live instance
is needed to record that blocker. Never substitute the serving localhost port.

## Doctor

Confirm the checkout and dependencies without reading credentials:

```sh
git rev-parse --show-toplevel
git rev-parse HEAD
python3 --version
BIN=~/.agent-rails/factory-runtime/bin
"$BIN/feature-map" check --repo .
```

Check each identity backend with `test-identity SERVICE --repo . --json`, where
SERVICE is `agent-lb`, `openai`, or `anthropic`. Each currently returns exit 3
and `status: unavailable`, with no identity, environment or cleanup command.
That is a diagnosed blocker, not a passing journey. Do not convert it to exit 0.
Missing CLI or invalid backend output is an error, not an identity grant.
The seed scripts never contact providers or touch the serving database.

Before enabling actual drives, the proof host must provide a fresh instance,
fixture-only database, synthetic request logs, a disposable provider identity,
an enforced spend cap, outbound containment, and cleanup for all allocated
state. No such backend is supplied by this slice. Prod verification, if later
needed, requires `agent-test-account --lane weekend-finish`; this does not
provision OpenAI or Anthropic identities or authorize operator credentials.

## Drive

The top three journeys cover the product's core proxy and the operator's
account and failure-investigation workflows. They are declared as `journey`
proofs with network and test-data effects. The current driver checks identity
availability and exits 3 without doing any live work. It is not an HTTP test
and cannot prove runtime behavior. When backends become available, replace
that blocker with an audited driver that performs these acceptance paths:

| Feature / proof | Intended acceptance path |
|---|---|
| `proxy / provider-response` | Send a bounded request through the disposable proxy with a disposable provider identity; observe a successful complete response and matching request log attributed to the seeded account. Run separately for OpenAI and Anthropic; never use operator subscriptions. |
| `accounts / pause-resume-account` | List the seeded account, pause it, confirm it is not routable, reactivate it, and confirm only the seeded account is eligible again. Do not export auth, probe a real account, or change other pool members. |
| `request-logs / inspect-failed-request` | Find a seeded failed request by its unique correlation id, filter by account/status/model, and verify the selected row's failure reason matches the fixture and excludes unrelated rows. Use synthetic logs, never operator request bodies. |

Record current attempts, one command at a time:

```sh
BIN=~/.agent-rails/factory-runtime/bin
"$BIN/feature-map" run --repo . --feature agent-lb/proxy --proof provider-response --env sandbox
"$BIN/feature-map" run --repo . --feature agent-lb/accounts --proof pause-resume-account --env sandbox
"$BIN/feature-map" run --repo . --feature agent-lb/request-logs --proof inspect-failed-request --env sandbox
```

Expected proof-host result: exit 3, blocked. Capture each command's return
code; do not chain these commands with `&&`, suppress failures, or mark a
blocked run as verified. The runner blocks before invoking the driver because
its identity lifecycle and side-effect isolation are unavailable. Studio
delegates journeys rather than driving them locally. Do not bypass that routing.

Studio delegation at `f9972aa29a7d5965d23093a355fa171fe1712007` recorded
`fail`, not `blocked`: `feature-map` was absent from the proof host's PATH.
A delegation failure is not evidence that a journey ran. Ship this repo with
`seat-run --box forge --ship "$PWD"` and invoke
`~/.agent-rails/factory-runtime/bin/feature-map run` on that configured proof
host, with the same feature, proof, SHA and environment
arguments above. This preserves the journey kind and declared side effects.
A blocked attempt can still report `record retained in spool; Studio
acknowledgement unavailable`; verify its immutable record and synchronize it
before claiming durable evidence. Never relabel the earlier failing records.

## Evidence

```sh
BIN=~/.agent-rails/factory-runtime/bin
"$BIN/feature-map" runs --repo . --latest --json | python3 -c 'import json,sys; records=json.load(sys.stdin)["records"]; assert len(records)>=3; print(str(len(records))+" journey records")'
"$BIN/feature-map" runs --repo . --sha "$(git rev-parse HEAD)" --history --json
```

The JSON document has a `records` array; count that array, not top-level keys.
Keep result, feature, proof, SHA, environment, timestamps and evidence references.
A blocked record documents an attempt, not successful regression coverage.
Require three distinct proof ids at the exact landed SHA; rerun after rebasing
or landing if the SHA changes. Never manufacture records or change a proof to
force a pass. The canonical store is `~/.agent-rails/runs/agent-lb/` on Studio;
workers spool until acknowledged. Use `feature-map runs --flush` for unacked
records. Inspect referenced summary evidence for the blocker reason; never
copy provider payloads, tokens or operator data into reports.

## Cleanup

Unavailable backends allocate nothing and return `cleanup_cmd: null`.
Do not delete any operator data or reset provider accounts. The runner owns
its temporary checkout and sandbox cleanup; verify `cleanup_ok` in referenced
evidence. A future successful seed must return an explicit cleanup argv, and
the isolated driver must invoke it in a finally path even after timeout or
failure. Do not advertise those backends as available before cleanup exists.
Keep append-only run records and their evidence. Never clean the durable store
or erase blocked/failing attempts to make the latest view green.
