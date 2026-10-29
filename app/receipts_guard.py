from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys

from app.core.thread_delivery import send
from app.core.config.service_database import LOCAL_DATABASE_URL, primary_database_url


def receipt_count(url: str, minutes: int) -> int:
    import psycopg

    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(
        url, connect_timeout=10, options="-c default_transaction_read_only=on -c statement_timeout=30000"
    ) as connection:
        row = connection.execute(
            "SELECT count(*) FROM request_logs WHERE requested_at >= (now() AT TIME ZONE 'UTC') - make_interval(mins => %s)",
            (minutes,),
        ).fetchone()
    return int(row[0]) if row else 0


def alert(target: str, sender: str, host: str, problem: str) -> int:
    text = (
        f"agent-lb receipts guard on {host}: {problem}. Token and efficiency audits read empty until this clears. "
        "Check the primary request_logs writer and this host's primary-database.url path."
    )
    result = send(target, text, timeout=60)
    if result.returncode != 0:
        print(f"receipts guard: T3 send exited {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Post to a lane when agent-lb receipts stop arriving.")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--to", default="rails")
    parser.add_argument("--from", dest="sender", default="agentlb-receipts")
    args = parser.parse_args(argv)
    host = socket.gethostname().split(".")[0]
    url = os.environ.get("AGENT_LB_DATABASE_URL") or primary_database_url() or LOCAL_DATABASE_URL
    try:
        count = receipt_count(url, args.minutes)
    except Exception as exc:
        problem = f"cannot read the primary receipts database ({type(exc).__name__})"
    else:
        print(f"receipts guard: {count} receipts in the last {args.minutes} min on {host}")
        if count:
            return
        problem = f"0 receipts in the last {args.minutes} min"
    print(f"receipts guard: {problem}", file=sys.stderr)
    raise SystemExit(alert(args.to, args.sender, host, problem) or 3)


if __name__ == "__main__":
    main()
