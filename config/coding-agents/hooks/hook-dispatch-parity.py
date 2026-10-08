#!/usr/bin/env python3
"""Parity fixture for hook-dispatch.py: the per-hook config and the dispatcher give Claude Code the same answers.

For every case (event, tool, payload) it runs, in a fresh sandbox HOME each time:
- old: every per-hook command from the registry's `per_hook` block whose group matches the tool by Claude Code's own
  matcher rules (names, aliases, regexes), each through /bin/sh -c with its own timeout, as Claude Code runs them;
- new: the dispatcher entries from the registry's `dispatch` block, with HOOK_DISPATCH_TRACE on.
It compares (1) each guard's own result (exit code, stdout, stderr when the exit code is not 0, timeout) between the
two paths, (2) the effect Claude Code builds from the results (decision, block message, updatedInput, context,
system message and error notices, continue), emulated here independently of the dispatcher's merge code, with the
floor rule applied to the per-hook results (a floor guard that fails refuses the call), and (3) the files each path
leaves in the sandbox. Results are compared as observed: a dispatcher entry that hits its timeout here is cancelled
as Claude Code cancels a hook. A case whose only difference may be a guard's timeout on a loaded machine is rerun
(at most twice) before it counts. One expected difference is modelled, as the floor rule is: a rewriter the
dispatcher execs into carries its own timeout as a timer (build lead decision (a), 2026-10-08), so where per-hook it
hit that timeout and was cancelled with nothing shown, the dispatcher's is killed by SIGALRM at it: no rewrite, and
a failed-hook notice.

Floor replay: each live floor guard is made to time out, crash, go missing and print a malformed answer (a stub in
the sandbox, never the real file); the dispatcher must refuse every one, and a non-floor control must fail open.

Processes per Bash call are observed as whole trees (see observe): a kqueue watch on each hook's shell from before it
runs; at every fork the tree is stopped, every child of every known process (unreaped ones too) is listed and
watched, and only then continued. The budget: hook-layer processes (dispatcher entries and the guard processes they
start) at most 2 per Bash call, and the dispatcher path's whole tree no larger than the per-hook path's for the same
command and cwd. A dispatcher-path count the watch did not observe completely fails; a self-test first proves exact
counts from a plain exec (1) to grandchildren (4).

The sandbox is test-owned: HOME, TMPDIR, lane, factory, ledger and state paths point inside it, HERDR_* and
FACTORY_* are dropped, AGENT_LB_URL points at a closed port, AUTOCLOSE_DRY_RUN and IDLE_REAPER_DRY_RUN are set.
Guard scripts are linked from the real HOME read-only; config files they read are copied. AskUserQuestion is never
run (its hook files a real ask). It holds install-policy's per-home lock (shared) while it runs, unless the caller
holds it (--lock-held). Exit 0 when every parity case matches and the floor replay holds (1: parity, 5: floor);
--require-process-count also requires the process budget (3).

Usage: hook-dispatch-parity.py [--home H] [--registry R] [--dispatcher D] [--out FILE] [--require-process-count]
       [--no-process-count] [--lock-held] [--require-expectations]
"""
import argparse
import copy
import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DEFAULT_TIMEOUT = 600.0
PATH_REF = re.compile(r'(?:\$HOME|~)/([^\s"\'$`\\;&|<>(){}]+)')
CONFIG_COPIES = (".agent-lb/managed/coding-agents/routing-table.json", ".claude/agents", ".claude/load-governor.json")
TEST_PANE = "hp:t1"
RTK_STATE = "home/Library/Application Support/rtk/"
FAKE_NPX = r"""#!/bin/sh
echo "$*" >> "$NPX_CALL_LOG"
if [ "$2" = "--find-config-path" ]; then
  d=$(pwd)
  while :; do
    for f in "$d"/.prettierrc* "$d"/prettier.config*; do [ -e "$f" ] && exit 0; done
    [ "$d" = / ] && exit 2
    d=$(dirname "$d")
  done
fi
exit 0
"""
HEAVY_DIR = os.path.join(os.path.expanduser("~"), "factory")  # under box-offload's roots, so it rewrites
NEVER_RUN = {"AskUserQuestion"}  # its hook registers a real ask with unblock


# ---------------------------------------------------------------------------------------------------- matching


# Claude Code 2.1.293's hook matcher (W3 in its bundle, read 2026-10-07): an empty matcher or `*` matches every
# tool; a matcher of only letters, digits, `_`, `|`, `,`, ` ` and `-` is a list of exact names split on `|` or `,`
# (each trimmed, each read through the tool alias table); anything else is a JavaScript regex searched (unanchored)
# in the tool name and in the names that alias to it, and an invalid regex matches nothing. Events without a tool
# (UserPromptSubmit, Stop) ignore the matcher. Tool families (MCP server families) are not modelled.
TOOL_ALIASES = {"Task": "Agent", "KillShell": "TaskStop", "KillBash": "TaskStop", "ListPeers": "ListAgents",
                "Brief": "SendUserMessage", "ListMcpResources": "ListMcpResourcesTool",
                "ReadMcpResource": "ReadMcpResourceTool", "ReadMcpResourceDir": "ReadMcpResourceDirTool"}
NAME_LIST = re.compile(r"^[a-zA-Z0-9_|, -]+$")
TOOL_EVENTS = ("PreToolUse", "PostToolUse")


def matches(matcher, tool, event="PreToolUse"):
    if event not in TOOL_EVENTS or tool is None or not matcher or matcher == "*":
        return True
    if not isinstance(matcher, str):
        return False
    if NAME_LIST.match(matcher):
        names = [TOOL_ALIASES.get(name.strip(), name.strip()) for name in re.split(r"[|,]", matcher) if name.strip()]
        return tool in names
    try:
        pattern = re.compile(matcher)  # JavaScript and Python agree on the regexes hook matchers use
    except re.error:
        return False
    return any(pattern.search(name) for name in [tool] + [a for a, t in TOOL_ALIASES.items() if t == tool])


def group_key(group):
    matcher = group.get("matcher")
    return "*" if matcher in (None, "", "*", ".*") else matcher


def old_hooks(per_hook, event, tool):
    seen, hooks = set(), []
    for group in per_hook.get(event, []):
        if not matches(group.get("matcher"), tool, event):
            continue
        for hook in group.get("hooks", []):
            if hook.get("command") in seen:
                continue  # Claude Code runs an identical command once
            seen.add(hook.get("command"))
            hooks.append(hook)
    return hooks


def new_hooks(dispatch, event, tool):
    seen, hooks = set(), []
    for group in dispatch.get(event, []):
        if not matches(group.get("matcher"), tool, event):
            continue
        for hook in group.get("hooks", []):
            if hook.get("command") not in seen:  # Claude Code runs an identical command once
                seen.add(hook.get("command"))
                hooks.append(hook)
    return hooks


# ---------------------------------------------------------------------------------------------------- running


