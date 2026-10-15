from __future__ import annotations

import glob
import html
import json
import re
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from app import audit_tokens as tokens


def add_parser(commands):
    parser = commands.add_parser("routing", help="Measure seats by subscription quota and accepted units.")
    parser.add_argument("--window", default="7d")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--compare-previous", action="store_true")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--html", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--db")


def harness(row):
    client = (row.get("useragent_group") or "").lower()
    if row.get("provider") == "anthropic" or client in ("claude-cli", "claude"):
        return "claude-code"
    if client in ("codex_exec", "codex-tui", "codex", "codex_cli_rs"):
        return "codex-cli"
    return "other"


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
            key = (tokens.safe_label(impl.get("agentType")), tokens.safe_label(impl.get("model")))
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
                label = tokens.safe_label(agent.get("label"))
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
            key = (f"studio_merge:{repo}", tokens.safe_label(vendors.get(branch)))
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


def weighted_median(parts):
    total = sum(len(values) * weight for values, weight in parts)
    if not total:
        return 0

    def kth(k):
        low = min(values[0] for values, _ in parts if values)
        high = max(values[-1] for values, _ in parts if values)
        while low < high:
            middle = (low + high) // 2
            if sum(bisect_right(values, middle) * weight for values, weight in parts) > k:
                high = middle
            else:
                low = middle + 1
        return low

    if total % 2:
        return kth(total // 2)
    return (kth(total // 2 - 1) + kth(total // 2)) / 2


def routing_rows(rows, agents_by_session, metas, quota=None):
    groups, agent_points = {}, defaultdict(lambda: defaultdict(float))
    first = {}
    outputs = defaultdict(lambda: defaultdict(int))
    clients = defaultdict(set)
    identities = defaultdict(set)
    model_agents = {}
    for sid, agents in agents_by_session.items():
        for agent in agents:
            by_model = defaultdict(list)
            for event in agent.get("events", []):
                by_model[event.get("model")].append(event)
            for model, events in by_model.items():
                first_event = min(events, key=tokens.event_time)
                start = sum(
                    first_event.get(k) or 0 for k in ("input_tokens", "cache_read_tokens", "cache_creation_tokens")
                )
                model_agents.setdefault((sid, model), []).append(agent | {"events": events, "start_tokens": start})
    for (_, model), shared in model_agents.items():
        anthropic = tokens.anthropic_price(model or "")

        def cost(event):
            if anthropic:
                return tokens.input_cost(event)
            return sum(v for k, v in tokens.price_row(event | {"provider": "openai"})[2].items() if k != "output")

        if len(shared) == 1:
            shared[0]["weight"] = 1.0 if any(cost(event) > 0 for event in shared[0]["events"]) else 0.0
            continue
        for agent in shared:
            agent["weight"] = sum(cost(event) for event in agent["events"])
    buckets = {}
    for row in rows:
        classes = tokens.token_classes(row)
        count = row.get("requests", 1)
        bucket_key = (
            row["sid"],
            row.get("model"),
            row["provider"],
            row.get("reasoning_effort"),
            row.get("useragent_group"),
        )
        bucket = buckets.get(bucket_key)
        if bucket is None:
            bucket = buckets[bucket_key] = dict(
                row=row,
                value=tokens.empty(),
                outputs=[],
                start=sum(classes[k] for k in ("fresh_input", "cache_read", "cache_write")),
            )
        value = bucket["value"]
        value["requests"] += count
        value["tokens"] += sum(classes.values())
        value["quota_points"] += row.get("quota_points", 0)
        value["quota_5h_points"] += row.get("quota_5h_points", 0)
        for name, amount in classes.items():
            value["token_classes"][name] += amount * count
        bucket["outputs"].append(classes["output"])
    for bucket in buckets.values():
        row = bucket["row"]
        for agent, part in tokens.allocate(bucket["value"], model_agents.get((row["sid"], row.get("model")), [])):
            seat = agent["seat"]
            if seat == "lead" and row["provider"] == "openai":
                seat = (
                    "codex:"
                    + tokens.safe_label(
                        metas.get(row["sid"], {}).get("originator") or row.get("useragent_group")
                    ).split()[0]
                )
            key = (
                row["provider"],
                seat,
                row.get("model") or "unknown",
                row.get("reasoning_effort") or "unknown",
                harness(row),
            )
            summary = groups.setdefault(
                key, dict(pool=key[0], seat=seat, model=key[2], effort=key[3], harness=key[4], **tokens.empty())
            )
            tokens.merge(summary, part)
            clients[key].add(tokens.safe_label(row.get("useragent_group")))
            identity = (row["sid"], agent["key"])
            if part["requests"] <= 0:
                continue
            identities[key].add(identity)
            if (key, identity) not in first:
                start_tokens = agent.get("start_tokens")
                first[(key, identity)] = bucket["start"] if start_tokens is None else start_tokens
            outputs[key][id(bucket)] += 1
            workflow_key = agent["key"].removesuffix(" review")
            agent_points[workflow_key][row["provider"]] += part["quota_points"]
    for item in (quota or {}).get("unattributed", []):
        key = (item["provider"], "unattributed", "unknown", "unknown", "other")
        summary = groups.setdefault(
            key, dict(pool=key[0], seat=key[1], model=key[2], effort=key[3], harness=key[4], **tokens.empty())
        )
        field = "quota_points" if item["window"] == "weekly" else "quota_5h_points"
        summary[field] += item["points"]
    by_bucket = {id(bucket): sorted(bucket["outputs"]) for bucket in buckets.values()}
    starts = defaultdict(list)
    for (key, _), value in first.items():
        starts[key].append(value)
    result = []
    for key, row in groups.items():
        classes = row["token_classes"]
        input_total = sum(classes[k] for k in ("fresh_input", "cache_read", "cache_write"))
        row.update(
            useragent_groups=sorted(clients[key]),
            agents=len(identities[key]),
            cache_hit_rate=classes["cache_read"] / input_total if input_total else 0,
            median_start_tokens=median(starts[key]) if identities[key] else 0,
            median_output_per_request=weighted_median([(by_bucket[ref], times) for ref, times in outputs[key].items()]),
            points_per_request=row["quota_points"] / row["requests"] if row["requests"] else None,
            provisional=row["requests"] < 10,
        )
        result.append(row)
    return sorted(result, key=lambda r: (r["pool"], -r["quota_points"])), agent_points


def movements(current, previous):
    moved = []
    for section, keys, metrics, count in (
        ("rows", ("pool", "seat", "model", "effort", "harness"), ("points_per_request", "cache_hit_rate"), "requests"),
        ("fold", ("seat", "model"), ("accept_rate", "points_per_accepted_piece"), "pieces"),
    ):
        old = {tuple(row[k] for k in keys): row for row in previous[section]}
        for row in current[section]:
            prior = old.get(tuple(row[k] for k in keys))
            if prior is None or min(row[count], prior[count]) < 10:
                continue
            for metric in metrics:
                before, after = prior[metric], row[metric]
                pairs = [(None, before, after)]
                if isinstance(after, dict):
                    pairs = [(pool, before.get(pool), value) for pool, value in after.items()]
                for pool, before, after in pairs:
                    if before is None or after is None:
                        continue
                    before = 0 if abs(before) < 1e-9 else before
                    after = 0 if abs(after) < 1e-9 else after
                    if before == 0 or before == after:
                        continue
                    if abs(after - before) / abs(before) > 0.25:
                        moved.append(
                            dict(
                                section=section,
                                row={k: row[k] for k in keys},
                                metric=metric,
                                pool=pool,
                                previous=before,
                                current=after,
                                previous_n=prior[count],
                                current_n=row[count],
                            )
                        )
    return moved


def report_for(args, since, until):
    notes = []
    rows, snapshots, _, _, _ = tokens.load_receipts(args, since, until, notes)
    allocation, quota = tokens.quota_allocation(rows, snapshots, since, until)
    for row, points in zip(rows, allocation, strict=True):
        row.update(quota_points=points["weekly"], quota_5h_points=points["5h"])
    index = tokens.session_index()
    sessions = {row["sid"] for row in rows}
    with ThreadPoolExecutor(max_workers=8) as pool:
        loaded = dict(
            zip(
                sessions,
                pool.map(
                    lambda sid: (
                        tokens.metadata(index.get(sid)),
                        tokens.session_agents(index.get(sid), since, until),
                    ),
                    sessions,
                ),
                strict=True,
            )
        )
    metas = {sid: meta for sid, (meta, _) in loaded.items()}
    agents = {sid: found for sid, (_, found) in loaded.items()}
    grouped, points = routing_rows(rows, agents, metas, quota)
    records = []
    for path in (Path.home() / ".claude/projects").glob("*/*/workflows/wf_*.json"):
        try:
            records.append((path.stem, json.loads(path.read_text())))
        except (OSError, ValueError):
            notes.append("Unreadable workflow record skipped.")
    return dict(
        window=dict(since=since.isoformat(), until=until.isoformat()),
        rows=grouped,
        fold=fold_units(records, points, since, until) + merge_units(since, until),
        quota=quota,
        notes=notes
        + [
            "Quota allocated by receipt audit input-side weights; unallocated intervals remain explicit.",
            "Fold points include all matched stages in this window, not lifetime costs outside the window.",
            "studio_merge rows count branches tried in the window; fix rounds are refusals before the merge.",
            "n < 10 is provisional and never flagged.",
        ],
    )


def text_report(report):
    lines = [f"Window: {report['window']['since']} to {report['window']['until']}"]
    for pool in sorted({r["pool"] for r in report["rows"]}):
        lines += [
            f"\nPOOL {pool}",
            "SEAT / MODEL / EFFORT / HARNESS | REQUESTS | POINTS | 5H | CACHE | START | OUTPUT | AGENTS",
        ]
        for row in report["rows"]:
            if row["pool"] == pool:
                lines.append(
                    f"{row['seat']} / {row['model']} / {row['effort']} / {row['harness']} | "
                    f"{row['requests']:.0f} | {row['quota_points']:.2f} | {row['quota_5h_points']:.2f} | "
                    f"{row['cache_hit_rate']:.1%} | {row['median_start_tokens']:.0f} | "
                    f"{row['median_output_per_request']:.0f} | {row['agents']}"
                    + (" (provisional)" if row["provisional"] else "")
                )
    lines += [
        "\nFOLD",
        "SEAT / MODEL | PIECES | ACCEPTED | RATE | SEAT RATE | FIX | REVIEW | MIN/ACCEPTED | POINTS/ACCEPTED BY POOL",
    ]
    for row in report["fold"]:
        wall = row["wall_minutes_per_accepted_piece"]
        wall_text = f"{wall:.1f}" if wall is not None else "n/a"
        points_text = ", ".join(
            f"{pool} {value:.2f}" if value is not None else f"{pool} n/a"
            for pool, value in sorted(row["points_per_accepted_piece"].items())
        )
        seat_rate = f"{row['seat_accept_rate']:.1%}" if row["seat_accept_rate"] is not None else "n/a"
        lines.append(
            f"{row['seat']} / {row['model']} | {row['pieces']} | {row['accepted']} | "
            f"{row['accept_rate']:.1%} | {seat_rate} | {row['fix_rounds']} | {row['review_rounds']} | "
            f"{wall_text} | {points_text}" + (" (provisional)" if row["provisional"] else "")
        )
    lines += ["\nMOVED"] + [json.dumps(m) for m in report.get("moved", [])]
    if not report.get("moved"):
        lines.append("None.")
    return "\n".join(lines + ["\nNotes:", *report["notes"]]) + "\n"


def run(args):
    try:
        since, until = tokens.window(args)
        report = report_for(args, since, until)
        if args.compare_previous:
            report["previous"] = report_for(args, since - (until - since), since)
            report["moved"] = movements(report, report["previous"])
        if args.json:
            output = json.dumps(report, indent=2) + "\n"
        else:
            output = text_report(report)
            if args.compare_previous:
                output += "\nPREVIOUS\n" + text_report(report["previous"])
            if args.html:
                output = (
                    '<!doctype html><html><meta charset="utf-8"><title>Routing audit</title>'
                    "<style>body{font:16px system-ui;margin:32px}pre{white-space:pre-wrap}</style>"
                    "<h1>Routing audit</h1><pre>" + html.escape(output) + "</pre></html>"
                )
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output)
            print(f"Wrote routing audit: {args.out}")
        else:
            print(output, end="")
    except BrokenPipeError:
        return
    except Exception as exc:
        raise SystemExit(
            f"Routing audit failed ({type(exc).__name__}); check local inputs and database availability."
        ) from None
