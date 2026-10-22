#!/usr/bin/env python3
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks"
MESSAGE = "hands-nudge (warn only): 15 hands-shaped Bash calls in this Opus lead; send builds, tests, probes and landing to gpt-implementer (seat-run) and keep Opus for decisions (models.md, Teams and workflows)."


def main():
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        transcript = home / "transcript.jsonl"
        env = dict(os.environ, HOME=directory, ROUTE_TABLE=str(HOOKS.parent / "routing-table.json"))
        env.pop("SEAT_GUARD_ALLOW_ANTHROPIC", None)
        times = []

        def model(name):
            transcript.write_text(json.dumps({"type": "assistant", "message": {"model": name}}) + "\n")

        def run(payload, hook="claude-bash-hands-nudge.py"):
            started = time.perf_counter()
            result = subprocess.run(
                [sys.executable, str(HOOKS / hook)],
                input=payload if isinstance(payload, str) else json.dumps(payload),
                text=True,
                capture_output=True,
                env=env,
                timeout=3,
            )
            if hook == "claude-bash-hands-nudge.py":
                times.append(time.perf_counter() - started)
            assert result.returncode == 0 and not result.stderr, result
            output = json.loads(result.stdout) if result.stdout else {}
            specific = output.get("hookSpecificOutput", {})
            assert "permissionDecision" not in specific, output
            return specific.get("additionalContext", "")

        def payload(session="lead", command="pytest scripts/checks", **extra):
            return dict(
                session_id=session,
                transcript_path=str(transcript),
                tool_name="Bash",
                tool_input={"command": command},
                **extra,
            )

        model("claude-opus-5-5")
        for _ in range(14):
            assert run(payload()) == ""
        assert run(payload()) == MESSAGE
        for _ in range(15):
            assert run(payload()) == ""
        print("PASS Opus: silent at 1-14, exact warning at 15, cooldown at 16-30")
        for name in ("claude-sonnet-4-6", "gpt-6.1-sol", "unknown"):
            model(name)
            for _ in range(16):
                assert run(payload(name)) == ""
            assert not (home / ".cache/claude-bash-hands-nudge" / name).exists()
        model("claude-opus-5-5")
        for extra in ({"agent_id": "worker"}, {"agent_type": "general-purpose"}):
            for _ in range(16):
                assert run(payload("subagent", **extra)) == ""
        for command in (
            "seat-run --seat gpt-implementer --prompt-file task",
            "codex exec task",
            "route resolve gpt-implementer",
            "factory-wait --repo agent-lb",
            "lane-post read abc",
            "git status",
            "git log -1",
            "git show HEAD",
            "git diff",
            "ls",
            "cat source.py",
            "rg pattern .",
            "OPUS-REASON=judgment pytest",
        ):
            assert run(payload("excluded", command)) == ""
        assert not (home / ".cache/claude-bash-hands-nudge/excluded").exists()
        for raw in ("{", "null", "[]", '{"tool_input":null}', '{"session_id":"../escape"}'):
            assert run(raw) == ""
        print("PASS other models, subagents, dispatch/read commands, exceptions and malformed input: silent")
        state_path = home / ".cache/claude-bash-hands-nudge/lead"
        state = json.loads(state_path.read_text())
        state.update(last=time.time() - 1801, warned=time.time() - 7201)
        state_path.write_text(json.dumps(state))
        assert run(payload()) == ""
        assert json.loads(state_path.read_text())["count"] == 1
        for _ in range(13):
            assert run(payload()) == ""
        assert run(payload()) == MESSAGE
        model("fable-5")
        for _ in range(14):
            assert run(payload("fable")) == ""
        assert run(payload("fable")) == MESSAGE
        transcript.write_text(
            json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5"}})
            + "\n"
            + json.dumps({"type": "user", "message": "x" * 70000})
            + "\n"
        )
        assert run(payload("old-model")) == ""
        print("PASS inactivity reset, cooldown expiry, Fable and bounded transcript tail")
        for command in (
            "psql db -c 'select 1'",
            "playwright test",
            "curl https://example.invalid",
            "node script.js",
            "npx playwright test",
            "pnpm build",
            "npm run build",
            "uv run app.py",
            "python3 scripts/probe.py",
            "make build",
            "git rebase origin/main",
            "git commit -m test",
            "git push origin/main",
        ):
            model("claude-opus-5-5")
            assert run(payload("shapes", command)) == ""
        assert json.loads((home / ".cache/claude-bash-hands-nudge/shapes").read_text())["count"] == 13
        for seat in ("general-purpose", "claude", "opus-seat-custom", "effort-low", "effort-xhigh", "gpt-implementer"):
            for exception in (False, True):
                prompt = (
                    "OPUS-REASON: judgment\n" if exception else ""
                ) + "Implement the file split, edit source.py and run checks."
                script = f"await agent({json.dumps(prompt)}, {{agentType: {json.dumps(seat)}}});"
                text = run({"tool_input": {"script": script}}, "workflow-seat-guard.py")
                if seat == "gpt-implementer" or exception:
                    assert not text, text
                else:
                    assert "hands work" in text and "warn only" in text, text
        print("PASS Workflow: mechanical Claude seats warn; OPUS-REASON and GPT seat silent")
        baseline = []
        measured = []
        model("claude-opus-5-5")
        for _ in range(15):
            started = time.perf_counter()
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-c", "pass"],
                input="",
                text=True,
                capture_output=True,
                env=env,
                timeout=3,
            )
            baseline.append(time.perf_counter() - started)
            assert result.returncode == 0 and not result.stderr, result
            run(payload("timing"))
            measured.append(times[-1])
        overhead = statistics.median(measured) - statistics.median(baseline)
        assert overhead < 0.03, f"hook median minus empty interpreter median {overhead:.6f}s >= 30ms"
        print(f"PASS hook incremental median {overhead * 1000:.2f}ms < 30ms (15 paired samples)")


if __name__ == "__main__":
    main()
