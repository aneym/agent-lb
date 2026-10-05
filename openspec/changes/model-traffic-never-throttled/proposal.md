## Why

On 2026-10-05 at 12:05 ET `game-mode on` set a 0.2 MB/s agent-lb upload hold. Claude Code calls then failed with `API Error: 503 upload throttle on at 200 KB/s; 3.4 MB queued ahead; waited 30 s > limit 30 s`: 1,664 Anthropic requests failed between 16:05 and 16:27 UTC (1,540 `upload_admission_rejected`, 124 `upload_admission_timeout`) until game mode was overridden off. Holds paced and queued every upstream write, and nearly every upstream write agent-lb makes is a model request body. Alex's call the same day: gaming mode must not cost work or quality. Game mode protects the shared uplink by pausing torrents, pacing box pushes and capping the gaming PC.

## What Changes

- Upload holds pace only writes made inside `upload_throttle.bulk_transfer()`. Every other write, including all model API request bodies and websocket frames, goes straight to the socket whatever the holds say.
- Upload admission only queues or refuses bulk uploads. Model requests are never queued and never get `upload_admission_rejected` or `upload_admission_timeout`.
- `agent-lb throttle status` says the cap applies to bulk transfers only.
- Hold storage, the CLI, owners, `yields_to` and the state file format are unchanged, so game mode and the adaptive cap keep working without edits.

## Capabilities

### Modified Capabilities

- `proxy-admission-control`: upload holds and upload admission never apply to model API traffic.

## Impact

- `app/core/upload_throttle.py`, `app/core/upload_admission.py`, `app/cli.py`.
- No caller marks a transfer as bulk today, so holds currently pace nothing in agent-lb. The admission queue stays for bulk callers; deleting it is a follow-up once nothing needs it.
