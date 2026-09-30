#!/usr/bin/env python3
"""Read-only daily routing observations; proposals never change the ladder."""
from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import re
import sqlite3
import subprocess
import tempfile
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

VERDICT = re.compile(r"<!--\s*rails-verdict\s+(\{.*?\})\s*-->", re.S)
JOBS = ("implement", "mechanical", "explore", "review")


def date(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def number(value):
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


def label(value):
    text = str(value or "unknown")
    return text if re.fullmatch(r"[A-Za-z0-9_.:/ -]{1,100}", text) else "unknown"


def command(args, errors, name, timeout=25):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            errors.append(f"{name}: exit {result.returncode}")
            return ""
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{name}: {type(exc).__name__}")
        return ""


def document(args, errors, name):
    raw = command(args, errors, name)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        errors.append(f"{name}: invalid JSON")
        return {}


def job(row):
    value = str(row.get("task_class") or row.get("agent_type") or row.get("subagent_type") or "").lower()
    if any(word in value for word in ("verif", "review")):
        return "review"
    return next((kind for kind in JOBS if kind in value), None)


def dispatch(path, since, until, errors):
    """Port E16's dispatch/closeout merge and same-worktree redo heuristic."""
    records = {}
    try:
        with path.open() as source:
            for index, line in enumerate(source):
                try:
                    row = json.loads(line)
                except ValueError:
                    errors.append("dispatch: invalid JSON")
                    continue
                if not isinstance(row, dict):
                    continue
                stamp = date(row.get("ts", row.get("timestamp")))
                if stamp is None or not since <= stamp < until:
                    continue
                key = (row.get("source", "agent"), row.get("session_id"),
                       row.get("name") or row.get("agent_type") or row.get("agent_id") or row.get("subagent_type"))
                if not key[1]:
                    key = (*key, index)
                merged = records.setdefault(key, {})
                first = merged.get("ts", stamp.isoformat())
                merged.update(row)
                merged["ts"] = first
    except OSError as exc:
        errors.append(f"dispatch: {type(exc).__name__}")
    return [row for row in records.values() if job(row)]


def store(path, since, until, errors):
    """SQLite backup folds committed WAL; only allowlisted columns leave the copy."""
    requests, usage = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="routing-audit-") as temporary:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(Path(temporary) / "store.db") as copy:
                    source.backup(copy)
                    copy.row_factory = sqlite3.Row
                    fields = {r[1] for r in copy.execute("PRAGMA table_info(request_logs)")}
                    allowed = ("requested_at", "session_id", "client_session_id", "account_id", "provider", "model",
                               "input_tokens", "output_tokens", "cached_input_tokens", "cache_read_tokens", "cost_usd")
                    selected = [field for field in allowed if field in fields]
                    if "requested_at" not in selected:
                        raise sqlite3.OperationalError("missing request timestamps")
                    for row in copy.execute("SELECT " + ",".join(selected) + " FROM request_logs"):
                        item = dict(row)
                        stamp = date(item["requested_at"])
                        if stamp is None or since <= stamp < until:
                            requests.append(item)
                    for row in copy.execute(
                        "SELECT account_id,provider,window,recorded_at,used_percent,reset_at FROM usage_history "
                        "WHERE window IN ('secondary','weekly','7d') ORDER BY recorded_at"
                    ):
                        item = dict(row)
                        stamp = date(item["recorded_at"])
                        if stamp is None or stamp < until:
                            usage.append(item)
    except (OSError, sqlite3.Error) as exc:
        errors.append(f"store: {type(exc).__name__}")
    if path == Path.home() / ".agent-lb/store.db" and not any(r.get("provider") == "anthropic" for r in usage):
        # Studio's live service is PostgreSQL; the SQLite store can be an old
        # OpenAI-only mirror. Query only accounting columns, never accounts.
        query = "SELECT json_agg(r) FROM (SELECT account_id,provider,\"window\",recorded_at,used_percent,reset_at "
        query += "FROM usage_history WHERE \"window\" IN ('secondary','weekly','7d') "
        query += "AND recorded_at < '" + until.isoformat() + "' ORDER BY recorded_at) r;"
        raw = command(["psql", "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb?connect_timeout=5",
                       "-w", "-At", "-c", query],
                      errors, "live quota")
        try:
            live = json.loads(raw) if raw.strip() else []
            if live:
                usage = live
        except ValueError:
            errors.append("live quota: invalid JSON")
        query = "SELECT json_agg(r) FROM (SELECT requested_at,session_id,client_session_id,account_id,provider,model,"
        query += "input_tokens,output_tokens,cache_read_tokens,cost_usd FROM request_logs WHERE requested_at >= '"
        query += since.isoformat() + "' AND requested_at < '" + until.isoformat() + "') r;"
        raw = command(["psql", "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb?connect_timeout=5",
                       "-w", "-At", "-c", query],
                      errors, "live requests", timeout=90)
        try:
            live = json.loads(raw) if raw.strip() else []
            if live:
                requests = live
        except ValueError:
            errors.append("live requests: invalid JSON")
    return requests, usage


