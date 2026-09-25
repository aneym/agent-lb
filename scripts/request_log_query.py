#!/usr/bin/env python3
"""Per-request receipt from the agent-lb request log.

Select requests by session id, API key (name, id or key prefix) and/or user agent
inside a time range, and print for each request and in total: account, pool
(provider/plan), model, input/output tokens, cache-read and cache-write tokens,
latency. Open Factory uses it for per-trial receipts.

    python3 scripts/request_log_query.py --key of-harbor --since 2026-09-25T19:00Z
    python3 scripts/request_log_query.py --session 9ba3e627-... --json

Token columns are normalised across providers: `input` is uncached input, and
`context` = input + cache_read + cache_write. Anthropic logs input without cache;
OpenAI logs input including its cached part, so cache_read is subtracted there.
Session ids: --session matches agent-lb's session_id or the client's own id
(client_session_id: Codex's `session-id` header = its rollout file id, Claude Code's
session UUID). Codex websockets log session_id `turn_<hex>`, minted per connection. Loopback clients (incl. OrbStack
containers via host.docker.internal) are logged keyless unless they send a key.
Accounts show as their alias or provider:id-prefix, never an email. Reads Postgres
through `psql` (AGENT_LB_DATABASE_URL, else the Studio default).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DEFAULT_DB = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"


def _dsn() -> str:
    url = os.environ.get("AGENT_LB_DATABASE_URL") or DEFAULT_DB
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _time(value: str) -> str:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(sep=" ")


def _psql_json(sql: str) -> list[dict]:
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t"
    result = subprocess.run(
        ["psql", _dsn(), "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", wrapped],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return json.loads(result.stdout or "[]")


def query(args: argparse.Namespace) -> list[dict]:
    where = []
    if args.session:
        sid = _literal(args.session)
        where.append(f"(r.session_id = {sid} or r.client_session_id = {sid})")
    if args.useragent:
        where.append(f"r.useragent ilike {_literal('%' + args.useragent + '%')}")
    if args.key:
        k = _literal(args.key)
        where.append(f"(k.name = {k} or k.id = {k} or k.key_prefix = {k} or r.api_key_id = {k})")
    since = _time(args.since) if args.since else None
    if since is None and not args.session:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(tzinfo=None).isoformat(sep=" ")
    if since:
        where.append(f"r.requested_at >= {_literal(since)}")
    if args.until:
        where.append(f"r.requested_at < {_literal(_time(args.until))}")
    sql = f"""
        select r.id, r.requested_at, r.session_id, r.client_session_id, r.request_kind, r.provider, r.model, r.status,
               r.useragent_group, r.transport,
               r.api_key_id, k.name as key_name,
               r.account_id, a.plan_type,
               coalesce(nullif(a.alias, ''), r.provider || ':' || left(r.account_id, 8)) as account,
               coalesce(r.input_tokens, 0) as raw_input, coalesce(r.output_tokens, 0) as output_tokens,
               coalesce(r.cache_read_tokens, r.cached_input_tokens, 0) as cache_read,
               coalesce(r.cache_creation_tokens, 0) as cache_write,
               r.latency_ms, r.latency_first_token_ms, r.cost_usd, r.error_code
        from request_logs r
        left join api_keys k on k.id = r.api_key_id
        left join accounts a on a.id = r.account_id
        where {" and ".join(where)} and r.deleted_at is null
        order by r.requested_at, r.id
        limit {int(args.limit)}
    """
    rows = _psql_json(sql)
    for row in rows:
        read = row["cache_read"]
        raw = row.pop("raw_input")
        row["input_tokens"] = raw if row["provider"] == "anthropic" else max(raw - read, 0)
        row["pool"] = f"{row['provider']}/{row['plan_type'] or '?'}"
        row["context_tokens"] = row["input_tokens"] + read + row["cache_write"]
        row["cache_read_ratio"] = round(read / row["context_tokens"], 4) if row["context_tokens"] else None
    return rows


def summarise(rows: list[dict]) -> dict:
    def empty() -> dict:
        return {"requests": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}

    total = empty()
    by_account: dict[str, dict] = defaultdict(empty)
    by_pool: dict[str, dict] = defaultdict(empty)
    latencies = []
    for row in rows:
        for bucket in (total, by_account[row["account"] or "(none)"], by_pool[row["pool"]]):
            bucket["requests"] += 1
            bucket["input"] += row["input_tokens"]
            bucket["output"] += row["output_tokens"]
            bucket["cache_read"] += row["cache_read"]
            bucket["cache_write"] += row["cache_write"]
            bucket["cost_usd"] += row["cost_usd"] or 0.0
        if row["latency_ms"] is not None:
            latencies.append(row["latency_ms"])
    context = total["input"] + total["cache_read"] + total["cache_write"]
    total["cache_read_ratio"] = round(total["cache_read"] / context, 4) if context else None
    total["errors"] = sum(1 for row in rows if row["status"] != "success")
    total["latency_ms_sum"] = sum(latencies)
    total["latency_ms_max"] = max(latencies) if latencies else None
    total["cost_usd"] = round(total["cost_usd"], 6)
    return {"total": total, "by_account": dict(by_account), "by_pool": dict(by_pool)}


def _table(rows: list[dict], summary: dict) -> str:
    head = (
        f"{'time (UTC)':19}  {'account':24} {'pool':18} {'model':22} "
        f"{'in':>7} {'out':>6} {'c.read':>8} {'c.write':>7} {'read%':>5} {'ms':>6} status"
    )
    lines = [head]
    for r in rows:
        ratio = "" if r["cache_read_ratio"] is None else f"{r['cache_read_ratio'] * 100:.0f}"
        lines.append(
            f"{str(r['requested_at'])[:19]:19}  {(r['account'] or '-')[:24]:24} "
            f"{r['pool'][:18]:18} {r['model'][:22]:22} "
            f"{r['input_tokens']:>7} {r['output_tokens']:>6} {r['cache_read']:>8} {r['cache_write']:>7} {ratio:>5} "
            f"{r['latency_ms'] if r['latency_ms'] is not None else '-':>6} {r['status']}"
        )
    t = summary["total"]
    ratio = "-" if t["cache_read_ratio"] is None else f"{t['cache_read_ratio'] * 100:.1f}%"
    lines.append(
        f"TOTAL {t['requests']} requests, {t['errors']} errors: in {t['input']} out {t['output']} "
        f"cache_read {t['cache_read']} cache_write {t['cache_write']} (read {ratio}), "
        f"latency sum {t['latency_ms_sum']} ms, cost ${t['cost_usd']:.4f}"
    )
    for label, groups in (("account", summary["by_account"]), ("pool", summary["by_pool"])):
        for name, g in sorted(groups.items()):
            lines.append(
                f"  by {label} {name}: {g['requests']} req, in {g['input']} out {g['output']} "
                f"read {g['cache_read']} write {g['cache_write']}"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", help="Claude Code / Codex session id (request_logs.session_id)")
    parser.add_argument("--key", help="API key name, id or key prefix (e.g. of-harbor)")
    parser.add_argument("--useragent", help="substring of the client user agent, e.g. 'codex_exec' or 'Ubuntu'")
    parser.add_argument("--since", help="ISO time, UTC if no offset (default: 24h ago unless --session)")
    parser.add_argument("--until", help="ISO time, exclusive")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--json", action="store_true", help="print {requests, summary} as JSON")
    args = parser.parse_args()
    if not (args.session or args.key or args.useragent):
        parser.error("give --session, --key and/or --useragent")
    rows = query(args)
    summary = summarise(rows)
    if args.json:
        print(json.dumps({"filter": vars(args), "requests": rows, "summary": summary}, indent=2, default=str))
    else:
        print(_table(rows, summary))


if __name__ == "__main__":
    main()
