# Public LLM-Usage Page — Architecture & Runbook

Internal runbook for the public portfolio usage page. It is backed by **anonymized
aggregate** LLM-usage data sourced from the codex-lb home server (Studio), published
to a public GitHub repo on a schedule, and read by the portfolio website from
`raw.githubusercontent.com`.

**Core invariant: the home server (Studio) is NEVER exposed to the internet.**
There is no inbound port, no public API endpoint, no API key, and no tunnel
serving Studio to the world. The only thing that crosses the trust boundary is a
small JSON file of rolled-up numbers, pushed *outbound* by Studio over SSH to a
public repo. The website only ever talks to GitHub's CDN.

---

## 1. Goal

A public portfolio usage page that shows real LLM-usage trends (cost, tokens,
requests, per-model and per-provider breakdowns) without:

- exposing Studio to any inbound traffic,
- publishing any raw request data, tokens/credentials, account IDs, emails, API
  keys, request/response bodies, IPs, or error text,
- requiring the website to hold an API key or call a private host.

What is public is strictly **aggregate** and **anonymized**: daily totals,
per-model rollups, and per-provider rollups. Nothing that can be tied back to a
person, account, or request.

---

## 2. Data flow (end to end)

```
                          STUDIO (home server, no inbound exposure)
  ┌─────────────────────────────────────────────────────────────────────┐
  │  request_logs (canonical) ── ~/.codex-lb/store.db                     │
  │            │                                                          │
  │            ▼                                                          │
  │  codex-lb GET /api/usage/public?days=N   (localhost only)            │
  │   → anonymized aggregates (totals / daily / by_model / by_provider)  │
  │            │                                                          │
  │            ▼                                                          │
  │  launchd publisher (every 900s)                                      │
  │   ~/.codex-lb/bin/llm-usage-publish.sh                               │
  │   • curl localhost endpoint for each window → usage-<N>.json         │
  │   • git commit in working clone ~/.codex-lb/llm-usage-repo           │
  │   • git push via repo-scoped SSH deploy key (OUTBOUND only)          │
  └───────────────────────────────────┬─────────────────────────────────┘
                                       │  (outbound SSH, deploy key)
                                       ▼
                       GitHub public repo  aneym/llm-usage
                       main: usage-7.json, usage-30.json, usage-365.json, …
                                       │
                                       ▼  (raw CDN, ~5min cache, CORS *)
       Website fetch:  https://raw.githubusercontent.com/aneym/llm-usage/main/usage-365.json
```

Key properties:

- **No inbound exposure.** Studio initiates an *outbound* SSH push. Nothing
  listens for the internet. The codex-lb HTTP server binds locally and the
  `/api/usage/public` route is only reached by the publisher over `localhost`.
- **No API key on the website.** The site reads a static JSON file from GitHub's
  raw CDN. The CDN serves `Access-Control-Allow-Origin: *` and caches ~5 minutes,
  so the browser can fetch cross-origin with no proxy and no secrets.
- **Latency budget.** Publisher runs every 15 minutes; raw CDN caches ~5 minutes.
  Worst-case staleness on the page is roughly 20 minutes, which is fine for a
  portfolio surface.

### The anonymized contract (`/api/usage/public`)

Implemented in `app/modules/public_usage/` (`service.py` builds the payload,
`api.py` exposes the route). The route:

- requires no dashboard session — it is intentionally anonymous,
- is gated by the `public_usage_enabled` setting (default `True`; returns 404 when off),
- sets `Access-Control-Allow-Origin: *` and `Cache-Control: public, max-age=300`,
- accepts `?days=N` (clamped to 7..730; default 365).

`build_public_usage()` aggregates `request_logs` into rolled-up numbers only —
the docstring is explicit: *"no account_id, email, api_key, request bodies, IPs,
or raw error text ever leaves this function."* It also excludes the internal
`limit_warmup` source so warm-up traffic doesn't pollute the public numbers.

Response shape (see `app/modules/public_usage/schemas.py`):

- `period` — `{ days, start, end }`
- `generated_at`, `source`
- `totals` — cost_usd, tokens, input/output/cached/reasoning tokens, requests,
  avg_latency_ms, success_rate
- `daily[]` — per-day `{ date, cost_usd, tokens, requests, top_model }`
- `by_model[]` — per-model rollup with `provider`, requests, token splits, cost
- `by_provider` — `{ openai, anthropic }`, each `{ requests, cost_usd, tokens }`
- `trends[]` — compact `{ t, cost, tokens, requests }` series for charting

---

## 3. Canonical & durable storage (three tiers)

There are three distinct stores, with three distinct purposes. Keep them straight.

| Tier | What | Where | Backup mechanism | Public? |
|------|------|-------|------------------|---------|
| **Canonical** | Raw `request_logs` (full, sensitive) | Studio `~/.codex-lb/store.db` | — (source of truth) | NO |
| **Private DR** | Full DB snapshot (canonical mirror) | MacBook `~/.codex-lb/backups/` | rsync DR job (existing) | NO |
| **Public aggregates** | Anonymized `usage-*.json` | GitHub `aneym/llm-usage` | git history = backup | YES |

