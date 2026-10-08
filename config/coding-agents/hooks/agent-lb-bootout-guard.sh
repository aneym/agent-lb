#!/bin/bash
# PreToolUse guard: agent-lb restarts go through lb-restart (blue/green, 2026-09-25).
# A raw kickstart/bootout of the service or the front holds every new connection
# while the old process drains (10-95 s measured 2026-09-25) and can cut streams.
# Escape hatch when lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok
# Adopted into agent-lb 2026-10-08 (hook-dispatcher-4): edit this source only. It is a floor guard, so input it
# cannot read (jq missing or failing) is refused, never allowed as an empty command. jq reads the input itself (a
# missing `cat` before it read as an empty command), and a matcher that fails (grep missing or erroring, exit > 1) is
# told apart from no match (exit 1) and refuses (2026-10-08 review: a missing grep let a raw kickstart through).
# S44 (2026-10-08): reads are checked (set -euo pipefail) and jq must read exactly one JSON input (`input` fails on an
# empty one); an allow ends with the receipt line `floor-ok agent-lb-bootout-guard.sh` when the dispatcher asks for it
# (HOOK_FLOOR_RECEIPT), and the dispatcher refuses an exit 0 without it.
# Guard trim (Alex, 2026-10-08 09:18 ET): a kept floor (keeps the orchestrators up). Its message names the floor and
# it accepts the Rails CoS approval record: a trailing `# alex-approval: <path>` naming an existing
# ~/.agent-rails/lanes/orchestrator-refs/alex-*-2026-*.md that carries a time.
set -euo pipefail
FLOOR="floor: keeps the orchestrators up (agent-lb carries every agent session)"
APPROVAL="Approved restarts pass with a trailing '# alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' (a first-hand Alex quote with a time)."
approved() {
  python3 - "$CMD" <<'PY'
import os, re, sys
cmd = sys.argv[1]
quote = None
comment = None
escaped = False
for i, c in enumerate(cmd):
    if escaped:
        escaped = False
        continue
    if c == "\\" and quote != "'":
        escaped = True
    elif quote:
        if c == quote:
            quote = None
    elif c in "\"'":
        quote = c
    elif c == "#" and i and cmd[i-1].isspace():
        end = cmd.find("\n", i)
        end = len(cmd) if end < 0 else end
        if cmd[i:end].startswith("# alex-approval:") and not cmd[end:].strip():
            before = cmd[:i].rsplit("\n", 1)[-1]
            if before.strip():
                comment = cmd[i:end]
        break
m = re.fullmatch(r"#\s*alex-approval:\s*(\S+)\s*", comment or "")
if not m:
    sys.exit(1)
home = os.path.expanduser("~")
path = re.sub(r"^(?:~|\$HOME|\$\{HOME\})/", home + "/", m[1])
refs = home + "/.agent-rails/lanes/orchestrator-refs/"
name = path[len(refs):]
if (not path.startswith(refs) or ".." in path or "/" in name
        or not re.fullmatch(r"alex-.+-2026-.+\.md", name) or os.path.islink(path)):
    sys.exit(1)
try:
    with open(path) as f:
        sys.exit(0 if re.search(r"\b\d{1,2}:\d{2}\b", f.read()) else 1)
except OSError:
    sys.exit(1)
PY
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' agent-lb-bootout-guard.sh; fi
  exit 0
}
JQ=/opt/homebrew/bin/jq
[ -x "$JQ" ] || JQ=jq
if ! CMD=$("$JQ" -n -r 'input | .tool_input.command // empty' 2>/dev/null); then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not read the hook input (jq missing or failed), so the call is refused." >&2
  exit 2
fi
[ -n "$CMD" ] || allow
case "$CMD" in *launchctl*) ;; *) allow ;; esac  # the pattern needs `launchctl`; the dispatcher's prefilter is this
MATCH=0
grep -qE 'launchctl[^|;&]*(bootout|kickstart|stop|kill)[^|;&]*com\.aneyman\.agent-lb(-front)?([^a-zA-Z0-9_-]|$)' <<<"$CMD" || MATCH=$?
if [ "$MATCH" -gt 1 ]; then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not match the command (grep exit $MATCH: missing or failed), so the call is refused." >&2
  exit 2
fi
if [ "$MATCH" -eq 0 ]; then
  if [ ! -f "$HOME/.agent-lb/raw-restart.ok" ] && ! approved; then
    {
      echo "BLOCKED ($FLOOR): raw launchctl restart of agent-lb. Use the blue/green restart instead:"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" [--from <worktree> --files <paths>]"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" --check   # boot+health-check only"
      echo "Add --reload-plist after editing the launchd plist."
      echo "Only if lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok, then remove it."
      echo "$APPROVAL"
    } >&2
    exit 2
  fi
fi
allow