def run_shell(command, payload, env, cwd, timeout):
    started = time.monotonic()
    proc = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = proc.communicate(payload, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = proc.communicate()
        timed_out = True
    code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    return {"command": command, "code": code, "out": out.decode("utf-8", "replace"),
            "err": err.decode("utf-8", "replace"), "timed_out": timed_out, "pid": proc.pid,
            "seconds": round(time.monotonic() - started, 3)}


def hook_timeout(hook):
    value = hook.get("timeout")
    return float(value) if isinstance(value, (int, float)) and value > 0 else DEFAULT_TIMEOUT


# -------------------------------------------------------------------------------- Claude Code's view


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
    """What Claude Code does with these hook results, per its per-hook rules (2.1.293), in config order.

    Probed on 2.1.293 (2026-10-07): it reads stdout as JSON whatever the exit code and then ignores stderr (a JSON
    answer with exit 1 is a success); `decision: block` or a PreToolUse deny blocks with the reason; an exit 2 with no
    such reason blocks with `[<command>]: <stderr>`; any other exit without JSON (a signal too: Node reports no code,
    which Claude Code reads as 1) is a non-blocking error notice to the user, `Failed with non-blocking status code:
    <stderr, trimmed>` (or `No stderr output`), and its stdout reaches only the transcript. A hook that hit its
    timeout is cancelled: nothing is shown for it (2.1.293 renders hook_cancelled only for UserPromptSubmit).
    `shown` is what the user is shown besides a block, in config order: system messages and those notices."""
    view = {"decision": None, "block": [], "updatedInput": None, "context": [], "shown": [], "continue": True,
            "stopReason": [], "plain": []}
    failed_stdout = False
    rank = {"allow": 1, "ask": 2, "deny": 3}
    for result in results:
        if result["timed_out"]:
            continue  # cancelled: no effect
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
            view["updatedInput"] = None  # a denied call never runs, so a rewrite of it has no effect
    if event == "UserPromptSubmit":
        view["context"] += [text.rstrip("\n") for text in view["plain"]]
    plain = view.pop("plain")
    # Several messages or contexts reach the model either as separate items (old) or joined (dispatcher).
    view["block_raw"] = "\n".join(view["block"])
    view["block_parts"] = [FLOOR_MESSAGE.sub(r"\1hook-dispatch: floor guard refusal", message)
                           for message in view["block"]]
    view["block"] = "\n".join(view["block_parts"])
    view["context"] = "\n".join(view["context"])
    view["shown"] = "\n".join(view["shown"])
    view["stopReason"] = "\n".join(view["stopReason"])
    view["transcript_only_stdout"] = (bool(plain) and event != "UserPromptSubmit") or failed_stdout
    return view


# ---------------------------------------------------------------------------------------------------- floor model
# The rule the dispatcher must hold, modelled here on its own (p13D and the simplify lead, 2026-10-07): on PreToolUse
# a floor guard that times out, crashes, is missing or prints something that is not a hook answer refuses the call,
# whatever its per-hook wrapper made of the failure; its exit 2 blocks under any wrapper. Every other guard fails
# open as before. The expected effect of the dispatcher path is the per-hook effect with this rule applied.
FLOOR_NAMES = ("workflow-seat-guard.py", "workflow-relay-guard.py", "seat-guard.py", "rm-dynamic-deny", "stash-guard",
               "railway-vars-guard.sh", "link-cli-guard.sh", "plutil-guard.sh", "kill-guard", "wide-scan-guard.sh",
               "agent-lb-bootout-guard.sh")
FLOOR_INLINE = "BLOCKED: Dangerous command"
FLOOR_MESSAGE = re.compile(r"^(\[[^\n]*\]: )hook-dispatch: floor guard [^\n]*(?:\n.*)?$", re.S)
NOTICE_PREFIX = "Failed with non-blocking status code: "
# Whatever trails the wrapper (`;`, whitespace, `&`) is read too, as hook-dispatch.py WRAPPER_TAIL does (2026-10-08).
WRAPPER = re.compile(r"\s+(?:2>\s*/dev/null)?\s*(?:\|\|\s*(?:true|\{\s*printf\s+%s\s+'[^']*'\s*;\s*\}))?[\s;&]*$")


def floor_of(command):
    for name in FLOOR_NAMES:
        if re.search(r"(?:^|[/\s\"'])%s(?=$|[\s\"';|&)])" % re.escape(name), command):
            return name
    return "dangerous-command" if FLOOR_INLINE in command else None


def bare_command(command):
    """A guard command without its `2>/dev/null`, `|| true` or `|| { printf ...; }` wrapper (the guard's own result)."""
    match = WRAPPER.search(command)
    return command[:match.start()] if match and match.group(0).strip() else command


def floor_failed(raw):
    """Whether a floor guard's own (unwrapped) result is a failure rather than an answer."""
    if raw["timed_out"]:
        return True
    if raw["code"] == 2:
        return False
    if raw["code"] != 0:
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
    """The per-hook results as the floor rule says the dispatcher must answer: a failed floor guard blocks. A guard
    the fold left per-hook (in `unfolded`) is outside the dispatcher, so Claude Code reads it as before."""
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
    """The per-hook results as the dispatcher must answer when it exec'd into a rewriter (a command in `execd`): one
    that hit its own timeout per-hook is killed by the timer armed before the exec, which Claude Code reads as a
    signal exit with no stderr (a failed-hook notice), never as a cancel and never as its late rewrite."""
    return [dict(result, code=128 + signal.SIGALRM, out="", err="", timed_out=False)
            if result["command"] in execd and result["timed_out"] else result for result in results]


def effective(result):
    """One guard's result as Claude Code reads it: stderr only matters when the exit code is not 0."""
    if result is None:
        return None
    if result["timed_out"]:
        return {"timed_out": True}
    return {"code": result["code"], "out": result["out"], "err": result["err"] if result["code"] != 0 else ""}


# ---------------------------------------------------------------------------------------------------- sandbox


class Sandbox(object):
    def __init__(self, root, real_home, registry, dispatcher, commands, extra_files=None, remove=()):
        self.root = root
        self.template = os.path.join(root, "template")
        self.real_home = real_home
        home = os.path.join(self.template, "home")
        os.makedirs(home)
        for command in commands:
            for rel in PATH_REF.findall(command):
                rel = rel.rstrip("/")
                source = os.path.join(real_home, rel)
                target = os.path.join(home, rel)
                if os.path.isfile(source) and not os.path.lexists(target):
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    os.symlink(source, target)
        for rel in CONFIG_COPIES:
            source = os.path.join(real_home, rel)
            target = os.path.join(home, rel)
            if os.path.isdir(source):
                shutil.copytree(source, target, symlinks=False, ignore=shutil.ignore_patterns("*.bak*"))
            elif os.path.isfile(source):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
        hooks_dir = os.path.join(home, ".claude", "hooks")
        os.makedirs(os.path.join(hooks_dir, "dispatch"), exist_ok=True)
        dispatcher_link = os.path.join(hooks_dir, "hook-dispatch.py")
        if os.path.lexists(dispatcher_link):
            os.unlink(dispatcher_link)
        os.symlink(os.path.abspath(dispatcher), dispatcher_link)
        # As installed: registry.json and the rev file every settings entry reads first (2026-10-08 review M3).
        names = ["registry.json"] + (["registry.%s.json" % registry["rev"]] if registry.get("rev") else [])
        for name in names:
            with open(os.path.join(hooks_dir, "dispatch", name), "w") as handle:
                json.dump(registry, handle, indent=2)
        for rel in remove:  # a guard the run must find missing: its link goes (never the real file behind it)
            path = os.path.join(self.template, rel)
            if os.path.islink(path) or os.path.isfile(path):
                os.unlink(path)
        for rel, (content, mode) in (extra_files or {}).items():
            path = os.path.join(self.template, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.lexists(path):
                os.unlink(path)  # a link to a real guard is replaced, never written through
            with open(path, "w") as handle:
                handle.write(content)
            os.chmod(path, mode)
        for rel in ("tmp", "work", "project", "lanes", "factory", "idle", "orch", "ledger", "skill", "bin"):
            os.makedirs(os.path.join(self.template, rel), exist_ok=True)
        with open(os.path.join(self.template, "factory", "machines.json"), "w") as handle:
            handle.write("[]\n")
        # Never the network: every run gets a fake npx that logs its argv and answers --find-config-path the way
        # prettier does (found only when a prettier config sits in the file's directory or a parent).
        with open(os.path.join(self.template, "bin", "npx"), "w") as handle:
            handle.write(FAKE_NPX)
        os.chmod(os.path.join(self.template, "bin", "npx"), 0o755)
        self.count = 0
        self.lock = threading.Lock()

    def fresh(self):
        with self.lock:
            self.count += 1
            path = os.path.join(self.root, "run-%04d" % self.count)
        shutil.copytree(self.template, path, symlinks=True)
        return path

    def env(self, run, extra=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("HERDR_", "FACTORY_", "HOOK_DISPATCH_", "CLAUDE_ENV_FILE", "TMUX"))}
        env.update({
            "HOME": os.path.join(run, "home"),
            "TMPDIR": os.path.join(run, "tmp") + "/",
            "PATH": os.path.join(run, "bin") + os.pathsep + os.environ.get("PATH", os.defpath),
            "AGENT_LB_URL": "http://127.0.0.1:9",
            "AUTOCLOSE_DRY_RUN": "1",
            "IDLE_REAPER_DRY_RUN": "1",
            "IDLE_AGENT_STATE_DIR": os.path.join(run, "idle"),
            "IDLE_AGENT_ORCH_DIR": os.path.join(run, "orch"),
            "LANE_BULLETIN_HOME": os.path.join(run, "lanes"),
            "FACTORY_HOME": os.path.join(run, "factory"),
            "FACTORY_MACHINES": os.path.join(run, "factory", "machines.json"),
            "FACTORY_HOST": "hookparity",
            "ROUTE_LEDGER": os.path.join(run, "ledger", "dispatch.jsonl"),
            "DISPATCH_LEDGER": os.path.join(run, "ledger", "dispatch.jsonl"),
            "SKILLSTATS_HOME": os.path.join(run, "skill"),
            "NPX_CALL_LOG": os.path.join(run, "npx-calls.log"),
            "CLAUDE_PROJECT_DIR": os.path.join(run, "project"),
            "LOAD_GOVERNOR_FAKE_LOAD": "1.0",
            "LOAD_GOVERNOR_FAKE_MEM": "10",
            "LOAD_GOVERNOR_FAKE_IDLE": "90",
            "ALEX_SAID_UNBLOCK": "",
            "LANG": os.environ.get("LANG") or "en_US.UTF-8",
        })
        env.update(extra or {})
        return env


def files_of(run):
    found = set()
    skip = {os.path.join(run, "trace.jsonl"), os.path.join(run, "npx-calls.log")}
    for dirpath, dirnames, filenames in os.walk(run):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        rel_dir = os.path.relpath(dirpath, run)
        if rel_dir.split(os.sep)[0] == "tmp" or rel_dir == os.path.join("home", ".claude", "hooks", "dispatch"):
            continue  # the dispatcher's own state (registry, failures.jsonl) is not a guard effect
        for name in filenames:
            path = os.path.join(dirpath, name)
            if path in skip or name.startswith("hook-dispatch-") or name.endswith(".pyc"):
                continue  # interpreter byte-code caches (~/Library/Caches/com.apple.python) are not guard effects
            found.add(os.path.relpath(path, run))
    return found


# ---------------------------------------------------------------------------------------------------- cases


def bash(command, cwd_rel="work", **extra):
    return dict(event="PreToolUse", tool="Bash", input={"command": command}, cwd_rel=cwd_rel, **extra)


def builtin_cases():
    deny = "deny"
    cases = [
        # allow
        dict(bash("ls -la"), name="allow-ls", expect="allow"),
        dict(bash("git status"), name="allow-git-status (rtk rewrite)", expect="allow"),
        dict(bash("echo hello"), name="allow-echo", expect="allow"),
        dict(bash("python3 -c 'print(1)'"), name="allow-python", expect="allow"),
        dict(bash("rg -n foo src"), name="allow-rg-scoped", expect="allow"),
        dict(bash("find . -name '*.py'"), name="allow-find-scoped", expect="allow"),
        dict(bash("git commit -m 'format permission docs'"), name="allow-rm-substring", expect="allow"),
        dict(bash("pkill -f kill-guard-test-marker"), name="allow-pkill (kill-guard is a PATH shim)", expect="allow"),
        dict(bash("DISPLAY_WAKE_SKIP=1 screencapture -x /tmp/x.png"), name="allow-display-skip", expect="allow"),
        dict(bash("screencapture -x out.png"), name="allow-display-wake-fires", expect="allow"),
        dict(bash("cd %s && python3 -m pytest -q checks" % HEAVY_DIR), name="allow-box-offload",
             expect="allow"),
        dict(bash("cd sub && rm a1 a2"), name="allow-rm-cd-rewrite", expect="allow", setup="rmcd"),
        dict(bash("open -a Preview out.png"), name="allow-open-other-app", expect="allow"),
        dict(bash("railway variables --json | jq 'keys'"), name="allow-railway-names", expect="allow"),
        dict(bash("link-cli auth status --filter-output authenticated"), name="allow-link-cli-filtered",
             expect="allow"),
        # deny, one guard each
        dict(bash("rm -rf /"), name="deny-dangerous-cmd", expect=deny),
        dict(bash("rm -rf $d"), name="deny-rm-dynamic-deny", expect=deny),
        dict(bash("launchctl kickstart -k gui/501/com.aneyman.agent-lb"), name="deny-agent-lb-bootout",
             expect=deny),
        # Since the 2026-10-08 guard edit a plain grep -r there is rewritten with -D skip; one with shell syntax
        # (here a pipe) is still refused.
        dict(bash("grep -r needle ~/.agent-rails/x | head -1"), name="deny-wide-scan-fifo", expect=deny),
        dict(bash("grep -r needle ~/.agent-rails/x"), name="rewrite-wide-scan-fifo", expect=None),
        dict(bash("rg needle /"), name="deny-wide-scan-root", expect=deny),
        dict(bash("rg -l b-123 ~/.agent-rails"), name="deny-wide-scan-lanes", expect=deny),
        dict(bash("link-cli auth status"), name="deny-link-cli-secret", expect=deny),
        dict(bash("railway variables --kv"), name="deny-railway-kv", expect=deny),
        dict(bash("railway run printenv"), name="deny-railway-printenv", expect=deny),
        dict(bash('open -a "Google Chrome" https://example.com'), name="deny-no-chrome", expect=deny),
        dict(bash("ssh pc 'start " + "chrome chrome://extensions'"), name="deny-no-chrome-pc", expect=deny),
        dict(bash("ssh pc chr'" + "ome'"), name="deny-no-chrome-pc-quote-split", expect=deny),
        dict(bash("ssh pc 'dir'"), name="allow-ssh-pc", expect="allow"),
        dict(bash("gh pr merge 5 --squash"), name="deny-no-direct-merge", expect=deny),
        dict(bash("open -a 'Herdr Shell'"), name="deny-herdr-shell-host", expect=deny),
        dict(bash("cua do click 10 10"), name="deny-desktop-guard", expect=deny),
        dict(bash("rm -rf / && gh pr merge 1 --squash"), name="deny-two-guards", expect=deny),
        dict(bash("git stash push -m parity", cwd_rel="work/repo"), name="deny-stash-guard", expect=deny,
             setup="stash"),
        dict(bash("git stash list", cwd_rel="work/repo"), name="allow-stash-list", expect="allow", setup="stash"),
        # malformed input: guards that fail closed refuse, the rest fail open
        dict(event="PreToolUse", tool="Bash", raw='{"tool_name":"Bash","tool_input":{"command":"rm -rf $d"',
             name="malformed-rm (rm-dynamic-deny fails closed)", expect=deny),
        dict(event="PreToolUse", tool="Bash", raw='{"tool_name":"Bash","tool_input":{"command":"cua do click"',
             name="malformed-cua (desktop-guard fails closed)", expect=deny),
        # Since hook-dispatcher-4 (2026-10-08) every floor guard refuses input it cannot read: the wide-scan and
        # bootout guards (jq fails on it) and the railway guard (since agent-lb 7a71805e). It failed open before.
        dict(event="PreToolUse", tool="Bash", raw="not json at all", name="malformed-plain (floor guards refuse)",
             expect=deny),
        dict(event="PreToolUse", tool="Bash", input={"command": 123}, name="non-string-command", expect=None),
        # Agent: the seat guard
        dict(event="PreToolUse", tool="Agent",
             input={"subagent_type": "general-purpose", "model": "gpt-5.4-mini", "prompt": "x", "description": "x"},
             name="deny-seat-guard-blocked-model", expect=deny),
        dict(event="PreToolUse", tool="Agent",
             input={"subagent_type": "Explore", "prompt": "find files", "description": "explore"},
             name="allow-seat-guard", expect=None),
        # Workflow: the relay guard and the workflow seat guard
        dict(event="PreToolUse", tool="Workflow", input={"script": "agent('do the thing')"},
             name="deny-relay-guard", expect=deny),
        dict(event="PreToolUse", tool="Workflow", input={"script": "// RELAYED-REQUEST-GUARD\nagent('x')"},
             name="allow-relay-guard", expect=None),
        # aside
        dict(event="PreToolUse", tool="mcp__aside__repl", input={"code": "await openTab('https://x')"},
             name="deny-aside-open-tab", expect=deny),
        dict(event="PreToolUse", tool="mcp__aside__repl", input={"code": "await listBrowserTabs()"},
             name="allow-aside", expect="allow"),
        # Read
        dict(event="PreToolUse", tool="Read", input={"file_path": "/etc/hosts"}, name="allow-read", expect=None),
        # PostToolUse
        dict(event="PostToolUse", tool="Bash", input={"command": "ls"}, response={"stdout": "x"},
             name="post-bash-bulletin", expect=None, setup="bulletin", pane=True),
        dict(event="PostToolUse", tool="Bash", input={"command": "ls"}, response={"stdout": "x"},
             name="post-bash-quiet", expect=None),
        dict(event="PostToolUse", tool="Edit", input={"file_path": "{run}/work/a.ts"}, name="post-edit-no-prettier",
             expect=None, setup="edit"),
        dict(event="PostToolUse", tool="Edit", input={"file_path": "{run}/work/pretty/a.ts"},
             name="post-edit-prettier-config", expect=None, setup="prettier"),
        dict(event="PostToolUse", tool="Agent", input={"subagent_type": "Explore", "prompt": "x"},
             response={"status": "completed"}, name="post-agent-idle", expect=None),
        dict(event="PostToolUse", tool="Skill", input={"skill": "jev"}, name="post-skill-stats", expect=None),
        dict(event="PostToolUse", tool="mcp__supabase__apply_migration", input={"name": "x"},
             name="post-supabase", expect=None),
        # UserPromptSubmit
        dict(event="UserPromptSubmit", prompt="parity test prompt", name="ups-quiet", expect=None),
        dict(event="UserPromptSubmit", prompt="parity test prompt", name="ups-bulletin-and-jev-alert", expect=None,
             setup="bulletin+jev", pane=True),
        # Stop
        dict(event="Stop", name="stop", expect=None),
    ]
    return cases


def setup_case(case, run):
    setup = case.get("setup") or ""
    work = os.path.join(run, "work")
    if "rmcd" in setup:
        os.makedirs(os.path.join(work, "sub"), exist_ok=True)
        for name in ("a1", "a2"):
            open(os.path.join(work, "sub", name), "w").close()
    if "edit" in setup:
        open(os.path.join(work, "a.ts"), "w").close()
    if "prettier" in setup:
        pretty = os.path.join(work, "pretty")
        os.makedirs(pretty, exist_ok=True)
        open(os.path.join(pretty, "a.ts"), "w").close()
        with open(os.path.join(pretty, ".prettierrc"), "w") as handle:
            handle.write("{}\n")
    if "stash" in setup:
        repo = os.path.join(work, "repo")
        os.makedirs(repo)
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "check": True}
        git = ["git", "-c", "user.name=parity", "-c", "user.email=parity@localhost", "-C", repo]
        quiet["env"] = dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
                            GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
        subprocess.run(["git", "init", "-q", repo], **quiet)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], **quiet)
        subprocess.run(git + ["worktree", "add", "-q", os.path.join(work, "repo-wt"), "-b", "side"], **quiet)
    if "bulletin" in setup:
        row = {"id": "b-hookparity-1", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "from": "hook-parity", "kind": "task", "topic": "", "text": "parity test bulletin", "ref": "",
               "to": [TEST_PANE], "named_to": [TEST_PANE]}
        with open(os.path.join(run, "lanes", "bulletin.jsonl"), "w") as handle:
            handle.write(json.dumps(row) + "\n")
    if "jev" in setup:
        os.makedirs(os.path.join(run, "home", ".jev"), exist_ok=True)
        with open(os.path.join(run, "home", ".jev", "ALERT"), "w") as handle:
            handle.write("parity test alert\n")


