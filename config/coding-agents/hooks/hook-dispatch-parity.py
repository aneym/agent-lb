#!/usr/bin/env python3
"""Parity fixture for hook-dispatch.py: the per-hook config and the dispatcher give Claude Code the same answers.

For every case (event, tool, payload) it runs, in a fresh sandbox HOME each time:
- old: every matching per-hook command from the registry's `per_hook` block, each through /bin/sh -c with its own
  timeout, as Claude Code runs them;
- new: the dispatcher entries from the registry's `dispatch` block, with HOOK_DISPATCH_TRACE on.
It compares (1) each guard's own result (exit code, stdout, stderr when the exit code is not 0, timeout) between the
two paths, (2) the effect Claude Code builds from the results (decision, block message, updatedInput, context,
system message, continue), emulated here independently of the dispatcher's merge code, and (3) the files each path
leaves in the sandbox. It also measures processes per Bash call (the dispatcher's own audit count plus the exec chain
check), the prettier gate, and crash and timeout behaviour with synthetic guards.

The sandbox is test-owned: HOME, TMPDIR, lane, factory, ledger and state paths point inside it, HERDR_* and
FACTORY_* are dropped, AGENT_LB_URL points at a closed port, AUTOCLOSE_DRY_RUN and IDLE_REAPER_DRY_RUN are set.
Guard scripts are linked from the real HOME read-only; config files they read are copied. AskUserQuestion is never
run (its hook files a real ask). Exit 0 when every parity case matches; --require-process-count also requires every
measured Bash call at <= 2 processes.

Usage: hook-dispatch-parity.py [--home H] [--registry R] [--dispatcher D] [--out FILE] [--require-process-count]
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DEFAULT_TIMEOUT = 600.0
PATH_REF = re.compile(r'(?:\$HOME|~)/([^\s"\'$`\\;&|<>(){}]+)')
CONFIG_COPIES = (".agent-lb/managed/coding-agents/routing-table.json", ".claude/agents", ".claude/load-governor.json")
TEST_PANE = "hp:t1"
RTK_STATE = "home/Library/Application Support/rtk/"
HEAVY_DIR = os.path.join(os.path.expanduser("~"), "factory")  # under box-offload's roots, so it rewrites
NEVER_RUN = {"AskUserQuestion"}  # its hook registers a real ask with unblock


# ---------------------------------------------------------------------------------------------------- matching


def matches(key, tool):
    if key == "*" or tool is None:
        return True
    return tool in [part.strip() for part in key.split("|") if part.strip()]


def group_key(group):
    matcher = group.get("matcher")
    return "*" if matcher in (None, "", "*", ".*") else matcher


def old_hooks(per_hook, event, tool):
    seen, hooks = set(), []
    for group in per_hook.get(event, []):
        if not matches(group_key(group), tool):
            continue
        for hook in group.get("hooks", []):
            if hook.get("command") in seen:
                continue  # Claude Code runs an identical command once
            seen.add(hook.get("command"))
            hooks.append(hook)
    return hooks


def new_hooks(dispatch, event, tool):
    return [hook for group in dispatch.get(event, []) if matches(group_key(group), tool)
            for hook in group.get("hooks", [])]


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

    It reads stdout as JSON whatever the exit code: `decision: block` or a PreToolUse deny blocks with the reason;
    an exit 2 with no such reason blocks with `[<command>]: <stderr>`; other exit codes are a notice to the user."""
    view = {"decision": None, "block": [], "updatedInput": None, "context": [], "system": [], "continue": True,
            "stopReason": [], "plain": []}
    rank = {"allow": 1, "ask": 2, "deny": 3}
    for result in results:
        if result["timed_out"]:
            continue  # cancelled: no effect
        obj = parse_json(result["out"])
        if obj is None:
            if result["code"] == 2:
                view["block"].append("[%s]: %s" % (result["command"], result["err"] or "No stderr output"))
            elif result["code"] == 0 and result["out"].strip():
                view["plain"].append(result["out"])
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
            view["system"].append(str(obj["systemMessage"]))
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
    view["block"] = "\n".join(message for message in view["block"])
    view["context"] = "\n".join(view["context"])
    view["system"] = "\n".join(view["system"])
    view["stopReason"] = "\n".join(view["stopReason"])
    view["transcript_only_stdout"] = bool(plain) and event != "UserPromptSubmit"
    return view


