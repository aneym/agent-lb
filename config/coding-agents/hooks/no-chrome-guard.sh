#!/bin/bash
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' no-chrome-guard.sh; fi
  exit 0
}
refuse() {
  echo "BLOCKED (floor: the owner's own browser and accounts): no-chrome-guard $1, so the call is refused (the guard fails closed)." >&2
  exit 2
}
JQ=/opt/homebrew/bin/jq
[ -x "$JQ" ] || JQ=jq
PAYLOAD=$(cat) || refuse "could not read the hook input"
CMD=$(printf '%s' "$PAYLOAD" | "$JQ" -n -r 'input | if type != "object" then error("object required") elif (.tool_input != null and (.tool_input | type) != "object") then error("tool_input object required") else .tool_input.command | if . == null then "" elif type == "string" and (contains("\u0000") | not) then . else error("command string required") end end' 2>/dev/null) || refuse "could not read the hook input (jq missing or failed)"
[ -z "$CMD" ] && allow
HERE=$(cd "$(dirname "$0")" && pwd -P) || refuse "could not resolve the classifier"
PC_KIND=$(/usr/bin/python3 -I "$HERE/no_chrome_classify.py" "$CMD") || refuse "could not parse the command"
case "$PC_KIND" in
  none|pc) allow ;;
  doubt) ;;
  browser|mac|macpc) ;;
  *) refuse "could not classify the command" ;;
esac
approved() {
  local ref
  ref=$(printf '%s\n' "$CMD" | sed -nE '$s/.*#[[:space:]]*(owner|alex)-approval:[[:space:]]*([^[:space:]]+)[[:space:]]*$/\2/p')
  [ -n "$ref" ] || return 1
  case "$ref" in \~/*) ref="$HOME/${ref#\~/}" ;; '$HOME/'*) ref="$HOME/${ref#\$HOME/}" ;; esac
  case "$ref" in *..*) return 1 ;; "$HOME"/.open-factory/approvals/owner-*-2026-*.md|"$HOME"/.open-factory/approvals/alex-*-2026-*.md) ;; *) return 1 ;; esac
  [ -f "$ref" ] && grep -qE '[0-9]{1,2}:[0-9]{2}' "$ref"
}
deny() {
  echo "BLOCKED (floor: the owner's own browser and accounts): agents never launch or drive the owner's browsers themselves (the owner, 2026-09-25/26; Chrome on his PC included, 2026-10-07).${1:+ $1} To look at a page, run: page-shot <url> [--widths 1440,390] [--themes light,dark]. It is windowless and prints JSON with the PNG paths. Rails test-account proofs: rails-browser signin --url <url> with the lane test-account packet. Business-account admin work: acct-browser start <account> / acct-browser-run <account> <script.py>. For one call on the owner's own browser: file an unblock permission ask (purpose permission, permission.tool Bash, permission.command = the exact command), and once the owner picks Allow once, run that command with ' # unblock-ask: <ticket>' appended. Each ticket allows one call. Or append ' # owner-approval: ~/.open-factory/approvals/owner-<topic>-2026-<date>.md' (a first-hand the owner quote with a time)." >&2
  exit 2
}
if [ "$PC_KIND" = doubt ]; then
  DOUBT_HOME=${FACTORY_HOME:-$HOME/.agent-rails/factory}/state
  SESSION=$(printf '%s' "$PAYLOAD" | "$JQ" -r '.session_id // "none" | tostring | gsub("[^A-Za-z0-9_-]"; "_")' 2>/dev/null) || SESSION=none
  mkdir -p "$DOUBT_HOME/no-chrome-guard-doubt" 2>/dev/null
  "$JQ" -cn --arg cmd "$CMD" --arg session "$SESSION" --arg pane "${HERDR_PANE_ID:-}" '{at: (now | todate), decision: "pass-unparsed", session: $session, pane: $pane, command: $cmd}' >>"$DOUBT_HOME/no-chrome-guard-doubt.jsonl" 2>/dev/null
  if [ ! -e "$DOUBT_HOME/no-chrome-guard-doubt/$SESSION" ]; then
    : >"$DOUBT_HOME/no-chrome-guard-doubt/$SESSION" 2>/dev/null
    "$JQ" -cn '{hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: "no-chrome-guard could not fully parse this command and found no browser action in it, so it ran. Warned once per session; unparsed calls are logged to factory state/no-chrome-guard-doubt.jsonl."}}'
  fi
  allow
fi
approved && allow
ASK=$(printf '%s' "$CMD" | sed -nE '$s/^(.*[[:space:]])?#[[:space:]]*unblock-ask:[[:space:]]*(ub_[A-Za-z0-9]+)[[:space:]]*$/\2/p')
[ -z "$ASK" ] && deny
PC_HIT=0
case "$PC_KIND" in browser|pc|macpc) PC_HIT=1 ;; esac
REASON=$(/usr/bin/python3 - "$ASK" "$CMD" "$PC_HIT" "$PAYLOAD" <<'PY'
import fcntl, json, os, re, subprocess, sys, time
ask_id, command, pc_hit, payload = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
home = os.environ.get("FACTORY_HOME") or os.path.join(os.environ["HOME"], ".agent-rails/factory")
log_path = os.path.join(home, "state/no-chrome-guard.jsonl")
action = re.sub(r"(^|(?<=\s))#[ \t]*unblock-ask:[ \t]*" + re.escape(ask_id) + r"[ \t]*$", "", command).strip()
try:
    session = json.loads(payload).get("session_id")
except ValueError:
    session = None


def verdict():
    try:
        out = subprocess.run([os.path.join(os.environ["HOME"], ".local/bin/unblock"), "show", ask_id, "--json"],
                             capture_output=True, text=True, timeout=15)
        ask = json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        ask = None
    if not isinstance(ask, dict):
        return "ask %s is unreadable" % ask_id
    permission = ask.get("permission") if isinstance(ask.get("permission"), dict) else {}
    via = ask.get("answered_via")
    if ask.get("ticket") != ask_id or ask.get("purpose") != "permission" or permission.get("tool") != "Bash":
        return "ask %s is not a Bash permission ask" % ask_id
    if ask.get("status") not in ("answered", "collected", "orphaned") or (ask.get("answers") or {}).get("verdict") != "allow_once":
        return "ask %s is not approved (Allow once)" % ask_id
    if not isinstance(via, str) or not via or via in ("local", "share-link:local"):
        return "ask %s was not answered by the owner on the page" % ask_id
    if str(permission.get("command") or "").strip() != action:
        return "this call differs from the command ask %s approved" % ask_id
    if pc_hit:
        try:
            with open(os.path.join(home, "state/pc-game-signal.json")) as handle:
                mode = json.load(handle).get("mode")
        except (OSError, ValueError, AttributeError):
            mode = None
        if mode != "normal":
            return "the PC is in game mode (or its game signal is unreadable); nothing touches it now"
    return None


reason = verdict()
os.makedirs(os.path.dirname(log_path), exist_ok=True)
with open(log_path, "a+") as log:
    fcntl.flock(log, fcntl.LOCK_EX)
    if reason is None:
        log.seek(0)
        for line in log:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("ask") == ask_id and row.get("decision") == "allow":
                reason = "ask %s was already used at %s" % (ask_id, row.get("at"))
                break
    log.seek(0, os.SEEK_END)
    log.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ask": ask_id,
                          "decision": "deny" if reason else "allow", "reason": reason, "action": action,
                          "pane": os.environ.get("HERDR_PANE_ID"), "session": session}) + "\n")
print(reason or "")
sys.exit(1 if reason else 0)
PY
) && allow
deny "${REASON:-the one-shot check failed}."
