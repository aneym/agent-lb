from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.audit_tokens import (
    AuditUsageError,
    load_receipts,
    pane_maps,
    price_row,
    quota_allocation,
    token_classes,
    window,
)


@dataclass
class TabRow:
    date: str
    window_start: str
    window_end: str
    tab: str
    pane: str
    configured_model: str
    configured_effort: str
    requests: int = 0
    models: dict[str, int] = field(default_factory=dict)
    dominant_model: str | None = None
    fresh_input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    points: float = 0.0
    usd: float = 0.0
    cache_write_share_of_input_cost: float = 0.0
    ok_model: bool = False
    note: str | None = None


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("tabs", help="Audit daily token use by agent tab.")
    parser.add_argument("--window", default="24h")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--db")
    parser.add_argument("--agents-dir", type=Path, default=Path.home() / ".agent-rails/agents")
    parser.add_argument("--tabs", default="cos,factory,raise,rails,recruiter")
    parser.add_argument("--out-jsonl", type=Path, default=Path.home() / ".agent-lb/audit/tab-token-audit.jsonl")
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--json", action="store_true")


def sqlite_receipts(url: str, since: datetime, until: datetime):
    path = url.removeprefix("sqlite+aiosqlite:///").removeprefix("sqlite:///")
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM request_logs WHERE deleted_at IS NULL "
                "AND julianday(requested_at) >= julianday(?) AND julianday(requested_at) < julianday(?) "
                "ORDER BY requested_at,id",
                (since.isoformat(), until.isoformat()),
            )
        ]
        snapshots = [
            dict(row)
            for row in connection.execute(
                'SELECT account_id,provider,"window",window_minutes,used_percent,recorded_at,reset_at '
                "FROM usage_history WHERE julianday(recorded_at) <= julianday(?) ORDER BY recorded_at",
                (until.isoformat(),),
            )
        ]
    for row in rows:
        row["sid"] = row.get("client_session_id") or row.get("session_id") or f"receipt:{row['id']}"
        row["requests"] = 1
        row["requested_at"] = datetime.fromisoformat(row["requested_at"])
        if row["requested_at"].tzinfo is None:
            row["requested_at"] = row["requested_at"].replace(tzinfo=UTC)
    for snapshot in snapshots:
        stamp = datetime.fromisoformat(snapshot["recorded_at"])
        snapshot["recorded_at"] = stamp.astimezone(UTC).replace(tzinfo=None) if stamp.tzinfo else stamp
    return rows, snapshots


def markdown(rows: list[TabRow]) -> str:
    def cell(value: str | None) -> str:
        return (value or "none").replace("|", "\\|").replace("\n", " ")

    lines = [
        f"# Tab token audit for {rows[0].date}",
        f"Window: {rows[0].window_start} to {rows[0].window_end}",
        "",
        "| Tab | Pane | Configured | Dominant | Requests | Fresh input | Cache read | Cache write | "
        "Output | Points | USD | Write/input cost |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {cell(row.tab)} | {cell(row.pane)} | {cell(row.configured_model)} | "
            f"{cell(row.dominant_model)} | {row.requests} | {row.fresh_input} | {row.cache_read} | "
            f"{row.cache_write} | {row.output} | {row.points:.2f} | ${row.usd:.4f} | "
            f"{row.cache_write_share_of_input_cost:.1%} |"
        )
    totals = [
        sum(getattr(row, key) for row in rows)
        for key in ("requests", "fresh_input", "cache_read", "cache_write", "output", "points", "usd")
    ]
    lines.append(
        f"| Total | | | | {totals[0]} | {totals[1]} | {totals[2]} | {totals[3]} | "
        f"{totals[4]} | {totals[5]:.2f} | ${totals[6]:.4f} | |"
    )
    mismatches = [row.tab for row in rows if row.requests and not row.ok_model]
    lines += ["", "Model mismatches: " + (", ".join(mismatches) or "none") + "."]
    lines += [
        "",
        "Requests include subagents and workflow agents started from the tab's session; "
        "a mismatch means most "
        "of the session's traffic ran on another model, not that the tab itself did.",
    ]
    lines.extend(f"{row.tab}: {row.note}" for row in rows if row.note)
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> None:
    try:
        since, until = window(args)
        tabs = args.tabs.split(",")
        if len(set(tabs)) != len(tabs) or any(not tab or Path(tab).name != tab or tab in (".", "..") for tab in tabs):
            raise AuditUsageError("--tabs must contain distinct tab directory names")
        result = []
        for tab in tabs:
            config = json.loads((args.agents_dir.expanduser() / tab / "agent.json").read_text())
            if not config["pane"] or not config["model"]:
                raise AuditUsageError("Each tab must configure a pane and model")
            result.append(
                TabRow(
                    until.date().isoformat(),
                    since.isoformat(),
                    until.isoformat(),
                    tab,
                    config["pane"],
                    config["model"],
                    config["effort"],
                )
            )
        panes, notes = pane_maps()
        if args.db and args.db.startswith(("sqlite:///", "sqlite+aiosqlite:///")):
            receipts, snapshots = sqlite_receipts(args.db, since, until)
        else:
            receipts, snapshots, _, _, _ = load_receipts(args, since, until, notes)
        points, _ = quota_allocation(receipts, snapshots, since, until)
        for row in result:
            input_cost = write_cost = 0.0
            for receipt, quota in zip(receipts, points, strict=True):
                if panes.get(receipt["sid"], {}).get("pane") != row.pane:
                    continue
                count = receipt["requests"]
                row.requests += count
                model = receipt["model"] or "unknown"
                row.models[model] = row.models.get(model, 0) + count
                tokens = token_classes(receipt)
                for key in ("fresh_input", "cache_read", "cache_write", "output"):
                    setattr(row, key, getattr(row, key) + tokens[key] * count)
                usd, provenance, costs = price_row(receipt)
                multiplier = 1 if provenance == "log" else count
                row.usd += usd * multiplier
                input_cost += sum(costs[key] for key in ("fresh_input", "cache_read", "cache_write")) * multiplier
                write_cost += costs["cache_write"] * multiplier
                row.points += quota["weekly"]
            row.dominant_model = max(row.models, key=row.models.get) if row.models else None
            family = row.configured_model.removeprefix("claude-")
            row.ok_model = bool(
                row.dominant_model
                and (row.dominant_model == row.configured_model or row.dominant_model.startswith(f"claude-{family}-"))
            )
            row.cache_write_share_of_input_cost = write_cost / input_cost if input_cost else 0.0
            if not row.requests:
                row.note = "No receipts attributed to this pane in the window."
        output = args.out_jsonl.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a") as stream:
            for row in result:
                stream.write(json.dumps(asdict(row)) + "\n")
        report = markdown(result)
        if args.out_md:
            path = args.out_md.expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report)
        print(json.dumps([asdict(row) for row in result]) if args.json else report, end="\n" if args.json else "")
        for note in notes:
            print(note, file=sys.stderr)
    except AuditUsageError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception as exc:
        message = f"Tab audit failed ({type(exc).__name__}); check tab configuration and database availability."
        raise SystemExit(message) from None


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(prog="agent-lb audit tabs")
    _commands = _parser.add_subparsers(dest="audit_command", required=True)
    add_parser(_commands)
    run(_parser.parse_args(["tabs", *sys.argv[1:]]))
