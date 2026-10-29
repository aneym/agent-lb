#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routing_audit_metrics import summarize, timestamp_rows  # type: ignore[missing-import]
from routing_audit_outcomes import load_outcomes, overlay_outcomes, proposals, resolve_rungs, summarize_outcomes  # type: ignore[missing-import]
from routing_audit_reports import daily_report, write_daily_report, write_outputs  # type: ignore[missing-import]
from routing_audit_sources import dispatch, fold_acceptance, prs, stand_ins, store  # type: ignore[missing-import]
from routing_audit_values import command, date, document, job  # type: ignore[missing-import]


from app.core.thread_delivery import thread_id


def main():
    parser = argparse.ArgumentParser(
        description="Read-only daily routing observations; proposals never change the ladder."
    )
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.scheduled and datetime.now(UTC).hour != 11:
        return
    end = datetime.now(UTC).replace(hour=11, minute=0, second=0, microsecond=0)
    if end > datetime.now(UTC):
        end -= timedelta(days=1)
    until = date(args.until) if args.until else end
    since = date(args.since) if args.since else until - timedelta(days=1) if until else None
    if not since or not until or since >= until:
        parser.error("since and until must be ISO timestamps with until after since")
    home, errors = Path.home(), []
    rolling = until - timedelta(days=7)
    runs = dispatch(home / ".claude/logs/dispatch.jsonl", min(since, rolling), until + timedelta(hours=1), errors)
    requests, usage = store(home / ".agent-lb/store.db", min(since, rolling), until, errors)
    pools = document(["route", "pools", "--json"], errors, "route pools")
    pool_rows = pools.get("pools", []) if isinstance(pools, dict) else []
    cursor = next((p for p in pool_rows if p.get("id") == "cursor-models"), {})
    percent = cursor.get("percentUsed", cursor.get("percent_used"))

    ladder = {}
    try:
        table = json.loads((home / ".agents/policy/coding-agents/routing-table.json").read_text())
        for kind in ("implement", "mechanical"):
            rungs = table.get("ladders", {}).get(table.get("ladder", "interim"), {}).get(kind, [])
            ladder[kind] = resolve_rungs([r for r in rungs if isinstance(r, dict) and r.get("id")], errors)
            for run in runs:
                if job(run) != kind or run.get("rung"):
                    continue
                model = str(run.get("model", ""))
                effort = run.get("effort") or run.get("reasoning_effort")
                if not effort:
                    effort = next((e for e in ("low", "medium", "high", "xhigh") if model.endswith("-" + e)), None)
                family = next((f for f in ("sol", "grok", "composer", "swe", "sonnet") if f in model), None)
                candidates = [
                    r
                    for r in rungs
                    if isinstance(r, dict)
                    and family
                    and family in str(r.get("model", ""))
                    and r.get("effort") == effort
                ]
                if len(candidates) == 1:
                    run["rung"] = candidates[0]["id"]
    except (OSError, ValueError, AttributeError):
        errors.append("ladder ordering: unavailable")
    outcomes_source, outcome_records = load_outcomes(
        home / ".agent-lb/audits/routing/outcomes.jsonl", min(since, rolling), until, errors
    )
    day_records = [row for row in outcome_records if since <= date(row["ts"]) < until]
    et_day = until.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    report_root = args.out if args.out else home / ".agent-rails/factory"

    try:
        payload = daily_report(
            {"since": since.isoformat(), "until": until.isoformat()}, day_records, ladder, percent, list(errors)
        )
        write_daily_report(payload, report_root / "daily" / et_day / "routing.json", errors)
    except Exception as exc:
        errors.append(f"daily report: {type(exc).__name__}")
    verdicts, merges, reverts = prs(min(since, rolling), until, errors)
    joined = fold_acceptance(home, runs, errors)

    for run in runs:
        if job(run) == "review" or "accepted" in run or not run.get("cwd"):
            continue
        branch = command(["git", "-C", run["cwd"], "branch", "--show-current"], [], "worktree branch").strip()
        pr = next((p for p in merges if branch and p.get("headRefName") == branch), None)
        if pr:
            run["pr"] = pr["number"]
    requests = timestamp_rows(requests, "requested_at", "requests", errors)
    usage = timestamp_rows(usage, "recorded_at", "usage", errors)
    rows = summarize(runs, verdicts, merges, reverts, requests, usage, since, until)
    week = summarize(runs, verdicts, merges, reverts, requests, usage, rolling, until)
    daily_rungs = summarize_outcomes([row for row in outcome_records if since <= date(row["ts"]) < until])
    rolling_rungs = summarize_outcomes([row for row in outcome_records if rolling <= date(row["ts"]) < until])
    overlay_outcomes(rows, daily_rungs)
    overlay_outcomes(week, rolling_rungs)
    proposal = (
        proposals(week, ladder, outcome_rungs=rolling_rungs)
        if outcomes_source == "outcomes"
        else proposals(week, ladder)
    )
    swaps = stand_ins(home / ".agent-lb/agent-lb.out.log", since, until, errors)
    errors.append("stand-in events: current server does not emit swap/return log lines; counts unverified")
    observed = sum(r["acceptance_observed"] for r in rows)
    total_runs = sum(r["runs"] for r in rows)
    notes = [
        f"Acceptance join rate: {observed}/{total_runs} daily runs; {joined} rolling runs joined to fold results.",
        "Execution ok is not acceptance; missing PR attribution cannot earn a ladder move.",
        "Redone is the same-worktree, same-job 60-minute heuristic, not proof of rejection.",
        "Weekly points are token-weighted positive same-reset snapshot deltas, not exact per-run quota.",
        "Missing quota attribution is unverified; zero attributed points is not proof of zero consumption.",
        "Recent merges have not completed their 48-hour revert observation window.",
    ]
    if until.weekday() == 0:
        notes.append("Monday: recalibrate Cursor-models percent against the Cursor dashboard (retests 5 and 6).")
    line = (
        f"routing audit {until:%Y-%m-%d}: {sum(r['finished'] for r in rows)}/{sum(r['runs'] for r in rows)} "
        f"finished; swaps {swaps['swaps']}/{swaps['swap_backs']}; cursor-models "
        f"{percent if percent is not None else 'unverified'}%; {'; '.join(proposal)}; "
        f"unverified sources {len(errors)}"
    )
    report = dict(
        since=since.isoformat(),
        until=until.isoformat(),
        rows=rows,
        rolling_7d=week,
        proposals=proposal,
        stand_ins=swaps,
        cursor_models_percent=percent,
        notes=notes,
        errors=errors,
        line=line,
        outcomes_source=outcomes_source,
        outcome_rungs=daily_rungs,
    )
    out = args.out or home / ".agent-lb/audits/routing"

    page = out / "routing-daily-audit.html" if args.out else home / ".claude/pretty-docs/routing-daily-audit.html"
    write_outputs(report, out, page)
    try:
        payload = daily_report(report, day_records, ladder, percent, errors)
        write_daily_report(payload, report_root / "daily" / et_day / "routing.json", errors)
    except Exception as exc:
        errors.append(f"daily report: {type(exc).__name__}")
    if args.post:
        before = len(errors)
        command(
            ["t3", "thread", "send", thread_id("rails"), line, "--mode", "queue"],
            errors,
            "t3 thread send",
        )
        if len(errors) != before:
            write_outputs(report, out, page)
    print(line)


if __name__ == "__main__":
    main()