def timestamp_rows(rows, field, name, errors=None):
    valid = [row for row in rows if date(row.get(field)) is not None]
    skipped = len(rows) - len(valid)
    if skipped and errors is not None:
        errors.append(f"{name}: {skipped} unparseable timestamps")
    return valid


def quota_points(requests, usage, since, errors=None):
    """Positive same-reset weekly deltas, token-weighted across matching requests."""
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
        # A boundary-spanning sample cannot be attributed from a truncated request window.
        if start < since:
            continue
        times = account_times.get((key[0], key[1]), [])
        candidates = accounts[(key[0], key[1])][bisect_right(times, start):bisect_right(times, stamp)]
        weights = [sum(number(r.get(k)) for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
                   for r in candidates]
        total = sum(weights)
        burn = max(0, number(sample["used_percent"]) - number(before["used_percent"]))
        if total:
            for row, weight in zip(candidates, weights):
                sid = row.get("client_session_id") or row.get("session_id")
                points[(sid, row.get("model"))][key[1]] += burn * weight / total
    return points


def prs(since, until, errors):
    args = [
        "gh", "pr", "list", "--repo", "shelf-group/agent-rails", "--state", "merged", "--search",
        "merged:>=" + (since - timedelta(hours=48)).strftime("%Y-%m-%d"), "--limit", "1000",
        "--json", "number,title,mergedAt,headRefName",
    ]
    payload = []
    for attempt in range(2):
        failures = []
        raw = command(args, failures, "PR list", timeout=90)
        try:
            payload = json.loads(raw) if raw else []
        except ValueError:
            failures.append("PR list: invalid JSON")
        if not failures:
            break
        if attempt == 1:
            errors.extend(failures)
    entries = payload if isinstance(payload, list) else []
    if len(entries) == 1000:
        errors.append("PR list: 1000-row limit; completeness unverified")

    def comments(pr):
        failures = []
        raw = document(["gh", "api", f"repos/shelf-group/agent-rails/issues/{pr['number']}/comments",
                        "--paginate", "--slurp"], failures, "PR comments")
        found = []
        for page in raw if isinstance(raw, list) else []:
            for comment in page if isinstance(page, list) else []:
                for match in VERDICT.finditer(comment.get("body", "")):
                    try:
                        item = json.loads(match[1])
                        if isinstance(item, dict):
                            found.append(item | {"pr": pr["number"]})
                    except ValueError:
                        failures.append("PR verdict: invalid JSON")
        return found, failures

    verdicts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for found, failures in executor.map(comments, entries):
            verdicts.extend(found)
            errors.extend(failures)
    reverts = []
    clone = Path("/Volumes/StudioExt/repos/agent-rails")
    git = "/opt/homebrew/bin/git" if Path("/opt/homebrew/bin/git").exists() else "git"
    command([git, "-C", str(clone), "fetch", "-q"], errors, "revert fetch", timeout=90)
    raw = command([git, "-C", str(clone), "log", "origin/main", "--grep=^Revert",
                   "--since=" + since.isoformat(), "--until=" + until.isoformat(),
                   "--format=%cI%x09%B%x00"], errors, "revert log", timeout=90)
    for record in raw.split("\0"):
        if "\t" not in record:
            continue
        timestamp, text = record.strip().split("\t", 1)
        stamp = date(timestamp)
        if not stamp or not since <= stamp < until:
            continue
        original_prs = {int(n) for n in re.findall(r"(?:#|/pull/)(\d+)\b", text)}
        for sha in re.findall(r"This reverts commit ([0-9a-f]{40})", text):
            subject = command([git, "-C", str(clone), "show", "-s", "--format=%s", sha],
                              errors, "reverted commit")
            original_prs.update(int(n) for n in re.findall(r"#(\d+)\b", subject))
        reverts.extend({"pr": pr, "ts": stamp.isoformat()} for pr in original_prs)
    if any((failure.startswith("PR comments:") or failure.startswith("PR list:"))
           and "row limit" not in failure for failure in errors):
        errors.append("verdict fallback: queue_pr.py stores verdicts in GitHub issue comments, not a local store")
    # Bodies/titles and arbitrary comment data must never appear in the output.
    return verdicts, [{"number": p["number"], "mergedAt": p.get("mergedAt"),
                       "headRefName": p.get("headRefName")} for p in entries], reverts


ACCEPTED_OUTCOMES = ("accepted", "held_pass", "override")


def load_outcomes(path, since, until, errors):
    """Outcome ledger for the audit window. A missing file is an empty source, not an error."""
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
    """Per rung (seat + model): acceptance excludes infra_blocked; override counts as accepted and itself."""
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
        rungs.append(dict(
            seat=seat, model=model, job=kind, pieces=len(items), accepted=accepted, overrides=overrides,
            rejected=rejected, infra_blocked=infra, dropped=dropped, acceptance_observed=counted,
            accepted_rate=accepted / counted if counted else None, fix_rounds=fix_rounds,
            fix_rounds_per_accepted=fix_rounds / accepted if accepted else None,
            median_wall_s=_median(walls),
            multi_attempt=multi, acceptance_source="outcomes",
        ))
    return sorted(rungs, key=lambda row: (row["job"], row["seat"], row["model"]))


def _matching_rungs(rungs, rung_id, kind):
    return [row for row in rungs if row["job"] == kind and rung_id in (
        row["model"], row["seat"], f"{row['seat']}/{row['model']}")]


def _combine_rungs(matches):
    accepted = sum(row["accepted"] for row in matches)
    observed = sum(row["acceptance_observed"] for row in matches)
    fix_rounds = sum(row["fix_rounds"] for row in matches)
    walls = [row["median_wall_s"] for row in matches if row["median_wall_s"] is not None]
    return dict(
        pieces=sum(row["pieces"] for row in matches), accepted=accepted,
        overrides=sum(row["overrides"] for row in matches),
        rejected=sum(row["rejected"] for row in matches),
        infra_blocked=sum(row["infra_blocked"] for row in matches),
        dropped=sum(row.get("dropped", 0) for row in matches),
        acceptance_observed=observed, accepted_rate=accepted / observed if observed else None,
        fix_rounds=fix_rounds, fix_rounds_per_accepted=fix_rounds / accepted if accepted else None,
        median_wall_s=sum(walls) / len(walls) if walls else None,
        multi_attempt=sum(row.get("multi_attempt", 0) for row in matches),
    )


def overlay_outcomes(rows, rungs):
    """Outcome counts replace the dispatch/PR acceptance join for a rung that has ledger rows."""
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
        for rung_id in order:
            matches = _matching_rungs(rungs, rung_id, kind)
            count = sum(row["pieces"] - row["infra_blocked"] - row.get("dropped", 0) for row in matches)
            if count < 20:
                insufficient.append(f"not enough outcomes (n={count})")
                chosen.append(None)
                continue
            combined = _combine_rungs(matches)
            chosen.append(combined if combined["accepted_rate"] is not None and combined["median_wall_s"] is not None
                          else None)
        for index, upper in enumerate(order):
            for lower_index, lower in enumerate(order[index + 1:], start=index + 1):
                slower, faster = chosen[lower_index], chosen[index]
                if not slower or not faster:
                    continue
                if (slower["accepted_rate"] >= faster["accepted_rate"] - 1 / 6
                        and slower["median_wall_s"] <= 1.10 * faster["median_wall_s"]):
                    result.append(
                        f"propose: move {lower} above {upper} ({kind}; "
                        f"accepted {slower['accepted']}/{slower['acceptance_observed']} vs "
                        f"{faster['accepted']}/{faster['acceptance_observed']}; "
                        f"median wall_s {slower['median_wall_s']:.2f} vs {faster['median_wall_s']:.2f})"
                    )
    if result:
        return result
    deduped = list(dict.fromkeys(insufficient))
    return deduped or ["no change"]


def fold_acceptance(home, runs, errors):
    paths = {}
    for path in (home / ".agent-rails/workflows/results").glob("*.json"):
        paths[path] = (None, datetime.fromtimestamp(path.stat().st_mtime, UTC))
    registries = home / ".agent-rails/workflows/tabs"
    for path in list(registries.glob("*.json*")) + list(registries.glob("by-tab/**/*.json*")):
        try:
            record = json.loads(path.read_text())
            started = record.get("started")
            stamp = datetime.fromtimestamp(started, UTC) if isinstance(started, (int, float)) else date(started)
            if record.get("out"):
                paths[Path(record["out"]).expanduser()] = (record.get("session"), stamp)
        except (OSError, ValueError, TypeError):
            continue
    matched = set()
    for path, (session, stamp) in paths.items():
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        def walk(value):
            if isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, dict):
                outcome = value.get("latest_verdict", value.get("verdict"))
                if isinstance(outcome, dict):
                    outcome = outcome.get("pass")
                accepted = (outcome is True or str(outcome).upper() == "PASS")
                known = isinstance(outcome, bool) or str(outcome).upper() in ("PASS", "FAIL")
                cwd = value.get("worktree", value.get("cwd"))
                candidates = [r for r in runs if job(r) == "implement" and (
                    (cwd and r.get("cwd") == cwd and stamp and
                     abs((date(r["ts"]) - stamp).total_seconds()) <= 86400)
                    or (session and r.get("session_id") == session and not cwd))]
                if known and candidates:
                    run = min(candidates, key=lambda r: abs((date(r["ts"]) - stamp).total_seconds())
                              if stamp else 0)
                    run["accepted"] = accepted
                    run["acceptance_source"] = "fold"
                    run["fix_rounds"] = number(value.get("fix_rounds"))
                    matched.add(id(run))
                for key in ("pieces", "results", "units"):
                    if key in value:
                        walk(value[key])
        walk(payload)
    return len(matched)


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
    fleet_spend = sum(number(r.get("cost_usd")) for r in requests
                      if r.get("provider") == "anthropic" and since <= date(r["requested_at"]) < until)
    fleet_weekly = 0.0
    previous = {}
    for sample in usage:
        key = (sample["account_id"], sample["provider"], sample["window"])
        before = previous.get(key)
        previous[key] = sample
        stamp = date(sample["recorded_at"])
        if (before and sample["provider"] == "anthropic" and since <= stamp < until
                and before["reset_at"] == sample["reset_at"]):
            fleet_weekly += max(0, number(sample["used_percent"]) - number(before["used_percent"]))
    by_pr = defaultdict(list)
    for row in verdicts:
        stamp = date(row.get("ts"))
        if stamp:
            by_pr[row.get("pr")].append(row)
    merges = {p["number"]: date(p.get("mergedAt")) for p in merged}
    for run in selected:
        model, kind = label(run.get("rung") or run.get("model")), job(run)
        row = groups.setdefault((model, kind), dict(
            model=model, job=kind, runs=0, finished=0, accepted=0, acceptance_observed=0, redone=0,
            fix_rounds=0, review_fails=0, reverted_48h=0, minutes=0.0, claude_weekly_points=0.0,
            openai_weekly_points=0.0, cursor_envelope_tokens=0.0, devin_envelope_tokens=0.0,
            anthropic_spend_usd=0.0,
        ))
        row["runs"] += 1
        row["finished"] += run.get("ok") is True
        row["minutes"] += number(run.get("wall_s")) / 60
        cwd, stamp = run.get("cwd"), date(run["ts"])
        seat = run.get("agent_type") or run.get("subagent_type") or run.get("name")
        ended = stamp + timedelta(seconds=number(run.get("wall_s")))
        rejected = run.get("accepted") is False or ("accepted" not in run and run.get("ok") is False)
        row["redone"] += bool(cwd and rejected and any(
            other.get("cwd") == cwd and job(other) == kind
            and (other.get("agent_type") or other.get("subagent_type") or other.get("name")) == seat
            and 0 < (date(other["ts"]) - ended).total_seconds() <= 3600 for other in runs
        ))
        if isinstance(run.get("accepted"), bool):
            row["acceptance_observed"] += 1
            row["accepted"] += run["accepted"]
            row["fix_rounds"] += number(run.get("fix_rounds"))
        sid = run.get("session_id")
        quota = points.get((sid, run.get("model")), {})
        if not quota:
            # Launcher sessions own workflow requests; dispatch child sessions
            # do not always equal the billing session. Allocate by model and time.
            matching = []
            for request in request_models[run.get("model")]:
                request_stamp = date(request["requested_at"])
                if request.get("model") != run.get("model") or not stamp <= request_stamp <= ended:
                    continue
                contenders = [r for r in selected if r.get("model") == run.get("model")
                              and date(r["ts"]) <= request_stamp <= date(r["ts"]) +
                              timedelta(seconds=number(r.get("wall_s")))]
                if contenders:
                    matching.append((request, len(contenders)))
            allocated = defaultdict(float)
            for request, count in matching:
                request_sid = request.get("client_session_id") or request.get("session_id")
                for provider, amount in points.get((request_sid, request.get("model")), {}).items():
                    allocated[provider] += amount / max(1, receipt_counts[(request_sid, request.get("model"))]) / count
            quota = allocated
        row["anthropic_spend_usd"] += sum(
            number(r.get("cost_usd")) for r in requests if r.get("provider") == "anthropic"
            and (r.get("client_session_id") or r.get("session_id")) == sid
            and r.get("model") == run.get("model") and since <= date(r["requested_at"]) < until
        )
        row["claude_weekly_points"] += quota.get("anthropic", 0)
        row["openai_weekly_points"] += quota.get("openai", 0)
        vendor = run.get("vendor")
        if vendor in ("cursor", "devin"):
            row[vendor + "_envelope_tokens"] += sum(number(run.get(k)) for k in ("tokens_in", "tokens_out"))
        run["_group"] = (model, kind)
    # Each PR's chronological FAIL -> PASS transition is counted once, not on each dispatch run.
    for pr, items in by_pr.items():
        items.sort(key=lambda item: date(item["ts"]))
        sessions = {v.get("session_id") or v.get("session") for v in items} - {None}
        authors = [r for r in selected if job(r) != "review" and (
            str(r.get("pr", r.get("pr_number"))) == str(pr) or r.get("session_id") in sessions)]
        for item in items:
            if not since <= date(item["ts"]) < until:
                continue
            reviewers = [r for r in selected if job(r) == "review" and (
                str(r.get("pr", r.get("pr_number"))) == str(pr)
                or (item.get("session_id") and r.get("session_id") == item["session_id"]))]
            if item.get("pass") is False and reviewers:
                groups[reviewers[-1]["_group"]]["review_fails"] += 1
        if not authors:
            continue
        group = groups[authors[-1]["_group"]]
        outcomes = [v for v in items if not v.get("override") and isinstance(v.get("pass"), bool)]
        if outcomes and "accepted" not in authors[-1]:
            group["acceptance_observed"] += 1
            group["accepted"] += outcomes[-1]["pass"] is True
            group["fix_rounds"] += sum(a["pass"] is False and b["pass"] is True
                                      for a, b in zip(outcomes, outcomes[1:]) if since <= date(b["ts"]) < until)
        merge = merges.get(pr)
        group["reverted_48h"] += bool(merge and any(
            r["pr"] == pr and timedelta(0) <= date(r["ts"]) - merge <= timedelta(hours=48) for r in reverts
        ))
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


