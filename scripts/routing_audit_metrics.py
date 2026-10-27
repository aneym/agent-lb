from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta

from routing_audit_values import date, job, label, number  # type: ignore[missing-import]


def timestamp_rows(rows, field, name, errors=None):
    valid = [row for row in rows if date(row.get(field)) is not None]
    skipped = len(rows) - len(valid)
    if skipped and errors is not None:
        errors.append(f"{name}: {skipped} unparseable timestamps")
    return valid


def quota_points(requests, usage, since, errors=None):

    requests = timestamp_rows(requests, "requested_at", "requests", errors)
    usage = timestamp_rows(usage, "recorded_at", "usage", errors)
    points, previous = defaultdict(lambda: defaultdict(float)), {}
    accounts = defaultdict(list)
    for request in requests:
        accounts[(request.get("account_id"), request.get("provider"))].append(request)
    account_times = {}
    for key, rows in accounts.items():
        rows.sort(key=lambda r: date(r["requested_at"]))
        account_times[key] = [date(r["requested_at"]) for r in rows]
    for sample in usage:
        key = (sample["account_id"], sample["provider"], sample["window"])
        before = previous.get(key)
        previous[key] = sample
        stamp = date(sample["recorded_at"])
        if not before or stamp < since or before["reset_at"] != sample["reset_at"]:
            continue
        start = date(before["recorded_at"])

        if start < since:
            continue
        times = account_times.get((key[0], key[1]), [])
        candidates = accounts[(key[0], key[1])][bisect_right(times, start) : bisect_right(times, stamp)]
        weights = [
            sum(number(r.get(k)) for k in ("input_tokens", "output_tokens", "cache_read_tokens")) for r in candidates
        ]
        total = sum(weights)
        burn = max(0, number(sample["used_percent"]) - number(before["used_percent"]))
        if total:
            for row, weight in zip(candidates, weights):
                sid = row.get("client_session_id") or row.get("session_id")
                points[(sid, row.get("model"))][key[1]] += burn * weight / total
    return points


