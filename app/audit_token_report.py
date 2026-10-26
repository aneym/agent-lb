from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median

from app.audit_allocation import allocate, empty, merge
from app.audit_failures import failure_counts
from app.audit_pricing import CLASSES, input_cost, price_row, token_classes
from app.audit_token_output import job_join
from app.audit_transcripts import classify, event_time, lane_source, safe_label

DIMS = ("account", "purpose", "lane", "seat", "kind", "model", "effort", "provider", "useragent", "pane", "session")


CACHE_KINDS = ("first_write", "ttl_expiry", "account_switch", "bust", "growth", "other_miss")


DETECTORS = CACHE_KINDS + (
    "retried",
    "failed_rate_limited",
    "failed_other",
    "oversized_start",
    "looping",
    "unattributed",
)


def cache_kind(row, previous):
    if (row.get("cache_creation_tokens") or 0) <= 20000:
        return None
    if previous is None:
        return "first_write"
    gap = (event_time(row) - event_time(previous)).total_seconds()
    write = row.get("cache_creation_tokens") or 0
    if gap > 300 and (row.get("write_1h") or 0) < write:
        return "ttl_expiry"
    if row.get("account_id") and row.get("account_id") != previous.get("account_id"):
        return "account_switch"
    if any(
        (stamp := event_time({"timestamp": boundary})) and event_time(previous) < stamp <= event_time(row)
        for boundary in row.get("compact_boundaries", [])
    ):
        return "compaction"
    prior = sum(previous.get(k) or 0 for k in ("input_tokens", "cache_creation_tokens", "cache_read_tokens"))
    reads = row.get("cache_read_tokens") or 0
    context = (row.get("input_tokens") or 0) + write + reads
    if prior and gap <= 300 and reads < prior * 0.1 and 0.95 * prior <= context <= 1.2 * prior:
        return "bust"
    if prior and reads >= prior * 0.9:
        return "growth"
    return "other_miss"


def waste_flags(row):

    if row.get("retry_index", 1) > 1 and row.get("request_id"):
        return ["retried"]
    if row.get("status") != "success":
        rate = row.get("upstream_status_code") == 429 or row.get("error_code") in ("429", "rate_limited")
        return ["failed_rate_limited" if rate else "failed_other"]
    if (
        row.get("first")
        and not row.get("preexisting")
        and (row.get("input_tokens") or 0) + (row.get("cache_creation_tokens") or 0) > 40000
    ):
        return ["oversized_start"]
    if row.get("looping"):
        return ["looping"]
    return []


