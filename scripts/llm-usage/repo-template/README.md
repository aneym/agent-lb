# llm-usage

Auto-published, **anonymized**, **aggregate** LLM usage statistics across providers — a public dataset that powers a personal portfolio page.

Today this covers **OpenAI** and **Anthropic**. The schema is provider-agnostic and extends cleanly to additional providers (Google, Mistral, xAI, local models, etc.) as they are added — see [Extensibility](#extensibility).

## What this is

This repo is a small, machine-readable view of how much LLM usage flows through my tools and projects, rolled up to safe aggregates. It is regenerated automatically and committed here so a static portfolio page can fetch it without a backend.

## What this is NOT

There is **no PII, no prompts, no completions, no account identifiers, no API keys, and no per-request content** in this repo. Every file contains only counts, sums, and averages computed over a time window. Nothing here can be traced back to an individual conversation, user, or credential.

## File layout

The dataset is published as a set of pre-aggregated JSON files, one per rolling window:

| File             | Window                         |
| ---------------- | ------------------------------ |
| `usage.json`     | Default view (365 days)        |
| `usage-7.json`   | Last 7 days                    |
| `usage-30.json`  | Last 30 days                   |
| `usage-90.json`  | Last 90 days                   |
| `usage-365.json` | Last 365 days (canonical full) |

`usage.json` is an alias of the default (365-day) window so consumers can fetch a single stable file without picking a period.

## Fetch URL

The canonical, full-history file:

```
https://raw.githubusercontent.com/aneym/llm-usage/main/usage-365.json
```

Swap the filename for `usage-7.json`, `usage-30.json`, or `usage-90.json` to fetch a narrower window, or `usage.json` for the default view.

## Update cadence

The files are refreshed roughly **every ~15 minutes** by an automated publisher that pulls each provider's usage/billing API, anonymizes and aggregates the results, and commits the regenerated JSON. `generatedAt` inside each file records the exact moment a snapshot was produced.

## JSON schema (top level)

Each `usage-*.json` file is a single object with these keys:

| Key           | Type     | Description                                                                          |
| ------------- | -------- | ------------------------------------------------------------------------------------ |
| `period`      | string   | The window this file covers (e.g. `"365"`, `"30"`).                                   |
| `generatedAt` | string   | ISO-8601 timestamp of when this snapshot was produced.                                |
| `source`      | string   | Identifier of the publisher/pipeline that generated the file.                        |
| `totals`      | object   | Aggregate cost, token, request, latency, and success-rate figures for the window.    |
| `daily`       | object[] | Per-day aggregates: date, cost, tokens, requests, and the top model that day.        |
| `byModel`     | object[] | Per-model breakdown: requests, token splits, and cost, with provider attribution.    |
| `byProvider`  | object   | Per-provider rollups (`openai`, `anthropic`, …) of requests, cost, and tokens.       |
| `trends`      | object[] | Compact time series for charts: `{ t, cost, tokens, requests }` points.              |

Full field-by-field definitions live in [`SCHEMA.md`](./SCHEMA.md).

## Extensibility

`byProvider` and `byModel` are open maps/lists, not fixed shapes. Adding a provider means the publisher emits a new key under `byProvider` and new entries in `byModel` (each tagged with its `provider`) — no schema change required for consumers. Charts and totals are computed across whatever providers are present, so the dataset generalizes to any number of LLM vendors.
