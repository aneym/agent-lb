---
id: model-door
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["features/agent-lb/model-door.md", "app/modules/model_door/"]
proofs: []
---
# Model door for cloud boxes

## Sub-features
A public door in front of agent-lb for agents that run outside our machines, such as one cloud box per customer. It takes only a Rails model pass minted for one box (`Authorization: Bearer rmp_...` or `x-api-key`), checks it with Rails' `POST /api/boxes/pass/check` (cached 30 s, so a revoke lands within a minute), refuses clients other than Claude Code with 403, asks Rails' `POST /api/spend/model/check` before forwarding and answers 402 `billing_error` when the account is capped, forwards to the box-local agent-lb proxy with our account under seat `rails-box`, and reports each `/v1/messages` call's model and tokens to Rails' `POST /api/spend/model/usage`, which prices it on the account's spend meter. No model key reaches the box.

## How to get to it
`python -m app.modules.model_door` from the runtime copy, run by the `agent-lb-model-door` user unit on ax102 on 127.0.0.1:2472, published through the `agent-lb-model-door-tunnel` cloudflared unit.

## Driving it
Point Claude Code at the door: `ANTHROPIC_BASE_URL=<door>` and `ANTHROPIC_AUTH_TOKEN=<model pass>`. `GET /health` answers without a pass. Every call is one line in `~/.agent-lb-door/door.jsonl` (box, agent, status, model, tokens, Rails' cost), never the pass.

## Gotchas
The door reads the Rails relay key from agent-secret `rails-one-relay-key` at start. The pool still needs Claude Code's own system identity in the request, as agent-lb does for every Claude subscription call.
