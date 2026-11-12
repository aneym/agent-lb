from __future__ import annotations

import json
import subprocess

from app.core.thread_delivery import on_agent_host


def rails_command(agent: str, tool: str) -> list[str]:
    script = f'd="$HOME/.agent-rails/agents/{agent}/.rails-one"; RAILS_HOME="$d" exec "$d/rails" {tool} - --json'
    return on_agent_host(agent, ["sh", "-c", script])


def call(agent: str, tool: str, arguments: dict, timeout: int = 45) -> dict:
    try:
        result = subprocess.run(
            rails_command(agent, tool), input=json.dumps(arguments), capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{tool} did not answer: {type(exc).__name__}"}
    try:
        payload = json.loads(result.stdout or "null")
    except ValueError:
        payload = None
    if result.returncode != 0 or not isinstance(payload, dict):
        detail = payload.get("error") if isinstance(payload, dict) else (result.stderr or result.stdout).strip()
        return {"error": f"{tool} exited {result.returncode}: {str(detail)[:300]}"}
    return payload


def ask_owner(agent: str, question: str, options: list[str]) -> dict:
    payload = call(agent, "ask", {"to": "owner", "question": question, "options": options})
    if "error" in payload:
        return {"landed": False, "detail": payload["error"]}
    ask = payload.get("ask") if isinstance(payload.get("ask"), dict) else payload
    ask_id = ask.get("id") or payload.get("askId") or payload.get("ask_id")
    if not ask_id:
        return {"landed": False, "detail": "Rails answered without an ask id"}
    return {"landed": True, "askId": str(ask_id), "repeat": bool(payload.get("repeat"))}


def withdraw(agent: str, ask_id: str) -> bool:
    return "error" not in call(agent, "ask_withdraw", {"ask_id": ask_id})


def reachable(agent: str) -> dict:
    script = f'd="$HOME/.agent-rails/agents/{agent}/.rails-one"; RAILS_HOME="$d" exec "$d/rails" whoami --json'
    try:
        result = subprocess.run(on_agent_host(agent, ["sh", "-c", script]), capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout or "null")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"reachable": False}
    return {"reachable": result.returncode == 0 and isinstance(payload, dict), "as": (payload or {}).get("agent_name")}
