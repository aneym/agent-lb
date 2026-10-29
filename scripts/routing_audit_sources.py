from __future__ import annotations

import concurrent.futures
import json
import re
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from routing_audit_values import command, date, document, job, number  # type: ignore[missing-import]

LIVE_DB = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
PRIMARY_URL_FILE = Path.home() / ".agent-lb/primary-database.url"
VERDICT = re.compile(r"<!--\s*rails-verdict\s+(\{.*?\})\s*-->", re.S)


def live_dsn():
    try:
        url = PRIMARY_URL_FILE.read_text().strip() or LIVE_DB
    except OSError:
        url = LIVE_DB
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url) + "?connect_timeout=5"


def dispatch(path, since, until, errors):

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
                key = (
                    row.get("source", "agent"),
                    row.get("session_id"),
                    row.get("name") or row.get("agent_type") or row.get("agent_id") or row.get("subagent_type"),
                )
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

    requests, usage = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="routing-audit-") as temporary:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(Path(temporary) / "store.db") as copy:
                    source.backup(copy)
                    copy.row_factory = sqlite3.Row
                    fields = {r[1] for r in copy.execute("PRAGMA table_info(request_logs)")}
                    allowed = (
                        "requested_at",
                        "session_id",
                        "client_session_id",
                        "account_id",
                        "provider",
                        "model",
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "cache_read_tokens",
                        "cost_usd",
                    )
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
        query = 'SELECT json_agg(r) FROM (SELECT account_id,provider,"window",recorded_at,used_percent,reset_at '
        query += "FROM usage_history WHERE \"window\" IN ('secondary','weekly','7d') "
        query += "AND recorded_at < '" + until.isoformat() + "' ORDER BY recorded_at) r;"
        raw = command(
            [
                "psql",
                live_dsn(),
                "-w",
                "-At",
                "-c",
                query,
            ],
            errors,
            "live quota",
        )
        try:
            live = json.loads(raw) if raw.strip() else []
            if live:
                usage = live
        except ValueError:
            errors.append("live quota: invalid JSON")
        query = "SELECT json_agg(r) FROM (SELECT requested_at,session_id,client_session_id,account_id,provider,model,"
        query += "input_tokens,output_tokens,cache_read_tokens,cost_usd FROM request_logs WHERE requested_at >= '"
        query += since.isoformat() + "' AND requested_at < '" + until.isoformat() + "') r;"
        raw = command(
            [
                "psql",
                live_dsn(),
                "-w",
                "-At",
                "-c",
                query,
            ],
            errors,
            "live requests",
            timeout=90,
        )
        try:
            live = json.loads(raw) if raw.strip() else []
            if live:
                requests = live
        except ValueError:
            errors.append("live requests: invalid JSON")
    return requests, usage


def prs(since, until, errors):
    args = [
        "gh",
        "pr",
        "list",
        "--repo",
        "shelf-group/agent-rails",
        "--state",
        "merged",
        "--search",
        "merged:>=" + (since - timedelta(hours=48)).strftime("%Y-%m-%d"),
        "--limit",
        "1000",
        "--json",
        "number,title,mergedAt,headRefName",
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
        raw = document(
            ["gh", "api", f"repos/shelf-group/agent-rails/issues/{pr['number']}/comments", "--paginate", "--slurp"],
            failures,
            "PR comments",
        )
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
    raw = command(
        [
            git,
            "-C",
            str(clone),
            "log",
            "origin/main",
            "--grep=^Revert",
            "--since=" + since.isoformat(),
            "--until=" + until.isoformat(),
            "--format=%cI%x09%B%x00",
        ],
        errors,
        "revert log",
        timeout=90,
    )
    for record in raw.split("\0"):
        if "\t" not in record:
            continue
        timestamp, text = record.strip().split("\t", 1)
        stamp = date(timestamp)
        if not stamp or not since <= stamp < until:
            continue
        original_prs = {int(n) for n in re.findall(r"(?:#|/pull/)(\d+)\b", text)}
        for sha in re.findall(r"This reverts commit ([0-9a-f]{40})", text):
            subject = command([git, "-C", str(clone), "show", "-s", "--format=%s", sha], errors, "reverted commit")
            original_prs.update(int(n) for n in re.findall(r"#(\d+)\b", subject))
        reverts.extend({"pr": pr, "ts": stamp.isoformat()} for pr in original_prs)
    if any(
        (failure.startswith("PR comments:") or failure.startswith("PR list:")) and "row limit" not in failure
        for failure in errors
    ):
        errors.append("verdict fallback: queue_pr.py stores verdicts in GitHub issue comments, not a local store")

    return (
        verdicts,
        [{"number": p["number"], "mergedAt": p.get("mergedAt"), "headRefName": p.get("headRefName")} for p in entries],
        reverts,
    )


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
                accepted = outcome is True or str(outcome).upper() == "PASS"
                known = isinstance(outcome, bool) or str(outcome).upper() in ("PASS", "FAIL")
                cwd = value.get("worktree", value.get("cwd"))
                candidates = [
                    r
                    for r in runs
                    if job(r) == "implement"
                    and (
                        (
                            cwd
                            and r.get("cwd") == cwd
                            and stamp
                            and abs((date(r["ts"]) - stamp).total_seconds()) <= 86400
                        )
                        or (session and r.get("session_id") == session and not cwd)
                    )
                ]
                if known and candidates:
                    run = min(candidates, key=lambda r: abs((date(r["ts"]) - stamp).total_seconds()) if stamp else 0)
                    run["accepted"] = accepted
                    run["acceptance_source"] = "fold"
                    run["fix_rounds"] = number(value.get("fix_rounds"))
                    matched.add(id(run))
                for key in ("pieces", "results", "units"):
                    if key in value:
                        walk(value[key])

        walk(payload)
    return len(matched)


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
