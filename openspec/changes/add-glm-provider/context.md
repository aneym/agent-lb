# GLM Provider Context

GLM Coding Plan access is API-key based for this integration. Unlike Anthropic
OAuth accounts, there is no `id_token` or refresh-token exchange to perform, so
the imported API key is encrypted into the account token slots and reused as the
upstream bearer token.

Z.AI exposes an Anthropic-compatible Messages endpoint at
`https://api.z.ai/api/anthropic`. Requests use the same `/v1/messages` shape that
Claude Code sends for Anthropic, but the quota and account pool must remain
provider-specific. The model prefix is the provider discriminator: canonical GLM
models begin with `glm-`, including long-context variants such as `glm-5.2[1m]`.

The local `glm` shell function should point Claude Code at agent-lb instead of
directly at Z.AI. The selected agent-lb account supplies the real Z.AI API key
upstream; the client-side token is only the downstream proxy credential (or a
placeholder when local proxy API-key enforcement is disabled).

Example local request after import:

```http
POST http://127.0.0.1:2455/v1/messages
Authorization: Bearer local-glm
anthropic-version: 2023-06-01
content-type: application/json

{"model":"glm-5.2","max_tokens":4,"messages":[{"role":"user","content":"ping"}]}
```
