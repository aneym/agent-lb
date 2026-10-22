import json
import sys

from hook_failure_recovery import answer_problem, deny_reason, parse_json  # type: ignore[missing-import]
from hook_output import cap_context, merge_objects  # type: ignore[missing-import]

RECEIPT = "floor-ok %s"


NOTICE_PREFIX = "Failed with non-blocking status code: "


class Result(object):
    __slots__ = ("code", "out", "err", "timed_out", "mode", "spawned", "floor_failure", "receipt")

    def __init__(self, code=0, out=b"", err=b"", timed_out=False, mode="", spawned=0):
        self.code, self.out, self.err, self.timed_out = code, out, err, timed_out
        self.mode, self.spawned = mode, spawned
        self.floor_failure = None
        self.receipt = False

    def empty(self):
        return not self.timed_out and self.code == 0 and not self.out


def apply_wrapper(plan, result):
    if result.timed_out:
        return result
    if plan.devnull:
        result.err = b""
    if result.code != 0:
        if plan.fallback_true:
            result.code = 0
        elif plan.fallback_text is not None:
            result.out += plan.fallback_text.encode("utf-8")
            result.code = 0
    return result


def take_receipt(result, name):
    want = (RECEIPT % name).encode("utf-8")
    body = result.out[:-1] if result.out.endswith(b"\n") else result.out
    cut = body.rfind(b"\n") + 1
    if body[cut:] == want:
        result.out, result.receipt = body[:cut], True
    return result


def floor_failure(result):
    if result.timed_out:
        return "timed out"
    if result.code == 2:
        return None
    if result.code == 127:
        return "is missing (exit 127)"
    if result.code != 0:
        return "crashed (exit %d)" % result.code
    if not result.receipt:
        return "exited 0 without its allow receipt"
    return answer_problem(result.out)


def finish(plan, raw):
    if plan.floor:
        take_receipt(raw, plan.floor)
        raw.floor_failure = floor_failure(raw)
        return raw
    if plan.kind == "inproc":
        return apply_wrapper(plan, raw)
    return raw


def block_message(plan, result, obj):
    reason = deny_reason(obj)
    if reason:
        return reason
    return "[%s]: %s" % (plan.command, result.err.decode("utf-8", "replace") or "No stderr output")


def encode(obj):
    return (json.dumps(obj, ensure_ascii=True) + "\n").encode("ascii")


def refusal(event, note, blocked=False):
    err = (note.rstrip("\n") + "\n").encode("utf-8", "backslashreplace")
    if event == "PreToolUse":
        obj = {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": note,
            }
        }
    elif blocked:
        obj = {"decision": "block", "reason": note}
    else:
        return Result(1, b"", err, mode="refused")
    return Result(2, encode(obj), err, mode="refused")


def notice_text(result):
    return NOTICE_PREFIX + (result.err.decode("utf-8", "replace").strip() or "No stderr output")


def refuse_failed_floor(plan, result):
    if result is None or not result.floor_failure:
        return result
    note = "hook-dispatch: floor guard %s %s, so this call is refused (a floor guard that fails denies)" % (
        plan.floor,
        result.floor_failure,
    )
    detail = result.err.decode("utf-8", "replace").strip()[-500:]
    return Result(
        2,
        b"",
        (note + ("\n" + detail if detail else "") + "\n").encode("utf-8", "backslashreplace"),
        False,
        "floor-refused",
        result.spawned,
    )


def merge(event, plans, results):
    try:
        answer = merge_answers(event, plans, results)
        answer.out = cap_context(answer.out, event)
        return answer
    except Exception as exc:
        messages = []
        for plan, result in zip(plans, results):
            if result is None or result.timed_out:
                continue
            try:
                obj = parse_json(result.out) if result.out else None
                if result.code == 2 or deny_reason(obj):
                    messages.append(block_message(plan, result, obj))
            except Exception:
                messages.append("[%s]: answer unreadable" % plan.command)
        blocked = bool(messages)
        if not messages:
            messages.append("hook-dispatch: could not merge the guards' answers (%s); refused" % exc.__class__.__name__)
        return refusal(event, "\n".join(messages), blocked)


