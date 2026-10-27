from __future__ import annotations

import json
from collections import defaultdict

from routing_audit_values import JOBS, command, date, label, number  # type: ignore[missing-import]

ACCEPTED_OUTCOMES = ("accepted", "held_pass", "override")


def load_outcomes(path, since, until, errors):

    if not path.exists():
        return "none yet", []
    records = []
    try:
        with path.open() as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError("outcome")
                    stamp = date(row.get("ts"))
                    if stamp is None:
                        raise ValueError("timestamp")
                    if since <= stamp < until:
                        row = dict(row)
                        row["ts"] = stamp.isoformat()
                        records.append(row)
                except (TypeError, ValueError) as exc:
                    errors.append(f"outcomes: {type(exc).__name__}")
    except OSError as exc:
        errors.append(f"outcomes: {type(exc).__name__}")
    return "outcomes", records


def _attempt_count(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return value


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize_outcomes(records):

    groups = defaultdict(list)
    for row in records:
        kind = str(row.get("job") or "implement").lower()
        if kind not in JOBS:
            kind = "implement"
        groups[(label(row.get("seat")), label(row.get("model")), kind)].append(row)
    rungs = []
    for (seat, model, kind), items in groups.items():
        accepted = overrides = rejected = infra = dropped = multi = 0
        fix_rounds = 0.0
        walls = []
        for item in items:
            outcome = str(item.get("outcome") or "")
            if _attempt_count(item.get("attempts")) > 1:
                multi += 1
            if item.get("wall_s") is not None:
                walls.append(number(item.get("wall_s")))
            if outcome == "infra_blocked":
                infra += 1
            elif outcome == "dropped":
                dropped += 1
            elif outcome == "rejected":
                rejected += 1
            elif outcome in ACCEPTED_OUTCOMES:
                accepted += 1
                fix_rounds += number(item.get("fix_rounds"))
                overrides += outcome == "override"
        counted = accepted + rejected
        rungs.append(
            dict(
                seat=seat,
                model=model,
                job=kind,
                pieces=len(items),
                accepted=accepted,
                overrides=overrides,
                rejected=rejected,
                infra_blocked=infra,
                dropped=dropped,
                acceptance_observed=counted,
                accepted_rate=accepted / counted if counted else None,
                fix_rounds=fix_rounds,
                fix_rounds_per_accepted=fix_rounds / accepted if accepted else None,
                median_wall_s=_median(walls),
                multi_attempt=multi,
                acceptance_source="outcomes",
            )
        )
    return sorted(rungs, key=lambda row: (row["job"], row["seat"], row["model"]))


def _rung_name(entry):
    if isinstance(entry, dict):
        return str(entry.get("id") or "")
    return entry


def resolve_rungs(rungs, errors):

    cache = {}
    resolved = []
    for rung in rungs:
        item = dict(rung)
        alias = item.get("model")
        if alias not in cache:
            if not alias:
                cache[alias] = None
            else:
                raw = command(["route", "resolve", str(alias)], errors, f"route resolve {alias}")
                cache[alias] = raw.strip() or None
        item["resolved"] = cache[alias]
        resolved.append(item)
    return resolved


def row_matches_rung(row, rung):

    if not isinstance(rung, dict) or row.get("seat") != rung.get("seat"):
        return False
    resolved = rung.get("resolved")
    if not resolved:
        return False
    model = row.get("model")
    if model == resolved:
        return True
    if rung.get("seat") in ("cursor-seat", "devin-seat"):
        return False
    return model is None or model == "unknown"


def _matching_rungs(rungs, rung, kind):
    if isinstance(rung, dict):
        return [row for row in rungs if (row.get("job") or "implement") == kind and row_matches_rung(row, rung)]
    return [
        row
        for row in rungs
        if row["job"] == kind and rung in (row["model"], row["seat"], f"{row['seat']}/{row['model']}")
    ]


def _combine_rungs(matches):
    accepted = sum(row["accepted"] for row in matches)
    observed = sum(row["acceptance_observed"] for row in matches)
    fix_rounds = sum(row["fix_rounds"] for row in matches)
    walls = [row["median_wall_s"] for row in matches if row["median_wall_s"] is not None]
    return dict(
        pieces=sum(row["pieces"] for row in matches),
        accepted=accepted,
        overrides=sum(row["overrides"] for row in matches),
        rejected=sum(row["rejected"] for row in matches),
        infra_blocked=sum(row["infra_blocked"] for row in matches),
        dropped=sum(row.get("dropped", 0) for row in matches),
        acceptance_observed=observed,
        accepted_rate=accepted / observed if observed else None,
        fix_rounds=fix_rounds,
        fix_rounds_per_accepted=fix_rounds / accepted if accepted else None,
        median_wall_s=sum(walls) / len(walls) if walls else None,
        multi_attempt=sum(row.get("multi_attempt", 0) for row in matches),
    )


def overlay_outcomes(rows, rungs):

    grouped = defaultdict(list)
    for rung in rungs:
        grouped[(rung["model"], rung["job"])].append(rung)
    for row in rows:
        matches = grouped.get((row["model"], row["job"]))
        if not matches:
            continue
        combined = _combine_rungs(matches)
        row.update(combined)
        row["acceptance_source"] = "outcomes"


def _outcome_proposals(rungs, ladder):
    result, insufficient = [], []
    for kind in ("implement", "mechanical"):
        order = ladder.get(kind, [])
        chosen = []
        for rung in order:
            matches = _matching_rungs(rungs, rung, kind)
            count = sum(row["pieces"] - row["infra_blocked"] - row.get("dropped", 0) for row in matches)
            if count < 20:
                insufficient.append(f"not enough outcomes (n={count})")
                chosen.append(None)
                continue
            combined = _combine_rungs(matches)
            chosen.append(
                combined if combined["accepted_rate"] is not None and combined["median_wall_s"] is not None else None
            )
        for index, upper in enumerate(order):
            for lower_index, lower in enumerate(order[index + 1 :], start=index + 1):
                slower, faster = chosen[lower_index], chosen[index]
                if not slower or not faster:
                    continue
                if (
                    slower["accepted_rate"] >= faster["accepted_rate"] - 1 / 6
                    and slower["median_wall_s"] <= 1.10 * faster["median_wall_s"]
                ):
                    result.append(
                        f"propose: move {_rung_name(lower)} above {_rung_name(upper)} ({kind}; "
                        f"accepted {slower['accepted']}/{slower['acceptance_observed']} vs "
                        f"{faster['accepted']}/{faster['acceptance_observed']}; "
                        f"median wall_s {slower['median_wall_s']:.2f} vs {faster['median_wall_s']:.2f})"
                    )
    if result:
        return result
    deduped = list(dict.fromkeys(insufficient))
    return deduped or ["no change"]


def proposals(rows, ladder, outcome_rungs=None):
    if outcome_rungs is not None:
        return _outcome_proposals(outcome_rungs, ladder)
    result = []
    for kind in ("implement", "mechanical"):
        order = [_rung_name(item) for item in ladder.get(kind, [])]
        eligible = {
            r["model"]: r
            for r in rows
            if r["job"] == kind
            and r["finished"] >= 6
            and r["acceptance_observed"] >= 6
            and r["accepted_rate"] is not None
        }
        for i, upper in enumerate(order):
            for lower in order[i + 1 :]:
                a, b = eligible.get(lower), eligible.get(upper)
                if not a or not b:
                    continue
                if (
                    a["accepted_rate"] >= b["accepted_rate"] - 1 / 6
                    and a["minutes_per_finished"] <= 1.10 * b["minutes_per_finished"]
                ):
                    result.append(
                        f"propose: move {lower} above {upper} ({kind}; "
                        f"accepted {a['accepted']}/{a['acceptance_observed']} vs "
                        f"{b['accepted']}/{b['acceptance_observed']}; "
                        f"minutes/finished {a['minutes_per_finished']:.2f} vs "
                        f"{b['minutes_per_finished']:.2f})"
                    )
    return result or ["no change"]
