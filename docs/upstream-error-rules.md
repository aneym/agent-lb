# Upstream error rules

Set `AGENT_LB_UPSTREAM_ERROR_RULES` to a JSON array and restart the instance.
Rules apply to HTTP errors on the Anthropic-compatible message path, including
GLM and Kimi. The first matching operator rule wins; built-in rules follow.
An unmatched response keeps its existing handling.

```sh
export AGENT_LB_UPSTREAM_ERROR_RULES='[{"provider":"glm","http_status":500,"error_code":"temporary_failure","message_regex":"try again","action":"cooldown","cooldown_seconds":120,"reason":"Temporary upstream failure"}]'
```

Each rule requires `provider`, `action` and a nonempty `reason`. Optional
`http_status` matches one error status (400–599). `error_code` matches the
upstream code exactly, ignoring case; `message_regex` searches the upstream
message, ignoring case. All supplied conditions must match. Omitted conditions
match any value. Codes come from `error.code`, then `error.type`, then the
outer `code`; flat error objects also work. Invalid regexes, actions, status
values and unknown fields fail settings validation at startup.

| Action | Result |
|---|---|
| `pause` | Pause the account with the supplied reason and try another account. |
| `cooldown` | Temporarily exclude the account and try another account. `cooldown_seconds` defaults to 60 and must be positive. |
| `fail_request` | Surface the failure immediately without penalising the account. |

The built-in GLM rule pauses on code `1113` at any error status. Kimi rules
pause on suspension or insufficient balance at 401, 402, 403, 429 and 500.
Suspension wins when both match. Their `balance_exhausted:` reasons and request
log codes remain unchanged.

Run `.venv/bin/python scripts/check_error_rules.py` to prove the rules against a
local HTTP upstream and a separate SQLite database per scenario under
`.local/error-rules/`. The runner forbids upstream connections outside its
loopback fake and avoids port 2455. It prints account state and request log
codes as JSON lines, then stops each app process it started.
