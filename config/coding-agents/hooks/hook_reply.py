import json


def warn(text, event="PreToolUse"):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    return 0


def rewrite(tool_input, changes, text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**tool_input, **changes}, "additionalContext": text}}))
    return 0