def payload_for(case, run):
    if "raw" in case:
        return case["raw"].encode("utf-8")
    cwd = os.path.join(run, case.get("cwd_rel", "work"))
    body = {"session_id": "hook-parity", "transcript_path": os.path.join(run, "transcript.jsonl"), "cwd": cwd,
            "permission_mode": "bypassPermissions", "hook_event_name": case["event"]}
    if case.get("tool"):
        body["tool_name"] = case["tool"]
        tool_input = json.loads(json.dumps(case.get("input", {})).replace("{run}", run))
        body["tool_input"] = tool_input
        body["tool_use_id"] = "toolu_hookparity"
        if case["event"] == "PostToolUse":
            body["tool_response"] = case.get("response", {})
    if case["event"] == "UserPromptSubmit":
        body["prompt"] = case.get("prompt", "")
    if case["event"] == "Stop":
        body["stop_hook_active"] = False
    return json.dumps(body).encode("utf-8")


def run_path(sandbox, case, hooks, extra, trace):
    """Run hooks the way Claude Code does (each through /bin/sh -c, own timeout) in a fresh sandbox run dir."""
    run = sandbox.fresh()
    setup_case(case, run)
    payload = payload_for(case, run)
    trace_path = os.path.join(run, "trace.jsonl")
    env = sandbox.env(run, dict(extra, HOOK_DISPATCH_TRACE=trace_path) if trace else extra)
    cwd = os.path.join(run, case.get("cwd_rel", "work"))
    results = []
    for hook in hooks:
        result = run_shell(hook["command"], payload, env, cwd, hook_timeout(hook))
        result["trace"] = []
        if not trace and case.get("event") == "PreToolUse" and floor_of(hook["command"]):
            # A floor guard's own result, before its wrapper: what the floor rule reads.
            result["floor"] = floor_of(hook["command"])
            bare = bare_command(hook["command"])
            if bare != hook["command"]:
                result["raw"] = run_shell(bare, payload, env, cwd, hook_timeout(hook))
        if trace and os.path.exists(trace_path):
            with open(trace_path) as handle:
                result["trace"] = [json.loads(line) for line in handle if line.strip()]
            os.unlink(trace_path)
        results.append(result)
    return run, results, files_of(run)


