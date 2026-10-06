from __future__ import annotations

import fcntl
import json
import math
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field, StrictInt, model_validator

_LOCK = threading.Lock()


class Candidate(BaseModel):
    rung: str
    seat: str
    model: str
    pool: str | None = None
    capacity_key: str = Field(min_length=1)
    capacity: StrictInt = Field(ge=0)


class ReservationRequest(BaseModel):
    job: str = Field(min_length=1, max_length=200)
    task_class: str = Field(alias="class")
    host: str
    ttl_s: StrictInt = Field(ge=1, le=3600)
    pinned: bool = False
    candidates: list[Candidate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pinned_candidates(self) -> ReservationRequest:
        if self.pinned and len(self.candidates) != 1:
            raise ValueError("pinned reservations require exactly one candidate")
        return self


class HeartbeatRequest(BaseModel):
    ttl_s: StrictInt | None = Field(default=None, ge=1, le=3600)


class ReleaseRequest(BaseModel):
    outcome: str = Field(pattern=r"^(ok|failed|cancelled)$")


def _timestamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _store():
    path = Path(os.environ.get("AGENT_LB_RESERVATIONS_FILE") or "~/.agent-lb/reservations.json").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text()) if path.exists() else {"live": [], "recent": []}
            now = datetime.now(timezone.utc).replace(microsecond=0)
            stamp = _timestamp(now)
            live = []
            for row in state["live"]:
                if row["expires_at"] <= stamp:
                    state["recent"].append({**row, "outcome": "expired", "released_at": row["expires_at"]})
                else:
                    live.append(row)
            state["live"] = live
            cutoff = _timestamp(now - timedelta(hours=24))
            state["recent"] = [row for row in state["recent"] if row["released_at"] > cutoff]
            yield state, now
            temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(state))
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def reserve(body: ReservationRequest) -> dict:
    with _store() as (state, now):
        for row in state["live"]:
            if row["job"] == body.job:
                return {**row, "status": "reserved", "existing": True, "overflow_from": []}
        full = []
        for candidate in body.candidates:
            rows = [row for row in state["live"] if row["capacity_key"] == candidate.capacity_key]
            if len(rows) < candidate.capacity:
                row = {**candidate.model_dump(), "reservation_id": "rsv-" + uuid.uuid4().hex[:12],
                       "job": body.job, "class": body.task_class, "host": body.host, "pinned": body.pinned,
                       "ttl_s": body.ttl_s, "created_at": _timestamp(now),
                       "expires_at": _timestamp(now + timedelta(seconds=body.ttl_s))}
                state["live"].append(row)
                return {**row, "status": "reserved", "overflow_from": [item["rung"] for item in full]}
            full.append({"rung": candidate.rung, "capacity_key": candidate.capacity_key,
                         "live": len(rows), "capacity": candidate.capacity})
        expiries = [datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
                    for row in state["live"] if row["capacity_key"] == full[0]["capacity_key"]]
        wait = math.ceil((min(expiries) - now).total_seconds()) if expiries else 5
        return {"status": "wait", "wait": max(5, min(60, wait)), "reason": "all candidate capacity is reserved",
                "full": full}


def heartbeat(reservation_id: str, body: HeartbeatRequest) -> tuple[int, dict]:
    with _store() as (state, now):
        for row in state["live"]:
            if row["reservation_id"] == reservation_id:
                row["expires_at"] = _timestamp(now + timedelta(seconds=body.ttl_s or row["ttl_s"]))
                return 200, {**row, "status": "reserved"}
        for row in state["recent"]:
            if row["reservation_id"] == reservation_id:
                return 410, {**row, "status": "gone"}
        return 404, {"status": "gone", "reservation_id": reservation_id}


def release(reservation_id: str, body: ReleaseRequest) -> tuple[int, dict]:
    with _store() as (state, now):
        for row in state["live"]:
            if row["reservation_id"] == reservation_id:
                state["live"].remove(row)
                row.update(outcome=body.outcome, released_at=_timestamp(now))
                state["recent"].append(row)
                return 200, {**row, "status": "released"}
        for row in state["recent"]:
            if row["reservation_id"] == reservation_id:
                return 200, {**row, "status": "released"}
        return 404, {"status": "gone", "reservation_id": reservation_id}


def snapshot() -> dict:
    with _store() as (state, _now):
        return state
