## Why
Kimi rejects empty tool result references with HTTP 400. The provider boundary currently forwards malformed and orphan references unchanged.

## What Changes
Normalize Kimi tool history after flattening tool declarations. Preserve valid IDs, generate deterministic IDs for malformed calls, and retain orphan result content as user text.

## Impact
Only Kimi request bodies change. Anthropic and other providers retain their existing payloads.
