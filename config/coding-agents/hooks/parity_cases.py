import json
import os
import subprocess
import time

import parity_offload  # type: ignore[missing-import]
from parity_sandbox import TEST_PANE  # type: ignore[missing-import]

HEAVY_DIR = os.path.join(os.path.expanduser("~"), "factory")


NEVER_RUN = {"AskUserQuestion"}


def bash(command, cwd_rel="work", **extra):
    return dict(event="PreToolUse", tool="Bash", input={"command": command}, cwd_rel=cwd_rel, **extra)


def poll_loop_cases():
    studio = 'until ! pgrep -f "check --staged" >/dev/null || [ $(grep -c . status.log) -gt 400 ]; do sleep 5; done'
    rows = [
        ("studio-check", studio, True),
        ("studio-eval", "eval 'until python3 scripts/studio_merge.py status job; do sleep 20; done'", True),
        ("studio-zsh", 'zsh -c "for k in a b; do seat-run --wait $k; sleep 15; done"', True),
        ("bash-fraction", "bash -c 'while ready; do sleep 0.5; done'", True),
        ("outside", "sleep 5", False),
        ("outside-body", "while ready; do echo ready; done; sleep 5", False),
        ("minute", "while ready; do sleep 60; done", False),
        ("long", "until ready; do sleep 120.5; done", False),
        ("heredoc", "cat > x <<EOF\nwhile ready; do sleep 5; done\nEOF", False),
        ("quoted-heredoc", "cat > x <<'EOF'\nwhile ready; do sleep 5; done\nEOF", False),
        ("echo", "echo 'while ready; do sleep 5; done'", False),
        ("comment", "echo ready # while ready; do sleep 5; done", False),
        ("substitution", "echo $(until ready; do sleep 3; done)", True),
        ("nested", "while ready; do for k in a; do sleep 3; done; done", True),
    ]
    cases = [
        dict(bash(cmd), name="poll-loop-" + name, expect="allow", poll_warning=warn, poll_repeat=warn)
        for name, cmd, warn in rows
    ]
    cases.append(
        dict(
            event="PreToolUse",
            tool="Bash",
            input={"command": studio, "run_in_background": True},
            name="poll-loop-background",
            expect="allow",
            poll_warning=False,
        )
    )
    return cases