def build_report(rows, aliases, weekly, metas, rules, dimensions, top, agents_by_session=None, preexisting=None):
    agents_by_session, preexisting = agents_by_session or {}, preexisting or set()
    totals, priced = empty(), Counter()
    groups = {dim: defaultdict(empty) for dim in DIMS}
    session_models = defaultdict(lambda: defaultdict(empty))
    sessions, tree = {}, {"name": "all", **empty(), "children": {}}
    detectors = (*DETECTORS, "compaction", "coordinator")
    wastes = {
        key: {**empty(), "write_tokens": 0, "premium_usd": 0.0, "agents": defaultdict(empty)} for key in detectors
    }
    daily = defaultdict(lambda: {kind: {"usd": 0.0, "quota_points": 0.0} for kind in ("bust", "ttl_expiry")})
    cache_receipts = set()
    agent_receipts = {}
    row_index = defaultdict(list)
    for index, row in enumerate(rows):
        row_index[row["sid"]].append((index, row))

    def add_waste(kind, value, sid, lane, seat, key, write=0, premium=0.0):
        waste = wastes[kind]
        merge(waste, value)
        waste["write_tokens"] += write
        waste["premium_usd"] += premium
        agent_key = (sid, lane, seat, key)
        merge(waste["agents"][agent_key], value)

    for sid, agents in agents_by_session.items():
        indexed = row_index.get(sid, [])
        if not indexed:
            continue
        times = [row["requested_at"].replace(tzinfo=UTC).timestamp() for _, row in indexed]
        purpose, lane, _ = classify(indexed[0][1], metas.get(sid, {}), rules)
        for agent in agents:
            sequences = defaultdict(list)
            for event in agent["events"]:
                sequences[event["sequence"]].append(event)
            looping = False
            for events in sequences.values():
                calls = [
                    tuple(call) for event in sorted(events, key=event_time) for call in event.get("tool_calls", [])
                ]
                counts = Counter()
                for index, call in enumerate(calls):
                    counts[call] += 1
                    if index >= 50:
                        expired = calls[index - 50]
                        counts[expired] -= 1
                        if not counts[expired]:
                            del counts[expired]
                    if index >= 49 and max(counts.values()) >= 20:
                        looping = True
                        break
            coordinator = (
                not looping
                and len(agent["events"]) > 300
                and median(e.get("loop_output", e.get("output_tokens", 0)) for e in agent["events"]) < 200
            )
            for events in sequences.values():
                previous = None
                for event in sorted(events, key=lambda e: event_time(e)):
                    kind = cache_kind(event, previous)
                    previous = event
                    point = event_time(event).timestamp()
                    pos = bisect_left(times, point)
                    nearest = min(
                        (i for i in (pos - 1, pos) if 0 <= i < len(times)), key=lambda i: abs(times[i] - point)
                    )
                    agent_receipts[indexed[nearest][0]] = (agent, event == events[0], looping, coordinator)
                    if not kind:
                        continue
                    premium = input_cost(event, premium=True)
                    write = event.get("cache_creation_tokens") or 0
                    value = empty() | {"requests": 1, "usd": premium, "tokens": write}
                    receipt = indexed[nearest][1]
                    weight = price_row(receipt)[0]
                    fraction = min(1.0, premium / weight) if weight else 0.0
                    value.update({k: receipt.get(k, 0) * fraction for k in ("quota_points", "quota_5h_points")})
                    add_waste(kind, value, sid, lane, agent["seat"], agent["key"], write, premium)
                    if kind in ("bust", "ttl_expiry"):
                        entry = daily[str(event_time(event).date())][kind]
                        entry["usd"] += premium
                        entry["quota_points"] += value["quota_points"]
                    cache_receipts.add(indexed[nearest][0])
    financial_rows = {}
    lane_usd = Counter()
    for row in rows:
        sid = row["sid"]
        purpose, lane, rule = classify(row, metas.get(sid, {}), rules)
        count = row["requests"]
        tokens = token_classes(row)
        usd, provenance, split = price_row(row)
        if provenance != "log":
            usd *= count
            split = {k: v * count for k, v in split.items()}
        lane_usd[lane_source(row, metas.get(sid, {})) or "none"] += usd
        value = {
            "requests": count,
            "tokens": sum(tokens[k] for k in CLASSES if k != "reasoning") * count,
            "usd": usd,
            "quota_points": row.get("quota_points", 0),
            "quota_5h_points": row.get("quota_5h_points", 0),
            "token_classes": {k: v * count for k, v in tokens.items()},
            "usd_classes": split,
        }
        priced[provenance] += count
        merge(totals, value)
        merge(session_models[sid][row["model"] or "unknown"], value)
        key = tuple(
            row.get(k)
            for k in (
                "provider",
                "sid",
                "account_id",
                "model",
                "reasoning_effort",
                "useragent_group",
                "source",
                "request_kind",
            )
        )
        if key not in financial_rows:
            financial_rows[key] = (row, empty())
        merge(financial_rows[key][1], value)
    for row, value in financial_rows.values():
        sid = row["sid"]
        purpose, lane, rule = classify(row, metas.get(sid, {}), rules)
        meta = metas.get(sid, {})
        pane = meta.get("pane")
        if pane and meta.get("pane_title"):
            pane = f"{pane} {meta['pane_title']}"
        pane = safe_label(pane)
        account = aliases.get(row["account_id"], safe_label(str(row["account_id"] or "unknown")[:8]))
        bridge = row["provider"] == "openai" and row.get("useragent_group") == "claude-cli"
        agents = agents_by_session.get(sid, []) if row["provider"] == "anthropic" or bridge else []
        if bridge:
            agents = [agent | {"weight": agent.get("openai_weight", 0)} for agent in agents]
        for agent, piece in allocate(value, agents):
            seat, kind = agent["seat"], agent["kind"]
            agent_purpose = purpose
            if kind == "workflow":
                agent_purpose = (
                    "workflow-verify" if re.search(r"verify|review", agent.get("label", ""), re.I) else "workflow"
                )
            if not agents:
                caller_seat = row.get("caller_seat")
                if isinstance(caller_seat, str):
                    caller_seat = caller_seat.strip()
                if caller_seat:
                    seat = safe_label(caller_seat)
                    if row["provider"] == "openai":
                        kind = "codex"
                    elif metas.get(sid, {}).get("entrypoint", "").startswith("sdk-"):
                        kind = "headless"
                    elif row["provider"] != "anthropic":
                        kind = "other"
                elif row["provider"] == "openai":
                    origin = metas.get(sid, {}).get("originator") or row.get("useragent_group") or "unknown"
                    seat, kind = "codex:" + safe_label(origin.split()[0]), "codex"
                elif metas.get(sid, {}).get("entrypoint", "").startswith("sdk-"):
                    kind = "headless"
                elif row["provider"] != "anthropic":
                    kind = "other"
            labels = dict(
                account=account,
                session=sid,
                provider=row["provider"],
                purpose=agent_purpose,
                lane=lane,
                pane=pane,
                seat=seat,
                kind=kind,
                model=row["model"] or "unknown",
                effort=row.get("reasoning_effort") or "unknown",
                useragent=safe_label(row.get("useragent_group")),
            )
            for dim in DIMS:
                merge(groups[dim][labels[dim]], piece)
            skey = (row["provider"], sid, agent_purpose, lane, seat, agent["key"])
            if skey not in sessions:
                sessions[skey] = {
                    "session": sid,
                    "provider": row["provider"],
                    "purpose": agent_purpose,
                    "lane": lane,
                    "seat": seat,
                    "kind": kind,
                    "agent": agent["key"],
                    "rule": rule,
                    **empty(),
                }
            merge(sessions[skey], piece)
            node = tree
            merge(node, piece)
            for dim in ("provider", "account", "purpose", "lane", "seat", "model"):
                node = node["children"].setdefault(labels[dim], {"name": labels[dim], **empty(), "children": {}})
                merge(node, piece)
    previous_rows = {}
    previous_accounts = {}
    for index, row in enumerate(rows):
        sid = row["sid"]
        purpose, lane, _ = classify(row, metas.get(sid, {}), rules)
        count = row["requests"]
        usd, provenance, split = price_row(row)
        tokens = token_classes(row)
        value = {
            "requests": count,
            "tokens": sum(tokens.values()),
            "usd": usd if provenance == "log" else usd * count,
            "quota_points": row.get("quota_points", 0),
            "quota_5h_points": row.get("quota_5h_points", 0),
            "token_classes": tokens,
            "usd_classes": split,
        }
        agents = agents_by_session.get(sid, [])
        sequence = (row["provider"], sid, row["model"], row["account_id"])
        previous = previous_rows.get(sequence)
        account_sequence = (row["provider"], sid, row["model"])
        prior_account = previous_accounts.get(account_sequence)
        if prior_account and prior_account.get("account_id") != row.get("account_id"):
            previous = prior_account
        event = row | {"timestamp": row.get("requested_at", datetime.now(UTC)).isoformat()}
        previous_rows[sequence] = event
        previous_accounts[account_sequence] = event
        if not agents and row["provider"] == "anthropic" and (kind := cache_kind(event, previous)):
            premium = input_cost(event, premium=True) * count
            write = (row.get("cache_creation_tokens") or 0) * count
            add_waste(
                kind,
                empty()
                | {
                    "requests": count,
                    "usd": premium,
                    "tokens": write,
                    "quota_points": row.get("quota_points", 0) * min(1.0, premium / usd) if usd else 0.0,
                    "quota_5h_points": row.get("quota_5h_points", 0) * min(1.0, premium / usd) if usd else 0.0,
                },
                sid,
                lane,
                "lead",
                "lead",
                write,
                premium,
            )
            if kind in ("bust", "ttl_expiry"):
                entry = daily[str(event_time(event).date())][kind]
                entry["usd"] += premium
                entry["quota_points"] += row.get("quota_points", 0) * min(1.0, premium / usd) if usd else 0.0
            continue
        if index in cache_receipts:
            continue
        agent, first, looping, coordinator = agent_receipts.get(
            index, ({"seat": "lead", "key": "lead"}, previous is None if not agents else False, False, False)
        )
        flags = waste_flags(row | {"first": first, "preexisting": sid in preexisting, "looping": looping})
        if not flags and coordinator:
            flags = ["coordinator"]
        if not flags and lane_source(row, metas.get(sid, {})) is None:
            flags = ["unattributed"]
        for flag in flags:
            add_waste(flag, value, sid, lane, agent["seat"], agent["key"])

    def finish_tree(node):
        node["children"] = [finish_tree(child) for child in node["children"].values()]
        return node

    for waste in wastes.values():
        waste["share"] = waste["usd"] / totals["usd"] if totals["usd"] else 0
        waste["pct"] = 100 * waste["usd"] / totals["usd"] if totals["usd"] else None
        waste["top"] = sorted(
            (
                {"session": sid[:12], "lane": lane, "seat": seat, "agent": key, **v}
                for (sid, lane, seat, key), v in waste.pop("agents").items()
            ),
            key=lambda v: v["usd"],
            reverse=True,
        )[:10]

    by = {
        dim: sorted(({"name": name, **v} for name, v in groups[dim].items()), key=lambda v: v["usd"], reverse=True)
        for dim in dict.fromkeys([*dimensions, "purpose", "seat"])
    }
    for entry in by.get("session", []):
        entry["by_model"] = sorted(
            ({"name": model, **value} for model, value in session_models[entry["name"]].items()),
            key=lambda value: value["usd"],
            reverse=True,
        )
    account_shares = []
    for provider in tree["children"].values():
        for account in provider["children"].values():
            account_shares.append(
                {
                    "provider": provider["name"],
                    "account": account["name"],
                    "usd_share": account["usd"] / provider["usd"] if provider["usd"] else 0,
                    "token_share": account["tokens"] / provider["tokens"] if provider["tokens"] else 0,
                    "latest_weekly": weekly.get(account["name"]),
                }
            )
    return {
        "totals": totals,
        "failures_by_pool": failure_counts(rows),
        "by": by,
        "tree": finish_tree(tree),
        "sessions": sorted(sessions.values(), key=lambda v: v["usd"], reverse=True)[:top],
        "waste": wastes,
        "attribution": attribution(lane_usd) | {"jobs": job_join(rows)},
        "waste_total_usd": sum(wastes[k]["usd"] for k in detectors if k != "first_write"),
        "cache_premium_by_day": dict(sorted(daily.items())),
        "priced_by": dict(priced),
        "account_shares": account_shares,
    }


