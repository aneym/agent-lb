from __future__ import annotations

import glob
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.audit_transcripts import safe_label


def instant(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def fold_units(records, agent_points, since, until):
    groups = {}
    for workflow, record in records:
        if record.get("workflowName") not in ("fold-pipeline", "fold-pipeline-v2"):
            continue
        result = record.get("result") or {}
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            continue
        agents = [a for a in record.get("workflowProgress", []) if a.get("type") == "workflow_agent"]
        for piece in result.get("pieces", []):
            matched = []
            for agent in agents:
                prefix, separator, fragment = (agent.get("label") or "").partition(":")
                if separator and fragment and piece.get("title", "").startswith(fragment):
                    matched.append((re.sub(r"-r\d+$", "", prefix), agent))
            impl = next((a for phase, a in matched if phase == "impl"), None)
            starts = [instant(a["startedAt"]) for _, a in matched if a.get("startedAt")]
            ends = [
                instant(a["startedAt"]) + timedelta(milliseconds=a.get("durationMs") or 0)
                for _, a in matched
                if a.get("startedAt")
            ]
            completed = max(ends) if ends else instant(record["timestamp"])
            if impl is None or not since <= completed < until:
                continue
            key = (safe_label(impl.get("agentType")), safe_label(impl.get("model")))
            row = groups.setdefault(
                key,
                dict(
                    seat=key[0],
                    model=key[1],
                    pieces=0,
                    accepted=0,
                    status_counts={},
                    fix_rounds=0,
                    review_rounds=0,
                    wall_minutes=0.0,
                    quota_points_by_pool={},
                ),
            )
            row["pieces"] += 1
            status = str(piece.get("status", "")).lower()
            row["accepted"] += status in ("pass", "override")
            row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
            row["fix_rounds"] += piece.get("fix_rounds", sum(phase == "fix" for phase, _ in matched))
            row["review_rounds"] += sum(phase in ("verify", "verify-codex", "lens-risk") for phase, _ in matched)
            row["wall_minutes"] += (completed - min(starts)).total_seconds() / 60 if starts else 0
            for _, agent in matched:
                label = safe_label(agent.get("label"))
                points = agent_points.get(f"workflow:{workflow}:{label}", {})
                for pool, amount in points.items():
                    row["quota_points_by_pool"][pool] = row["quota_points_by_pool"].get(pool, 0) + amount
    return finish_units(groups)


STUDIO_MERGE_STATES = "/Volumes/StudioExt/repos/*/.git/studio-merge"


def read_jsonl(path):
    try:
        with open(path) as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    yield item
    except OSError:
        return


def merge_units(since, until, states=None):
    start, end = since.timestamp(), until.timestamp()
    groups = {}
    for state in sorted({str(Path(found).resolve()) for found in glob.glob(states or STUDIO_MERGE_STATES)}):
        repo = Path(state).parent.parent.name
        vendors = {
            item["branch"]: item.get("author_vendor")
            for item in read_jsonl(Path(state) / "receipts.jsonl")
            if item.get("branch") and item.get("author_vendor")
        }
        attempts = defaultdict(list)
        for item in read_jsonl(Path(state) / "log.jsonl"):
            at, branch = item.get("at"), item.get("branch")
            if isinstance(at, (int, float)) and branch and start <= at < end:
                attempts[branch].append((at, item.get("ok") is True, item.get("code")))
        for branch, tries in attempts.items():
            tries.sort(key=lambda attempt: attempt[0])
            merged = next((i for i, attempt in enumerate(tries) if attempt[1]), None)
            key = (f"studio_merge:{repo}", safe_label(vendors.get(branch)))
            row = groups.setdefault(
                key,
                dict(
                    seat=key[0],
                    model=key[1],
                    pieces=0,
                    accepted=0,
                    status_counts={},
                    fix_rounds=0,
                    review_rounds=0,
                    wall_minutes=0.0,
                    quota_points_by_pool={},
                ),
            )
            row["pieces"] += 1
            status = "pass" if merged is not None else "refused"
            row["accepted"] += merged is not None
            row["status_counts"][status] = row["status_counts"].get(status, 0) + 1
            refusals = tries[:merged] if merged is not None else tries
            row["fix_rounds"] += sum(1 for attempt in refusals if not attempt[1])
            if merged is not None:
                row["wall_minutes"] += (tries[merged][0] - tries[0][0]) / 60
    return finish_units(groups)


def finish_units(groups):
    for row in groups.values():
        accepted = row["accepted"]
        row["accept_rate"] = accepted / row["pieces"]
        seat_pieces = row["pieces"] - sum(
            row["status_counts"].get(status, 0) for status in ("infra_blocked", "pipeline_error")
        )
        row["seat_accept_rate"] = accepted / seat_pieces if seat_pieces else None
        row["provisional"] = row["pieces"] < 10
        row["wall_minutes_per_accepted_piece"] = row["wall_minutes"] / accepted if accepted else None
        row["points_per_accepted_piece"] = {
            pool: amount / accepted if accepted else None for pool, amount in row["quota_points_by_pool"].items()
        }
    return sorted(groups.values(), key=lambda r: (r["seat"], r["model"]))
