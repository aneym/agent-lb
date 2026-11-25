from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import pool_reset_call, pool_reset_readings
from app.pool_reset_rule import candidates
from app.usage_export_sql import rows

DEFAULT_STATE = Path.home() / ".agent-rails" / "factory" / "pool-reset-auto"
WINDOW_MINUTES = 10
MODELS = {"claude-opus-5-5": 3, "claude-fable-5-2": 1}
REFUSALS = ("no_available_anthropic_accounts", "anthropic_quota_cooldown")
PREFIX = "[Factory → Rails]"

_ROUTER_SQL = """
SELECT model,
       count(*) FILTER (WHERE status = 'success') AS ok,
       count(*) FILTER (WHERE coalesce(upstream_error_code, error_code) = ANY(%(refusals)s)) AS refused
FROM request_logs
WHERE deleted_at IS NULL AND provider = 'anthropic' AND model = ANY(%(models)s)
  AND requested_at >= %(since)s AND requested_at <= %(until)s
GROUP BY model
"""


def router_closed(cursor: Any, now: datetime, minutes: int = WINDOW_MINUTES) -> tuple[bool, dict[str, Any]]:
    seen = {
        row["model"]: {"ok": row["ok"], "refused": row["refused"]}
        for row in rows(
            cursor,
            _ROUTER_SQL,
            {
                "refusals": list(REFUSALS),
                "models": list(MODELS),
                "since": now - timedelta(minutes=minutes),
                "until": now,
            },
        )
    }
    counts = {model: seen.get(model, {"ok": 0, "refused": 0}) for model in MODELS}
    closed = all(counts[m]["ok"] == 0 and counts[m]["refused"] >= least for m, least in MODELS.items())
    return closed, counts


def _held(readings: dict[str, Any], ids: dict[str, str], state: Path) -> None:
    path = state / "held.json"
    known = json.loads(path.read_text()) if path.exists() else {}
    for account in readings["accounts_now"]:
        name = account["account"]
        if name not in ids or account.get("resets_held") is not None:
            continue
        try:
            available = pool_reset_call.preview(ids[name])["available"]
        except pool_reset_call.CallError:
            available = None
        if isinstance(available, int):
            account["resets_held"] = available
        elif name in known:
            account["resets_held"] = known[name]["count"]


def _write(path: Path, document: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=1, default=str) + "\n")
    temporary.replace(path)


def _fire(cursor: Any, state: Path, once: Path, now: datetime, counts: dict[str, Any], args: argparse.Namespace) -> str:
    ids = pool_reset_readings.owned(cursor)
    readings = pool_reset_readings.headroom(cursor)
    _held(readings, ids, state)
    ranked, skipped = candidates(readings, set(ids), set(), now.replace(tzinfo=timezone.utc))
    if not ranked:
        return "no candidate: " + "; ".join(skipped)[:400]
    recent = pool_reset_readings.redemptions(cursor, now.replace(tzinfo=timezone.utc) - timedelta(days=1))
    override = bool(recent)
    tried = []
    for entry in ranked[:2]:
        account = entry["account"]
        try:
            seen = pool_reset_call.preview(ids[account])
        except pool_reset_call.CallError as exc:
            tried.append(f"{account} preview failed ({exc})")
            continue
        if not seen["redeemable_now"]:
            tried.append(f"{account} not redeemable ({seen['ineligible_reason']})")
            continue
        receipt: dict[str, Any] = {
            "kind": "surge redemption",
            "by": "agent-lb-pool-reset-surge timer",
            "approval": args.approval,
            "day": args.day,
            "account": account,
            "at": datetime.now(timezone.utc).isoformat(),
            "router": counts,
            "pick": entry,
            "override_daily_limit": override,
            "state": "claimed",
        }
        _write(once, receipt)
        try:
            result = pool_reset_call.consume(ids[account], override)
        except pool_reset_call.CallError as exc:
            receipt.update(state="failed", error=str(exc)[:300])
            _write(once, receipt)
            return f"{account} consume failed ({exc}); no second try today"
        receipt.update(
            state=result.get("status"),
            result={key: result.get(key) for key in ("status", "code", "windowsReset")},
            before=pool_reset_readings.around(cursor, account, datetime.fromisoformat(receipt["at"])),
        )
        _write(once, receipt)
        text = (
            f"{PREFIX} {now.strftime('%H:%M')}Z UTC: no Opus or Fable account was selectable, so the one surge reset "
            f"allowed today was redeemed on account {account} ({result.get('status')}), freeing about "
            f"{entry['freed_points_estimate']:g} points. Alex's 11:50 AM ET yes covers it; receipt {once}."
        )
        receipt["sent"] = pool_reset_call.send(args.send_host, f"pool-reset-surge-{args.day}", text)
        _write(once, receipt)
        return f"redeemed {account}: {result.get('status')}"
    return "not redeemed, nothing consumed: " + "; ".join(tried)[:400]


def run(args: argparse.Namespace) -> dict[str, Any]:
    state: Path = args.state
    state.mkdir(parents=True, exist_ok=True)
    once = state / f"surge-{args.day}.json"
    with pool_reset_readings.connect() as connection, connection.cursor() as cursor:
        now = pool_reset_readings.db_now(cursor)
        record: dict[str, Any] = {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": args.day, "live": args.live}
        if now.strftime("%Y-%m-%d") != args.day:
            return {**record, "outcome": f"outside {args.day}, nothing to do"}
        if once.exists():
            return {**record, "outcome": f"already fired today, see {once.name}"}
        closed, counts = router_closed(cursor, now)
        record["router"] = counts
        if not closed:
            return {**record, "outcome": "Opus or Fable still selectable, no redeem"}
        if not args.live:
            return {**record, "outcome": "would redeem (dry run)"}
        return {**record, "outcome": _fire(cursor, state, once, now, counts, args)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Redeem one banked Claude reset on a given day, only once no Opus or Fable account is selectable."
    )
    parser.add_argument("--day", required=True, help="UTC date YYYY-MM-DD this approval covers")
    parser.add_argument("--approval", required=True, help="path of the approval record behind it")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--send-host")
    args = parser.parse_args(argv)
    try:
        record = run(args)
    except Exception as exc:
        print(f"pool reset surge: failed ({type(exc).__name__}: {str(exc)[:300]})", file=sys.stderr)
        raise SystemExit(1) from None
    if args.live:
        with (args.state / "surge-runs.jsonl").open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    print(json.dumps(record, default=str), flush=True)


if __name__ == "__main__":
    main()