def effective(result):
    """One guard's result as Claude Code reads it: stderr only matters when the exit code is not 0."""
    if result is None:
        return None
    if result["timed_out"]:
        return {"timed_out": True}
    return {"code": result["code"], "out": result["out"], "err": result["err"] if result["code"] != 0 else ""}


# ---------------------------------------------------------------------------------------------------- sandbox


class Sandbox(object):
    def __init__(self, root, real_home, registry, dispatcher, commands, extra_files=None):
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
        with open(os.path.join(hooks_dir, "dispatch", "registry.json"), "w") as handle:
            json.dump(registry, handle, indent=2)
        for rel, (content, mode) in (extra_files or {}).items():
            path = os.path.join(self.template, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(content)
            os.chmod(path, mode)
        for rel in ("tmp", "work", "project", "lanes", "factory", "idle", "orch", "ledger", "skill", "bin"):
            os.makedirs(os.path.join(self.template, rel), exist_ok=True)
        with open(os.path.join(self.template, "factory", "machines.json"), "w") as handle:
            handle.write("[]\n")
        self.count = 0

    def fresh(self):
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
    skip = {os.path.join(run, "trace.jsonl")}
    for dirpath, dirnames, filenames in os.walk(run):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__",)]
        rel_dir = os.path.relpath(dirpath, run)
        if rel_dir.split(os.sep)[0] == "tmp":
            continue
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
        dict(bash("grep -r needle ~/.agent-rails/x"), name="deny-wide-scan-fifo", expect=deny),
        dict(bash("rg needle /"), name="deny-wide-scan-root", expect=deny),
        dict(bash("rg -l b-123 ~/.agent-rails"), name="deny-wide-scan-lanes", expect=deny),
        dict(bash("link-cli auth status"), name="deny-link-cli-secret", expect=deny),
        dict(bash("railway variables --kv"), name="deny-railway-kv", expect=deny),
        dict(bash("railway run printenv"), name="deny-railway-printenv", expect=deny),
        dict(bash('open -a "Google Chrome" https://example.com'), name="deny-no-chrome", expect=deny),
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
        dict(event="PreToolUse", tool="Bash", raw="not json at all", name="malformed-plain (fail open)",
             expect="allow"),
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
        npx = os.path.join(run, "bin", "npx")  # never the network: a fake npx that records its argv
        with open(npx, "w") as handle:
            handle.write('#!/bin/sh\necho "$@" >> "%s/npx-calls.log"\nexit 0\n' % run)
        os.chmod(npx, 0o755)
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
        if trace and os.path.exists(trace_path):
            with open(trace_path) as handle:
                result["trace"] = [json.loads(line) for line in handle if line.strip()]
            os.unlink(trace_path)
        results.append(result)
    return run, results, files_of(run)


def same(value, run):
    return json.loads(json.dumps(value).replace(run, "<run>"))


def run_case(sandbox, registry, case):
    event, tool = case["event"], case.get("tool")
    olds = old_hooks(registry["per_hook"], event, tool)
    news = new_hooks(registry["dispatch"], event, tool)
    extra = {"HERDR_PANE_ID": TEST_PANE} if case.get("pane") else {}
    outcome = {"name": case["name"], "event": event, "tool": tool, "mismatches": []}
    run, old_results, old_files = run_path(sandbox, case, olds, extra, False)
    run2, new_results, new_files = run_path(sandbox, case, news, extra, True)
    old_view, new_view = same(effect(event, old_results), run), same(effect(event, new_results), run2)
    # (2) what Claude Code does with the answers
    if old_view != new_view:
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
    for old in old_results:
        if old["command"] not in new_by_command:
            outcome["mismatches"].append({"what": "guard did not run on the new path", "command": old["command"]})
            continue
        new_eff, mode = new_by_command[old["command"]]
        if mode.startswith("skipped ("):
            continue  # a rewriter skipped because a deny or a later rewrite decides; the effect check covers it
        old_eff = same(effective(old), run)
        new_eff = same(new_eff, run2)
        if old_eff != new_eff:
            outcome["mismatches"].append({"what": "guard result", "command": old["command"], "mode": mode,
                                          "old": old_eff, "new": new_eff})
    extra_new = set(new_by_command) - {old["command"] for old in old_results}
    if extra_new:
        outcome["mismatches"].append({"what": "guards only on the new path", "commands": sorted(extra_new)})
    # (3) side effects in the sandbox. A rewriter the dispatcher skipped (a guard denied, or a later guard rewrote
    # the input) leaves no state of its own: rtk's warning timestamp is not a guard effect.
    if any(str(mode).startswith("skipped (") for _c, _k, mode, _r in modes):
        old_files = {f for f in old_files if not f.startswith(RTK_STATE)}
        new_files = {f for f in new_files if not f.startswith(RTK_STATE)}
    if old_files != new_files:
        outcome["mismatches"].append({"what": "files", "only_old": sorted(old_files - new_files),
                                      "only_new": sorted(new_files - old_files)})
    outcome["decision"] = old_view["decision"] or "allow"
    outcome["block_message"] = old_view["block"][:400]
    outcome["new_decision"] = new_view["decision"] or "allow"
    outcome["new_block_message"] = new_view["block"][:400]
    if case.get("expect"):
        outcome["expect"] = case["expect"]
        outcome["expect_ok"] = outcome["decision"] == case["expect"]
    outcome["modes"] = modes
    outcome["old_processes"] = len(old_results)
    outcome["new_processes"] = sum(
        (1 + sum(int(record.get("spawned", 0)) for record in result["trace"])) if result["trace"] else 1
        for result in new_results)
    outcome["result"] = "pass" if not outcome["mismatches"] else "fail"
    return outcome



# ---------------------------------------------------------------------------------------------------- process count


PROCESS_COMMANDS = ("ls -la", "git status", "echo hello", "cat README.md", "python3 -c 'print(1)'", "rg -n foo src",
                    "find . -name '*.py'", "git log --oneline -5", "cd %s && pytest -q" % HEAVY_DIR,
                    "jq . package.json", "npm run build", "sed -n 1,20p a.txt")


def process_count(sandbox, registry):
    rows = []
    for command in PROCESS_COMMANDS:
        total, detail = 0, []
        for event in ("PreToolUse", "PostToolUse"):
            case = dict(bash(command), event=event, response={"stdout": ""}, name="count")
            run = sandbox.fresh()
            payload = payload_for(case, run)
            trace_path = os.path.join(run, "trace.jsonl")
            env = sandbox.env(run, {"HOOK_DISPATCH_TRACE": trace_path})
            for hook in new_hooks(registry["dispatch"], event, "Bash"):
                result = run_shell(hook["command"], payload, env, os.path.join(run, "work"), hook_timeout(hook))
                try:
                    with open(trace_path) as handle:
                        records = [json.loads(line) for line in handle if line.strip()]
                except OSError:
                    records = []
                record = records[-1] if records else {}
                os.unlink(trace_path) if os.path.exists(trace_path) else None
                same_pid = record.get("pid") == result["pid"]
                spawned = int(record.get("spawned", 0))
                count = 1 + spawned if same_pid else 2 + spawned
                total += count
                detail.append({"event": event, "processes": count, "sh_exec_into_python": same_pid,
                               "spawned_by_dispatcher": spawned, "exec": record.get("exec"),
                               "external": [row["command"][:50] for row in record.get("hooks", [])
                                            if row.get("mode") == "external"]})
        rows.append({"command": command, "processes": total, "detail": detail})
    return rows


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
    groups = [{"matcher": "Bash", "hooks": hooks}, {"matcher": "Agent", "hooks": closed}]
    return build_registry({"PreToolUse": groups}, ["crash.py", "hang.py", "failclosed.py"])


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
    sandbox = Sandbox(os.path.join(root, "synthetic"), real_home, registry, dispatcher, [], extra)
    out = []
    for name, tool in (("crash-and-timeout-fail-open", "Bash"), ("crash-fail-closed-and-timeout", "Agent")):
        case = dict(event="PreToolUse", tool=tool, input={"command": "true", "prompt": "x"}, name=name, expect=None)
        out.append(run_case(sandbox, registry, case))
    return out


# ---------------------------------------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--registry")
    parser.add_argument("--dispatcher")
    parser.add_argument("--out")
    parser.add_argument("--workdir", help="sandbox parent (default: a new temp dir, removed afterwards)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox")
    parser.add_argument("--only", help="regex: run only cases whose name matches")
    parser.add_argument("--require-process-count", action="store_true")
    parser.add_argument("--require-expectations", action="store_true",
                        help="also require each deny/allow case to get its expected decision (the live guard set)")
    args = parser.parse_args()
    home = os.path.abspath(args.home)
    registry_path = args.registry or os.path.join(home, ".claude", "hooks", "dispatch", "registry.json")
    dispatcher = args.dispatcher or os.path.join(home, ".claude", "hooks", "hook-dispatch.py")
    with open(registry_path) as handle:
        registry = json.load(handle)
    root = tempfile.mkdtemp(prefix="hook-parity-", dir=args.workdir)
    started = time.time()
    report = {"schema": 1, "registry": registry_path, "dispatcher": dispatcher, "home": home,
              "dispatcher_sha256": hashlib.sha256(open(dispatcher, "rb").read()).hexdigest(),
              "python": sys.executable, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))}
    try:
        commands = [hook["command"] for groups in registry["per_hook"].values() for group in groups
                    for hook in group.get("hooks", [])]
        sandbox = Sandbox(os.path.join(root, "live"), home, registry, dispatcher, commands)
        cases = [case for case in builtin_cases() if case.get("tool") not in NEVER_RUN]
        if args.only:
            cases = [case for case in cases if re.search(args.only, case["name"])]
        report["cases"] = [run_case(sandbox, registry, case) for case in cases]
        report["crash_timeout"] = [] if args.only else crash_cases(root, dispatcher, home)
        report["process_count"] = [] if args.only else process_count(sandbox, registry)
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
    parity_ok = all(case["result"] == "pass" for case in report["cases"] + report["crash_timeout"])
    missed = [case["name"] for case in report["cases"] if case.get("expect_ok") is False]
    report["expectations_failed"] = missed
    counts = [row["processes"] for row in report["process_count"]]
    count_ok = all(count <= 2 for count in counts)
    report["parity"] = "pass" if parity_ok else "fail"
    report["process_count_max"] = max(counts) if counts else None
    report["process_count_ok"] = count_ok
    report["seconds"] = round(time.time() - started, 1)
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
    failed = [case["name"] for case in report["cases"] + report["crash_timeout"] if case["result"] != "pass"]
    print("parity %s: %d cases, %d failed%s; processes per Bash call max %s"
          % (report["parity"], len(report["cases"]) + len(report["crash_timeout"]), len(failed),
             (" (" + ", ".join(failed) + ")") if failed else "", report["process_count_max"]))
    if not parity_ok:
        return 1
    if args.require_process_count and not count_ok:
        return 3
    if args.require_expectations and missed:
        print("expected decision missed: " + ", ".join(missed))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
