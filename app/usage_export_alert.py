from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ALERT_TO = "factory"
LOW_HOURS = 3.0
REARM_HOURS = 6.0
FALLBACK_SENDER = Path.home() / ".agent-rails/factory-runtime/bin/orch-watch-alert"


def _load(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"armed": True}
    return state if isinstance(state, dict) else {"armed": True}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(state))
    os.replace(temp, path)


def _send(message: str) -> None:
    sender = shutil.which("orch-watch-alert") or str(FALLBACK_SENDER)
    result = subprocess.run(
        [sender, "--message", message],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=dict(os.environ, ORCH_WATCH_ALERT_TO=os.environ.get("ORCH_WATCH_ALERT_TO") or ALERT_TO),
    )
    if result.returncode:
        reason = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(f"orch-watch-alert exited {result.returncode}: {(reason[-1] if reason else '')[:300]}")


def _ours(headroom: dict[str, Any]) -> dict[str, Any]:
    return (headroom.get("points_by_owner") or {}).get("ours") or {}


def _message(headroom: dict[str, Any]) -> str:
    ours = _ours(headroom)
    return (
        f"agent-lb: our own accounts are low: {ours['hours_left_last_hour_rate']} h of weekly all-models room left "
        f"at the last hour's pace ({ours['points_per_hour_last_1h']} points an hour; {ours['hours_left_24h_rate']} h at "
        f"the 24 h average), {ours['points_left']} points on {ours['accounts_with_room']} of our accounts; next weekly "
        f"reset on ours {ours['next_weekly_reset']} in {ours['next_weekly_reset_hours']} h. Teammate-owned accounts are "
        "not counted. Source: headroom.json points_by_owner.ours."
    )


def check(headroom: dict[str, Any], state_path: Path, generated_at: str) -> str:
    state = _load(state_path)
    ours = _ours(headroom)
    hours = ours.get("hours_left_last_hour_rate")
    reset_hours = ours.get("next_weekly_reset_hours")
    if hours is None:
        return "alert: no figure"
    if not state.get("armed", True):
        if hours > REARM_HOURS:
            _save(state_path, {**state, "armed": True, "rearmed_at": generated_at})
            return f"alert: rearmed at {hours} h"
        return f"alert: held at {hours} h"
    if hours < LOW_HOURS and (reset_hours is None or reset_hours > hours):
        _send(_message(headroom))
        _save(state_path, {"armed": False, "fired_at": generated_at, "fired_hours": hours})
        return f"alert: sent to {ALERT_TO} at {hours} h"
    return f"alert: armed at {hours} h"