def npx_calls(run):
    try:
        with open(os.path.join(run, "npx-calls.log")) as handle:
            return [line.rstrip("\n").replace(run, "<run>") for line in handle]
    except OSError:
        return []


def same(value, run):
    return json.loads(json.dumps(value).replace(run, "<run>"))


FLOOR_REFUSAL = re.compile(r"^(\[[^\n]*\]: )hook-dispatch: floor guard refusal$")


def same_effect(old_view, new_view):
    """The two views agree. The dispatcher joins every block message into one reason, so a floor refusal that is not
    the first message is not normalized there (FLOOR_MESSAGE reads from the start): it matches the expected refusal
    when it starts with that guard's `[<command>]: hook-dispatch: floor guard ` and the messages around it are equal
    (2026-10-08: with the railway guard install-policy registers but has not yet installed, a fresh home's first fold
    failed the fixture on that alone)."""
    def strip(view):
        return {key: value for key, value in view.items() if key not in ("block", "block_parts", "block_raw")}

    if strip(old_view) != strip(new_view):
        return False
    if old_view["block"] == new_view["block"]:
        return True
    pattern = "\n".join(
        re.escape(match.group(1) + "hook-dispatch: floor guard ") + ".*?" if match else re.escape(part)
        for part, match in ((part, FLOOR_REFUSAL.match(part)) for part in old_view["block_parts"]))
    return bool(pattern) and re.fullmatch(pattern, new_view["block_raw"], re.S) is not None


