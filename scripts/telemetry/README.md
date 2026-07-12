# Log telemetry (agent-lb.err.log, agent-lb.out.log, front logs, watchdog logs, etc.)

Every `~/.agent-lb/*.log` file on this machine and on `studio` is tailed by
[vector](https://vector.dev) and shipped to **PostHog Logs** (OTLP/HTTP,
project "Prove It", id 154276) as one queryable store, labeled by `instance`
(`macbook` | `studio`) and `file`. The shipper (`com.aneyman.agent-lb-vector`)
is a standalone launchd job — it does not depend on `agent-lb` or
`agent-lb-front` and survives their restarts.

## Why PostHog Logs

PostHog Logs is a real, documented product (not a beta/preview feature): it's
a generic OTLP receiver at `https://us.i.posthog.com/i/v1/logs`, auth via the
project token (`phc_...`, public/embeddable — not a personal API key) as a
Bearer header or `?token=` query param. First 10GB/month free, then
$0.25/GB; 14-day retention by default (source:
https://posthog.com/docs/logs, https://posthog.com/docs/logs/start-here,
https://posthog.com/docs/logs/installation/other — fetched 2026-07-11).

## Why vector's plain `http` sink, not the native `opentelemetry` sink

Vector ships a purpose-built `opentelemetry` sink (`encoding.codec: otlp`)
that natively batches multiple minimal OTLP requests into one. In testing,
that native batch-merge **silently dropped events** whenever multiple lines
shared the same resource/scope (which every line here does, by design) — see
vector's own docs warning and https://github.com/vectordotdev/vector/issues/22054.
The fix: use the plain `http` sink type with `encoding.codec: otlp` (still
protobuf, which PostHog's endpoint requires — `codec: json` gets HTTP 400)
but `batch.max_events: 1`, so there is never more than one event for the
encoder to merge. Confirmed via repeated marker tests: the native sink lost
marker lines mixed into concurrent traffic; the `batch.max_events: 1` http
sink delivered every marker across dozens of trials.

## Known limitation: ingest time only, not original line timestamp

An earlier version of the VRL transform (`scripts/telemetry/vector.yaml`,
`shape_otlp`) parsed a leading ISO timestamp out of each line (e.g.
`2026-07-11T21:44:54Z ...`) and used it as `timeUnixNano`. Testing showed
this causes **silent, 100%-reproducible drops**: every event whose
`timeUnixNano` came from a parsed line timestamp vanished after ingestion (no
vector-side error, HTTP 200, but never queryable), while events using
`timeUnixNano == observedTimeUnixNano` (ingest time) always arrived. The root
cause is on PostHog's ingestion side and wasn't isolated further (no
visibility into their server-side handling from here). The transform now
always uses ingest time for both fields, trading original-timestamp fidelity
for guaranteed delivery — the higher priority. If original-timestamp
ordering becomes important, this is the place to revisit (perhaps via a
PostHog support ticket referencing this behavior), not something to re-add
casually.

## How to query

Via the PostHog MCP (`mcp__posthog__query-logs`), from any Claude session
with the `posthog` MCP connected:

```
call query-logs {"query": {"serviceNames": ["agent-lb"], "dateRange": {"date_from": "-1h"}, "orderBy": "latest", "limit": 50}}
```

Filter to one machine:

```
call query-logs {"query": {"serviceNames": ["agent-lb"], "filterGroup": [{"key": "instance", "type": "log_attribute", "operator": "exact", "value": "studio"}], "dateRange": {"date_from": "-1h"}}}
```

Filter to one file:

```
call query-logs {"query": {"serviceNames": ["agent-lb"], "filterGroup": [{"key": "file", "type": "log_attribute", "operator": "exact", "value": "/Users/aneyman/.agent-lb/watchdog.log"}], "dateRange": {"date_from": "-1h"}}}
```

Full-text search body content — **note**: text-filtered search
(`searchTerm` / `type: "log"` with `icontains`/`regex`) appeared to lag or
miss very recent lines in testing, even when the same lines were immediately
visible via unfiltered `instance`/`file`/timestamp-only queries. For "did
this just happen" debugging, prefer a narrow `dateRange` with no text filter
and scan the `body` field yourself; use text search for older/settled data.

Without MCP, curl the REST API directly (needs a personal API key, `phx_...`,
which is NOT embedded anywhere in this repo):

```
curl -s "https://us.i.posthog.com/api/projects/154276/logs/query/" \
  -H "Authorization: Bearer $POSTHOG_PERSONAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": {"serviceNames": ["agent-lb"], "dateRange": {"date_from": "-1h"}}}'
```

## Checking shipper health

```
# macbook
launchctl print gui/$(id -u)/com.aneyman.agent-lb-vector | grep -E "state|pid"
tail -n 50 ~/.agent-lb/vector.err.log

# studio
ssh studio 'launchctl print gui/$(id -u)/com.aneyman.agent-lb-vector | grep -E "state|pid"'
ssh studio 'tail -n 50 ~/.agent-lb/vector.err.log'
```

`vector.err.log` is where vector's own structured logs land (INFO/WARN/ERROR
— the file's name is a launchd convention, not a sign of trouble by itself).
Repeated identical error lines get rate-limited/suppressed after the first
few — a burst of `Failed serializing frame` / `Bad Request` errors on
startup that then goes silent means the config was broken and got fixed by a
later edit; it's not still happening unless the errors keep recurring.

## Where buffers/checkpoints live

- `~/.agent-lb/vector-data/` — file-read checkpoints (per-file byte offset,
  survives restarts) and the disk-backed send buffer (`buffer.type: disk`,
  256MB cap) that absorbs PostHog outages without losing data — vector
  retries from the buffer once the endpoint is reachable again.
- `~/.agent-lb/vector.out.log` / `~/.agent-lb/vector.err.log` — the
  shipper's own stdout/stderr (launchd `StandardOutPath`/`StandardErrorPath`).

## Reinstalling / updating config

Edit `scripts/telemetry/vector.yaml`, then on each machine:

```
scripts/telemetry/install-log-shipper.sh
```

It's idempotent (validates the config before installing, bootout+bootstrap,
safe to re-run). `INSTANCE` is auto-detected from hostname (falls back to
`macbook` unless the hostname contains "studio"); override with
`AGENT_LB_TELEMETRY_INSTANCE=<name>` if a machine's hostname doesn't match.

Uninstall: `scripts/telemetry/install-log-shipper.sh --uninstall`.
