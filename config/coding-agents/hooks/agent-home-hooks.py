import json
import os
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "PreCompact", "SessionEnd")
BUDGET = {"SessionStart": 28, "SessionEnd": 28}
DEFAULT_BUDGET = 8
MANAGED = (
    Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if sys.platform == "darwin"
    else Path("/etc/claude-code/managed-settings.json")
)
FLAG = "allow" + "ManagedHooksOnly"
RUNTIME_BIN = Path.home() / ".agent-rails/rails-host-runtime/current/packages/agent-runtime/bin"
DENY = ("deny", "ask")


def managed_only():
    try:
        return json.loads(MANAGED.read_text()).get(FLAG) is True
    except (OSError, ValueError, AttributeError):
        return False


def agent_home(payload):
    raw = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ""
    if not raw:
        return None
    home = Path(raw)
    try:
        card = json.loads((home / "agent.json").read_text())
    except (OSError, ValueError):
        return None
    return home if isinstance(card, dict) and card.get("home") is True else None


def matches(matcher, value):
    if matcher in (None, "", "*"):
        return True
    if not isinstance(matcher, str) or not isinstance(value, str):
        return False
    try:
        return re.fullmatch(matcher, value) is not None
    except re.error:
        return value in matcher.split("|")


def owned(command, bin_dir):
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or not os.path.isabs(argv[0]):
        return None
    real = os.path.realpath(argv[0])
    if os.path.dirname(real) != bin_dir or not os.path.isfile(real) or not os.access(real, os.X_OK):
        return None
    return argv


def planned(home, event, payload):
    try:
        settings = json.loads((home / ".claude" / "settings.json").read_text())
    except (OSError, ValueError):
        return []
    groups = (settings.get("hooks") or {}).get(event) if isinstance(settings, dict) else None
    if not isinstance(groups, list):
        return []
    subject = payload.get("source") if event == "SessionStart" else payload.get("tool_name")
    bin_dir = os.path.realpath(RUNTIME_BIN)
    found = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if event in ("SessionStart", "PreToolUse", "PostToolUse") and not matches(group.get("matcher"), subject):
            continue
        for hook in group.get("hooks") or []:
            if not isinstance(hook, dict) or hook.get("type") != "command":
                continue
            argv = owned(str(hook.get("command") or ""), bin_dir)
            if argv:
                timeout = hook.get("timeout")
                limit = timeout if isinstance(timeout, (int, float)) and timeout > 0 else 60
                found.append((argv, float(limit), hook.get("async") is True))
    return found


def detach(argv, data, cwd):
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            start_new_session=True,
        )
        assert proc.stdin is not None
        proc.stdin.write(data)
        proc.stdin.close()
    except (OSError, ValueError):
        pass


def run(argv, data, cwd, limit, outputs, index):
    try:
        done = subprocess.run(argv, input=data, capture_output=True, cwd=cwd, timeout=limit)
    except (OSError, ValueError, subprocess.SubprocessError):
        return
    if done.returncode == 0:
        outputs[index] = done.stdout.decode("utf-8", "replace")


def merged(event, texts):
    contexts, messages, top, specific = [], [], {}, {}
    for text in texts:
        head = text.strip()
        if not head:
            continue
        try:
            data = json.loads(head) if head[:1] == "{" else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            if event in ("SessionStart", "UserPromptSubmit"):
                contexts.append(head)
            continue
        hook = data.get("hookSpecificOutput")
        if isinstance(hook, dict):
            if isinstance(hook.get("additionalContext"), str) and hook["additionalContext"].strip():
                contexts.append(hook["additionalContext"].strip())
            if hook.get("permissionDecision") in DENY and "permissionDecision" not in specific:
                specific["permissionDecision"] = hook["permissionDecision"]
                if isinstance(hook.get("permissionDecisionReason"), str):
                    specific["permissionDecisionReason"] = hook["permissionDecisionReason"]
        if isinstance(data.get("systemMessage"), str) and data["systemMessage"].strip():
            messages.append(data["systemMessage"].strip())
        if data.get("decision") == "block" and "decision" not in top:
            top["decision"] = "block"
            if isinstance(data.get("reason"), str):
                top["reason"] = data["reason"]
        if data.get("continue") is False and "continue" not in top:
            top["continue"] = False
            if isinstance(data.get("stopReason"), str):
                top["stopReason"] = data["stopReason"]
    if contexts and event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
        specific["additionalContext"] = "\n\n".join(contexts)
    if messages:
        top["systemMessage"] = "\n".join(messages)
    if specific:
        top["hookSpecificOutput"] = {"hookEventName": event, **specific}
    return top


def main(argv):
    if len(argv) != 1 or argv[0] not in EVENTS:
        return 0
    event = argv[0]
    data = sys.stdin.buffer.read()
    if not managed_only():
        return 0
    try:
        payload = json.loads(data or b"{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    home = agent_home(payload)
    if home is None:
        return 0
    plan = planned(home, event, payload)
    budget = BUDGET.get(event, DEFAULT_BUDGET)
    outputs: dict[int, str] = {}
    workers = []
    for index, (command, limit, background) in enumerate(plan):
        if background:
            detach(command, data, str(home))
            continue
        worker = threading.Thread(
            target=run, args=(command, data, str(home), min(limit, budget), outputs, index), daemon=True
        )
        worker.start()
        workers.append(worker)
    for worker in workers:
        worker.join(budget + 1)
    result = merged(event, [outputs[index] for index in sorted(outputs)])
    if result:
        sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
