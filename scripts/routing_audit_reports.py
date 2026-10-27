from __future__ import annotations

import html
import json
import os
import tempfile
from datetime import UTC

from routing_audit_outcomes import row_matches_rung, summarize_outcomes  # type: ignore[missing-import]
from routing_audit_values import JOBS, date  # type: ignore[missing-import]


def render(report):
    columns = (
        "model",
        "job",
        "runs",
        "finished",
        "accepted",
        "redone",
        "fix_rounds",
        "review_fails",
        "reverted_48h",
        "minutes_per_finished",
        "claude_weekly_points_per_finished",
        "claude_weekly_points_status",
        "claude_weekly_points_fleet_estimate",
        "openai_weekly_points_per_finished",
        "cursor_envelope_tokens",
        "devin_envelope_tokens",
    )
    lines = [
        "# Routing daily audit",
        "",
        report["line"],
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
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
    body = "".join(
        "<tr><td>" + html.escape(r["until"][:10]) + "</td><td>" + html.escape(r["line"]) + "</td></tr>" for r in history
    )
    latest = history[0] if history else report
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Routing daily audit</title><link rel="stylesheet" href="_themes/agent-rails.css">'
        "<style>body{max-width:1200px;margin:40px auto;padding:20px}pre{white-space:pre-wrap}"
        "td,th{padding:12px;text-align:left;border-bottom:1px solid #ccc}</style><main>"
        "<h1>Routing daily audit</h1><pre>" + html.escape(render(latest)) + "</pre>"
        "<h2>14-day history</h2><table><tr><th>Day</th><th>Score</th></tr>" + body + "</table></main></html>"
    )


def _zulu(value):
    stamp = date(value)
    if stamp is None:
        return str(value)
    return stamp.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip_line(text, limit=120):
    if len(text) <= limit:
        return text
    head = text[:limit]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;")


def _head_label(model):
    text = str(model or "").replace("-", " ").strip()
    if not text:
        return "the head"
    return text[0].upper() + text[1:]


def _implement_rows(records):
    rows = []
    for row in records:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("job") or "implement").lower()
        if kind not in JOBS:
            kind = "implement"
        if kind == "implement":
            rows.append(row)
    return rows


def _metric(key, value, unit):
    return {"key": key, "value": value, "unit": unit}


def _keyed_count(records, rows, key, predicate):
    if not any(isinstance(row, dict) and key in row for row in records):
        return None
    return sum(1 for row in rows if predicate(row))


def daily_report(report, outcome_records_for_day, rungs, percent, errors):

    records = [row for row in outcome_records_for_day if isinstance(row, dict)]
    implement = _implement_rows(records)
    order = rungs.get("implement", []) if isinstance(rungs, dict) else list(rungs or [])
    head_rung = next((rung for rung in order if isinstance(rung, dict)), None)
    head_rows = [row for row in implement if head_rung and row_matches_rung(row, head_rung)]
    summarized = summarize_outcomes(head_rows)
    head_accepted = sum(row["accepted"] for row in summarized)
    head_observed = sum(row["acceptance_observed"] for row in summarized)
    n = len(implement)
    head = len(head_rows)
    cursor_devin = sum(row.get("seat") in ("cursor-seat", "devin-seat") for row in implement)
    sol = sum(row.get("seat") == "gpt-implementer" for row in implement)
    claude = sum(row.get("seat") in ("sonnet-implementer", "opus-seat", "effort-xhigh") for row in implement)
    pinned = _keyed_count(records, implement, "pinned", lambda row: row.get("pinned") is True)
    pins_ignored = _keyed_count(records, implement, "pin_ignored", lambda row: row.get("pin_ignored") is True)
    fell_back = _keyed_count(
        records,
        implement,
        "first_seat",
        lambda row: row.get("first_seat") is not None and row.get("first_seat") != row.get("seat"),
    )
    if n == 0:
        line = "No fold code pieces finished in the window."
    else:
        model = (head_rung or {}).get("resolved") or (head_rung or {}).get("model")
        line = (
            f"{head} of {n} fold code pieces ran on {_head_label(model)}, "
            f"{sol} on Sol, {claude} on Claude; {head_accepted} of {head_observed} accepted."
        )
    ratio = (sol + claude) / n if n else 0
    ladder_unread = any("ladder ordering: unavailable" in str(item) for item in errors)
    if n >= 10 and ratio > 0.5:
        status = "red"
    elif n == 0 or ratio > 0.2 or ladder_unread:
        status = "warn"
    else:
        status = "ok"
    return {
        "contract": "daily-report@0",
        "source": "routing",
        "owner": "w5H:pNE",
        "window": {"since": _zulu(report.get("since")), "until": _zulu(report.get("until"))},
        "line": _clip_line(line),
        "status": status,
        "metrics": [
            _metric("fold_code_pieces", n, "pieces"),
            _metric("ladder_head_pieces", head, "pieces"),
            _metric("ladder_head_pct", round(head * 100 / n, 1) if n else None, "%"),
            _metric("cursor_devin_pieces", cursor_devin, "pieces"),
            _metric("sol_pieces", sol, "pieces"),
            _metric("claude_pieces", claude, "pieces"),
            _metric("ladder_head_accepted", head_accepted, "pieces"),
            _metric("ladder_head_observed", head_observed, "pieces"),
            _metric("pinned_pieces", pinned, "pieces"),
            _metric("pins_ignored", pins_ignored, "pieces"),
            _metric("fell_back_pieces", fell_back, "pieces"),
            _metric("cursor_models_pct", percent, "%"),
            _metric("unverified_sources", len({str(item).split(":", 1)[0] for item in errors}), "count"),
        ],
        "link": {
            "url": "https://app.rails.so/workspace/rails-admin?route=scoping/routing-next",
            "label": "Routing",
        },
    }


def write_daily_report(payload, path, errors):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".routing-", dir=path.parent, suffix=".json")
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        errors.append(f"daily report: {type(exc).__name__}")
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
