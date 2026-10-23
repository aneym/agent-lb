from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path

from claude_lb_common import headless_invocation  # pyrefly: ignore[missing-import]


def dispatch_document(value: str) -> dict:
    if not value.lstrip().startswith("{"):
        return {}
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return document if isinstance(document, dict) else {}


def dispatch_id(value: str) -> str:
    if not value.lstrip().startswith("{"):
        return value
    decision = dispatch_document(value).get("decision_id")
    return decision if isinstance(decision, str) else ""


def tag_headers(environ: Mapping[str, str]) -> dict[str, str]:
    tags = {
        header: environ[name]
        for name, header in (
            ("AGENT_LB_INTENT", "x-agent-lb-intent"),
            ("AGENT_LB_LANE", "x-agent-lb-lane"),
            ("AGENT_LB_SEAT", "x-agent-lb-seat"),
            ("FACTORY_PROJECT", "x-factory-project"),
            ("FACTORY_JOB", "x-factory-job"),
        )
        if environ.get(name)
    }
    raw_dispatch = environ.get("AGENT_LB_DISPATCH") or ""
    decision = dispatch_id(raw_dispatch)
    if decision:
        tags["x-agent-lb-dispatch"] = decision
    document = dispatch_document(raw_dispatch)
    job = environ.get("AGENT_LB_JOB") or document.get("job_id")
    if isinstance(job, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,63}", job):
        tags["x-agent-lb-job"] = job
    if "x-agent-lb-lane" not in tags:
        lane = document.get("lane") or Path(environ.get("LANE_DIR") or "").name
        if isinstance(lane, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._:/-]{0,127}", lane):
            tags["x-agent-lb-lane"] = lane
    return tags


def resolve_lane_tags(argv: list[str]) -> str:

    if os.environ.get("AGENT_LB_INTENT"):
        return "explicit intent"
    if headless_invocation(argv) or any(arg.startswith("--print=") for arg in argv):
        return "print mode"
    if os.environ.get("CLAUDECODE"):
        return "child process"
    tab = os.environ.get("HERDR_TAB_ID")
    if not tab:
        return "no herdr tab"
    if (
        re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", tab) is None
        or re.fullmatch(r"[A-Za-z0-9_-]+:[A-Za-z0-9_-]+", tab) is None
    ):
        return "invalid herdr tab"

    result: list[tuple[str, str, str]] = []

    def read_tags() -> None:
        try:
            home = Path.home()
            managed = home / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
            fallback = home / ".agents" / "policy" / "coding-agents" / "routing-table.json"
            configured = (os.environ.get("ROUTE_TABLE") or "").strip()
            registry = home / ".agent-rails" / "workflows" / "kinds" / f"{tab.replace(':', '_')}.json"
            table = Path(configured).expanduser() if configured else managed if managed.is_file() else fallback
            rollout = json.loads(table.read_text()).get("policy", {}).get("stand_in_rollout", {})
            stage = rollout.get("stage", "off")
            if stage not in {"canary", "half", "all"}:
                result.append(("", "", "rollout off"))
                return
            try:
                kind = json.loads(registry.read_text())
            except FileNotFoundError:
                kind = {}
            review_lane = (
                any(
                    re.search(r"(?<![A-Za-z])(?:review|verif|audit)", str(value), re.IGNORECASE)
                    for value in (kind.get("lane", ""), kind.get("title", ""))
                )
                or str(kind.get("kind", "")).lower() == "review"
            )
            lane = kind.get("lane") or tab
            if not isinstance(lane, str) or re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", lane) is None:
                lane = tab
            registry_kind = kind.get("kind")
            tabs, lanes = rollout.get("tabs", []), rollout.get("lanes", [])
            if not isinstance(tabs, list) or not isinstance(lanes, list) or not isinstance(lane, str):
                raise ValueError("invalid rollout or registry")
            if review_lane:
                result.append(("", "", "review lane"))
            elif registry_kind in {"ephemeral", "background"}:
                result.append(("", "", "excluded registry kind"))
            elif (
                stage == "all"
                or tab in tabs
                or kind.get("lane") in lanes
                or (stage == "half" and int(hashlib.sha256(tab.encode()).hexdigest(), 16) % 2 == 0)
            ):
                result.append(("orchestrator" if registry_kind == "orchestrator" else "lane-tab", lane, stage))
            else:
                result.append(("", "", "outside canary"))
        except Exception:
            result.append(("", "", "policy or registry unreadable"))

    reader = threading.Thread(target=read_tags, daemon=True)
    reader.start()
    reader.join(0.045)
    if reader.is_alive() or not result:
        return "policy lookup timed out"
    intent, lane, reason = result[0]
    if intent:
        label = ""
        try:
            lookup = subprocess.run(
                ["herdr", "tab", "get", tab],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if lookup.returncode == 0:
                label = json.loads(lookup.stdout)["result"]["tab"]["label"]
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            pass
        if re.search(r"(?<![A-Za-z])(?:review|verif|audit)", str(label), re.IGNORECASE):
            return "review lane"
    previous = {name: os.environ.get(name) for name in ("AGENT_LB_INTENT", "AGENT_LB_LANE")}
    try:
        if intent:
            os.environ["AGENT_LB_INTENT"] = intent
            if not previous["AGENT_LB_LANE"]:
                os.environ["AGENT_LB_LANE"] = lane
    except Exception:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        return "tag assignment failed"
    return reason


def safe_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:/-]", "?", value)[:128]


def stand_in_banner() -> str:
    intent = os.environ.get("AGENT_LB_INTENT")
    if not intent:
        return "stand-in: off"
    lane = os.environ.get("AGENT_LB_LANE")
    return f"stand-in: {safe_tag(intent)}" + (f" ({safe_tag(lane)})" if lane else " (lane: none)")