def merge_answers(event, plans, results):
    if event == "PreToolUse":
        results = [refuse_failed_floor(p, r) for p, r in zip(plans, results)]
    pairs = [(p, r) for p, r in zip(plans, results) if r is not None and not r.empty()]
    pairs = [(p, r) for p, r in pairs if not r.timed_out]
    if not pairs:
        return Result(0, b"", b"")
    parsed = [(p, r, parse_json(r.out) if r.out else None) for p, r in pairs]
    blocks = [(p, r, obj) for p, r, obj in parsed if r.code == 2 or deny_reason(obj)]
    if len(pairs) == 1 and not (blocks and blocks[0][1].code == 2 and not deny_reason(blocks[0][2])):
        _p, only, _obj = parsed[0]
        return Result(only.code, only.out, only.err)

    items = [
        obj if obj is not None else notice_text(r) for _p, r, obj in parsed if obj is not None or r.code not in (0, 2)
    ]
    objects = [item for item in items if isinstance(item, dict)]
    notices = [item for item in items if not isinstance(item, dict)]
    plain = [r.out for _p, r, obj in parsed if obj is None and r.code == 0 and r.out]
    errors = [r for _p, r, obj in parsed if obj is None and r.code not in (0, 2)]
    if blocks:
        messages = [block_message(p, r, obj) for p, r, obj in blocks]
        merged = merge_objects(event, items, plain) if items or plain else {}
        merged.pop("decision", None)
        merged.pop("reason", None)
        if event == "PreToolUse":
            specific = dict(merged.get("hookSpecificOutput") or {}, hookEventName=event)
            specific.pop("updatedInput", None)
            specific["permissionDecision"] = "deny"
            specific["permissionDecisionReason"] = "\n".join(messages)
            merged["hookSpecificOutput"] = specific
        else:
            merged["decision"] = "block"
            merged["reason"] = "\n".join(messages)
        return Result(2, encode(merged), b"".join(r.err for _p, r, _obj in blocks))

    notice_err = (
        ("\n".join(notices)[len(NOTICE_PREFIX) :] + "\n").encode("utf-8", "backslashreplace") if notices else b""
    )
    if not objects and not plain:
        return Result(errors[0].code, b"", notice_err)
    if not objects and (not errors or event != "UserPromptSubmit"):
        return Result(errors[0].code if errors else 0, b"".join(plain), notice_err)

    return Result(0, encode(merge_objects(event, items, plain)), b"")


def updates_input(result):
    obj = parse_json(result.out) if result is not None and result.code == 0 and result.out else None
    specific = obj.get("hookSpecificOutput") if obj and isinstance(obj.get("hookSpecificOutput"), dict) else {}
    return "updatedInput" in specific


def subagent_bash(payload, event):
    return (
        event == "PreToolUse"
        and isinstance(payload, dict)
        and "agent_id" in payload
        and payload.get("tool_name") == "Bash"
    )


def suppress_subagent_owner(result, payload, event):
    if not subagent_bash(payload, event) or result.code != 0:
        return result
    obj = parse_json(result.out) if result.out else {}
    if obj is None or deny_reason(obj):
        return result
    specific = obj.setdefault("hookSpecificOutput", {})
    original = payload.get("tool_input")
    updated = specific.get("updatedInput", original)
    if not isinstance(updated, dict) or not isinstance(updated.get("command"), str):
        return result
    prefix = "export FACTORY_OWNER_PANE=none;"
    command = updated["command"]
    if not command.startswith(prefix):
        command = prefix + " " + command
    specific["hookEventName"] = event
    specific["updatedInput"] = dict(updated, command=command)
    return Result(result.code, encode(obj), result.err)


def answer(result):
    out, err = sys.stdout.buffer, sys.stderr.buffer
    if result.out:
        out.write(result.out)
        out.flush()
    if result.err:
        err.write(result.err)
        err.flush()
    return result.code
