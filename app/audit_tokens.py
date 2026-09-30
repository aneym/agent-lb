"""Read-only receipt accounting. Heuristics are candidates, never savings claims."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import tomllib
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

from app.core.anthropic.models import AnthropicUsage
from app.core.anthropic.pricing import calculate_anthropic_cost_breakdown
from app.core.anthropic.pricing import get_pricing_for_model as anthropic_price
from app.core.usage.pricing import UsageTokens, calculate_cost_breakdown_from_usage, get_pricing_for_model

CLASSES = ("fresh_input", "cache_read", "cache_write", "output", "reasoning")
DIMS = ("account", "purpose", "lane", "seat", "kind", "model", "effort", "provider", "useragent")
CACHE_KINDS = ("first_write", "ttl_expiry", "account_switch", "bust", "growth", "other_miss")
DETECTORS = CACHE_KINDS + (
    "retried",
    "failed_rate_limited",
    "failed_other",
    "oversized_start",
    "looping",
    "unattributed",
)
RULE_PATH = Path(__file__).resolve().parent.parent / "config/audit/purpose_rules.toml"


def add_parser(subparsers):
    audit = subparsers.add_parser("audit", help="Read-only receipt audits.")
    commands = audit.add_subparsers(dest="audit_command", required=True)
    tokens = commands.add_parser("tokens", help="Account for tokens and list-price dollars.")
    tokens.add_argument("--window", default="7d")
    tokens.add_argument("--since")
    tokens.add_argument("--until")
    tokens.add_argument("--by", default="provider,account,purpose,lane,seat,model")
    output = tokens.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--html", action="store_true")
    tokens.add_argument("--out", type=Path)
    tokens.add_argument("--db")
    tokens.add_argument("--top", type=int, default=30)
    tokens.add_argument("--snapshot-panes-only", action="store_true")


def window(args):
    def parse(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    until = parse(args.until) if args.until else datetime.now(UTC)
    match = re.fullmatch(r"([1-9]\d*)([hd])", args.window)
    if not match:
        raise ValueError("--window must be a positive number followed by h or d")
    since = parse(args.since) if args.since else until - timedelta(hours=int(match[1]) * (24 if match[2] == "d" else 1))
    if since >= until:
        raise ValueError("--since must precede --until")
    return since, until


def token_classes(row):
    def count(key):
        return max(0, row.get(key) or 0)

    inp, cached = count("input_tokens"), count("cached_input_tokens")
    anthropic = row.get("provider") == "anthropic"
    return dict(
        zip(
            CLASSES,
            (
                inp if anthropic else inp - min(inp, cached),
                count("cache_read_tokens") if anthropic else min(inp, cached),
                count("cache_creation_tokens") if anthropic else 0,
                count("output_tokens"),
                count("reasoning_tokens"),
            ),
            strict=True,
        )
    )


def price_row(row):
    """Return dollars, provenance and a class split reconciled to recorded price."""
    split = {key: 0.0 for key in CLASSES if key != "reasoning"}
    breakdown = None
    provider, model = row.get("provider"), row.get("model") or ""
    if provider == "anthropic" and (resolved := anthropic_price(model)):
        if any(
            row.get(k) is not None
            for k in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
        ):
            breakdown = calculate_anthropic_cost_breakdown(
                AnthropicUsage(
                    input_tokens=row.get("input_tokens"),
                    output_tokens=row.get("output_tokens"),
                    cache_creation_input_tokens=row.get("cache_creation_tokens"),
                    cache_read_input_tokens=row.get("cache_read_tokens"),
                ),
                resolved[1],
            )
            split.update(
                fresh_input=breakdown.input_usd,
                cache_read=breakdown.cache_read_input_usd,
                cache_write=breakdown.cache_creation_input_usd,
                output=breakdown.output_usd,
            )
    elif provider == "openai" and (resolved := get_pricing_for_model(model, None, None)):
        if row.get("input_tokens") is not None and row.get("output_tokens") is not None:
            breakdown = calculate_cost_breakdown_from_usage(
                UsageTokens(
                    row["input_tokens"],
                    row["output_tokens"],
                    min(max(0, row.get("cached_input_tokens") or 0), max(0, row["input_tokens"])),
                ),
                resolved[1],
                service_tier=row.get("service_tier"),
            )
            split.update(
                fresh_input=breakdown.input_usd, cache_read=breakdown.cached_input_usd, output=breakdown.output_usd
            )
    estimated = sum(split.values())
    if row.get("cost_usd") is not None:
        usd, source = float(row["cost_usd"]), "log"
        if estimated:
            split = {k: v * usd / estimated for k, v in split.items()}
        else:
            split["unallocated"] = usd
    elif breakdown is not None:
        usd, source = estimated, "recomputed"
    else:
        usd, source = 0.0, "unpriced"
    return usd, source, split


def safe_label(value, fallback="unknown"):
    value = str(value or fallback)
    # Alias/title can contain an email; never persist it.
    return fallback if "@" in value else value[:160]


def pane_maps():
    home = Path.home() / ".agent-lb/audit/pane-snapshots"
    mapping, notes = {}, []
    for path in sorted(home.glob("*.jsonl")):
        for line in path.open():
            try:
                item = json.loads(line)
                mapping[item["session"]] = item
            except (ValueError, KeyError):
                continue
    try:
        result = subprocess.run(["herdr", "pane", "list"], capture_output=True, text=True, timeout=5)
        payload = json.loads(result.stdout)
        panes = payload if isinstance(payload, list) else payload.get("result", payload).get("panes", [])
        current = []
        for pane in panes:
            session = pane.get("agent_session") or {}
            sid = session.get("value") if isinstance(session, dict) else session
            if sid:
                item = {
                    "session": sid,
                    "pane": safe_label(pane.get("pane_id")),
                    "title": safe_label(pane.get("terminal_title_stripped")),
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                mapping[sid] = item
                current.append(item)
        if current:
            home.mkdir(parents=True, exist_ok=True)
            with (home / f"{datetime.now(UTC):%Y-%m-%d}.jsonl").open("a") as stream:
                for item in current:
                    stream.write(json.dumps(item) + "\n")
        elif result.returncode:
            notes.append("Live herdr pane map unavailable; historical snapshots retained.")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        notes.append("Live herdr pane map unavailable; historical snapshots retained.")
    return mapping, notes


def session_index():
    index = {}
    for project in (Path.home() / ".claude/projects").glob("*"):
        if project.is_dir():
            for path in project.glob("*.jsonl"):
                index[path.stem] = path
    # Codex rollout filenames end with the session UUID. No content-wide grep.
    for path in (Path.home() / ".codex/sessions").glob("*/*/*/rollout-*.jsonl"):
        match = re.search(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$", path.name)
        if match:
            index.setdefault(match[1], path)
    return index


def prompt_text(content):
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    if not isinstance(content, str):
        return ""
    content = re.sub(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", r"\1", content, flags=re.S)
    content = re.sub(r"<system-reminder>.*?</system-reminder>", "", content, flags=re.S).strip()
    if content.startswith(("<command-", "<local-command", "Caveat:")):
        return ""
    return content


def transcript_digest(path):
    """Cache only attribution hints and deduplicated usage, never full messages."""
    stat = path.stat()
    signature = [str(path), stat.st_size, stat.st_mtime_ns, RULE_PATH.stat().st_mtime_ns, 7]
    cache = Path.home() / ".agent-lb/audit/transcript-cache" / (hashlib.sha1(str(path).encode()).hexdigest() + ".json")
    try:
        cached = json.loads(cache.read_text())
        if cached["signature"] == signature:
            return cached["digest"]
    except (OSError, ValueError, KeyError):
        pass
    result = {"prompt": "", "cwd": "", "title": "", "entrypoint": "", "originator": "", "events": []}
    seen = set()
    with path.open() as stream:
        for line in stream:
            # Metadata is sparse; avoid parsing tool results and assistant content without usage.
            if not any(
                marker in line
                for marker in (
                    '"usage"',
                    '"type":"user"',
                    '"type": "user"',
                    '"ai-title"',
                    '"session_meta"',
                    '"role":"user"',
                    '"role": "user"',
                )
            ):
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            payload = item.get("payload") or {}
            result["cwd"] = item.get("cwd") or payload.get("cwd") or result["cwd"]
            result["entrypoint"] = item.get("entrypoint") or payload.get("entrypoint") or result["entrypoint"]
            result["originator"] = payload.get("originator") or result["originator"]
            if item.get("type") == "ai-title":
                result["title"] = safe_label(item.get("aiTitle"), "")
            message = item.get("message") or {}
            if not result["prompt"] and (item.get("type") == "user" or payload.get("role") == "user"):
                result["prompt"] = prompt_text(message.get("content", payload.get("content", "")))[:600]
            if item.get("type") != "assistant" or not message.get("usage") or not message.get("id"):
                continue
            if message["id"] in seen:
                continue
            seen.add(message["id"])
            usage = message["usage"]
            tier = usage.get("cache_creation") or {}
            result["events"].append(
                {
                    "timestamp": item.get("timestamp"),
                    "model": message.get("model") or "",
                    "input_tokens": usage.get("input_tokens") or 0,
                    "cached_input_tokens": usage.get("cached_input_tokens") or 0,
                    "output_tokens": usage.get("output_tokens") or 0,
                    "cache_creation_tokens": usage.get("cache_creation_input_tokens") or 0,
                    "cache_read_tokens": usage.get("cache_read_input_tokens") or 0,
                    "write_1h": tier.get("ephemeral_1h_input_tokens") or 0,
                    "write_5m": tier.get("ephemeral_5m_input_tokens") or 0,
                    "loop_output": usage.get("output_tokens") or 0,
                }
            )
    # Persist only safe hints, never emails or complete prompt bodies.
    result["prompt"] = safe_label(result["prompt"], "") if "@" in result["prompt"] else result["prompt"]
    result["cwd"] = safe_label(result["cwd"], "")
    result["originator"] = safe_label(result["originator"], "")
    cache.parent.mkdir(parents=True, exist_ok=True)
    with RULE_PATH.open("rb") as rules_file:
        rules = tomllib.load(rules_file)["rules"]
    result["entrypoint"] = result["entrypoint"] or ("sdk-cli" if "exec" in result["originator"].lower() else "cli")
    hints = {
        rule["name"]: bool(
            re.search(
                rule["pattern"],
                (result["prompt"] + " " + result["title"])
                if rule["field"] == "text"
                else result.get(rule["field"], ""),
                re.I,
            )
        )
        for rule in rules
        if rule["field"] not in ("useragent", "source", "request_kind")
    }
    result["rule_hints"] = hints
    rollout_uuid = re.search(r"^rollout-.*-([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$", path.stem)
    sid = rollout_uuid[1][-8:] if rollout_uuid else path.stem
    result["lane_hint"] = classify(
        {"sid": sid, "useragent_group": "Claude" if ".claude" in path.parts else "Codex"}, result, []
    )[1]
    result["fold_hint"] = next(
        (
            name
            for pattern, name in (
                (r"verify|VERDICT", "fold-verify"),
                (r"fix round|fix:", "fold-fix"),
                (r"panel|lens", "fold-panel"),
            )
            if re.search(pattern, result["prompt"] + " " + result["title"], re.I)
        ),
        "fold-implement",
    )
    cached_result = result | {"prompt": ""}
    cache.write_text(json.dumps({"signature": signature, "digest": cached_result}))
    return result


def metadata(path):
    return transcript_digest(path) if path else {}


def event_time(event):
    try:
        stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError, KeyError):
        return None


def input_cost(event, premium=False):
    resolved = anthropic_price(event.get("model") or "")
    if not resolved:
        return 0.0
    price = resolved[1]
    write = event.get("cache_creation_tokens") or 0
    one_hour = min(write, event.get("write_1h") or 0)
    cost = (write - one_hour) * price.cache_creation_5m_input_per_1m + one_hour * price.cache_creation_1h_input_per_1m
    if premium:
        return (cost - write * price.cache_read_input_per_1m) / 1_000_000
    return (
        cost
        + (event.get("input_tokens") or 0) * price.input_per_1m
        + (event.get("cache_read_tokens") or 0) * price.cache_read_input_per_1m
    ) / 1_000_000


def session_agents(path, since, until):
    if not path or ".claude" not in path.parts:
        return []
    files = [(path, "lead", "lead", "lead", "")]
    for child in (path.parent / path.stem / "subagents").glob("**/agent-*.jsonl"):
        try:
            info = json.loads(child.with_suffix(".meta.json").read_text())
        except (OSError, ValueError):
            info = {}
        agent_type = safe_label(info.get("agentType"))
        workflow = next((part for part in child.parts if part.startswith("wf_")), None)
        label = safe_label(info.get("description") or info.get("workflowPhase") or agent_type)
        if workflow and re.search(r"verify|review", str(info.get("description") or ""), re.I):
            label += " review"
        key = f"workflow:{workflow}:{label}" if workflow else f"subagent:{agent_type}"
        files.append(
            (
                child,
                key,
                agent_type if agent_type != "unknown" else "workflow-subagent" if workflow else agent_type,
                "workflow" if workflow else "subagent",
                label,
            )
        )
    agents = {}
    for file, key, seat, kind, label in files:
        try:
            if file.stat().st_mtime < since.timestamp():
                continue
            digest = transcript_digest(file)
        except OSError:
            continue
        agent = agents.setdefault(key, {"key": key, "seat": seat, "kind": kind, "label": label, "events": []})
        # Retain file identity for sequences even when several agents have the same seat.
        events = [
            event | {"sequence": str(file)}
            for event in digest["events"]
            if (stamp := event_time(event)) and since <= stamp < until
        ]
        agent["events"].extend(events)
    for agent in agents.values():
        agent["weight"] = sum(input_cost(event) for event in agent["events"])
    return list(agents.values())


def classify(row, meta, rules):
    if row.get("sid", "").startswith("unattributed-quota:"):
        return "unattributed-quota", "unattributed-quota", "empty-interval"
    headless = meta.get("entrypoint", "").startswith("sdk-")
    fields = {
        **meta,
        "text": meta.get("prompt", "")[:600] + " " + meta.get("title", ""),
        "useragent": row.get("useragent_group") or "",
        "source": row.get("source") or "",
        "request_kind": row.get("request_kind") or "",
    }
    prompt = meta.get("prompt", "")
    lane_match = re.search(r"lanes/([^/\s]+)/", prompt)
    brief = re.search(r"([\w-]+-brief)\.md", prompt, re.I)
    lane = (
        meta.get("pane_title")
        or (lane_match[1] if lane_match else "")
        or (brief[1] if brief else "")
        or (meta.get("lane_hint") if meta.get("lane_hint") != "unknown" else "")
        or meta.get("title")
        or "unknown"
    )
    if lane == "unknown":
        client = safe_label(row.get("useragent_group") or row.get("seat"), "client")
        sid = row.get("sid") or row.get("client_session_id") or row.get("session_id") or row.get("agent") or "unknown"
        lane = f"{client} {safe_label(sid)[:8]}"
    for rule in rules:
        if rule.get("headless") and not headless:
            continue
        if meta.get("rule_hints", {}).get(rule["name"]) or re.search(
            rule["pattern"], fields.get(rule["field"], ""), re.I
        ):
            purpose = rule["purpose"]
            if purpose == "fold":
                text = fields["text"]
                purpose = meta.get("fold_hint") or next(
                    (
                        name
                        for pattern, name in (
                            (r"verify|VERDICT", "fold-verify"),
                            (r"fix round|fix:", "fold-fix"),
                            (r"panel|lens", "fold-panel"),
                        )
                        if re.search(pattern, text, re.I)
                    ),
                    "fold-implement",
                )
            return purpose, safe_label(lane), rule["name"]
    purpose = "ad-hoc" if meta.get("entrypoint") == "cli" else "headless-other" if headless else "unknown"
    if purpose == "unknown":
        client = safe_label(row.get("useragent_group"), "other")
        return (
            "unlinked",
            f"{client} (no rollout)" if row.get("provider") == "openai" else f"{client} (no transcript)",
            "unlinked",
        )
    return purpose, safe_label(lane), "entrypoint"


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
    prior = sum(previous.get(k) or 0 for k in ("input_tokens", "cache_creation_tokens", "cache_read_tokens"))
    reads = row.get("cache_read_tokens") or 0
    context = (row.get("input_tokens") or 0) + write + reads
    if prior and gap <= 300 and reads < prior * 0.1 and 0.95 * prior <= context <= 1.2 * prior:
        return "bust"
    if prior and reads >= prior * 0.9:
        return "growth"
    return "other_miss"


def waste_flags(row):
    """Exactly one non-cache assignment; retries exclude the original attempt."""
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


QUERY = """
WITH base AS (
 SELECT id, account_id, provider, model, reasoning_effort, useragent_group, source, request_kind,
 service_tier, input_tokens, output_tokens, cached_input_tokens, cache_creation_tokens, cache_read_tokens,
 reasoning_tokens, cost_usd, status, error_code, upstream_status_code, requested_at, request_id,
 coalesce(nullif(client_session_id,''),nullif(session_id,''),'receipt:' || id::text) AS sid
 FROM request_logs WHERE deleted_at IS NULL AND requested_at >= %s AND requested_at < %s
)
SELECT *, 1 AS requests,
 CASE WHEN request_id IS NULL THEN 1
 ELSE row_number() OVER (PARTITION BY request_id ORDER BY requested_at,id) END AS retry_index
