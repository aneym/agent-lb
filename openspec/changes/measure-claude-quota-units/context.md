# Quota receipts

The existing proxy stores 5h/7d utilization, caller seat, room, user, machine and session on request logs. Status and reset evidence were not retained. This change keeps the unified header family only; it never records authorization headers.

Quota percentages belong to an account. The top-five total sums percentage points across accounts and does not imply equal account capacities. Attribution is an estimate: other clients can consume an account outside the proxy, readings are rounded, and headers arrive before output generation finishes. Initial readings and final requests without another reading cannot establish a delta. Reset changes or drops break an interval rather than inventing usage.

Run `clients/lb-cache --quota --since 24h --json` against the serving PostgreSQL database. It reads the last baseline for each account/window and the requests since that baseline. The table shows all per-account shares and five callers ranked by weekly then 5h use.

Check: a recorded-header parser/report scenario plus the existing request-boundary scenarios. Install with the documented `lb-restart --from` file deployment and startup migration; retain the Claude cache evaluation receipt after restart.

## Worker receipt, 2026-10-14

Author vendor: OpenAI. The recorded live fixture is `_OVERAGE_BILLING_HEADERS` in `tests/integration/test_anthropic_proxy.py`, captured 2026-07-01. Synthetic surrounding readings exercise attribution, missing tokens, resets, cache reads, multiple accounts and partial windows; they are not live quota measurements. The new parser/report check plus existing `test_lb_cache.py` passed, 4 tests. The existing room/utilization migration scenario passed, 1 test and 12 deselected. Full request-boundary scenarios stalled in async fixture setup and were interrupted; they did not pass. SQLite upgrade to head and schema check reported `migration_policy=ok` and `schema_drift=none`. OpenSpec main specs, the strict change check, lint and compilation passed. One existing line in `app/audit_tabs.py` was wrapped to clear the baseline lint error.

The worker has no proxy at 127.0.0.1:2455, no reachable PostgreSQL at the CLI default, and no installed lb-restart. The 24h report exited 3 on database connection failure. History and the top five remain unmeasured. Git origin access failed DNS resolution. Factory grant verification reported the supplied grant absent on this worker; host discovery was denied a write to its pool lock. PostgreSQL verification could not start because socket creation was denied. No credentials were read and no runtime was changed. Fresh review remains pending; the brief forbids messaging other agents.

On the serving machine, verify grant `g-factory-burn-2026-10-09-r0-95add312ead3`, push this branch, run the request-boundary scenarios, and request the required reviewer with author vendor OpenAI. Fast-forward origin/main only if it is an ancestor of this commit and the serving checkout is clean. Run `clients/lb-cache --quota --since 24h --json` and the table version against the serving database, saving both receipts outside git.

Deploy using `~/.agent-lb/bin/lb-restart --reason "capture Claude subscription quota evidence" --from <worktree> --files app/core/anthropic/quota_headers.py app/db/models.py app/db/alembic/versions/20261014_040000_add_unified_header_evidence.py app/modules/proxy/anthropic_service.py app/modules/request_logs/repository.py app/modules/request_logs/schemas.py app/modules/request_logs/mappers.py clients/lb-cache`. Verify health and the affected request-log endpoint, then run `python3 scripts/claude_cache_eval.py` and retain its receipt. Frame calls retain evidence but are excluded from inference attribution because they do not consume message quota.