def run_case(sandbox, registry, case):
    event, tool = case["event"], case.get("tool")
    olds = old_hooks(registry["per_hook"], event, tool)
    news = new_hooks(registry["dispatch"], event, tool)
    extra = {"HERDR_PANE_ID": TEST_PANE} if case.get("pane") else {}
    outcome = {"name": case["name"], "event": event, "tool": tool, "mismatches": []}
    run, old_results, old_files = run_path(sandbox, case, olds, extra, False)
    run2, new_results, new_files = run_path(sandbox, case, news, extra, True)
    # Each result is compared as observed: a dispatcher entry that hit its timeout here (this harness kills it as
    # Claude Code does) is a cancelled hook, exactly as a per-hook hook that hit its own.
    unfolded = {result["command"] for result in new_results if not result["trace"]}  # left per-hook by the fold
    execd = {record["exec"] for result in new_results for record in result["trace"] if record.get("exec")}
    old_plain = same(effect(event, old_results), run)
    expected = with_exec_timer(old_results, execd)
    old_view = same(effect(event, with_floor(event, expected, unfolded)), run)
    new_view = same(effect(event, new_results), run2)
    # (2) what Claude Code does with the answers
    if not same_effect(old_view, new_view):
        outcome["mismatches"].append({"what": "effect", "old": old_view, "new": new_view})
    # (1) each guard: the dispatcher's own record of it, or its direct run when it was left per-hook
    new_by_command, modes = {}, []
    for result in new_results:
        if not result["trace"]:
            new_by_command[result["command"]] = (effective(result), "per-hook")
            continue
        for record in result["trace"]:
            for row in record.get("hooks", []):
                modes.append([row["command"][:70], row.get("kind"), row.get("mode"), row.get("reason")])
                if record.get("exec") == row["command"] and "code" not in row:
                    new_by_command[row["command"]] = (effective(result), "exec")  # this process became the guard
                elif str(row.get("mode", "")).startswith("skipped ("):
                    new_by_command[row["command"]] = (None, row["mode"])
                elif "code" in row:
                    new_by_command[row["command"]] = (effective(row), row.get("mode"))
    for old in expected:
        if old["command"] not in new_by_command:
            outcome["mismatches"].append({"what": "guard did not run on the new path", "command": old["command"]})
            continue
        new_eff, mode = new_by_command[old["command"]]
        if mode.startswith("skipped ("):
            continue  # a rewriter skipped because a deny or a later rewrite decides; the effect check covers it
        raw = old.get("raw") or old
        if mode == "per-hook":
            raw = old  # left per-hook: no floor rule, the same command on both paths
        elif old.get("floor") and floor_failed(raw):
            continue  # the floor rule turns this failure into a refusal; the effect check covers it
        elif old.get("floor") and raw["code"] == 2 and not raw["timed_out"]:
            old = dict(old, code=2, out=raw["out"], err=raw["err"])  # its block stands under any wrapper
        old_eff = same(effective(old), run)
        new_eff = same(new_eff, run2)
        if old_eff != new_eff:
            outcome["mismatches"].append({"what": "guard result", "command": old["command"], "mode": mode,
                                          "old": old_eff, "new": new_eff})
    extra_new = set(new_by_command) - {old["command"] for old in old_results}
    if extra_new:
        outcome["mismatches"].append({"what": "guards only on the new path", "commands": sorted(extra_new)})
    old_npx, new_npx = npx_calls(run), npx_calls(run2)
    outcome["npx_calls"] = {"old": old_npx, "new": new_npx}
    if new_npx != old_npx and (new_npx or any("--find-config-path" not in call for call in old_npx)):
        outcome["mismatches"].append({"what": "npx calls", "old": old_npx, "new": new_npx})
    # (3) side effects in the sandbox. A rewriter the dispatcher skipped (a guard denied, or a later guard rewrote
    # the input) leaves no state of its own: rtk's warning timestamp is not a guard effect.
    if any(str(mode).startswith("skipped (") for _c, _k, mode, _r in modes):
        old_files = {f for f in old_files if not f.startswith(RTK_STATE)}
        new_files = {f for f in new_files if not f.startswith(RTK_STATE)}
    if old_files != new_files:
        outcome["mismatches"].append({"what": "files", "only_old": sorted(old_files - new_files),
                                      "only_new": sorted(new_files - old_files)})
    outcome["decision"] = old_plain["decision"] or "allow"
    outcome["block_message"] = old_plain["block"][:400]
    outcome["new_decision"] = new_view["decision"] or "allow"
    outcome["new_block_message"] = new_view["block"][:400]
    # What the dispatcher path showed and how long its entries ran: the exec'd rewriter's timeout case reads these.
    outcome["new_updated_input"] = new_view["updatedInput"]
    outcome["new_shown"] = new_view["shown"][:400]
    outcome["new_codes"] = [result["code"] for result in new_results]
    outcome["new_seconds"] = max([result["seconds"] for result in new_results] or [0])
    outcome["new_timed_out"] = any(result["timed_out"] for result in new_results)
    outcome["execd"] = sorted(execd)
    if case.get("expect"):
        outcome["expect"] = case["expect"]
        outcome["expect_ok"] = outcome["decision"] == case["expect"]
    outcome["modes"] = modes
    # An exec'd rewriter killed by its own timer hit its timeout too (a loaded machine can push a quick one past it).
    outcome["timeouts"] = any(r["timed_out"] for r in old_results + new_results) or any(
        row.get("timed_out") for r in new_results for record in r["trace"] for row in record.get("hooks", [])) or any(
        r["code"] == 128 + signal.SIGALRM and any(record.get("exec") for record in r["trace"]) for r in new_results)
    outcome["result"] = "pass" if not outcome["mismatches"] else "fail"
    return outcome



# ---------------------------------------------------------------------------------------------------- process count


PROCESS_COMMANDS = ("ls -la", "git status", "echo hello", "cat README.md", "python3 -c 'print(1)'", "rg -n foo src",
                    "find . -name '*.py'", "git log --oneline -5", "cd %s && pytest -q" % HEAVY_DIR,
                    "jq . package.json", "npm run build", "sed -n 1,20p a.txt")
_NATIVE = {}


def _native():
    """libproc (child pids, zombies included) and libc sysctl (a process's argv), loaded once; macOS only."""
    if not _NATIVE:
        import ctypes
        _NATIVE["ctypes"] = ctypes
        _NATIVE["proc"] = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        _NATIVE["libc"] = ctypes.CDLL(None, use_errno=True)
    return _NATIVE


def child_pids(pid):
    """Every child of pid the kernel still lists: running, stopped, or exited and not yet reaped (a zombie)."""
    native = _native()
    ctypes = native["ctypes"]
    buf = (ctypes.c_int * 4096)()
    count = native["proc"].proc_listchildpids(pid, buf, ctypes.sizeof(buf))
    return [buf[i] for i in range(max(0, min(count, 4096))) if buf[i] > 0]


def reaped_children(pid):
    """True when pid has reaped a child (rusage_info_v1 ri_child_elapsed_abstime > 0), None when unreadable."""
    native = _native()
    ctypes = native["ctypes"]
    buf = (ctypes.c_uint64 * 20)()  # ri_uuid (2 words), then 16 counters; RUSAGE_INFO_V1 = 1
    if native["proc"].proc_pid_rusage(pid, 1, buf) != 0:
        return None
    return buf[17] > 0


def proc_argv(pid):
    """A process's argv (sysctl KERN_PROCARGS2), or None when it is gone or unreadable."""
    native = _native()
    ctypes, libc = native["ctypes"], native["libc"]
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0 or not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    raw = buf.raw[:size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    parts = raw[4:].split(b"\0")
    index = 1  # parts[0] is the exec path, then NUL padding
    while index < len(parts) and parts[index] == b"":
        index += 1
    return [part.decode("utf-8", "replace") for part in parts[index:index + argc]]


def runs_dispatcher(pid):
    argv = proc_argv(pid) or []
    return any(word.endswith("hook-dispatch.py") for word in argv[:3])


def observe(command, payload, env, cwd, timeout, grace=5.0):
    """Run `/bin/sh -c command` as Claude Code does and count every process its tree starts, observed.

    The child waits on a pipe until a kqueue watch (NOTE_FORK, NOTE_EXEC, NOTE_EXIT) is on its pid. On every fork
    event the watched tree's process groups are stopped (SIGSTOP), every child of every known process is listed
    (libproc lists unreaped children too, so a child that already exited is still found while its parent cannot reap
    it), each new one is watched and its group stopped too, and only then is the tree continued. `processes` counts
    every pid so found; `hook_layer` counts the root and each child a dispatcher process forked (the guard
    processes it starts; a process an in-process guard starts counts here too, which only makes the count higher).
    `observed` is False (nothing claimed) when the watch could not be set, the root did not exit in time, part of the
    tree outlived it by more than `grace` seconds, a process forked more children than were found, or a process
    reaped a child before its watch was on."""
    row = {"observed": False, "processes": None, "processes_lower": None, "hook_layer": None, "code": None,
           "why": None, "tree": []}
    if not hasattr(select, "kqueue") or sys.platform != "darwin":
        row["why"] = "no kqueue/libproc on this platform"
        return row
    work = tempfile.mkdtemp(prefix="observe-", dir=env.get("TMPDIR") or None)
    paths = {name: os.path.join(work, name) for name in ("in", "out", "err")}
    with open(paths["in"], "wb") as handle:
        handle.write(payload)
    fds = [os.open(paths["in"], os.O_RDONLY), os.open(paths["out"], os.O_WRONLY | os.O_CREAT, 0o600),
           os.open(paths["err"], os.O_WRONLY | os.O_CREAT, 0o600)]
    gate_r, gate_w = os.pipe()
    root = os.fork()
    if root == 0:  # child: wait for the watch, then become the hook's shell
        try:
            os.close(gate_w)
            os.read(gate_r, 1)
            os.close(gate_r)
            os.setsid()
            os.chdir(cwd)
            for target, fd in enumerate(fds):
                os.dup2(fd, target)
            os.execve("/bin/sh", ["/bin/sh", "-c", command], env)
        finally:
            os._exit(127)
    os.close(gate_r)
    for fd in fds:
        os.close(fd)
    own_group = os.getpgrp()
    kq = select.kqueue()
    known = {root: {"parent": None, "by_dispatcher": False}}
    forks, live, stopped, missed, gaps = {}, set(), set(), [], []
    flags = select.KQ_NOTE_FORK | select.KQ_NOTE_EXEC | select.KQ_NOTE_EXIT

    def watch(pid):
        try:
            kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                      flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=flags)], 0, 0)
            live.add(pid)
            return True
        except OSError:
            return False  # it already exited: counted, with nothing more to watch

    def freeze(pids):
        for pid in pids:
            try:
                group = os.getpgid(pid)
            except OSError:
                continue
            if group != own_group and group not in stopped:
                try:
                    os.killpg(group, signal.SIGSTOP)
                    stopped.add(group)
                except OSError:
                    pass

    def thaw():
        for group in list(stopped):
            try:
                os.killpg(group, signal.SIGCONT)
            except OSError:
                pass
        stopped.clear()

    def sweep():
        frontier = [(pid, False) for pid in known]
        while frontier:
            pid, new = frontier.pop()
            for child in child_pids(pid):
                if child in known:
                    continue
                known[child] = {"parent": pid, "by_dispatcher": pid in live and runs_dispatcher(pid)}
                freeze([child])
                watch(child)
                frontier.append((child, True))
            # A process found here ran unwatched from its fork until now: a child it started and reaped in that
            # window sent no event and is listed nowhere. Its rusage still shows the reaped child, so such a gap
            # makes the count incomplete, never a low count claimed as observed (2026-10-08 review M1). Read after
            # its children are listed, so a child reaped since then errs toward incomplete.
            if new and reaped_children(pid) is not False:
                gaps.append(pid)

    watching = watch(root)
    if not watching:
        row["why"] = "kqueue watch failed"
    os.write(gate_w, b"x")
    os.close(gate_w)
    started = time.monotonic()
    root_exit = None
    status = None
    try:
        while watching:
            now = time.monotonic()
            if root_exit is None and now - started > timeout:
                row["why"] = "no exit within %ss" % timeout
                break
            if root_exit is not None and not live:
                break
            if root_exit is not None and now - root_exit > grace:
                row["why"] = "processes outlived the hook by %ss: %s" % (grace, sorted(live))
                break
            wait = max(0.0, (timeout - (now - started)) if root_exit is None else grace - (now - root_exit))
            forked = False
            for event in kq.control(None, 64, min(wait, 0.5)):
                if event.fflags & select.KQ_NOTE_FORK:
                    forks[event.ident] = forks.get(event.ident, 0) + 1
                    forked = True
                if event.fflags & select.KQ_NOTE_EXIT:
                    live.discard(event.ident)
                    if event.ident == root and root_exit is None:
                        root_exit = time.monotonic()
            if forked:
                try:
                    freeze(list(live))
                    sweep()
                finally:
                    thaw()
            if root_exit is not None and status is None:
                _pid, status = os.waitpid(root, 0)
    finally:
        thaw()
        kq.close()
        leftover = sorted(live)
        if leftover:  # every one of them descends from the child this call forked: test-owned
            for pid in leftover:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except OSError:
                    pass
        if status is None:
            try:
                os.killpg(root, signal.SIGKILL)
            except OSError:
                pass
            _pid, status = os.waitpid(root, 0)
        shutil.rmtree(work, ignore_errors=True)
    for pid, count in forks.items():
        found = sum(1 for info in known.values() if info["parent"] == pid)
        if count > found:
            missed.append({"pid": pid, "fork_events": count, "children_found": found})
    row["code"] = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)
    row["tree"] = [{"pid": pid, "parent": info["parent"], "by_dispatcher": info["by_dispatcher"]}
                   for pid, info in sorted(known.items())]
    row["processes"] = len(known)
    # Each fork event is at least one process even when its child was never seen (a posix_spawnp that tries each
    # PATH entry creates and loses one per miss), so this bound holds when the count is incomplete.
    row["processes_lower"] = max(len(known), 1 + sum(forks.values()))
    row["hook_layer"] = 1 + sum(1 for info in known.values() if info["by_dispatcher"])
    row["missed"] = missed
    row["gaps"] = gaps
    if missed and not row["why"]:
        row["why"] = "a process forked more children than were found: %s" % missed
    if gaps and not row["why"]:
        row["why"] = "a process reaped a child before it was watched: %s" % gaps
    row["observed"] = bool(watching and root_exit is not None and not leftover and not missed and not gaps
                           and not row["why"])
    return row