- **Canonical** raw request logs live only on Studio in `~/.codex-lb/store.db`.
  This is the single writer and the source of truth. It contains everything —
  it never leaves Studio except as an encrypted DR snapshot.
- **Private DR backup** of the *full* DB is handled by the existing disaster-recovery
  rsync (separate from this feature): `~/.codex-lb/bin/backup-from-studio.sh`, driven
  by launchd `com.aneyman.codex-lb-backup` on the MacBook (every 43200s / 12h). It
  takes a consistent SQLite `.backup` snapshot on Studio, delta-pulls it plus
  `encryption.key` to `~/.codex-lb/backups/store-latest.db`, integrity-checks it, and
  retains the 14 most recent timestamped copies (APFS clones, near-zero cost). This is
  the private backup of canonical data and is unchanged by the usage page.
- **Public aggregates** — the *anonymized* JSON files are versioned in the public
  GitHub repo. The git history of `aneym/llm-usage` IS the backup for the published
  aggregates: every 15-minute publish is a commit, so the full time series is
  reconstructable from history. We do not need a separate backup of the JSON.

**The full DB and any tokens/credentials are NEVER published.** Only the rolled-up,
anonymized aggregates ever reach GitHub.

---

## 4. Components & locations

All paths below are on **Studio** unless stated otherwise.

| Component | Repo source | Installed / runtime location |
|-----------|-------------|------------------------------|
| Publisher script | `scripts/llm-usage/llm-usage-publish.sh` | `~/.codex-lb/bin/llm-usage-publish.sh` |
| launchd plist | `scripts/llm-usage/com.aneyman.llm-usage-publish.plist` | `~/Library/LaunchAgents/com.aneyman.llm-usage-publish.plist` |
| launchd label | — | `com.aneyman.llm-usage-publish` (interval 900s) |
| Deploy key (SSH, repo-scoped) | — (generated on Studio) | `~/.ssh/llm-usage-deploy` (+ `.pub`) |
| Working clone of public repo | — | `~/.codex-lb/llm-usage-repo` |
| Publisher log | — | `~/.codex-lb/llm-usage-publish.log` |
| Public repo | — | GitHub `aneym/llm-usage` (deploy key has write) |
| codex-lb HTTP service | `app/main.py` | localhost, default port `2455` (override via `PORT`) |
| Public usage route/service | `app/modules/public_usage/{api,service,schemas}.py` | `GET /api/usage/public` |

The launchd interval mirrors the convention of the existing backup agent
(`StartInterval` integer, `RunAtLoad`, single log file for stdout+stderr).

### Publisher script responsibilities

1. For each window (e.g. 7, 30, 365), `curl` the local endpoint:
   `http://127.0.0.1:2455/api/usage/public?days=<N>` → write `usage-<N>.json`
   into the working clone.
2. `git add` changed files, `git commit` (skip if nothing changed), `git push`.
3. Push uses the repo-scoped deploy key via a per-command
   `GIT_SSH_COMMAND="ssh -i ~/.ssh/llm-usage-deploy -o IdentitiesOnly=yes"`,
   so it never falls back to a personal key.
4. Append a timestamped line to `~/.codex-lb/llm-usage-publish.log`.
5. Single-flight lock (atomic `mkdir`) so a manual run and the launchd fire can't
   race the same commit/push — same pattern as the backup script.

The deploy key is **repo-scoped** (added under aneym/llm-usage → Settings → Deploy
keys, with write access), so a leak grants write to *only* this one public repo of
anonymized aggregates — never to Studio, never to private repos, never inbound.

---

## 5. Operations & restore

### Re-run the publisher manually

On Studio:

```bash
# Direct run (writes JSON, commits, pushes; honors the single-flight lock):
~/.codex-lb/bin/llm-usage-publish.sh

# Or kick the launchd job immediately:
launchctl kickstart -k gui/$(id -u)/com.aneyman.llm-usage-publish

# Watch the log:
tail -f ~/.codex-lb/llm-usage-publish.log
```

Verify the endpoint is healthy first (should return JSON, not 404):

```bash
curl -s 'http://127.0.0.1:2455/api/usage/public?days=7' | head -c 400
```

A 404 means `public_usage_enabled` is off — set it true and restart codex-lb.

### Reload / reinstall the launchd agent

```bash
launchctl bootout  gui/$(id -u)/com.aneyman.llm-usage-publish 2>/dev/null || true
cp scripts/llm-usage/com.aneyman.llm-usage-publish.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.aneyman.llm-usage-publish.plist
launchctl print gui/$(id -u)/com.aneyman.llm-usage-publish | grep -E 'state|last exit'
```

### Rotate the deploy key