def proposals(rows, ladder, outcome_rungs=None):
    if outcome_rungs is not None:
        return _outcome_proposals(outcome_rungs, ladder)
    result = []
    for kind in ("implement", "mechanical"):
        order = ladder.get(kind, [])
        eligible = {r["model"]: r for r in rows if r["job"] == kind and r["finished"] >= 6
                    and r["acceptance_observed"] >= 6 and r["accepted_rate"] is not None}
        for i, upper in enumerate(order):
            for lower in order[i + 1:]:
                a, b = eligible.get(lower), eligible.get(upper)
                if not a or not b:
                    continue
                if (a["accepted_rate"] >= b["accepted_rate"] - 1 / 6
                        and a["minutes_per_finished"] <= 1.10 * b["minutes_per_finished"]):
                    result.append(f"propose: move {lower} above {upper} ({kind}; "
                                  f"accepted {a['accepted']}/{a['acceptance_observed']} vs "
                                  f"{b['accepted']}/{b['acceptance_observed']}; "
                                  f"minutes/finished {a['minutes_per_finished']:.2f} vs "
                                  f"{b['minutes_per_finished']:.2f})")
    return result or ["no change"]


def stand_ins(path, since, until, errors):
    swaps = backs = 0
    try:
        with path.open() as source:
            for line in source:
                stamp = date(line[:23].replace(",", "."))
                if not stamp or not since <= stamp < until:
                    continue
                lower = line.lower()
                swaps += "standing in" in lower or "stand-in swap:" in lower
                backs += "swap-back" in lower or "stand-in return:" in lower
    except OSError as exc:
        errors.append(f"stand-in log: {type(exc).__name__}")
    return {"swaps": swaps, "swap_backs": backs}