def attribution(lane_usd):
    total = sum(lane_usd.values())
    missing = lane_usd.get("none", 0.0)
    return {
        "usd": total,
        "lane_usd": total - missing,
        "unattributed_usd": missing if total else None,
        "unattributed_pct": 100 * missing / total if total else None,
        "coverage": (total - missing) / total if total else None,
        "by_source": {source: usd for source, usd in sorted(lane_usd.items())},
    }


def add_quota_units(report, quota):
    report["quota"] = quota

    def clean(value):
        if isinstance(value, dict):
            for key in ("capacity_points", "headroom_points", "fleet_share"):
                value.pop(key, None)
            for child in value.values():
                clean(child)
        elif isinstance(value, list):
            for child in value:
                clean(child)

    def counts(values, expected=None):
        if not values:
            return
        target = round(sum(value["requests"] for value in values)) if expected is None else expected
        for value in values:
            value["requests"] = round(value["requests"])
        max(values, key=lambda value: value["requests"])["requests"] += target - sum(
            value["requests"] for value in values
        )

    for values in report["by"].values():
        counts(values, report["totals"]["requests"])
    counts(report["sessions"])

    def tree_counts(node):
        counts(node["children"], node["requests"])
        for child in node["children"]:
            tree_counts(child)

    tree_counts(report["tree"])
    for waste in report["waste"].values():
        counts(waste["top"])

    total_capacity = sum(v["capacity_points"] for k, v in quota["providers"].items() if k.endswith(":weekly"))

    def visit(value, capacity=total_capacity):
        if isinstance(value, dict):
            if "quota_points" in value:
                value["account_weeks"] = value["quota_points"] / 100
                value["fleet_share"] = value["quota_points"] / capacity if capacity else 0
            for child in value.values():
                visit(child, capacity)
        elif isinstance(value, list):
            for child in value:
                visit(child, capacity)

    for key in ("totals", "by", "tree", "sessions", "waste"):
        visit(report[key])
    for provider in report["tree"]["children"]:
        capacity = quota["providers"].get(provider["name"] + ":weekly", {}).get("capacity_points", 0)
        visit(provider, capacity)
    clean(report)
