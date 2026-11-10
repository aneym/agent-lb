from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import pool_reset_call, pool_reset_readings
from app.core.claude_reset_week import week_start
from app.pool_reset_rule import Redemption, banked_total, decide, minutes_left, pace

DEFAULT_STATE = Path.home() / ".agent-rails" / "factory" / "pool-reset-auto"
CONFIRM_WAIT_SECONDS = 210
CONFIRM_POLL_SECONDS = 15
PREFIX = "[Factory → Rails]"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=1, default=str) + "\n")
    temporary.replace(path)


def _held(state: Path, push: str | None) -> bool:
    if (state / "hold").exists():
        return True
    if not push:
        return False
    host, _, directory = push.partition(":")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, f"test -e {directory}/hold"]
    try:
        return subprocess.run(command, capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _push(state: Path, push: str | None) -> str:
    if not push:
        return "push off"
    command = ["rsync", "-a", "--mkpath", "--exclude", "hold", "--exclude", ".*.tmp", "--timeout=60"]
    command += ["-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", f"{state}/", push.rstrip("/") + "/"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"push {type(exc).__name__}"
    return "pushed" if done.returncode == 0 else f"push exit {done.returncode}"


def _lasts(points: float | None, per_hour: float | None) -> str:
    minutes = minutes_left(points or 0.0, per_hour)
    if minutes is None:
        return "an unknown time (no pace in the last hour)"
    if minutes >= 90:
        return f"about {minutes / 60:.1f} hours"
    return f"about {minutes:.0f} minutes"


def _pool_after(cursor: Any) -> dict[str, Any]:
    readings = pool_reset_readings.headroom(cursor)
    points = readings["points_pace"]["points_left"]
    per_hour = pace(readings)
    return {
        "points_left": points,
        "points_per_hour_last_1h": per_hour,
        "lasts": _lasts(points, per_hour),
        "banked_total": banked_total(readings),
        "banked_by_account": {a["account"]: a["resets_held"] for a in readings["accounts_now"]},
    }


def _finish(cursor: Any, state: Path, receipt: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    pool = _pool_after(cursor)
    receipt.update(
        confirmed=True,
        after=pool_reset_readings.around(cursor, receipt["account"], datetime.fromisoformat(receipt["at"])),
        pool_after=pool,
        banked_on_account_after=pool["banked_by_account"].get(receipt["account"]),
        banked_pool_after=pool["banked_total"],
        week_cap_left=receipt["week_cap"] - receipt["reset_number"],
    )
    moment = datetime.fromisoformat(receipt["at"])
    line = (
        f"{PREFIX} {moment.strftime('%H:%M')}Z UTC: reset {receipt['reset_number']} of {receipt['week_cap']} "
        f"this week redeemed on account {receipt['account']} (confirmed by agent-lb's readings). The pool now has "
        f"{pool['points_left']:g} points left, {pool['lasts']} at the last hour's pace; {pool['banked_total']} resets "
        "still banked across the pool."
    )
    receipt["line"] = line
    receipt["sent"] = pool_reset_call.send(args.send_host, f"pool-reset-{receipt['account']}-{_stamp(moment)}", line)
    _write(state / "receipts" / f"{_stamp(moment).replace(':', '')}-{receipt['account']}.json", receipt)
    (state / "pending.json").unlink(missing_ok=True)
    return receipt


def _confirm(cursor: Any, state: Path, receipt: dict[str, Any], args: argparse.Namespace, wait: float) -> str:
    deadline = time.monotonic() + wait
    at = datetime.fromisoformat(receipt["at"])
    while True:
        if pool_reset_readings.dropped(pool_reset_readings.around(cursor, receipt["account"], at)):
            _finish(cursor, state, receipt, args)
            return f"confirmed {receipt['account']}"
        if time.monotonic() >= deadline:
            return f"unconfirmed {receipt['account']}"
        time.sleep(CONFIRM_POLL_SECONDS)


def _fail(state: Path, decision: dict[str, Any], failures: list[str], args: argparse.Namespace, now: datetime) -> str:
    reason = "; ".join(failures)
    (state / "hold").write_text(f"stopped {_stamp(now)} after a failed redemption: {reason}\n")
    line = (
        f"{PREFIX} {now.strftime('%H:%M')}Z UTC: reset {decision['week_used'] + 1} of {decision['week_cap']} this week "
        f"failed ({reason}); nothing was redeemed and the timer is on hold until its hold file is removed. The pool has "
        f"{decision['points_left']:g} points left, {_lasts(decision['points_left'], decision['points_per_hour_last_1h'])} "
        f"at the last hour's pace; {decision['banked_total']} resets still banked across the pool."
    )
    sent = pool_reset_call.send(args.send_host, f"pool-reset-failed-{_stamp(now)}", line)
    _write(
        state / "receipts" / f"{_stamp(now).replace(':', '')}-failed.json",
        {"line": line, "sent": sent, "failures": failures},
    )
    return f"failed: {reason}"


def _redeem(
    cursor: Any,
    state: Path,
    decision: dict[str, Any],
    ids: dict[str, str],
    records: list[Redemption],
    args: argparse.Namespace,
    now: datetime,
) -> str:
    override = decision["override_daily_limit"]
    failures = []
    for entry in decision["candidates"][:2]:
        account = entry["account"]
        try:
            seen = pool_reset_call.preview(ids[account])
            if not seen["redeemable_now"]:
                failures.append(f"{account} not redeemable ({seen['ineligible_reason']})")
                continue
            at = datetime.now(timezone.utc)
            result = pool_reset_call.consume(ids[account], override)
        except pool_reset_call.CallError as exc:
            failures.append(f"{account} call failed ({exc})")
            continue
        if result.get("status") != "redeemed":
            failures.append(f"{account} not redeemed ({result.get('code')})")
            continue
        receipt = {
            "kind": "redemption",
            "by": "agent-lb-pool-reset-auto timer",
            "account": account,
            "at": at.isoformat(),
            "result": {key: result.get(key) for key in ("status", "code", "windowsReset")},
            "pick": entry,
            "trigger": decision["reason"],
            "before": pool_reset_readings.around(cursor, account, at),
            "pool_before": {k: decision[k] for k in ("points_left", "usable_points", "points_per_hour_last_1h")},
            "override_daily_limit": override,
            "reset_number": decision["week_used"] + 1,
            "week_cap": decision["week_cap"],
            "confirmed": False,
        }
        _write(state / "pending.json", receipt)
        return _confirm(cursor, state, receipt, args, CONFIRM_WAIT_SECONDS)
    return _fail(state, decision, failures, args, now)


def _load_redemptions(path: Path) -> list[Redemption]:
    return [
        Redemption(account=item["account"], at=datetime.fromisoformat(item["at"]), confirmed=item["confirmed"])
        for item in json.loads(path.read_text())
    ]


def _run(args: argparse.Namespace) -> tuple[str, dict[str, Any], str]:
    state: Path = args.state
    state.mkdir(parents=True, exist_ok=True)
    held = _held(state, args.push)
    with pool_reset_readings.connect() as connection, connection.cursor() as cursor:
        ids = pool_reset_readings.owned(cursor)
        if args.readings:
            readings = json.loads(args.readings.read_text())
            now = datetime.fromisoformat(args.now) if args.now else datetime.fromisoformat(readings["generated_at"])
            mode = "copy"
        else:
            readings = pool_reset_readings.headroom(cursor)
            now = pool_reset_readings.db_now(cursor).replace(tzinfo=timezone.utc)
            mode = "live" if args.live else "dry"
        if args.redemptions:
            records = _load_redemptions(args.redemptions)
            mode = "copy"
        else:
            records = pool_reset_readings.redemptions(cursor, week_start(now))
        decision = decide(readings, now=now, owned=set(ids), redemptions=records, held=held)
        recent = any(record.at > now - timedelta(days=1) for record in records)
        decision["override_daily_limit"] = recent and 1 <= decision["week_used"] < decision["week_cap"]
        outcome = "no action"
        pending = state / "pending.json"
        if mode == "live" and pending.exists() and not held:
            outcome = _confirm(cursor, state, json.loads(pending.read_text()), args, 0)
        elif mode == "live" and decision["action"] == "redeem":
            outcome = _redeem(cursor, state, decision, ids, records, args, now)
    return mode, decision, outcome


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Redeem a banked Claude reset when the pool is about to run dry.")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--live", action="store_true", help="act on the decision; without it the run is a dry run")
    parser.add_argument("--readings", type=Path, help="decide on a copy of headroom readings (dry)")
    parser.add_argument("--redemptions", type=Path, help="decide with this list of redemptions (dry)")
    parser.add_argument("--now", help="ISO time to decide at, with --readings")
    parser.add_argument("--send-host", help="ssh host whose t3 sends the lines")
    parser.add_argument("--push", help="host:dir that mirrors the state folder and may hold a hold file")
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        mode, decision, outcome = _run(args)
    except Exception as exc:
        print(f"pool reset auto: failed ({type(exc).__name__}: {str(exc)[:300]})", file=sys.stderr)
        raise SystemExit(1) from None
    record = {
        "at": decision["now"],
        "mode": mode,
        "action": decision["action"],
        "reason": decision["reason"],
        "usable_points": decision["usable_points"],
        "points_per_hour_last_1h": decision["points_per_hour_last_1h"],
        "minutes_left": decision["minutes_left_at_last_hour_pace"],
        "would_take": decision["would_take"],
        "week": f"{decision['week_used']} of {decision['week_cap']}",
        "outcome": outcome,
        "wall_s": round(time.monotonic() - started, 2),
    }
    if mode != "copy":
        with (args.state / "runs.jsonl").open("a") as handle:
            handle.write(json.dumps(record) + "\n")
        _write(args.state / "last.json", {**record, "decision": decision})
        record["push"] = _push(args.state, args.push)
    print(json.dumps({**record, "decision": decision} if mode != "live" else record, indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