The deploy key only grants write to the public aggregates repo, so rotation is
low-stakes, but to rotate:

```bash
# 1. Generate a new key on Studio (no passphrase so launchd can use it headless).
ssh-keygen -t ed25519 -f ~/.ssh/llm-usage-deploy.new -N "" -C "llm-usage-deploy"

# 2. Add the new public key to GitHub: aneym/llm-usage → Settings → Deploy keys
#    → Add deploy key → paste ~/.ssh/llm-usage-deploy.new.pub → enable "Allow write access".
cat ~/.ssh/llm-usage-deploy.new.pub

# 3. Swap it in and confirm the working clone can push with the new key.
mv ~/.ssh/llm-usage-deploy.new     ~/.ssh/llm-usage-deploy
mv ~/.ssh/llm-usage-deploy.new.pub ~/.ssh/llm-usage-deploy.pub
GIT_SSH_COMMAND="ssh -i ~/.ssh/llm-usage-deploy -o IdentitiesOnly=yes" \
  git -C ~/.codex-lb/llm-usage-repo push

# 4. Delete the OLD deploy key from the GitHub repo settings.
```

### Recover / re-create the working clone

If `~/.codex-lb/llm-usage-repo` is lost or corrupted, re-clone with the deploy key:

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/llm-usage-deploy -o IdentitiesOnly=yes" \
  git clone git@github.com:aneym/llm-usage.git ~/.codex-lb/llm-usage-repo
# Then re-run the publisher; it will regenerate and commit current usage-*.json.
~/.codex-lb/bin/llm-usage-publish.sh
```

The published time series is preserved in the repo's git history regardless — a
lost working clone loses nothing durable.

### How the website consumes it

The website reads the static JSON from the raw CDN. No proxy, no API key — a
one-line `getUsage()` change to point at the raw URL:

```ts
// getUsage(): fetch anonymized aggregates straight from the public repo CDN.
// CORS is "*" and the CDN caches ~5 min, so this is a plain client-side fetch.
export async function getUsage(): Promise<PublicUsage> {
  const res = await fetch(
    "https://raw.githubusercontent.com/aneym/llm-usage/main/usage-365.json",
    { cache: "no-store" }, // CDN already caches ~5min; avoid double-stale on the page
  );
  if (!res.ok) throw new Error(`usage fetch failed: ${res.status}`);
  return res.json();
}
```

Swap `usage-365.json` for `usage-30.json` / `usage-7.json` to render shorter
windows. The response shape matches `PublicUsageResponse` (§2).

### Troubleshooting

- **Page is stale (> ~20 min):** check `~/.codex-lb/llm-usage-publish.log` for push
  errors; confirm the launchd job's `last exit` is 0; confirm codex-lb is up on `:2455`.
- **404 from the endpoint:** `public_usage_enabled` is false — re-enable and restart codex-lb.
- **Push rejected / auth fail:** deploy key missing write access or removed from the
  repo — re-add or rotate (§ Rotate the deploy key).
- **Nothing committing:** likely no data change since last run (commit is skipped on
  no diff) — confirm new `request_logs` exist for the window.

---

## 6. Extensibility — adding a new provider

Adding a new provider is **automatic** for the data pipeline: once that provider's
requests land in `request_logs` with a non-null `provider` value, they flow into the
aggregates with no change to the publisher, the SSH/launchd plumbing, or the website
fetch. `build_public_usage()` groups by `RequestLog.provider`, so a new provider
shows up in `by_model[].provider` and in the daily/totals rollups immediately.

The one place that is explicitly enumerated today is `by_provider`, which exposes
exactly `openai` and `anthropic` (see `PublicUsageByProvider` in
`app/modules/public_usage/schemas.py`, populated in `service.py`). To surface a
third provider as its own top-level `by_provider` entry, add a field to that schema
and a corresponding `_provider_entry(<NEW_PROVIDER_NAME>)` line in
`build_public_usage()`. Until then, a new provider's traffic is still fully counted
in `totals` and visible per-model — it just isn't broken out as its own
`by_provider` bucket.

Provider names are the canonical constants `OPENAI_PROVIDER_NAME` /
`ANTHROPIC_PROVIDER_NAME` from `app/core/providers/`; a new provider should define
and use its own `*_PROVIDER_NAME` constant the same way.

---

## 7. Security summary

- Studio has **no inbound exposure** — the only network action is an *outbound* git
  push over SSH. No port, no tunnel, no public API host.
- The website holds **no secret** — it reads a static, anonymized JSON over HTTPS
  from GitHub's raw CDN (CORS `*`, ~5 min cache).
- The deploy key is **repo-scoped** to the public aggregates repo only; a leak grants
  write to that one public repo of anonymized numbers — nothing else.
- The **full DB and any tokens/credentials are never published.** Canonical raw logs
  stay on Studio; the private DR copy stays on the MacBook (encrypted-at-rest key
  pulled alongside); only rolled-up anonymized aggregates reach GitHub.