def render(report):
    columns = ("model", "job", "runs", "finished", "accepted", "redone", "fix_rounds", "review_fails",
               "reverted_48h", "minutes_per_finished", "claude_weekly_points_per_finished",
               "claude_weekly_points_status", "claude_weekly_points_fleet_estimate",
               "openai_weekly_points_per_finished", "cursor_envelope_tokens", "devin_envelope_tokens")
    lines = ["# Routing daily audit", "", report["line"], "", "| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for row in report["rows"]:
        lines.append("| " + " | ".join(str(row[k]) if row[k] is not None else "unverified" for k in columns) + " |")
    lines += ["", "## Rolling 7-day ladder", "", *report["proposals"], ""]
    if "outcomes_source" in report:
        lines += ["## Rung sources", ""]
        if report["outcomes_source"] == "none yet":
            lines.append("outcomes: none yet")
        for rung in report.get("outcome_rungs") or []:
            lines.append(f"{rung['seat']} {rung['model']} ({rung['job']}): {rung['acceptance_source']}")
        covered = {(rung["model"], rung["job"]) for rung in report.get("outcome_rungs") or []}
        for row in report["rows"]:
            if (row["model"], row["job"]) not in covered:
                lines.append(f"{row['model']} {row['job']}: {row.get('acceptance_source', 'dispatch')}")
        lines.append("")
    lines += ["## Notes", "", *report["notes"]]
    lines += ["", "Unverified sources: " + ("; ".join(report["errors"]) or "none")]
    return "\n".join(lines) + "\n"


def write_outputs(report, out, page):
    out.mkdir(parents=True, exist_ok=True)
    day = report["until"][:10]
    (out / (day + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    (out / (day + ".md")).write_text(render(report))
    history = []
    for path in sorted(out.glob("????-??-??.json"), reverse=True)[:14]:
        try:
            item = json.loads(path.read_text())
            history.append(item)
        except (OSError, ValueError):
            continue
    body = "".join("<tr><td>" + html.escape(r["until"][:10]) + "</td><td>" + html.escape(r["line"])
                   + "</td></tr>" for r in history)
    latest = history[0] if history else report
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Routing daily audit</title><link rel="stylesheet" href="_themes/agent-rails.css">'
                    '<style>body{max-width:1200px;margin:40px auto;padding:20px}pre{white-space:pre-wrap}'
                    'td,th{padding:12px;text-align:left;border-bottom:1px solid #ccc}</style><main>'
                    '<h1>Routing daily audit</h1><pre>' + html.escape(render(latest)) + '</pre>'
                    '<h2>14-day history</h2><table><tr><th>Day</th><th>Score</th></tr>'
                    + body + '</table></main></html>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    verdicts, merges, reverts = prs(min(since, rolling), until, errors)
    pools = document(["route", "pools", "--json"], errors, "route pools")
    pool_rows = pools.get("pools", []) if isinstance(pools, dict) else []
    cursor = next((p for p in pool_rows if p.get("id") == "cursor-models"), {})
    percent = cursor.get("percentUsed", cursor.get("percent_used"))
    # Read canonical ordering without importing the routing CLI or editing its table.
    ladder = {}
    try:
        table = json.loads((home / ".agents/policy/coding-agents/routing-table.json").read_text())
        for kind in ("implement", "mechanical"):
            rungs = table.get("ladders", {}).get(table.get("ladder", "interim"), {}).get(kind, [])
            ladder[kind] = [r.get("id") for r in rungs if isinstance(r, dict) and r.get("id")]
            for run in runs:
                if job(run) != kind or run.get("rung"):
                    continue
                model = str(run.get("model", ""))
                effort = run.get("effort") or run.get("reasoning_effort")
                if not effort:
                    effort = next((e for e in ("low", "medium", "high", "xhigh")
                                   if model.endswith("-" + e)), None)
                family = next((f for f in ("sol", "grok", "composer", "swe", "sonnet") if f in model), None)
                candidates = [r for r in rungs if isinstance(r, dict) and family
                              and family in str(r.get("model", "")) and r.get("effort") == effort]
                if len(candidates) == 1:
                    run["rung"] = candidates[0]["id"]
    except (OSError, ValueError, AttributeError):
        errors.append("ladder ordering: unavailable")
    joined = fold_acceptance(home, runs, errors)
    # PR branch fallback is read-only and only joins existing worktrees.
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
    outcomes_source, outcome_records = load_outcomes(
        home / ".agent-lb/audits/routing/outcomes.jsonl", min(since, rolling), until, errors)
    daily_rungs = summarize_outcomes(
        [row for row in outcome_records if since <= date(row["ts"]) < until])
    rolling_rungs = summarize_outcomes(
        [row for row in outcome_records if rolling <= date(row["ts"]) < until])
    overlay_outcomes(rows, daily_rungs)
    overlay_outcomes(week, rolling_rungs)
    proposal = (proposals(week, ladder, outcome_rungs=rolling_rungs)
                if outcomes_source == "outcomes" else proposals(week, ladder))
    swaps = stand_ins(home / ".agent-lb/agent-lb.out.log", since, until, errors)
    errors.append("stand-in events: current server does not emit swap/return log lines; counts unverified")
    observed = sum(r["acceptance_observed"] for r in rows)
    total_runs = sum(r["runs"] for r in rows)
    notes = [f"Acceptance join rate: {observed}/{total_runs} daily runs; {joined} rolling runs joined to fold results.",
             "Execution ok is not acceptance; missing PR attribution cannot earn a ladder move.",
             "Redone is the same-worktree, same-job 60-minute heuristic, not proof of rejection.",
             "Weekly points are token-weighted positive same-reset snapshot deltas, not exact per-run quota.",
             "Missing quota attribution is unverified; zero attributed points is not proof of zero consumption.",
             "Recent merges have not completed their 48-hour revert observation window."]
    if until.weekday() == 0:
        notes.append("Monday: recalibrate Cursor-models percent against the Cursor dashboard (retests 5 and 6).")
    line = (f"routing audit {until:%Y-%m-%d}: {sum(r['finished'] for r in rows)}/{sum(r['runs'] for r in rows)} "
            f"finished; swaps {swaps['swaps']}/{swaps['swap_backs']}; cursor-models "
            f"{percent if percent is not None else 'unverified'}%; {'; '.join(proposal)}; "
            f"unverified sources {len(errors)}")
    report = dict(since=since.isoformat(), until=until.isoformat(), rows=rows, rolling_7d=week,
                  proposals=proposal, stand_ins=swaps, cursor_models_percent=percent,
                  notes=notes, errors=errors, line=line, outcomes_source=outcomes_source,
                  outcome_rungs=daily_rungs)
    out = args.out or home / ".agent-lb/audits/routing"
    # An explicit output directory is a dry-run boundary: do not overwrite the published page.
    page = out / "routing-daily-audit.html" if args.out else home / ".claude/pretty-docs/routing-daily-audit.html"
    write_outputs(report, out, page)
    if args.post:
        before = len(errors)
        command(["lane-post", "post", "--to", "w5H:pNE", "--from", "routing-audit", "--kind", "info",
                 "--topic", "routing-audit", "--wake", "never", line], errors, "lane-post")
        if len(errors) != before:
            write_outputs(report, out, page)
    print(line)


if __name__ == "__main__":
    main()
