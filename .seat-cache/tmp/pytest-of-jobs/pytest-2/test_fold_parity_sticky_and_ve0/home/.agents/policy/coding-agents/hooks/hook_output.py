import hashlib
import json

_RANK = {"allow": 1, "ask": 2, "deny": 3}


def text_of(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=True)


def bounded(text):
    raw = text.encode("utf-8")
    if len(raw) <= 1024:
        return text
    marker = "\n[hook-context:" + hashlib.sha256(raw).hexdigest()[:12] + "]"
    return raw[: 1024 - len(marker.encode())].decode("utf-8", "ignore") + marker


def cap_context(data, event):
    try:
        obj = json.loads(data)
    except (ValueError, UnicodeError):
        return bounded(data.decode("utf-8", "replace")).encode()
    if not isinstance(obj, dict):
        return data
    specific = obj.get("hookSpecificOutput")
    changed = False
    if isinstance(specific, dict) and isinstance(specific.get("additionalContext"), str):
        before = specific["additionalContext"]
        specific["additionalContext"] = bounded(before)
        changed = before != specific["additionalContext"]
    if isinstance(obj.get("systemMessage"), str):
        before = obj["systemMessage"]
        used = len(specific.get("additionalContext", "").encode()) if isinstance(specific, dict) else 0
        remaining = max(0, 1024 - used)
        obj["systemMessage"] = bounded(before).encode()[:remaining].decode("utf-8", "ignore")
        changed = changed or before != obj["systemMessage"]
    return (json.dumps(obj, ensure_ascii=True) + "\n").encode() if changed else data


def merge_objects(event, objects, plain):

    merged, specific = {}, {}
    contexts, reasons_by_rank, system = [], {}, []
    decision, decision_reasons, stop_reasons = None, [], []
    for obj in objects:
        if not isinstance(obj, dict):
            system.append(obj)
            continue
        for key, value in obj.items():
            if key == "hookSpecificOutput" and isinstance(value, dict):
                for skey, svalue in value.items():
                    if skey == "additionalContext":
                        if svalue not in (None, ""):
                            contexts.append(str(svalue))
                    elif skey == "permissionDecision":
                        rank = _RANK.get(svalue, 0) if isinstance(svalue, str) else 0
                        reasons_by_rank.setdefault(rank, [])
                        if value.get("permissionDecisionReason"):
                            reasons_by_rank[rank].append(text_of(value["permissionDecisionReason"]))
                        if rank > _RANK.get(str(specific.get("permissionDecision")), 0):
                            specific["permissionDecision"] = svalue
                    elif skey in ("permissionDecisionReason", "hookEventName"):
                        continue
                    elif skey == "updatedInput":
                        specific["updatedInput"] = svalue
                    elif skey not in specific:
                        specific[skey] = svalue
            elif key == "continue":
                merged["continue"] = merged.get("continue", True) and bool(value)
                if value is False and obj.get("stopReason"):
                    stop_reasons.append(str(obj["stopReason"]))
            elif key == "stopReason":
                continue
            elif key == "systemMessage":
                if value:
                    system.append(str(value))
            elif key == "decision":
                if value == "block":
                    decision = "block"
                    if obj.get("reason"):
                        decision_reasons.append(text_of(obj["reason"]))
                elif decision is None:
                    decision = value
            elif key == "reason":
                continue
            elif key == "suppressOutput":
                merged["suppressOutput"] = merged.get("suppressOutput", False) or bool(value)
            elif key not in merged:
                merged[key] = value
    if event == "UserPromptSubmit":
        contexts.extend(p.decode("utf-8", "replace").rstrip("\n") for p in plain)
    if "permissionDecision" in specific:
        reasons = reasons_by_rank.get(_RANK.get(specific["permissionDecision"], 0)) or []
        if reasons:
            specific["permissionDecisionReason"] = "\n".join(reasons)
    if contexts:
        specific["additionalContext"] = "\n".join(contexts)
    if specific:
        merged["hookSpecificOutput"] = dict([("hookEventName", event)] + list(specific.items()))
    if stop_reasons:
        merged["stopReason"] = "\n".join(stop_reasons)
    if system:
        merged["systemMessage"] = "\n".join(system)
    if decision is not None:
        merged["decision"] = decision
        if decision_reasons:
            merged["reason"] = "\n".join(decision_reasons)
    return merged
