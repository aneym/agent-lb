from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

FIELD = "fable_for_hard_things"
FLAG_HOST = "ax102"
FLAG_PATH = "/home/jobs/.agent-rails/factory/pool-pacing/flag.json"
FLAG_SCHEMA = "pool-pacing/1"
COVER_HOURS = 48
FABLE_LINE_PCT = 70.0


def default_flag() -> str:
    return FLAG_PATH if socket.gethostname().split(".")[0] == FLAG_HOST else f"{FLAG_HOST}:{FLAG_PATH}"


def _fetch(spec: str) -> str:
    host, sep, path = spec.partition(":")
    if not sep or spec.startswith("/"):
        return Path(spec).read_text()
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "cat", path]
    done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        raise OSError(f"ssh {host} cat exited {done.returncode}")
    return done.stdout


def read_flag(spec: str) -> dict[str, Any]:
    found: dict[str, Any] = {"source": spec}
    try:
        flag = json.loads(_fetch(spec))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {**found, "on": True, "read": False, "error": type(exc).__name__}
    if not isinstance(flag, dict) or flag.get("schema") != FLAG_SCHEMA or not isinstance(flag.get("on"), bool):
        return {**found, "on": True, "read": False, "error": f"not a {FLAG_SCHEMA} flag with a true or false on"}
    return {**found, "on": flag["on"], "read": True, "since": flag.get("since"), "set_by": flag.get("set_by")}


def _when(value: str | None) -> datetime | None:
    return None if not value else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _cover(headroom: dict[str, Any]) -> dict[str, Any]:
    now = _when(headroom["window"]["as_of_utc"])
    assert now is not None
    until = now + timedelta(hours=COVER_HOURS)
    turning = {}
    for account in headroom["accounts_now"]:
        reset = _when(account.get("weekly_all_resets"))
        used = account.get("weekly_all_points_used")
        if reset is not None and now < reset <= until and used:
            turning[account["account"]] = used
    pace = headroom.get("points_pace", {})
    per_hour = pace.get("weekly_all_points_per_hour_24h")
    left = float(pace.get("points_left") or 0.0)
    turning_points = round(sum(turning.values()), 2)
    needed = None if per_hour is None else round(float(per_hour) * COVER_HOURS, 2)
    return {
        "points_left": left,
        "points_turning_48h": turning_points,
        "accounts_turning_48h": turning,
        "points_per_hour_24h": per_hour,
        "points_needed_48h": needed,
        "covered": needed is not None and left + turning_points >= needed,
    }


def judge(headroom: dict[str, Any], flag: dict[str, Any]) -> dict[str, Any]:
    cover = _cover(headroom)
    fable_max = headroom.get("pool_summary", {}).get("fable_bucket_used_pct_max")
    have = round(cover["points_left"] + cover["points_turning_48h"], 2)
    fails = []
    if cover["points_needed_48h"] is None:
        fails.append("the 24-hour points pace could not be read, so the 48-hour cover is unknown")
    elif not cover["covered"]:
        fails.append(
            f"{have:g} points left and turning in the next 48 hours do not cover the "
            f"{cover['points_needed_48h']:g} that 48 hours at the 24-hour pace needs"
        )
    if fable_max is None or fable_max >= FABLE_LINE_PCT:
        shown = "unread" if fable_max is None else f"{fable_max:g}%"
        fails.append(f"the fullest Fable account is at {shown}, not under {FABLE_LINE_PCT:g}%")
    if not flag["read"]:
        fails.append(f"the pacing flag could not be read ({flag['error']}), so pacing counts as on")
    elif flag["on"]:
        fails.append("pacing is on")
    reason = (
        "All three hold: the pool covers 48 hours, every Fable account is under 70%, and pacing is off."
        if not fails
        else "Not allowed: " + "; ".join(fails) + "."
    )
    return {
        "allowed": not fails,
        "reason": reason,
        "inputs": {
            **cover,
            "fable_bucket_used_pct_max": fable_max,
            "fable_line_pct": FABLE_LINE_PCT,
            "pacing_on": flag["on"],
            "pacing_flag": flag,
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"Print {FIELD} for a headroom.json and a pool pacing flag.")
    parser.add_argument("--headroom", type=Path, required=True)
    parser.add_argument("--flag", default=default_flag(), help="flag path, or host:path read over ssh")
    args = parser.parse_args(argv)
    result = judge(json.loads(args.headroom.read_text()), read_flag(args.flag))
    json.dump({FIELD: result}, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