FROM base ORDER BY requested_at,id
"""


def quota_allocation(rows, snapshots, since, until):
    """Allocate observed increments, ignoring jitter within a reset period."""
    grouped = defaultdict(list)
    for snap in snapshots:
        minutes = snap.get("window_minutes")
        provider = snap["provider"]
        weekly = (provider == "anthropic" and snap["window"] == "secondary") or (
            provider == "openai" and minutes == 10080 and snap["window"] == "primary"
        )
        short = provider == "anthropic" and minutes == 300 and snap["window"] == "primary"
        if weekly or short:
            grouped[(provider, snap["account_id"], "weekly" if weekly else "5h")].append(snap)
    receipt_groups = defaultdict(list)
    for index, row in enumerate(rows):
        receipt_groups[(row["provider"], row["account_id"])].append((index, row))
    allocated = [dict(weekly=0.0, **{"5h": 0.0}) for _ in rows]
    missing, accounts, calibration = [], [], defaultdict(list)
    for (provider, account, window_name), history in grouped.items():
        history.sort(key=lambda v: v["recorded_at"])
        history = [v for v in history if v["used_percent"] is not None and v["reset_at"] is not None]
        if not history:
            continue
        indexed = receipt_groups[(provider, account)]
        times = [row["requested_at"].replace(tzinfo=UTC) for _, row in indexed]
        maximum, previous, reset, previous_used = 0.0, None, None, None
        used, capacity = 0.0, 0.0
        periods = set()
        observed = False
        for snap in history:
            stamp = snap["recorded_at"].replace(tzinfo=UTC)
            end = datetime.fromtimestamp(snap["reset_at"], UTC)
            period_length = timedelta(minutes=10080 if window_name == "weekly" else 300)
            current_used = float(snap["used_percent"])
            credit_reset = previous_used is not None and (
                previous_used - current_used > 50 or (current_used <= 5 and previous_used >= 20)
            )
            if reset is None or abs((end - reset).total_seconds()) >= 3600 or credit_reset:
                maximum = 0.0
                reset = end
            delta = max(0.0, float(snap["used_percent"]) - maximum)
            maximum = max(maximum, float(snap["used_percent"]))
            if since <= stamp <= until:
                observed = True
                periods.add((reset - period_length, reset))
            if previous is not None and since < stamp <= until:
                used += delta
                left = bisect_left(times, max(previous, since))
                while left < len(times) and times[left] <= max(previous, since):
                    left += 1
                right = bisect_left(times, stamp)
                while right < len(times) and times[right] <= stamp:
                    right += 1
                candidates = indexed[left:right]
                weights = []
                for _, row in candidates:
                    usd = price_row(row)[0]
                    tokens = token_classes(row)
                    weights.append(
                        usd
                        if usd > 0
                        else tokens["fresh_input"]
                        + tokens["cache_write"]
                        + tokens["output"]
                        + 0.1 * tokens["cache_read"]
                    )
                total = sum(weights)
                if total:
                    remainder = delta
                    for (index, _), weight in zip(candidates[:-1], weights[:-1], strict=True):
                        part = delta * weight / total
                        allocated[index][window_name] += part
                        remainder -= part
                    allocated[candidates[-1][0]][window_name] += remainder
                    calibration[(provider, window_name, "all")].append((total, delta))
                    models = defaultdict(float)
                    for (_, row), weight in zip(candidates, weights, strict=True):
                        models[row["model"] or "unknown"] += weight
                    for model, weight in models.items():
                        if not weight:
                            continue
                        calibration[(provider, window_name, model)].append((weight, delta * weight / total))
                elif delta:
                    missing.append(
                        {"provider": provider, "account_id": account, "window": window_name, "points": delta}
                    )
            previous = stamp
            previous_used = current_used
        if observed:
            in_window = [
                snap["recorded_at"].replace(tzinfo=UTC)
                for snap in history
                if since <= snap["recorded_at"].replace(tzinfo=UTC) <= until
            ]
            presence = (max(in_window) - min(in_window)).total_seconds() if in_window else 0.0
            capacity = 100 * presence / (7 * 86400 if window_name == "weekly" else 5 * 3600)
            latest = history[-1]
            accounts.append(
                {
                    "provider": provider,
                    "account_id": account,
                    "window": window_name,
                    "quota_points": used,
                    "capacity_points": capacity,
                    "latest_used_percent": latest["used_percent"],
                    "headroom_now": max(0.0, 100 - latest["used_percent"])
                    if latest["reset_at"] > until.timestamp()
                    else 0.0,
                    "reset_at": latest["reset_at"],
                }
            )
    providers = {}
    for account in accounts:
        key = account["provider"] + ":" + account["window"]
        summary = providers.setdefault(
            key, {"accounts": 0, "quota_points": 0.0, "capacity_points": 0.0, "headroom_now": 0.0}
        )
        summary["accounts"] += 1
        summary["quota_points"] += account["quota_points"]
        summary["capacity_points"] += account["capacity_points"]
        summary["headroom_now"] += account["headroom_now"]
    for key, summary in providers.items():
        summary["headroom_points"] = summary["capacity_points"] - summary["quota_points"]
        summary["account_weeks" if key.endswith("weekly") else "5h_windows_used"] = summary["quota_points"] / 100
        summary["fleet_share"] = (
            summary["quota_points"] / summary["capacity_points"] if summary["capacity_points"] else 0
        )
        summary["sustainable_points_per_week"] = summary["accounts"] * 100 if key.endswith("weekly") else None
        sustainable = summary["accounts"] * 100 * (until - since).total_seconds() / (7 * 86400)
        summary["used_over_sustainable"] = summary["quota_points"] / sustainable if sustainable else 0
    fits = []
    for (provider, window_name, model), pairs in calibration.items():
        slope = sum(x * y for x, y in pairs) / sum(x * x for x, _ in pairs)
        mean = sum(y for _, y in pairs) / len(pairs)
        variance = sum((y - mean) ** 2 for _, y in pairs)
        ratios = [y / x for x, y in pairs]
        fits.append(
            {
                "provider": provider,
                "window": window_name,
                "model": model,
                "intervals": len(pairs),
                "points_per_weight": slope,
                "ratio_min": min(ratios),
                "ratio_max": max(ratios),
                "r_squared": 1 - sum((y - slope * x) ** 2 for x, y in pairs) / variance if variance > 1e-20 else None,
            }
        )
    return allocated, {"providers": providers, "accounts": accounts, "calibration": fits, "unattributed": missing}


def empty():
    return {
        "requests": 0,
        "tokens": 0,
        "usd": 0.0,
        "quota_points": 0.0,
        "quota_5h_points": 0.0,
        "token_classes": dict.fromkeys(CLASSES, 0),
        "usd_classes": {},
    }


def merge(target, value):
    for key in ("requests", "tokens", "usd"):
        target[key] += value[key]
    for key in ("quota_points", "quota_5h_points"):
        target[key] = target.get(key, 0) + value.get(key, 0)
    for key in ("token_classes", "usd_classes"):
        for name, amount in value[key].items():
            target[key][name] = target[key].get(name, 0) + amount


def allocate(value, agents):
    """Reconcile each LB class exactly, with the floating remainder on lead."""
    positive = [agent for agent in agents if agent.get("weight", 0) > 0]
    if not positive:
        return [({"key": "lead", "seat": "lead", "kind": "lead"}, value)]
    lead = next(
        (agent for agent in agents if agent["key"] == "lead"),
        {"key": "lead", "seat": "lead", "kind": "lead", "weight": 0},
    )
    ordered = [agent for agent in positive if agent["key"] != "lead"] + [lead]
    total = sum(agent["weight"] for agent in positive)
    remainder = {**value, "token_classes": value["token_classes"].copy(), "usd_classes": value["usd_classes"].copy()}
    allocated = []
    for agent in ordered[:-1]:
        fraction = agent["weight"] / total
        piece = {
            key: value.get(key, 0) * fraction
            for key in ("requests", "tokens", "usd", "quota_points", "quota_5h_points")
        }
        piece.update(
            {
                key: {name: amount * fraction for name, amount in value[key].items()}
                for key in ("token_classes", "usd_classes")
            }
        )
        for key in ("requests", "tokens", "usd", "quota_points", "quota_5h_points"):
            remainder[key] = remainder.get(key, 0) - piece[key]
        for key in ("token_classes", "usd_classes"):
            for name, amount in piece[key].items():
                remainder[key][name] -= amount
        allocated.append((agent, piece))
    return allocated + [(lead, remainder)]


def build_report(rows, aliases, weekly, metas, rules, dimensions, top, agents_by_session=None, preexisting=None):
    agents_by_session, preexisting = agents_by_session or {}, preexisting or set()
    totals, priced = empty(), Counter()
    groups = {dim: defaultdict(empty) for dim in DIMS}
    sessions, tree = {}, {"name": "all", **empty(), "children": {}}
    wastes = {
        key: {**empty(), "write_tokens": 0, "premium_usd": 0.0, "agents": defaultdict(empty)} for key in DETECTORS
    }
    daily = defaultdict(lambda: {"bust": 0.0, "ttl_expiry": 0.0})
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

    # Transcript sequences, not interleaved session/model receipts, own cache events.
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
            for events in sequences.values():
                previous = None
                looping = len(events) > 300 and median(e.get("loop_output", 0) for e in events) < 200
                for event in sorted(events, key=lambda e: event_time(e)):
                    kind = cache_kind(event, previous)
                    previous = event
                    point = event_time(event).timestamp()
                    pos = bisect_left(times, point)
                    nearest = min(
                        (i for i in (pos - 1, pos) if 0 <= i < len(times)), key=lambda i: abs(times[i] - point)
                    )
                    agent_receipts[indexed[nearest][0]] = (agent, event == events[0], looping)
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
                        daily[str(event_time(event).date())][kind] += premium
                    cache_receipts.add(indexed[nearest][0])
    financial_rows = {}
    for row in rows:
        sid = row["sid"]
        purpose, lane, rule = classify(row, metas.get(sid, {}), rules)
        count = row["requests"]
        tokens = token_classes(row)
        usd, provenance, split = price_row(row)
        if provenance != "log":
            usd *= count
            split = {k: v * count for k, v in split.items()}
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
        account = aliases.get(row["account_id"], safe_label(str(row["account_id"] or "unknown")[:8]))
        agents = agents_by_session.get(sid, []) if row["provider"] == "anthropic" else []
        for agent, piece in allocate(value, agents):
            seat, kind = agent["seat"], agent["kind"]
            agent_purpose = purpose
            if kind == "workflow":
                agent_purpose = (
                    "workflow-verify" if re.search(r"verify|review", agent.get("label", ""), re.I) else "workflow"
                )
            if not agents:
                if row["provider"] == "openai":
                    origin = metas.get(sid, {}).get("originator") or row.get("useragent_group") or "unknown"
                    seat, kind = "codex:" + safe_label(origin.split()[0]), "codex"
                elif metas.get(sid, {}).get("entrypoint", "").startswith("sdk-"):
                    kind = "headless"
                elif row["provider"] != "anthropic":
                    kind = "other"
            labels = dict(
                account=account,
                provider=row["provider"],
                purpose=agent_purpose,
                lane=lane,
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
                daily[str(event_time(event).date())][kind] += premium
            continue
        if index in cache_receipts:
            continue
        agent, first, looping = agent_receipts.get(
            index, ({"seat": "lead", "key": "lead"}, previous is None if not agents else False, False)
        )
        flags = waste_flags(row | {"first": first, "preexisting": sid in preexisting, "looping": looping})
        if not flags and purpose in ("unknown", "unlinked"):
            flags = ["unattributed"]
        for flag in flags:
            add_waste(flag, value, sid, lane, agent["seat"], agent["key"])

    def finish_tree(node):
        node["children"] = [finish_tree(child) for child in node["children"].values()]
        return node

    for waste in wastes.values():
        waste["share"] = waste["usd"] / totals["usd"] if totals["usd"] else 0
        waste["top"] = sorted(
            (
                {"session": sid[:12], "lane": lane, "seat": seat, "agent": key, **v}
                for (sid, lane, seat, key), v in waste.pop("agents").items()
            ),
            key=lambda v: v["usd"],
            reverse=True,
        )[:10]
    # Purpose and seat are always present in text/JSON, even with a custom --by.
    by = {
        dim: sorted(({"name": name, **v} for name, v in groups[dim].items()), key=lambda v: v["usd"], reverse=True)
        for dim in dict.fromkeys([*dimensions, "purpose", "seat"])
    }
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
        "by": by,
        "tree": finish_tree(tree),
        "sessions": sorted(sessions.values(), key=lambda v: v["usd"], reverse=True)[:top],
        "waste": wastes,
        "waste_total_usd": sum(wastes[k]["usd"] for k in DETECTORS if k != "first_write"),
        "cache_premium_by_day": dict(sorted(daily.items())),
        "priced_by": dict(priced),
        "account_shares": account_shares,
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


def text_report(report):
    lines = [
        f"Window: {report['window']['since']} to {report['window']['until']}",
        f"Receipts: {report['totals']['requests']:,}  Tokens: {report['totals']['tokens']:,}  "
        f"Quota: {report['totals'].get('quota_points', 0):,.2f} points / "
        f"{report['totals'].get('account_weeks', 0):.3f} account-weeks; "
        f"secondary list price: ${report['totals']['usd']:,.2f}",
        f"Pricing: {report['priced_by']}",
    ]
    for dim, values in report["by"].items():
        lines += [f"\n{dim.upper():<40} {'POINTS':>10} {'ACCT-WEEKS':>16} {'USD':>12}"]
        lines += [
            f"{v['name'][:40]:<40} {v.get('quota_points', 0):>10,.2f} {v.get('account_weeks', 0):>16,.3f} "
            f"{v['usd']:>12,.2f}"
            for v in values
        ]
    lines += ["\nEXCLUSIVE WASTE CANDIDATES (cache USD is write premium; first_write is not waste)"]
    lines += [
        f"{k:<25} {v.get('quota_points', 0):>10,.2f} ${v['usd']:>12,.2f} {v['share']:>8.1%}"
        for k, v in report["waste"].items()
    ]
    for name, summary in report.get("quota", {}).get("providers", {}).items():
        lines.append(f"{name}: {summary['quota_points']:.2f} points; headroom now {summary['headroom_now']:.2f}")
    lines += ["\nNotes:"] + report["notes"]
    return "\n".join(lines) + "\n"


def html_report(report):
    page = [
        '<!doctype html><html><meta charset="utf-8"><title>Token audit</title>',
        "<style>body{font:16px system-ui;margin:32px;max-width:1200px}table{border-collapse:collapse;width:100%}"
        "th,td{padding:8px;text-align:right;border-bottom:1px solid #ddd}th:first-child,td:first-child{text-align:left}"
        "</style><h1>Token audit</h1>",
        f"<p>Quota: {report['totals'].get('quota_points', 0):,.2f} points / "
        f"{report['totals'].get('account_weeks', 0):.3f} account-weeks; secondary list price: "
        f"${report['totals']['usd']:,.2f}</p>",
    ]
    for dim, values in report["by"].items():
        page.append(
            f"<h2>{html.escape(dim)}</h2><table><tr><th>Name</th><th>Quota points</th>"
            "<th>Account-weeks</th><th>USD</th></tr>"
        )
        for value in values:
            page.append(
                f"<tr><td>{html.escape(value['name'])}</td><td>{value.get('quota_points', 0):,.2f}</td>"
                f"<td>{value.get('account_weeks', 0):,.3f}</td><td>{value['usd']:,.2f}</td></tr>"
            )
        page.append("</table>")
    page.append(
        "<h2>Exclusive waste candidates</h2><p>Cache dollars are write premiums; first_write is not waste.</p>"
        "<table><tr><th>Kind</th><th>Requests</th><th>Write tokens</th><th>USD</th></tr>"
    )
    for kind, value in report["waste"].items():
        page.append(
            f"<tr><td>{kind}</td><td>{value.get('quota_points', 0):,.2f}</td><td>{value['write_tokens']:,}</td>"
            f"<td>{value['usd']:,.2f}</td></tr>"
        )
    return "".join(page) + "</table></html>"


def load_receipts(args, since, until, notes):
    import psycopg
    from psycopg.rows import dict_row

    from app.core.config.settings import get_settings

    url = args.db or os.environ.get("AGENT_LB_DATABASE_URL") or get_settings().database_url
    if not args.db and url.startswith("sqlite"):
        notes.append("Configured SQLite default has no live receipts; using local PostgreSQL audit source.")
        url = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"
    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(
        url, options="-c default_transaction_read_only=on -c statement_timeout=90000", row_factory=dict_row
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,alias FROM accounts")
            aliases = {r["id"]: safe_label(r["alias"], r["id"][:8]) for r in cursor.fetchall()}
            cursor.execute(
                """SELECT DISTINCT ON (account_id) account_id,used_percent,recorded_at,reset_at
            FROM usage_history WHERE (window_minutes=10080 OR (provider='anthropic' AND "window"='secondary'))
            AND recorded_at <= %s AND reset_at > %s ORDER BY account_id,recorded_at DESC""",
                (until.replace(tzinfo=None), int(until.timestamp())),
            )
            weekly = {
                aliases.get(r["account_id"], r["account_id"][:8]): {
                    "used_percent": r["used_percent"],
                    "recorded_at": r["recorded_at"].isoformat(),
                    "reset_at": r["reset_at"],
                }
                for r in cursor.fetchall()
            }
            cursor.execute(
                """SELECT account_id,provider,"window",window_minutes,used_percent,recorded_at,reset_at
                FROM usage_history WHERE recorded_at >= %s AND recorded_at <= %s
                ORDER BY provider,account_id,recorded_at""",
                ((since - timedelta(days=14)).replace(tzinfo=None), until.replace(tzinfo=None)),
            )
            snapshots = cursor.fetchall()
            cursor.execute(QUERY, (since.replace(tzinfo=None), until.replace(tzinfo=None)))
            rows = cursor.fetchall()
            cursor.execute(
                """SELECT DISTINCT coalesce(nullif(client_session_id,''),nullif(session_id,'')) AS sid
                FROM request_logs WHERE deleted_at IS NULL AND requested_at >= %s AND requested_at < %s""",
                ((since - timedelta(hours=1)).replace(tzinfo=None), since.replace(tzinfo=None)),
            )
            preexisting = {r["sid"] for r in cursor.fetchall()}
    return rows, snapshots, aliases, weekly, preexisting


def run(args):
    try:
        since, until = window(args)
        dimensions = args.by.split(",")
        if any(dim not in DIMS for dim in dimensions) or args.top < 1:
            raise ValueError("Invalid --by dimension or --top (must be positive)")
        panes, notes = pane_maps()
        if args.snapshot_panes_only:
            print("Pane snapshot complete. " + " ".join(notes))
            return
        rows, snapshots, aliases, weekly, preexisting = load_receipts(args, since, until, notes)
        quota_rows, quota = quota_allocation(rows, snapshots, since, until)
        for row, points in zip(rows, quota_rows, strict=True):
            row.update(quota_points=points["weekly"], quota_5h_points=points["5h"])
        for index, item in enumerate(quota["unattributed"]):
            rows.append(
                {
                    "sid": f"unattributed-quota:{index}",
                    "provider": item["provider"],
                    "account_id": item["account_id"],
                    "model": "unknown",
                    "requests": 0,
                    "cost_usd": 0.0,
                    "requested_at": until.replace(tzinfo=None),
                    "status": "success",
                    "quota_points": item["points"] if item["window"] == "weekly" else 0.0,
                    "quota_5h_points": item["points"] if item["window"] == "5h" else 0.0,
                }
            )
        index = session_index()
        metas = {}
        for sid in {row["sid"] for row in rows}:
            meta = metadata(index.get(sid))
            if sid in panes:
                meta.update(pane_title=panes[sid]["title"], pane=panes[sid]["pane"])
            metas[sid] = meta
        with RULE_PATH.open("rb") as stream:
            rules = tomllib.load(stream)["rules"]
        agents = {
            sid: session_agents(index.get(sid), since, until)
            for sid in {row["sid"] for row in rows if row["provider"] == "anthropic"}
        }
        report = build_report(rows, aliases, weekly, metas, rules, dimensions, args.top, agents, preexisting)
        add_quota_units(report, quota)
        report.update(
            window={"since": since.isoformat(), "until": until.isoformat()},
            generated_at=datetime.now(UTC).isoformat(),
            notes=notes
            + [
                "Rolling UTC window, not account reset windows; weekly quota snapshots are separate.",
                "Reasoning is reported but not added to output. Missing usage is not estimated.",
                "Observed cost splits use current model/tier proportions; unknown splits unallocated.",
                "Waste assignments are exclusive candidates, not proven savings; cache USD is premium over reads.",
                "LB totals are allocated by transcript input-side cost, not transcript output.",
                "No transcript prompt text is emitted. Unknown purpose remains explicit.",
            ],
        )
        if args.json:
            output = json.dumps(report, indent=2) + "\n"
        elif args.html:
            output = html_report(report)
        else:
            output = text_report(report)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output)
            print(f"Wrote token audit: {args.out}")
        else:
            print(output, end="")
    except Exception as exc:
        # DB errors may include connection credentials: never echo those exceptions.
        message = f"Token audit failed ({type(exc).__name__}); check local inputs and database availability."
        raise SystemExit(message) from None
