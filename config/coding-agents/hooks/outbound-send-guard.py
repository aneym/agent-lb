import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

TRIGGER = re.compile(
    r"osascript|osacompile|applescript|jxa|messages|imessage|imsg|mail|sms|swaks|mutt|ssmtp|bluebubbles|sendblue|shortcuts|twilio|postmark|sendgrid|resend"
    r"|mailgun|smtp|telnyx|nexmo|vonage|plivo|sendblue|loopmessage|bluebubbles|base64|\\x|eval|\.scpt|xxd|graph\.microsoft"
    r"|brevo|sendinblue|mailjet|\.sh\b|\.py\b|\.js\b|\.command\b|\./|[A-Za-z0-9+/]{40,}",
    re.I,
)
NEXT = (
    "Ask Alex for this exact text through Rails: raise a hold for it (hold_raise, kind message, the exact text) so it lands "
    "on his Needs you card, or send it with the Rails gated tool (gmail_send and the like). After his Allow, hold_get "
    "returns a signed receipt: write it to a file (mode 600, never print it), hold_consume it, then cite the file on the "
    'call: AGENT_OUTBOUND_APPROVAL=<receipt file> <command>, --approval <receipt file>, or an "approval" field on an MCP '
    "call. A local approval file, a note or an agent grant is not his yes; a yes covers that exact message only."
)
LOG = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "state", "outbound-send-guard.jsonl")


def log(kind, decision, tool, payload_text, texts):
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool if not tool.startswith("mcp__") else "mcp",
        "kind": kind,
        "decision": decision,
        "call_sha256": hashlib.sha256(payload_text.encode("utf-8", "replace")).hexdigest()[:16],
        "text_sha256": sorted(
            {hashlib.sha256(t.encode("utf-8")).hexdigest()[:16] for t in texts if isinstance(t, str) and t}
        )[:8],
    }
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def deny(kind, why):
    reason = (
        "BLOCKED: direct send to a real person (%s): %s. No message to a real person goes out without Alex's yes for that exact message (kept floor).\nnext: %s"
        % (kind, why, NEXT)
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    print(reason, file=sys.stderr)
    return 2


def verdict(payload):
    from outbound_receipt import check, cited_path, mark_used  # type: ignore[missing-import]
    from outbound_sends import bash_scan, mcp_send  # type: ignore[missing-import]

    tool = str(payload.get("tool_name") or "")
    data = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or os.getcwd())
    if tool.startswith("mcp__"):
        kind, texts = mcp_send(tool, data)
        command = ""
    elif tool in ("Bash", "shell", "local_shell", "exec_command"):
        command = data.get("command") if isinstance(data, dict) else None
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        if not isinstance(command, str) or not TRIGGER.search(command):
            return 0
        scan = bash_scan(command, cwd)
        kind, texts = (scan.kinds[0] if scan.kinds else None), scan.texts
    else:
        return 0
    if not kind:
        return 0
    payload_text = json.dumps(data, sort_keys=True, default=str)
    why, hold_id = check(cited_path(command, data), texts, cwd)
    if why is None:
        mark_used(hold_id)
        log(kind, "allowed-receipt", tool, payload_text, texts)
        return 0
    log(kind, "refused", tool, payload_text, texts)
    return deny(kind, why)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        return verdict(payload)
    except Exception:
        raw = json.dumps(payload.get("tool_input") or {}, default=str)
        if re.search(r"messages|imessage|imsg|mail|sms|twilio|shortcuts", raw, re.I) and re.search(
            r"send|osascript|shortcuts", raw, re.I
        ):
            return deny(
                "unreadable",
                "the guard failed while reading a call that touches Messages or Mail sending, so it fails closed",
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
