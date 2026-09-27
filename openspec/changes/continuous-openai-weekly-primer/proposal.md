# Continuous OpenAI weekly-window primer

## Why

An OpenAI quota window starts counting down only when a request lands on the account. The optional HOLD warm-up requires a previously exhausted sample and runs inside planner working hours. A partially used weekly window can reset without either condition, leaving its new window unstarted for hours.

## What changes

- Detect fresh, unstarted OpenAI usage windows from zero usage and a reset timestamp approximately one full window after the sample, whether the weekly window is primary or secondary.
- Send an opt-in primer within one usage refresh regardless of working hours or previous exhaustion, while rejecting exhausted quota and unsafe accounts.
- Persist hourly claim buckets, space non-failed sends by 30 minutes, retry explicit failures with backoff and a six-hour brake after exhausted retries, and never retry uncertain pending sends.
- Persist upstream outcomes before writing request logs, and include successful OpenAI primers in last-primed status.

## Impact

`usage-refresh-policy` gains continuous OpenAI priming without changing the legacy HOLD/SEED warm-up paths or Anthropic priming. Uses the existing attempt table and OpenAI Responses sender; no schema changes.