SELFTEST = (  # command, exact processes, exact hook_layer
    ("exec /usr/bin/true", 1, 1),
    ("/usr/bin/true; /usr/bin/true", 3, 1),
    ("sleep 0; exec /usr/bin/true", 2, 1),  # a wrapper that forks once, then becomes its target
    # a shell, a Python parent and two Python children (the case the audit-hook count reported as 2)
    ("python3 -c 'import subprocess, sys; [subprocess.run([sys.executable, \"-c\", \"pass\"]) for _ in range(2)]'; "
     "exit 0", 4, 1),
    # grandchildren: a Python child that starts two of its own, each started without a fork of the shell
    ("exec python3 -c 'import subprocess, sys; subprocess.run([sys.executable, \"-c\", \"import subprocess, sys; "
     "[subprocess.run([\\\"/usr/bin/true\\\"]) for _ in range(2)]\"])'", 4, 1),
)


def counter_selftest(env, cwd):
    """The observer counts exactly, from a plain exec (1) to grandchildren (4), and claims nothing it missed. A case
    the watch did not observe completely (a short-lived child gone before it was found, on a loaded machine) is
    observed again, up to three times, as the dispatcher's counts are (harden run hook-dispatcher-20261008T090944Z,
    load 57: `/usr/bin/true; /usr/bin/true` saw 2 forks and found 1 child); only a complete observation counts, and
    it must be exact."""
    rows = []
    for command, processes, hook_layer in SELFTEST:
        for attempt in range(1, 4):
            seen = observe(command, b"", env, cwd, 30)
            if seen["observed"]:
                break
        rows.append({"command": command, "expected": processes, "processes": seen["processes"],
                     "hook_layer": seen["hook_layer"], "observed": seen["observed"], "why": seen["why"],
                     "attempts": attempt,
                     "ok": seen["observed"] and seen["processes"] == processes and seen["hook_layer"] == hook_layer})
    return {"ok": all(row["ok"] for row in rows), "cases": rows}


CWD_CLAUDE = "under HOME (a .claude directory above)"
CWD_PLAIN = "outside HOME (no .claude directory above)"


def measure_call(sandbox, registry, command, where, path):
    """One Bash call (PreToolUse + PostToolUse) on one path ("dispatcher" or "per-hook"), every hook observed."""
    total, layer, detail, complete = 0, 0, [], True
    for event in ("PreToolUse", "PostToolUse"):
        run = sandbox.fresh()
        cwd_rel = "home/project" if where == CWD_CLAUDE else "work"
        os.makedirs(os.path.join(run, cwd_rel), exist_ok=True)
        case = dict(bash(command, cwd_rel=cwd_rel), event=event, response={"stdout": ""}, name="count")
        payload = payload_for(case, run)
        env = sandbox.env(run)
        hooks = new_hooks(registry["dispatch"], event, "Bash") if path == "dispatcher" else \
            old_hooks(registry["per_hook"], event, "Bash")
        for hook in hooks:
            seen = observe(hook["command"], payload, env, os.path.join(run, cwd_rel), hook_timeout(hook) + 5)
            complete = complete and seen["observed"]
            total += (seen["processes"] if path == "dispatcher" else seen["processes_lower"]) or 0
            layer += seen["hook_layer"] or 0
            detail.append({"event": event, "command": hook["command"][:80], "processes": seen["processes"],
                           "processes_lower": seen["processes_lower"],
                           "hook_layer": seen["hook_layer"], "observed": seen["observed"], "why": seen["why"]})
        shutil.rmtree(run, ignore_errors=True)
    return {"processes": total, "hook_layer": layer, "complete": complete, "detail": detail}


def process_count(sandbox, registry, jobs=4):
    """Processes per Bash call (PreToolUse + PostToolUse), observed as full trees, on both paths, with the session's
    cwd under a .claude directory (HOME and below; rtk finds the project there) and under none (worktrees on another
    volume; rtk then runs `git rev-parse --show-toplevel`). The budget (build lead, w5W:p3, 2026-10-07): hook-layer
    processes (dispatcher entries plus the guard processes they start; a guard the dispatcher execs into adds none)
    at most 2 per Bash call, and the dispatcher path's whole tree no larger than the per-hook path's for the same
    command and cwd. The dispatcher path must be observed completely; the per-hook count is a lower bound either
    way (a fork the watch could not attribute only makes it larger), so it may stand when incomplete."""
    pairs = [(where, command) for where in (CWD_CLAUDE, CWD_PLAIN) for command in PROCESS_COMMANDS]

    def one(pair):
        where, command = pair
        # An incomplete watch (a gap, a missed fork) is no count at all, so it is observed again, up to three times;
        # only a complete observation is used.
        for attempt in range(1, 4):
            new = measure_call(sandbox, registry, command, where, "dispatcher")
            if new["complete"]:
                break
        old = measure_call(sandbox, registry, command, where, "per-hook")
        ok = new["complete"] and new["hook_layer"] <= 2 and new["processes"] <= old["processes"]
        return {"command": command, "cwd": where, "attempts": attempt,
                "processes": new["processes"] if new["complete"] else None,
                "hook_layer": new["hook_layer"] if new["complete"] else None, "per_hook_processes": old["processes"],
                "per_hook_complete": old["complete"], "ok": ok, "detail": new["detail"],
                "per_hook_detail": old["detail"]}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, pairs))


