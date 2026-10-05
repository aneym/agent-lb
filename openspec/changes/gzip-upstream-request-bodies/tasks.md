## 1. Compress

- [x] 1.1 Gzip Anthropic Messages and count_tokens bodies of 16 KiB or more.
- [x] 1.2 Gzip Codex responses stream and compact bodies of 16 KiB or more, on both the direct and the routed client.

## 2. Proof

- [x] 2.1 Against a real local HTTP upstream, a large `/v1/messages` call through the app and a large Codex responses stream both arrive gzipped and decode to the request.
