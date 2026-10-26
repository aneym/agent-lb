from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from app.status_cli import DEFAULT_BASE_URL, StatusObservationError, get_status_json


def share(pool: dict) -> str:
    return f"{pool['poolSharePct']:.1f}%"


def line(row: dict) -> str:
    title = (row.get("title") or row["rootThreadId"])[:48]
    claude, codex = row["pools"]["anthropic"], row["pools"]["openai"]
    sessions = len(row["nativeSessions"]) + sum(len(child["nativeSessions"]) for child in row["children"])
    requests = claude["requests"] + codex["requests"]
    return (
        f"{title:<48}  {row['host']:<7}  claude {share(claude):>6}  codex {share(codex):>6}"
        f"  {requests:>6} req  {sessions} sessions"
    )


def footer(pools: dict, host: str | None = None) -> list[str]:
    out = []
    scope = f" on {host}" if host else ""
    rest = "; all hosts: " if host else ", "
    for name, label in (("anthropic", "Claude"), ("openai", "Codex")):
        pool = pools[name]
        matched = pool["matchedRequestPct"]
        coverage = "no T3 requests" if matched is None else f"{matched:.1f}% of T3 requests matched a thread"
        out.append(
            f"{label}: {coverage}; T3 threads{scope} {share(pool['t3Threads'])}{rest}"
            f"unregistered {share(pool['t3Unregistered'])}, outside T3 {share(pool['outsideT3'])}, "
            f"unexplained {share(pool['unexplained'])} of the pool's week"
        )
    return out


def run(args) -> None:
    base = (args.base_url or os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    query = {"range": args.range}
    if args.host:
        query["host"] = args.host
    if args.pool:
        query["pool"] = args.pool
    try:
        result = get_status_json(f"{base}/api/t3/threads/burn?{urlencode(query)}", args.timeout)
    except StatusObservationError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(2) from exc
    result["threads"] = result["threads"][: max(args.limit, 0)]
    if args.json:
        print(json.dumps(result, sort_keys=True))
        return
    for row in result["threads"]:
        print(line(row))
    for text in footer(result["pools"], result.get("host")):
        print(text)
