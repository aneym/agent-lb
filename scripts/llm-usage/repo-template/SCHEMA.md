# SCHEMA

Field-by-field definition of the JSON contract published in every `usage-*.json` file.

All monetary values are in **USD**. All token values are integer counts. All timestamps are ISO-8601 strings. Every figure is an **aggregate** over the file's window — there is no per-request or per-prompt data anywhere in the contract.

## Top level

| Field         | Type     | Description                                                              |
| ------------- | -------- | ----------------------------------------------------------------------- |
| `period`      | string   | The rolling window this file covers, in days (e.g. `"7"`, `"365"`).     |
| `generatedAt` | string   | ISO-8601 timestamp marking when this snapshot was generated.            |
| `source`      | string   | Identifier of the publisher/pipeline that produced the file.            |
| `totals`      | object   | Window-wide aggregate metrics. See [`totals`](#totals).                 |
| `daily`       | object[] | Per-day aggregates, one entry per day. See [`daily[]`](#daily).         |
| `byModel`     | object[] | Per-model breakdown across the window. See [`byModel[]`](#bymodel).     |
| `byProvider`  | object   | Per-provider rollups keyed by provider. See [`byProvider`](#byprovider).|
| `trends`      | object[] | Compact chart series for the window. See [`trends[]`](#trends).         |

## `totals`

Window-wide totals aggregated across every provider and model.

| Field               | Type   | Description                                                          |
| ------------------- | ------ | ------------------------------------------------------------------- |
| `costUsd`           | number | Total spend over the window, in USD.                                |
| `tokens`            | number | Total tokens (input + output) processed.                           |
| `inputTokens`       | number | Total prompt/input tokens.                                         |
| `outputTokens`      | number | Total completion/output tokens.                                    |
| `cachedInputTokens` | number | Input tokens served from prompt cache (subset of `inputTokens`).   |
| `reasoningTokens`   | number | Reasoning/thinking tokens (subset of output, where reported).      |
| `requests`          | number | Total number of API requests.                                     |
| `avgLatencyMs`      | number | Mean request latency over the window, in milliseconds.            |
| `successRate`       | number | Fraction of requests that succeeded, `0`–`1`.                      |

## `daily[]`

One entry per calendar day in the window, ordered by date.

| Field      | Type   | Description                                            |
| ---------- | ------ | ----------------------------------------------------- |
| `date`     | string | Calendar day, `YYYY-MM-DD`.                           |
| `costUsd`  | number | Spend for that day, in USD.                           |
| `tokens`   | number | Total tokens for that day.                            |
| `requests` | number | Request count for that day.                           |
| `topModel` | string | Model with the most usage that day (by primary metric).|

## `byModel[]`

One entry per model that saw usage in the window.

| Field               | Type   | Description                                                       |
| ------------------- | ------ | ---------------------------------------------------------------- |
| `model`             | string | Canonical model id (e.g. `gpt-5.4-mini`, `claude-opus-4-8`).     |
| `label`             | string | Human-friendly display name for the model.                       |
| `provider`          | string | Provider this model belongs to (e.g. `openai`, `anthropic`).     |
| `requests`          | number | Request count for this model.                                    |
| `inputTokens`       | number | Input tokens for this model.                                     |
| `outputTokens`      | number | Output tokens for this model.                                    |
| `cachedInputTokens` | number | Cached input tokens for this model (subset of `inputTokens`).    |
| `tokens`            | number | Total tokens (input + output) for this model.                    |
| `costUsd`           | number | Spend attributed to this model, in USD.                          |

## `byProvider`

An object keyed by provider name. Each value is a rollup for that provider.

| Key                  | Type   | Description                                  |
| -------------------- | ------ | -------------------------------------------- |
| `openai`             | object | Rollup for OpenAI. See [provider rollup](#provider-rollup-shape). |
| `anthropic`          | object | Rollup for Anthropic. Same shape as above.   |
| _(future providers)_ | object | Additional keys appear as providers are added (e.g. `google`, `mistral`). |

### Provider rollup shape

| Field      | Type   | Description                               |
| ---------- | ------ | ----------------------------------------- |
| `requests` | number | Total requests for this provider.         |
| `costUsd`  | number | Total spend for this provider, in USD.    |
| `tokens`   | number | Total tokens for this provider.           |

## `trends[]`

A compact, chart-friendly time series. Keys are abbreviated to keep the payload small for the portfolio page.

| Field      | Type   | Description                                    |
| ---------- | ------ | ---------------------------------------------- |
| `t`        | string | Time bucket label (e.g. `YYYY-MM-DD`).         |
| `cost`     | number | Spend for the bucket, in USD.                  |
| `tokens`   | number | Total tokens for the bucket.                   |
| `requests` | number | Request count for the bucket.                  |

## Extensibility

`byProvider` is an open map and `byModel` is an open list. When a new provider is onboarded, the publisher adds a key under `byProvider` and emits `byModel[]` entries tagged with that `provider` — **no change to the contract is required**. Consumers should iterate over whatever keys/entries are present rather than assuming a fixed set of providers, so the dataset scales to any number of LLM vendors. `totals` and `trends` remain cross-provider sums computed over everything present in the window.
