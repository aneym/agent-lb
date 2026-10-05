## Why

On 2026-10-05 Studio's uplink measured about 8 Mbps. Request bodies from about 20 Claude sessions used roughly 465 KB/s of it, and one 700 KB request took about 15 s just to upload, which stalled model calls. These bodies are mostly repeated transcript JSON and gzip about 3.8x. Probes on 2026-10-05 showed that Anthropic's Messages API and the ChatGPT Codex backend both accept `Content-Encoding: gzip` request bodies. Anthropic rejects zstd, br and deflate with `request_body_encoding_unsupported`.

## What Changes

- Anthropic Messages and count_tokens requests, Codex `/codex/responses` HTTP streams and `/codex/responses/compact` send a gzipped body with `Content-Encoding: gzip` once the compact JSON serialization reaches 16 KiB. The uncompressed send below that is unchanged (aiohttp's own serialization), so a body just under the threshold can go out slightly larger than 16 KiB uncompressed.
- Smaller bodies, the GLM and Kimi Anthropic-compatible upstreams, and websocket transport are unchanged.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `outbound-http-clients`: large model-call request bodies go upstream gzipped.

## Impact

- `app/core/clients/upstream_body.py` (new), `app/core/clients/proxy.py`, `app/modules/proxy/anthropic_service.py`.
- Upload bytes for large model calls drop about 3.8x. Compression runs off the event loop and takes about 10 ms for a 650 KB body.
