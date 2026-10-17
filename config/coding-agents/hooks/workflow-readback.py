import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


DISPATCH_LINE = re.compile(r"(?im)^[\s>*`-]*Dispatch:\s*`?(dcn_[0-9A-HJKMNP-TV-Z]{26})\b")


def decision_id(agent):
    value = agent.get("decision_id")
    if isinstance(value, str) and value.startswith("dcn_"):
        return value
    for key in ("prompt", "result"):
        found = DISPATCH_LINE.search(agent.get(key) or "") if isinstance(agent.get(key), str) else None
        if found:
            return found.group(1)
    return None


def agent_rows(response):
    agents = response.get("agents") or []
    rows = []
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict):
            continue
        rows.append(
            {
                "label": agent.get("label") if isinstance(agent.get("label"), str) else None,
                "agent_type": agent.get("agentType"),
                "decision_id": decision_id(agent),
            }
        )
    return rows


def usage_tokens(usage):
    usage = usage if isinstance(usage, dict) else {}
    return (
        number(usage.get("input_tokens", usage.get("tokens_in"))),
        number(usage.get("output_tokens", usage.get("tokens_out"))),
    )


def agent_tokens(response):
    totals, found = [0, 0], False
    agents = response.get("agents")
    for agent in agents if isinstance(agents, list) else []:
        if not isinstance(agent, dict):
            continue
        tokens_in, tokens_out = usage_tokens(agent.get("usage"))
        if tokens_in is None and tokens_out is None:
            continue
        found = True
        totals[0] += tokens_in or 0
        totals[1] += tokens_out or 0
    return tuple(totals) if found else (None, None)


def receipt_tokens(session_id, response, now):
    duration = number(response.get("duration_ms", response.get("durationMs")))
    if not isinstance(session_id, str) or not session_id or duration is None:
        return None, None
    script = Path(
        os.environ.get(
            "AGENT_LB_REQUEST_QUERY", str(Path.home() / ".agent-lb/runtime/agent-lb/scripts/request_log_query.py")
        )
    )
    if not script.is_file():
        return None, None
    try:
        since = (now - timedelta(milliseconds=duration)).isoformat()
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--session",
                session_id,
                "--since",
                since,
                "--until",
                now.isoformat(),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        total = json.loads(result.stdout)["summary"]["total"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, OverflowError):
        return None, None
    if not isinstance(total, dict) or not total.get("requests"):
        return None, None
    tokens_in = sum(number(total.get(key)) or 0 for key in ("input", "cache_read", "cache_write"))
    return tokens_in, number(total.get("output"))


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Workflow":
        return
    response = payload.get("tool_response") or {}
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = {}
    response = response if isinstance(response, dict) else {}
    now = datetime.now(timezone.utc)
    usage = response.get("usage") or {}
    usage = usage if isinstance(usage, dict) else {}
    state = Path(os.environ.get("FACTORY_HOME", str(Path.home() / ".open-factory/state")))
    capture = Path(os.environ.get("FACTORY_UNIFIED_UTILIZATION", str(state / "unified-utilization.json")))
    workflow_id = response.get("workflow_id") or response.get("id")
    quota = number(response.get("quota_percent", usage.get("quota_percent")))
    if quota is None and capture.is_file():
        try:
            captured = json.loads(capture.read_text())
        except (OSError, ValueError):
            captured = {}
        if workflow_id and captured.get("workflow_id") == workflow_id:
            quota = number(captured.get("quota_percent"))
    if quota is not None and quota > 100:
        quota = None
    tokens_in, tokens_out = usage_tokens(usage)
    token_source = "tokens"
    for source, lookup in (
        ("agents", lambda: agent_tokens(response)),
        ("agent-lb-receipts", lambda: receipt_tokens(payload.get("session_id"), response, now)),
    ):
        if tokens_in is not None or tokens_out is not None:
            break
        tokens_in, tokens_out = lookup()
        token_source = source
    record = {
        "at": now.isoformat(),
        "workflow_id": workflow_id,
        "session_id": payload.get("session_id") if isinstance(payload.get("session_id"), str) else None,
        "quota_percent": quota,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "usage_source": "unified-utilization" if quota is not None else token_source,
        "agents": agent_rows(response),
    }
    if quota is None and tokens_in is None and tokens_out is None:
        record["usage_source"] = "unavailable"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "workflow-readback.jsonl").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
