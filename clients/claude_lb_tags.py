from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path


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


def safe_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:/-]", "?", value)[:128]
