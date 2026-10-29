import hashlib
import json
import os
import re
import signal
import subprocess
import time

from floor_hooks import FLOOR_TEXT, FLOOR_WORD, INLINE_FLOOR  # type: ignore[missing-import]

DEFAULT_TIMEOUT = 600.0


def reap_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def run_shell(command, payload, env, cwd, timeout):
    started = time.monotonic()
    proc = subprocess.Popen(
        ["/bin/sh", "-c", command],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(payload, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        reap_group(proc.pid)
        out, err = proc.communicate()
        timed_out = True
    reap_group(proc.pid)
    code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    return {
        "command": command,
        "code": code,
        "out": out.decode("utf-8", "replace"),
        "err": err.decode("utf-8", "replace"),
        "timed_out": timed_out,
        "pid": proc.pid,
        "seconds": round(time.monotonic() - started, 3),
    }


def hook_timeout(hook):
    value = hook.get("timeout")
    return float(value) if isinstance(value, (int, float)) and value > 0 else DEFAULT_TIMEOUT


def parse_json(text):
    text = text.strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def effect(event, results):
    view = {
        "decision": None,
        "block": [],
        "updatedInput": None,
        "context": [],
        "shown": [],
        "continue": True,
        "stopReason": [],
        "plain": [],
    }
    failed_stdout = False
    rank = {"allow": 1, "ask": 2, "deny": 3}
    for result in results:
        if result["timed_out"]:
            continue
        obj = parse_json(result["out"])
        if obj is None:
            if result["code"] == 2:
                view["block"].append("[%s]: %s" % (result["command"], result["err"] or "No stderr output"))
            elif result["code"] == 0:
                if result["out"].strip():
                    view["plain"].append(result["out"])
            else:
                view["shown"].append(NOTICE_PREFIX + (result["err"].strip() or "No stderr output"))
                failed_stdout = failed_stdout or bool(result["out"].strip())
            continue
        specific = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
        reason = None
        if obj.get("decision") == "block":
            reason = obj.get("reason") or "Blocked by hook"
        decision = specific.get("permissionDecision") if event == "PreToolUse" else None
        if decision == "deny":
            reason = specific.get("permissionDecisionReason") or obj.get("reason") or "Blocked by hook"
        elif decision in rank and rank[decision] > rank.get(view["decision"], 0):
            view["decision"] = decision
        if reason is None and result["code"] == 2:
            reason = "[%s]: %s" % (result["command"], result["err"] or "No stderr output")
        if reason is not None:
            view["block"].append(reason)
        if "updatedInput" in specific:
            view["updatedInput"] = specific["updatedInput"]
        if specific.get("additionalContext"):
            view["context"].append(str(specific["additionalContext"]))
        if obj.get("systemMessage"):
            view["shown"].append(str(obj["systemMessage"]))
        if obj.get("continue") is False:
            view["continue"] = False
            if obj.get("stopReason"):
                view["stopReason"].append(str(obj["stopReason"]))
    if view["block"]:
        view["decision"] = "deny" if event == "PreToolUse" else "block"
        if event == "PreToolUse":
            view["updatedInput"] = None
    if event == "UserPromptSubmit":
        view["context"] += [text.rstrip("\n") for text in view["plain"]]
    plain = view.pop("plain")

    view["block_raw"] = "\n".join(view["block"])
    view["block_parts"] = [
        FLOOR_MESSAGE.sub(r"\1hook-dispatch: floor guard refusal", message) for message in view["block"]
    ]
    view["block"] = "\n".join(view["block_parts"])
    view["context"] = "\n".join(view["context"])
    view["shown"] = "\n".join(view["shown"])
    view["stopReason"] = "\n".join(view["stopReason"])
    view["transcript_only_stdout"] = (bool(plain) and event != "UserPromptSubmit") or failed_stdout
    return view


FLOOR_MESSAGE = re.compile(r"^(\[[^\n]*\]: )hook-dispatch: floor guard [^\n]*(?:\n.*)?$", re.S)


NOTICE_PREFIX = "Failed with non-blocking status code: "


RECEIPT_ENV = "HOOK_FLOOR_RECEIPT"


COMMAND_DIGESTS = {
    "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c": '"$HOME/.claude/hooks/dangerous-command-guard.sh"',
}


DIRECT = re.compile(
    r'^\s*(?:(/usr/bin/python3|python3|/bin/bash|bash)\s+)?("(?:\$HOME|)/[^"$`\\]*"|(?:\$HOME|~)?/'
    r'[^\s"\'$`\\;&|<>(){}*?\[\]]+)((?:\s+[A-Za-z0-9_./=:+-]+)*)\s*$'
)


def legacy(command):
    return COMMAND_DIGESTS.get(hashlib.sha256(command.encode("utf-8")).hexdigest())


def floor_of(command):
    command = legacy(command) or command
    match = FLOOR_WORD.search(command)
    if match:
        return match.group(1)
    return INLINE_FLOOR if FLOOR_TEXT in command else None


def direct_argv(command, name, home):
    match = DIRECT.match(legacy(command) or command)
    if not match:
        return None
    script = match.group(2).strip('"')
    if script.startswith("$HOME/"):
        script = home + script[len("$HOME") :]
    elif script.startswith("~/"):
        script = home + script[1:]
    if os.path.basename(script) != name:
        return None
    return ([match.group(1)] if match.group(1) else []) + [script] + match.group(3).split()


def run_direct(argv, name, payload, env, cwd, timeout):
    started = time.monotonic()
    env = dict(env, **{RECEIPT_ENV: "1"})
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "command": " ".join(argv),
            "code": 127 if isinstance(exc, FileNotFoundError) else 126,
            "out": "",
            "err": str(exc),
            "timed_out": False,
            "receipt": False,
            "seconds": 0.0,
        }
    try:
        out, err = proc.communicate(payload, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        reap_group(proc.pid)
        out, err = proc.communicate()
        timed_out = True
    reap_group(proc.pid)
    code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    text, receipt = out.decode("utf-8", "replace"), False
    want = "floor-ok %s" % name
    body = text[:-1] if text.endswith("\n") else text
    cut = body.rfind("\n") + 1
    if body[cut:] == want:
        text, receipt = body[:cut], True
    return {
        "command": " ".join(argv),
        "code": code,
        "out": text,
        "err": err.decode("utf-8", "replace"),
        "timed_out": timed_out,
        "receipt": receipt,
        "seconds": round(time.monotonic() - started, 3),
    }


def floor_failed(raw):
    if raw.get("opaque") or raw["timed_out"]:
        return True
    if raw["code"] == 2:
        return False
    if raw["code"] != 0 or not raw.get("receipt"):
        return True
    text = raw["out"].strip()
    if not text:
        return False
    obj = parse_json(text)
    if obj is None:
        return True
    specific = obj.get("hookSpecificOutput")
    if specific is not None and not isinstance(specific, dict):
        return True
    specific = specific or {}
    decision = specific.get("permissionDecision", "allow")
    if not isinstance(decision, str) or decision not in ("allow", "ask", "deny"):
        return True
    reason = specific.get("permissionDecisionReason")
    if reason is not None and not isinstance(reason, str):
        return True
    return "decision" in obj and obj["decision"] not in ("block", "approve")


def with_floor(event, results, unfolded=()):
    if event != "PreToolUse":
        return results
    out = []
    for result in results:
        raw = result.get("raw") or result
        blocked = raw["code"] == 2 and not raw["timed_out"]
        if result.get("floor") and result["command"] not in unfolded and (floor_failed(raw) or blocked):
            if floor_failed(raw):
                result = dict(result, code=2, out="", err="hook-dispatch: floor guard refusal", timed_out=False)
            else:
                result = dict(result, code=raw["code"], out=raw["out"], err=raw["err"], timed_out=False)
        out.append(result)
    return out


def with_exec_timer(results, execd):
    return [
        dict(result, code=128 + signal.SIGALRM, out="", err="", timed_out=False)
        if result["command"] in execd and result["timed_out"]
        else result
        for result in results
    ]


def effective(result):
    if result is None:
        return None
    if result["timed_out"]:
        return {"timed_out": True}
    return {"code": result["code"], "out": result["out"], "err": result["err"] if result["code"] != 0 else ""}
