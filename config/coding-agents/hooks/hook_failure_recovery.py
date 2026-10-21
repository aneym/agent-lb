import json

_DECISIONS = {"allow", "ask", "deny"}


def answer_problem(out):

    if not out.strip():
        return None
    obj = parse_json(out)
    if obj is None:
        return "printed output that is not a JSON hook answer"
    specific = obj.get("hookSpecificOutput")
    if specific is not None:
        if not isinstance(specific, dict):
            return "printed a malformed hook answer (hookSpecificOutput is not an object)"
        decision = specific.get("permissionDecision", "allow")
        if not isinstance(decision, str) or decision not in _DECISIONS:
            return "printed a malformed hook answer (permissionDecision %s)" % json.dumps(decision)
        reason = specific.get("permissionDecisionReason")
        if reason is not None and not isinstance(reason, str):
            return "printed a malformed hook answer (permissionDecisionReason is not text)"
    if "decision" in obj and obj["decision"] not in ("block", "approve"):
        return "printed a malformed hook answer (decision %s)" % json.dumps(obj["decision"])
    return None


def parse_json(out):
    text = out.decode("utf-8", "replace").strip()
    if not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def text_of(value):

    return value if isinstance(value, str) else json.dumps(value)


def deny_reason(obj):
    if not obj:
        return None
    specific = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    if specific.get("permissionDecision") == "deny":
        reason = specific.get("permissionDecisionReason") or obj.get("reason")
        return text_of(reason) if reason else "Blocked by hook"
    if obj.get("decision") == "block":
        return text_of(obj["reason"]) if obj.get("reason") else "Blocked by hook"
    return None