def summarize(runs, verdicts, merged, reverts, requests, usage, since, until, errors=None):
    requests = timestamp_rows(requests, "requested_at", "requests", errors)
    usage = timestamp_rows(usage, "recorded_at", "usage", errors)
    selected = [r for r in runs if since <= date(r["ts"]) < until]
    points = quota_points(requests, usage, since)
    groups = {}
    request_models = defaultdict(list)
    receipt_counts = defaultdict(int)
    for request in requests:
        request_models[request.get("model")].append(request)
        receipt_counts[(request.get("client_session_id") or request.get("session_id"), request.get("model"))] += 1
    fleet_spend = sum(
        number(r.get("cost_usd"))
        for r in requests
        if r.get("provider") == "anthropic" and since <= date(r["requested_at"]) < until
    )
    fleet_weekly = 0.0
    previous = {}
    for sample in usage:
        key = (sample["account_id"], sample["provider"], sample["window"])
        before = previous.get(key)
        previous[key] = sample
        stamp = date(sample["recorded_at"])
        if (
            before
            and sample["provider"] == "anthropic"
            and since <= stamp < until
            and before["reset_at"] == sample["reset_at"]
        ):
            fleet_weekly += max(0, number(sample["used_percent"]) - number(before["used_percent"]))
    by_pr = defaultdict(list)
    for row in verdicts:
        stamp = date(row.get("ts"))
        if stamp:
            by_pr[row.get("pr")].append(row)
    merges = {p["number"]: date(p.get("mergedAt")) for p in merged}
    for run in selected:
        model, kind = label(run.get("rung") or run.get("model")), job(run)
        row = groups.setdefault(
            (model, kind),
            dict(
                model=model,
                job=kind,
                runs=0,
                finished=0,
                accepted=0,
                acceptance_observed=0,
                redone=0,
                fix_rounds=0,
                review_fails=0,
                reverted_48h=0,
                minutes=0.0,
                claude_weekly_points=0.0,
                openai_weekly_points=0.0,
                cursor_envelope_tokens=0.0,
                devin_envelope_tokens=0.0,
                anthropic_spend_usd=0.0,
            ),
        )
        row["runs"] += 1
        row["finished"] += run.get("ok") is True
        row["minutes"] += number(run.get("wall_s")) / 60
        cwd, stamp = run.get("cwd"), date(run["ts"])
        seat = run.get("agent_type") or run.get("subagent_type") or run.get("name")
        ended = stamp + timedelta(seconds=number(run.get("wall_s")))
        rejected = run.get("accepted") is False or ("accepted" not in run and run.get("ok") is False)
        row["redone"] += bool(
            cwd
            and rejected
            and any(
                other.get("cwd") == cwd
                and job(other) == kind
                and (other.get("agent_type") or other.get("subagent_type") or other.get("name")) == seat
                and 0 < (date(other["ts"]) - ended).total_seconds() <= 3600
                for other in runs
            )
        )
        if isinstance(run.get("accepted"), bool):
            row["acceptance_observed"] += 1
            row["accepted"] += run["accepted"]
            row["fix_rounds"] += number(run.get("fix_rounds"))
        sid = run.get("session_id")
        quota = points.get((sid, run.get("model")), {})
        if not quota:
            matching = []
            for request in request_models[run.get("model")]:
                request_stamp = date(request["requested_at"])
                if request.get("model") != run.get("model") or not stamp <= request_stamp <= ended:
                    continue
                contenders = [
                    r
                    for r in selected
                    if r.get("model") == run.get("model")
                    and date(r["ts"]) <= request_stamp <= date(r["ts"]) + timedelta(seconds=number(r.get("wall_s")))
                ]
                if contenders:
                    matching.append((request, len(contenders)))
            allocated = defaultdict(float)
            for request, count in matching:
                request_sid = request.get("client_session_id") or request.get("session_id")
                for provider, amount in points.get((request_sid, request.get("model")), {}).items():
                    allocated[provider] += amount / max(1, receipt_counts[(request_sid, request.get("model"))]) / count
            quota = allocated
        row["anthropic_spend_usd"] += sum(
            number(r.get("cost_usd"))
            for r in requests
            if r.get("provider") == "anthropic"
            and (r.get("client_session_id") or r.get("session_id")) == sid
            and r.get("model") == run.get("model")
            and since <= date(r["requested_at"]) < until
        )
        row["claude_weekly_points"] += quota.get("anthropic", 0)
        row["openai_weekly_points"] += quota.get("openai", 0)
        vendor = run.get("vendor")
        if vendor in ("cursor", "devin"):
            row[vendor + "_envelope_tokens"] += sum(number(run.get(k)) for k in ("tokens_in", "tokens_out"))
        run["_group"] = (model, kind)

    for pr, items in by_pr.items():
        items.sort(key=lambda item: date(item["ts"]))
        sessions = {v.get("session_id") or v.get("session") for v in items} - {None}
        authors = [
            r
            for r in selected
            if job(r) != "review"
            and (str(r.get("pr", r.get("pr_number"))) == str(pr) or r.get("session_id") in sessions)
        ]
        for item in items:
            if not since <= date(item["ts"]) < until:
                continue
            reviewers = [
                r
                for r in selected
                if job(r) == "review"
                and (
                    str(r.get("pr", r.get("pr_number"))) == str(pr)
                    or (item.get("session_id") and r.get("session_id") == item["session_id"])
                )
            ]
            if item.get("pass") is False and reviewers:
                groups[reviewers[-1]["_group"]]["review_fails"] += 1
        if not authors:
            continue
        group = groups[authors[-1]["_group"]]
        outcomes = [v for v in items if not v.get("override") and isinstance(v.get("pass"), bool)]
        if outcomes and "accepted" not in authors[-1]:
            group["acceptance_observed"] += 1
            group["accepted"] += outcomes[-1]["pass"] is True
            group["fix_rounds"] += sum(
                a["pass"] is False and b["pass"] is True
                for a, b in zip(outcomes, outcomes[1:])
                if since <= date(b["ts"]) < until
            )
        merge = merges.get(pr)
        group["reverted_48h"] += bool(
            merge
            and any(r["pr"] == pr and timedelta(0) <= date(r["ts"]) - merge <= timedelta(hours=48) for r in reverts)
        )
    for row in groups.values():
        for field in ("minutes", "claude_weekly_points", "openai_weekly_points"):
            row[field + "_per_finished"] = row[field] / row["finished"] if row["finished"] else None
        row["claude_weekly_points_status"] = "observed" if requests else "unverified"
        if not requests:
            row["claude_weekly_points_per_finished"] = None
            row["openai_weekly_points_per_finished"] = None
        row["claude_weekly_points_fleet_estimate"] = (
            row["anthropic_spend_usd"] * fleet_weekly / fleet_spend if fleet_spend else None
        )
        if row["anthropic_spend_usd"] > 0 and row["claude_weekly_points"] == 0:
            row["claude_weekly_points_status"] = "quantized"
            row["claude_weekly_points"] = None
            row["claude_weekly_points_per_finished"] = None
        row["accepted_rate"] = row["accepted"] / row["acceptance_observed"] if row["acceptance_observed"] else None
    return sorted(groups.values(), key=lambda row: (row["job"], row["model"]))