# ---------------------------------------------------------------------------------------------------- crash/timeout


SYNTHETIC = {
    "crash.py": ("import sys\nraise RuntimeError('synthetic crash')\n", 0o755),
    "hang.py": ("import time\ntime.sleep(30)\n", 0o755),
    "failclosed.py": ("import sys\ntry:\n    raise ValueError('bad input')\nexcept Exception as e:\n"
                      "    sys.stderr.write('synthetic guard error (%s); refused\\n' % type(e).__name__)\n"
                      "    sys.exit(2)\n", 0o755),
    "crash.sh": ("#!/bin/bash\necho 'synthetic shell crash' >&2\nexit 1\n", 0o755),
    "hang.sh": ("#!/bin/bash\nsleep 30\n", 0o755),
    "failclosed.sh": ("#!/bin/bash\necho 'BLOCKED: synthetic shell refusal' >&2\nexit 2\n", 0o755),
}
# A stand-in for `rtk hook claude` (on the sandbox PATH): rewrites the command at once, or after 1.45 s for a
# `late-rewriter` one. The RewriteLate entry gives it a 1 s timeout: per-hook it is cancelled at 1 s; the dispatcher
# execs into it with that timeout armed as a timer, so it is killed at 1 s and its late rewrite never lands (build
# lead decision (a), 2026-10-08), well before the entry's own timeout (6 s) would have cancelled it.
FAKE_RTK = """#!/bin/sh
input=$(cat)
case "$input" in
  *late-rewriter*) sleep 1.45 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"rtk rewritten"}}}'
"""


def synthetic_registry():
    prefix = "$HOME/synth/"
    hooks = [
        {"type": "command", "command": "/usr/bin/python3 \"%scrash.py\"" % prefix},
        {"type": "command", "command": "python3 \"%shang.py\"" % prefix, "timeout": 2},
        {"type": "command", "command": "\"%scrash.sh\"" % prefix},
        {"type": "command", "command": "\"%shang.sh\"" % prefix, "timeout": 2},
        {"type": "command", "command": "/usr/bin/python3 \"%scrash.py\" 2>/dev/null || true" % prefix},
        {"type": "command", "command": "/usr/bin/python3 \"%scrash.py\" 2>/dev/null || { printf %%s "
                                       "'{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
                                       "\"additionalContext\":\"synthetic fallback\"}}'; }" % prefix},
    ]
    closed = [
        {"type": "command", "command": "/usr/bin/python3 \"%sfailclosed.py\"" % prefix},
        {"type": "command", "command": "\"%sfailclosed.sh\"" % prefix},
        {"type": "command", "command": "python3 \"%shang.py\"" % prefix, "timeout": 2},
    ]
    groups = [{"matcher": "Bash", "hooks": hooks}, {"matcher": "Agent", "hooks": closed},
              {"matcher": "RewriteSlow", "hooks": [{"type": "command", "command": "rtk hook claude", "timeout": 2.45}]},
              {"matcher": "RewriteLate", "hooks": [{"type": "command", "command": "rtk hook claude", "timeout": 1}]}]
    return build_registry({"PreToolUse": groups}, ["crash.py", "hang.py", "failclosed.py"])


# ---------------------------------------------------------------------------------------------------- floor replay
# Each live floor guard is made to fail four ways in a sandbox (its script, or the `bash` an inline guard runs, is
# replaced by a stub; never the real file) and run on both paths: the dispatcher must refuse every one. One
# non-floor guard is failed the same ways as a control: it must fail open on both paths, as before.
FLOOR_MODES = ("timeout", "crash", "missing", "malformed")
PY_STUBS = {"timeout": "import time\ntime.sleep(30)\n",
            "crash": "raise RuntimeError('synthetic floor crash')\n",
            "malformed": "import sys\nsys.stdin.read()\nprint('not a hook answer {')\n"}
SH_STUBS = {"timeout": "#!/bin/bash\nexec sleep 30\n",
            "crash": "#!/bin/bash\necho 'synthetic floor crash' >&2\nexit 1\n",
            "missing": "#!/bin/bash\necho \"bash: $0: No such file or directory\" >&2\nexit 127\n",
            "malformed": "#!/bin/bash\ncat >/dev/null\necho 'not a hook answer {'\n"}
# The Bash input reaches every pinned floor guard's prefilter (dangerous-command, railway, link-cli; launchctl for the
# bootout guard, a floor guard since 2026-10-08), so each one runs; a guard its prefilter skips never runs, and so
# cannot fail. Never executed.
REPLAY_INPUT = {"Bash": {"command": "echo launchctl railway link-cli; rm -" "rf /tmp/hook-dispatch-floor-replay"},
                "Agent": {"subagent_type": "Explore", "prompt": "x", "description": "x"},
                "Workflow": {"script": "agent('x', {agentType: 'Explore'})"}}


def replay_leaves(registry):
    """(floor name or None, matcher key, hook) for each floor guard on PreToolUse, then one non-floor control."""
    leaves, control = [], None
    for group in registry["per_hook"].get("PreToolUse", []):
        key = group_key(group)
        for hook in group.get("hooks", []):
            name = floor_of(hook.get("command", ""))
            if name and not any(other["command"] == hook["command"] for _n, _k, other in leaves):
                leaves.append((name, key, hook))
            elif control is None and not name and key == "Bash" and SHAPE_SCRIPT.match(hook.get("command", "")):
                control = (None, key, hook)
    return leaves + ([control] if control else [])


SHAPE_SCRIPT = re.compile(r'^\s*(?:(/usr/bin/python3|python3)\s+)?("\$HOME/[^"]+"|\$HOME/\S+|~/\S+)')


def replay_case(root, dispatcher, real_home, inproc, index, name, key, hook, mode):
    command = hook["command"]
    match = SHAPE_SCRIPT.match(command)
    extra, remove = {}, []
    if match:
        rel = match.group(2).strip('"').replace("$HOME/", "", 1).replace("~/", "", 1)
        python = bool(match.group(1)) or (interpreter_line(os.path.join(real_home, rel)) or "").find("python") >= 0
        if mode == "missing":
            remove.append(os.path.join("home", rel))
        else:
            extra[os.path.join("home", rel)] = ((("#!/usr/bin/env python3\n" + PY_STUBS[mode]) if python
                                               else SH_STUBS[mode]), 0o755)
    else:  # an inline guard: the `bash` it starts fails
        extra[os.path.join("bin", "bash")] = (SH_STUBS[mode], 0o755)
    timed = dict(hook, timeout=1) if mode == "timeout" else dict(hook, timeout=min(hook_timeout(hook), 20))
    registry = build_registry({"PreToolUse": [{"matcher": key, "hooks": [timed]}]}, inproc)
    sandbox = Sandbox(os.path.join(root, "floor-%03d" % index), real_home, registry, dispatcher, [command], extra,
                      remove)
    tool = key.split("|")[0] if key != "*" else "Bash"
    case = dict(event="PreToolUse", tool=tool, input=REPLAY_INPUT.get(tool, {"command": "ls"}), expect=None,
                name="floor-%s-%s" % (name or "control", mode))
    outcome = run_case(sandbox, registry, case)
    shutil.rmtree(sandbox.root, ignore_errors=True)
    want = "deny" if name else outcome["decision"]
    return {"name": case["name"], "leaf": name, "control": name is None, "mode": mode, "command": command[:120],
            "per_hook_decision": outcome["decision"], "decision": outcome["new_decision"],
            "message": outcome["new_block_message"][:300], "parity": outcome["result"],
            "mismatches": outcome["mismatches"],
            "ok": outcome["new_decision"] == want and outcome["result"] == "pass"}


def interpreter_line(path):
    try:
        with open(path, "rb") as handle:
            first = handle.readline(200).decode("utf-8", "replace")
    except OSError:
        return None
    return first if first.startswith("#!") else None


def floor_replay(root, dispatcher, real_home, registry, jobs=4):
    inproc = list(registry.get("inproc") or [])
    jobs_list = [(index, name, key, hook, mode) for index, (name, key, hook) in enumerate(replay_leaves(registry))
                 for mode in FLOOR_MODES]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(lambda job: replay_case(root, dispatcher, real_home, inproc, job[0] * 10 +
                                                     FLOOR_MODES.index(job[4]), *job[1:]), jobs_list))