def builtin_cases():
    deny = "deny"
    cases = [
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
        *parity_offload.cases(bash),
        *poll_loop_cases(),
        dict(bash("cd sub && rm a1 a2"), name="allow-rm-cd-rewrite", expect="allow", setup="rmcd"),
        dict(bash("open -a Preview out.png"), name="deny-open-other-app", expect=deny),
        dict(bash("wslview https://x"), name="deny-wslview", expect=deny),
        dict(bash("flyctl deploy"), name="deny-flyctl", expect=deny),
        dict(bash("railway variables --json | jq 'keys'"), name="deny-railway-names", expect=deny),
        dict(
            bash("link-cli auth status --filter-output authenticated"), name="allow-link-cli-filtered", expect="allow"
        ),
        dict(bash("rm -rf /"), name="deny-dangerous-cmd", expect=deny),
        dict(bash("rm -rf $d"), name="deny-rm-dynamic-deny", expect=deny),
        dict(bash("launchctl kickstart -k gui/501/com.aneyman.agent-lb"), name="deny-agent-lb-bootout", expect=deny),
        dict(bash("grep -r needle ~/.agent-rails/x | head -1"), name="deny-wide-scan-fifo", expect=None),
        dict(bash("grep -r needle ~/.agent-rails/x"), name="rewrite-wide-scan-fifo", expect=None),
        dict(bash("rg needle /"), name="deny-wide-scan-root", expect=None),
        dict(bash("rg -l b-123 ~/.agent-rails"), name="deny-wide-scan-lanes", expect=None),
        dict(bash("link-cli auth status"), name="deny-link-cli-secret", expect=deny),
        dict(bash("railway variables --kv"), name="deny-railway-kv", expect=deny),
        dict(bash("railway run printenv"), name="deny-railway-printenv", expect=deny),
        dict(bash('open -a "Google Chrome" https://example.com'), name="deny-no-chrome", expect=deny),
        dict(bash("ssh pc 'start " + "chrome chrome://extensions'"), name="deny-no-chrome-pc", expect=deny),
        dict(bash("ssh pc chr'" + "ome'"), name="deny-no-chrome-pc-quote-split", expect=deny),
        dict(bash('codex exec "open" "-a" "Safari"'), name="allow-browser-brief-data", expect="allow"),
        dict(bash('git commit -m "open the room"'), name="allow-browser-commit-data", expect="allow"),
        dict(bash("tail ~/Library/Logs/x.log"), name="allow-browser-log-path", expect="allow"),
        dict(bash("page-shot https://app.rails.so"), name="allow-browser-page-shot", expect="allow"),
        dict(
            bash("python3 -c 'pw.chromium.launch(headless=True)'"), name="allow-browser-isolated-python", expect="allow"
        ),
        dict(
            bash("node -e 'pw.chromium.connectOverCDP(\"http://localhost:9222\")'"),
            name="deny-browser-cdp-attach",
            expect=deny,
        ),
        dict(bash("ssh pc 'dir'"), name="allow-ssh-pc", expect="allow"),
        dict(bash("gh pr merge 5 --squash"), name="deny-no-direct-merge", expect=None),
        dict(bash("open -a 'Herdr Shell'"), name="deny-herdr-shell-host", expect=None),
        dict(bash("cua do click 10 10"), name="deny-desktop-guard", expect=deny),
        dict(bash("rm -rf / && gh pr merge 1 --squash"), name="deny-two-guards", expect=deny),
        dict(
            bash("git stash push -m parity", cwd_rel="work/repo"), name="deny-stash-guard", expect=deny, setup="stash"
        ),
        dict(bash("git stash list", cwd_rel="work/repo"), name="allow-stash-list", expect="allow", setup="stash"),
        dict(bash("cat ~/.agent-rails/rails-host/host.credential"), name="deny-host-credential", expect=deny),
        dict(bash("ls ~/.agent-rails/rails-host"), name="allow-host-credential-ls", expect="allow"),
        dict(
            event="PreToolUse",
            tool="Bash",
            raw='{"tool_name":"Bash","tool_input":{"command":"rm -rf $d"',
            name="malformed-rm (rm-dynamic-deny fails closed)",
            expect=deny,
        ),
        dict(
            event="PreToolUse",
            tool="Bash",
            raw='{"tool_name":"Bash","tool_input":{"command":"cua do click"',
            name="malformed-cua (desktop-guard fails closed)",
            expect=deny,
        ),
        dict(
            event="PreToolUse",
            tool="Bash",
            raw="not json at all",
            name="malformed-plain (floor guards refuse)",
            expect=deny,
        ),
        dict(event="PreToolUse", tool="Bash", input={"command": 123}, name="non-string-command", expect=None),
        dict(
            event="PreToolUse",
            tool="Workflow",
            input={"script": "agent('do the thing')"},
            name="deny-relay-guard",
            expect=None,
        ),
        dict(
            event="PreToolUse",
            tool="Workflow",
            input={"script": "// RELAYED-REQUEST-GUARD\nagent('x')"},
            name="allow-relay-guard",
            expect=None,
        ),
        dict(
            event="PreToolUse",
            tool="mcp__aside__repl",
            input={"code": "await listBrowserTabs()"},
            name="allow-aside",
            expect="allow",
        ),
        dict(event="PreToolUse", tool="Read", input={"file_path": "/etc/hosts"}, name="allow-read", expect=None),
        dict(
            event="PostToolUse",
            tool="Bash",
            input={"command": "ls"},
            response={"stdout": "x"},
            name="post-bash-bulletin",
            expect=None,
            setup="bulletin",
            pane=True,
        ),
        dict(
            event="PostToolUse",
            tool="Bash",
            input={"command": "ls"},
            response={"stdout": "x"},
            name="post-bash-quiet",
            expect=None,
        ),
        dict(
            event="PostToolUse",
            tool="Edit",
            input={"file_path": "{run}/work/a.ts"},
            name="post-edit-no-prettier",
            expect=None,
            setup="edit",
        ),
        dict(
            event="PostToolUse",
            tool="Edit",
            input={"file_path": "{run}/work/pretty/a.ts"},
            name="post-edit-prettier-config",
            expect=None,
            setup="prettier",
        ),
        dict(
            event="PostToolUse",
            tool="Agent",
            input={"subagent_type": "Explore", "prompt": "x"},
            response={"status": "completed"},
            name="post-agent-idle",
            expect=None,
        ),
        dict(event="PostToolUse", tool="Skill", input={"skill": "jev"}, name="post-skill-stats", expect=None),
        dict(
            event="PostToolUse",
            tool="mcp__supabase__apply_migration",
            input={"name": "x"},
            name="post-supabase",
            expect=None,
        ),
        dict(event="UserPromptSubmit", prompt="parity test prompt", name="ups-quiet", expect=None),
        dict(
            event="UserPromptSubmit",
            prompt="parity test prompt",
            name="ups-bulletin-and-jev-alert",
            expect=None,
            setup="bulletin+jev",
            pane=True,
        ),
        dict(event="Stop", name="stop", expect=None),
    ]
    return cases


def setup_case(case, run):
    setup = case.get("setup") or ""
    work = os.path.join(run, "work")
    if "offload" in setup:
        parity_offload.setup(case, run)
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
        quiet["env"] = dict(
            os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z"
        )
        subprocess.run(["git", "init", "-q", repo], **quiet)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "base"], **quiet)
        subprocess.run(git + ["worktree", "add", "-q", os.path.join(work, "repo-wt"), "-b", "side"], **quiet)
    if "bulletin" in setup:
        row = {
            "id": "b-hookparity-1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "from": "hook-parity",
            "kind": "task",
            "topic": "",
            "text": "parity test bulletin",
            "ref": "",
            "to": [TEST_PANE],
            "named_to": [TEST_PANE],
        }
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
    body = {
        "session_id": "hook-parity",
        "transcript_path": os.path.join(run, "transcript.jsonl"),
        "cwd": cwd,
        "permission_mode": "bypassPermissions",
        "hook_event_name": case["event"],
    }
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
