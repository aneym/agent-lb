"""Bounded, read-only projection of the routing ledger."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.modules.routing_view.schemas import Candidate, Counts, Decision, DecisionsResponse, Outcome, Pick

BYTE_BUDGET = 8 * 1024 * 1024


def _stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _tail(path: Path, budget: int) -> tuple[list[dict[str, Any]], bool]:
    if not path.is_file():
        return [], False
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        start = max(0, size - budget)
        stream.seek(start)
        if start:
            stream.readline()  # discard the partial first row
        rows = []
        for line in stream:
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("event") in ("dispatch", "closeout", "of_decision"):
                    rows.append(row)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
    return rows, start > 0


def _closeout_matches(dispatch: dict, closeout: dict) -> bool:
    if dispatch.get("session_id") != closeout.get("session_id"):
        return False
    digest = closeout.get("prompt_sha256")
    if digest:
        return dispatch.get("prompt_sha256") == digest
    name = closeout.get("name")
    if name:
        return str(dispatch.get("name") or "").lower() == str(name).lower()
    seat = closeout.get("subagent_type")
    return bool(seat) and str(dispatch.get("subagent_type") or "").lower() == str(seat).lower()


def _candidate(row: dict) -> Candidate:
    # of_decision writes candidate IDs, not expanded seat metadata.
    return Candidate(
        seat=_string(row.get("seat") or row.get("id")),
        model=_string(row.get("model")),
        score=_number(row.get("score")),
        excluded_reason=_string(row.get("excludedReason") or row.get("reason")),
    )


def _projection(row: dict, index: int, closeout: dict | None, match: str) -> Decision:
    kind = row["event"]
    is_dispatch = kind == "dispatch"
    pick = (
        Pick(
            seat=_string(row.get("subagent_type")),
            model=_string(row.get("model")),
            subagent_type=_string(row.get("subagent_type")),
        )
        if is_dispatch
        else Pick(
            seat=_string(row.get("seat")),
            model=_string(row.get("model")),
            subagent_type=_string(row.get("subagent_type")),
        )
        if row.get("seat")
        else None
    )
    if is_dispatch:
        state = (
            "failed"
            if row.get("denied")
            else "open"
            if closeout is None
            else "ok"
            if closeout.get("ok") is True
            else "failed"
        )
    else:
        state = "ok" if row.get("seat") and row.get("seat") != "driver" else "failed"
    outcome = Outcome(
        state=state,
        duration_s=_number(closeout.get("duration_s")) if closeout else None,
        tokens_in=closeout.get("tokens_in") if closeout and isinstance(closeout.get("tokens_in"), int) else None,
        tokens_out=closeout.get("tokens_out") if closeout and isinstance(closeout.get("tokens_out"), int) else None,
        # Raw closeout errors may contain a final response or prompt. Never expose them.
        error="subagent failed" if closeout and closeout.get("ok") is not True else None,
        match=match,
    )
    candidates = []
    if not is_dispatch:
        candidates = (
            [_candidate(c) for c in row.get("candidates", []) if isinstance(c, dict)]
            if isinstance(row.get("candidates"), list)
            else []
        )
        excluded = row.get("excluded")
        if isinstance(excluded, list):
            candidates.extend(_candidate(c) for c in excluded if isinstance(c, dict))
    stable_id = (
        _string(row.get("decision_id") or row.get("id"))
        or hashlib.sha256(f"{index}:{row.get('ts')}:{kind}".encode()).hexdigest()[:16]
    )
    return Decision(
        id=stable_id,
        ts=_stamp(row.get("ts")),
        session_id=_string(row.get("session_id")),
        task_class=_string(row.get("task_class")),
        kind=kind,
        pick=pick,
        candidates=candidates,
        abstained=bool(row.get("abstain")) if not is_dispatch else False,
        recheck=_string(row.get("validation")) if not is_dispatch else None,
        fallback=_string(row.get("fallback")) if not is_dispatch else None,
        decider_ms=_number(row.get("decider_ms")) if not is_dispatch else None,
        decider_cost_usd=_number(row.get("decider_usd")) if not is_dispatch else None,
        policy_version=row.get("policy_version") if isinstance(row.get("policy_version"), (str, int)) else None,
        outcome=outcome,
    )


def read_decisions(
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    task_class: str | None = None,
    outcome: Literal["ok", "failed", "no_pick", "open"] | None = None,
    limit: int = 100,
    byte_budget: int = BYTE_BUDGET,
) -> DecisionsResponse:
    path = Path(os.environ.get("ROUTE_LEDGER") or Path.home() / ".claude/logs/dispatch.jsonl").expanduser()
    rows, truncated = _tail(path, byte_budget)
    # Iterate forward so a closeout can only consume a preceding, unambiguous dispatch.
    open_rows: list[tuple[int, dict]] = []
    paired: dict[int, tuple[dict, str]] = {}
    ambiguous: set[int] = set()
    for index, row in enumerate(rows):
        if row["event"] == "dispatch" and not row.get("denied"):
            open_rows.append((index, row))
        elif row["event"] == "closeout":
            if row.get("matched") is False or row.get("match") == "prompt_hash_ambiguous":
                continue
            matches = [(i, d) for i, d in open_rows if _closeout_matches(d, row)]
            # A name or seat can disambiguate a digest, but never guess between identical keys.
            for field in ("subagent_type", "name"):
                wanted = row.get(field)
                if len(matches) > 1 and wanted:
                    narrowed = [(i, d) for i, d in matches if str(d.get(field) or "").lower() == str(wanted).lower()]
                    if narrowed:
                        matches = narrowed
            if len(matches) == 1:
                i, _ = matches[0]
                paired[i] = row, "exact"
                open_rows = [(j, d) for j, d in open_rows if j != i]
            elif len(matches) > 1:
                ambiguous.update(i for i, _ in matches)
    lower = (
        since.astimezone(timezone.utc)
        if since and since.tzinfo
        else since.replace(tzinfo=timezone.utc)
        if since
        else None
    )
    upper = (
        until.astimezone(timezone.utc)
        if until and until.tzinfo
        else until.replace(tzinfo=timezone.utc)
        if until
        else None
    )
    filtered: list[tuple[Decision, bool]] = []
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row["event"] == "closeout":
            continue
        stamp = _stamp(row.get("ts"))
        if stamp is None or lower and stamp < lower or upper and stamp >= upper:
            continue
        if task_class is not None and row.get("task_class") != task_class:
            continue
        close, match = paired.get(index, (None, "ambiguous" if index in ambiguous else "none"))
        decision = _projection(row, index, close, match)
        if outcome == "no_pick":
            if decision.kind != "of_decision" or row.get("pick") is not None:
                continue
        elif outcome and decision.outcome.state != outcome:
            continue
        filtered.append((decision, row.get("pick") is None if row["event"] == "of_decision" else False))
    counts = Counts(
        total=len(filtered),
        fallbacks=sum(bool(d.fallback) for d, _ in filtered),
        no_pick=sum(no_pick for _, no_pick in filtered),
        failed=sum(d.outcome.state == "failed" for d, _ in filtered),
    )
    return DecisionsResponse(
        decisions=[d for d, _ in filtered[:limit]], counts=counts, ledger_path=str(path), truncated=truncated
    )
