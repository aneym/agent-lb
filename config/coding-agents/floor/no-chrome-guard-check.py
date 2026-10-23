import argparse
import json
import os
import subprocess
from pathlib import Path


def cases():
    return [
        ("a", 2, 'open -a "Google Chrome" https://x'),
        (
            "b",
            2,
            '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Google/Chrome"',
        ),
        ("c", 2, 'osascript -e \'tell application "Safari" to open location "https://x"\''),
        ("d", 2, 'node -e \'require("playwright").chromium.connectOverCDP("http://localhost:9222")\''),
        (
            "e",
            0,
            "codex exec - <<'BRIEF'\nLibrary/Logs/factory-ff.error.log\nPlease open the room. Never launch Chrome or Safari.\nBRIEF",
        ),
        ("f", 0, 'git commit -m "open the room"'),
        ("g", 0, "tail ~/Library/Logs/x.log"),
        ("h", 0, "page-shot https://app.rails.so"),
        (
            "i",
            0,
            "LANE_DIR=$HOME/.agent-rails/lanes/shelf-nate-sim rails-browser --lane shelf-nate-sim signin --url https://app.rails.so",
        ),
        ("quoted-brief-argv", 0, 'codex exec "open" "-a" "Safari"'),
        ("quoted-brief-app", 0, 'codex exec "/Applications/Safari.app/Contents/MacOS/Safari"'),
        ("quoted-brief-profile", 0, 'codex exec "$HOME/Library/Application Support/Google/Chrome"'),
        ("commit-profile-text", 0, 'git commit -m "$HOME/Library/Application Support/Google/Chrome"'),
        (
            "launch-brief",
            0,
            'seat-run --bg --name verifier -- codex exec "Library/Logs/x.log; never open Chrome or Safari"',
        ),
        (
            "plist-log",
            0,
            "plutil -lint ~/Library/LaunchAgents/factory.plist && tail ~/Library/Logs/factory-ff.error.log",
        ),
        ("acct-browser", 0, "acct-browser start test-account"),
        ("headless-cli", 0, "chromium --headless --user-data-dir=/tmp/no-chrome-fresh-profile https://app.rails.so"),
        (
            "python-fresh",
            0,
            "python3 - <<'PY'\nfrom playwright.sync_api import sync_playwright\nwith sync_playwright() as pw:\n    browser = pw.chromium.launch(headless=True)\nPY",
        ),
        (
            "python-test-account-state",
            0,
            "python3 -c 'b = pw.chromium.launch(headless=True); ctx = b.new_context(storage_state=\"/tmp/agent-test-account/web-state.json\")'",
        ),
        (
            "python-owner-state",
            2,
            "python3 -c 'b = pw.chromium.launch(headless=True); ctx = b.new_context(storage_state=\"~/Library/Application Support/Google/Chrome/state.json\")'",
        ),
        (
            "python-unknown-state",
            2,
            "python3 -c 'b = pw.chromium.launch(headless=True); ctx = b.new_context(storage_state=state)'",
        ),
        (
            "python-unknown-browser",
            2,
            "python3 -c 'ctx = b.new_context(storage_state=\"/tmp/agent-test-account/web-state.json\")'",
        ),
        (
            "python-heredoc-copy",
            2,
            "cp -R /tmp/input /tmp/python-profile && python3 - <<'PY'\nctx = pw.chromium.launch_persistent_context(\"/tmp/python-profile\", headless=True)\nPY",
        ),
        (
            "python-fresh-profile",
            0,
            "python3 -c 'ctx = pw.chromium.launch_persistent_context(\"/tmp/agent-test-account-fresh\", headless=True)'",
        ),
        (
            "optional-verifier-wrapper",
            0,
            'vs=(); vs=("$HOME/.local/bin/verify-slot" codex-verifier --); seat-run --bg --name proof -- ${vs[@]+"${vs[@]}"} codex exec "open Chrome Safari"',
        ),
        (
            "optional-owner-wrapper",
            2,
            'vs=(open -a Safari); seat-run --bg --name proof -- ${vs[@]+"${vs[@]}"} codex exec "open Chrome Safari"',
        ),
        ("uv-python-cdp", 2, "uv run python3 -c 'pw.chromium.connect_over_cdp(\"http://localhost:9222\")'"),
        ("uv-codex-profile-data", 0, 'uv run --no-sync codex exec "$HOME/Library/Application Support/Google/Chrome"'),
        (
            "uv-python-heredoc",
            2,
            "uv run --no-sync python3 - <<'PY'\npw.chromium.connect_over_cdp('http://localhost:9222')\nPY",
        ),
        ("uv-codex-data", 0, 'uv run codex exec "/Applications/Safari.app/Contents/MacOS/Safari"'),
        ("unknown-brief-data", 0, 'verifier --brief "open" "-a" "Safari"'),
        ("wrapped-browser", 2, "launcher chromium --user-data-dir=/tmp/not-headless"),
        ("unknown-wrapper", 2, 'seat-run --bg --name proof -- ${vs[@]+"${vs[@]}"} codex exec "open Chrome Safari"'),
        ("seat-run-launch", 2, "seat-run --bg --name proof -- open -a Safari"),
        (
            "seat-run-data",
            0,
            'seat-run --bg --name proof -- codex exec "/Applications/Safari.app/Contents/MacOS/Safari"',
        ),
        ("xcrun-launch", 2, "xcrun open -a Safari"),
        (
            "python-copy-profile",
            2,
            "cp -R /tmp/input /tmp/python-profile && python3 -c 'ctx = pw.chromium.launch_persistent_context(\"/tmp/python-profile\", headless=True)'",
        ),
        ("default-profile", 2, 'chromium --headless --user-data-dir="$HOME/Library/Application Support/Google/Chrome"'),
        (
            "profile-copy",
            2,
            'cp -R "$HOME/Library/Application Support/Google/Chrome" /tmp/profile && chromium --headless --user-data-dir=/tmp/profile',
        ),
        ("profile-cookie", 2, 'cat "$HOME/Library/Application Support/Google/Chrome/Default/Cookies"'),
        (
            "python-owner-profile",
            2,
            "python3 -c 'ctx = pw.chromium.launch_persistent_context(\"~/Library/Application Support/Google/Chrome\", headless=True)'",
        ),
        (
            "python-dynamic-profile",
            2,
            "python3 -c 'ctx = pw.chromium.launch_persistent_context(profile, headless=True)'",
        ),
        (
            "python-extra-args",
            2,
            "python3 -c 'browser = pw.chromium.launch(headless=True, args=[\"--user-data-dir=owner\"])'",
        ),
        ("python-cdp", 2, "python3 -c 'pw.chromium.connect_over_cdp(\"http://localhost:9222\")'"),
        (
            "python-fresh-then-owner",
            2,
            "python3 -c 'b = pw.chromium.launch(headless=True); pw.chromium.connect_over_cdp(\"http://localhost:9222\")'",
        ),
        ("python-subprocess", 2, 'python3 -c \'import subprocess; subprocess.run(["open", "-a", "Safari"])\''),
        ("heredoc-shell", 2, "bash <<'CODE'\nopen -a Safari\nCODE"),
        (
            "heredoc-code",
            2,
            "node <<'CODE'\nrequire('playwright').chromium.connectOverCDP('http://localhost:9222')\nCODE",
        ),
        ("heredoc-substitution", 2, "codex exec - <<BRIEF\n$(open -a Safari)\nBRIEF"),
        ("quoted-substitution", 2, 'codex exec "$(open -a Safari)"'),
        ("after-data", 2, 'codex exec "never open Safari"; open -a Safari'),
        ("shell-wrapper", 2, "bash -lc 'open -a Safari'"),
        ("arch-wrapper", 2, "arch -arm64 open -a Safari"),
        ("split-name", 2, "o\\pen -a Saf''ari https://x"),
        ("owner-aside", 2, "open -a Aside https://x"),
        ("owner-firefox-applescript", 2, "osascript -e 'tell application \"Firefox\" to activate'"),
        ("owner-brave-applescript", 2, "osascript -e 'tell application \"Brave Browser\" to activate'"),
        ("owner-edge-bundle", 2, "open -b com.microsoft.edgemac https://x"),
        ("owner-firefox-bundle", 2, "open -b org.mozilla.firefox"),
        ("owner-firefox", 2, "open -a Firefox https://x"),
        ("owner-brave", 2, 'open -a "Brave Browser" https://x'),
        ("owner-edge", 2, 'open -a "Microsoft Edge" https://x'),
        ("pc-browser", 2, "ssh pc 'start chrome'"),
        ("broken-quote", 2, 'codex exec "Library open Chrome'),
        ("broken-heredoc", 2, "codex exec - <<'BRIEF'\nLibrary open Chrome\n"),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hook", type=Path, default=Path(__file__).resolve().parents[1] / "hooks/no-chrome-guard.sh")
    args = parser.parse_args()
    failed = []
    env = dict(os.environ, HOOK_FLOOR_RECEIPT="1")
    for name, expected, command in cases():
        result = subprocess.run(
            ["/bin/bash", str(args.hook)],
            input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
        )
        ok = result.returncode == expected
        ok = ok and (
            "floor-ok no-chrome-guard.sh" in result.stdout if expected == 0 else "BLOCKED (floor" in result.stderr
        )
        print(f"{name}: {'ALLOWED' if result.returncode == 0 else 'REFUSED'} ({'PASS' if ok else 'FAIL'})")
        if not ok:
            failed.append((name, result.returncode, result.stderr))
    invalid_inputs = (
        "{not json",
        '{"tool_input":{"command":123}}',
        '{"tool_input":[]}',
        '{"tool_input":{"command":false}}',
        '{"tool_input":false}',
        json.dumps({"tool_input": {"command": "ls\x00"}}),
    )
    for payload in invalid_inputs:
        result = subprocess.run(
            ["/bin/bash", str(args.hook)], input=payload, text=True, capture_output=True, timeout=30, env=env
        )
        if result.returncode != 2 or "fails closed" not in result.stderr:
            failed.append(("invalid-input", result.returncode, result.stderr))
    print(json.dumps({"cases": len(cases()) + len(invalid_inputs), "failures": failed}))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
