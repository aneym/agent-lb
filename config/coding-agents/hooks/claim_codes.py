import json
import re
import sys

CODE = re.compile(r"rac_[A-Za-z0-9_-]{16,}")
MASK = "rac_***"
FILTER = "perl -pe 'BEGIN{$|=1} s/rac_[A-Za-z0-9_-]{16,}/rac_***/g'"


def holds(value):
    return CODE.search(json.dumps(value, ensure_ascii=False)) is not None


def scrub(value):
    if isinstance(value, str):
        return CODE.sub(MASK, value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def output_fields(payload):
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "PostToolUse":
        return None
    response = payload.get("tool_response")
    if response is None or not holds(response):
        return None
    clean = scrub(response)
    mcp = str(payload.get("tool_name") or "").startswith("mcp__")
    if "turn_id" in payload:
        return {"updatedMCPToolOutput": clean} if mcp else None
    fields = {"updatedToolOutput": clean}
    if mcp:
        fields["updatedMCPToolOutput"] = clean
    return fields


def post(payload):
    fields = output_fields(payload)
    if fields:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", **fields}}))


if __name__ == "__main__":
    try:
        post(json.load(sys.stdin))
    except ValueError:
        pass