def build_registry(per_hook, inproc):
    """The registry install-policy writes, for a per-hook block (kept in step with install-policy.fold)."""
    entries, dispatch = {}, {}
    for event, groups in per_hook.items():
        keys = []
        for group in groups:
            key = group_key(group)
            if key not in keys:
                keys.append(key)
            entries.setdefault(event, {}).setdefault(key, []).extend(copy.deepcopy(group["hooks"]))
        dispatch[event] = []
        for key in keys:
            hooks = entries[event][key]
            entry = {"type": "command",
                     "command": "python3 \"$HOME/.claude/hooks/hook-dispatch.py\" %s '%s'" % (event, key),
                     "timeout": int(sum(hook_timeout(h) for h in hooks) + 5)}
            group = {"hooks": [entry]}
            if key != "*":
                group = {"matcher": key, "hooks": [entry]}
            dispatch[event].append(group)
    return {"schema": 1, "per_hook": per_hook, "entries": entries, "dispatch": dispatch, "inproc": inproc}


def crash_cases(root, dispatcher, real_home):
    registry = synthetic_registry()
    extra = {os.path.join("home", "synth", name): spec for name, spec in SYNTHETIC.items()}
    extra[os.path.join("bin", "rtk")] = (FAKE_RTK, 0o755)
    sandbox = Sandbox(os.path.join(root, "synthetic"), real_home, registry, dispatcher, [], extra)
    out = []
    for name, tool, command in (("crash-and-timeout-fail-open", "Bash", "true"),
                                ("crash-fail-closed-and-timeout", "Agent", "true"),
                                ("rewriter-execd-rewrites", "RewriteSlow", "echo plain-rewriter"),
                                ("rewriter-late-times-out", "RewriteLate", "echo late-rewriter")):
        case = dict(event="PreToolUse", tool=tool, input={"command": command, "prompt": "x"}, name=name, expect=None)
        outcome, attempts = run_case(sandbox, registry, case), 1
        while timing_only(outcome) and attempts < 3:  # a loaded machine: the slow rewriter ran past its 2.45 s
            attempts += 1
            outcome = dict(run_case(sandbox, registry, case), attempts=attempts)
        out.append(outcome)
    return out


# ---------------------------------------------------------------------------------------------------- main


def install_lock(home, wait=600):
    """Hold install-policy's per-home lock (shared) while the fixture reads the live guards, so a sync or hand install
    cannot change them mid-run; waits for a running install. Same path as install-policy.locked_main."""
    import fcntl
    digest = hashlib.sha256(str(os.path.realpath(os.path.expanduser(home))).encode()).hexdigest()[:16]
    lock_dir = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
    handle = open(os.path.join(lock_dir, "agent-lb-install-policy-%d-%s.lock" % (os.getuid(), digest)), "a")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() > deadline:
                handle.close()
                raise SystemExit("error: an install-policy run held the install lock for %ss" % wait)
            time.sleep(1)


def timing_only(case):
    """A failed case in which some guard hit its timeout on one path: rerun before calling it a difference."""
    return case["result"] != "pass" and case.get("timeouts")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--registry")
    parser.add_argument("--dispatcher")
    parser.add_argument("--out")
    parser.add_argument("--workdir", help="sandbox parent (default: a new temp dir, removed afterwards)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox")
    parser.add_argument("--only", help="regex: run only cases whose name matches")
    parser.add_argument("--jobs", type=int, default=4, help="cases run at once (each in its own sandbox runs)")
    parser.add_argument("--require-process-count", action="store_true")
    parser.add_argument("--no-process-count", action="store_true",
                        help="skip the process count (install-policy's gate: parity and the floor replay only)")
    parser.add_argument("--lock-held", action="store_true",
                        help="the caller (install-policy) holds the per-home install lock already")
    parser.add_argument("--require-expectations", action="store_true",
                        help="also require each deny/allow case to get its expected decision (the live guard set)")
    args = parser.parse_args()
    home = os.path.abspath(args.home)
    lock = None if args.lock_held else install_lock(home)
    registry_path = args.registry or os.path.join(home, ".claude", "hooks", "dispatch", "registry.json")
    dispatcher = args.dispatcher or os.path.join(home, ".claude", "hooks", "hook-dispatch.py")
    with open(registry_path) as handle:
        registry = json.load(handle)
    if args.workdir:
        os.makedirs(args.workdir, exist_ok=True)
        # A run killed by a timeout cannot clean up; its sandbox is removed by the next run after an hour.
        for name in os.listdir(args.workdir):
            stale = os.path.join(args.workdir, name)
            if name.startswith("hook-parity-") and os.path.isdir(stale) and not os.path.islink(stale) \
                    and time.time() - os.path.getmtime(stale) > 3600:
                shutil.rmtree(stale, ignore_errors=True)
    root = tempfile.mkdtemp(prefix="hook-parity-", dir=args.workdir)
    started = time.time()
    report = {"schema": 2, "registry": registry_path, "dispatcher": dispatcher, "home": home,
              "dispatcher_sha256": hashlib.sha256(open(dispatcher, "rb").read()).hexdigest(),
              "python": sys.executable, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))}
    try:
        commands = [hook["command"] for groups in registry["per_hook"].values() for group in groups
                    for hook in group.get("hooks", [])]
        sandbox = Sandbox(os.path.join(root, "live"), home, registry, dispatcher, commands)
        cases = [case for case in builtin_cases() if case.get("tool") not in NEVER_RUN]
        if args.only:
            cases = [case for case in cases if re.search(args.only, case["name"])]
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            report["cases"] = list(pool.map(lambda case: run_case(sandbox, registry, case), cases))
        # A guard that hit its timeout on one path only (a loaded machine) is rerun, at most twice; a difference
        # that is not about time fails at once, and one that persists fails.
        by_name = {case["name"]: case for case in cases}
        for index, outcome in enumerate(report["cases"]):
            attempts = 1
            while timing_only(outcome) and attempts < 3:
                attempts += 1
                outcome = dict(run_case(sandbox, registry, by_name[outcome["name"]]), attempts=attempts)
            report["cases"][index] = outcome
        report["crash_timeout"] = [] if args.only else crash_cases(root, dispatcher, home)
        report["floor_replay"] = [] if args.only else floor_replay(root, dispatcher, home, registry, args.jobs)
        skip_count = bool(args.only) or args.no_process_count
        report["process_counter_selftest"] = None if skip_count else counter_selftest(
            sandbox.env(sandbox.fresh()), root)
        report["process_count"] = [] if skip_count else process_count(sandbox, registry, args.jobs)
        guards = sorted({hook["command"] for groups in registry["per_hook"].values() for group in groups
                         for hook in group.get("hooks", [])})
        report["guards"] = len(guards)
        report["not_run"] = sorted({hook["command"] for group in registry["per_hook"].get("PreToolUse", [])
                                    if group_key(group) in NEVER_RUN for hook in group.get("hooks", [])})
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            report["sandbox"] = root
        if lock is not None:
            lock.close()
    parity_ok = all(case["result"] == "pass" for case in report["cases"] + report["crash_timeout"])
    floor_ok = all(row["ok"] for row in report["floor_replay"])
    missed = [case["name"] for case in report["cases"] if case.get("expect_ok") is False]
    report["expectations_failed"] = missed
    rows = report["process_count"]
    # Unmeasured counts as failed: a count the watch did not observe completely proves nothing.
    selftest_ok = bool(report.get("process_counter_selftest") and report["process_counter_selftest"]["ok"])
    count_ok = bool(rows) and selftest_ok and all(row["ok"] for row in rows)
    report["parity"] = "pass" if parity_ok else "fail"
    report["floor"] = "pass" if floor_ok else "fail"
    layers = [row["hook_layer"] for row in rows]
    report["hook_layer_max"] = max(layers) if layers and all(isinstance(n, int) for n in layers) else None
    totals = [row["processes"] for row in rows]
    report["process_count_max"] = max(totals) if totals and all(isinstance(n, int) for n in totals) else None
    report["process_count_ok"] = count_ok
    report["seconds"] = round(time.time() - started, 1)
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
    failed = [case["name"] for case in report["cases"] + report["crash_timeout"] if case["result"] != "pass"]
    floor_failed_rows = [row["name"] for row in report["floor_replay"] if not row["ok"]]
    print("parity %s: %d cases, %d failed%s; floor replay %s (%d runs%s); hook-layer processes per Bash call max %s"
          % (report["parity"], len(report["cases"]) + len(report["crash_timeout"]), len(failed),
             (" (" + ", ".join(failed) + ")") if failed else "", report["floor"], len(report["floor_replay"]),
             (", failed: " + ", ".join(floor_failed_rows)) if floor_failed_rows else "", report["hook_layer_max"]))
    if not parity_ok:
        return 1
    if not floor_ok:
        return 5
    if args.require_process_count and not count_ok:
        return 3
    if args.require_expectations and missed:
        print("expected decision missed: " + ", ".join(missed))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
