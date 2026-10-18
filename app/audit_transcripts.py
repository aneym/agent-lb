from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from app.audit_pricing import input_cost

RULE_PATH = Path(__file__).resolve().parent.parent / "config/audit/purpose_rules.toml"


def safe_label(value, fallback="unknown"):
    value = str(value or fallback)

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


DIGESTS = {}


def transcript_digest(path):
    stat = path.stat()
    signature = [str(path), stat.st_size, stat.st_mtime_ns, RULE_PATH.stat().st_mtime_ns, 8]
    memo = DIGESTS.get(str(path))
    if memo is not None and memo[0] == signature:
        return memo[1]
    digest = read_digest(path, signature)
    DIGESTS[str(path)] = (signature, digest)
    return digest


def read_digest(path, signature):
    cache = Path.home() / ".agent-lb/audit/transcript-cache" / (hashlib.sha1(str(path).encode()).hexdigest() + ".json")
    try:
        cached = json.loads(cache.read_text())
        if cached["signature"] == signature:
            return cached["digest"]
    except (OSError, ValueError, KeyError):
        pass
    result = {
        "prompt": "",
        "cwd": "",
        "title": "",
        "entrypoint": "",
        "originator": "",
        "events": [],
        "compact_boundaries": [],
    }
    seen = set()
    events_by_id, tools_by_id, seen_tools = {}, defaultdict(list), set()
    with path.open() as stream:
        for line in stream:
            if not any(
                marker in line
                for marker in (
                    '"usage"',
                    '"tool_use"',
                    '"isCompactSummary"',
                    '"compact_boundary"',
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
            if item.get("isCompactSummary") or (
                item.get("type") == "system" and item.get("subtype") == "compact_boundary"
            ):
                if item.get("timestamp"):
                    result["compact_boundaries"].append(item["timestamp"])
            if item.get("type") == "assistant":
                content = message.get("content") or []
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    canonical = json.dumps(block.get("input", {}), sort_keys=True, separators=(",", ":"))
                    pair = [block.get("name") or "", hashlib.sha1(canonical.encode()).hexdigest()]
                    identity = (message.get("id"), block.get("id") or tuple(pair))
                    if identity not in seen_tools:
                        seen_tools.add(identity)
                        tools_by_id[message.get("id")].append(pair)
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
            events_by_id[message["id"]] = result["events"][-1]
    for message_id, event in events_by_id.items():
        event["tool_calls"] = tools_by_id[message_id]
        event["compact_boundaries"] = result["compact_boundaries"]

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

        events = [
            event | {"sequence": str(file)}
            for event in digest["events"]
            if (stamp := event_time(event)) and since <= stamp < until
        ]
        agent["events"].extend(events)
    for agent in agents.values():
        agent["weight"] = sum(input_cost(event) for event in agent["events"] if event["model"].startswith("claude-"))
        agent["openai_weight"] = sum(
            input_cost(event) for event in agent["events"] if event["model"].startswith("gpt-")
        )
    return list(agents.values())


def lane_source(row, meta):
    if row.get("sid", "").startswith("unattributed-quota:"):
        return None
    if isinstance(row.get("lane"), str) and row["lane"].strip():
        return "receipt"
    prompt = meta.get("prompt", "")
    for source, found in (
        ("pane", meta.get("pane_title")),
        ("lane_path", re.search(r"lanes/([^/\s]+)/", prompt)),
        ("brief", re.search(r"([\w-]+-brief)\.md", prompt, re.I)),
        ("transcript", meta.get("lane_hint") if meta.get("lane_hint") != "unknown" else None),
        ("title", meta.get("title")),
    ):
        if found:
            return source
    return None


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
    receipt_lane = row.get("lane").strip() if isinstance(row.get("lane"), str) else ""
    lane = (
        receipt_lane
        or meta.get("pane_title")
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
        fallback = f"{client} (no rollout)" if row.get("provider") == "openai" else f"{client} (no transcript)"
        return "unlinked", safe_label(receipt_lane) if receipt_lane else fallback, "unlinked"
    return purpose, safe_label(lane), "entrypoint"
