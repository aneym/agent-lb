from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SEATS = {
    "explore",
    "general-purpose",
    "claude",
    "effort-low",
    "effort-xhigh",
    "opus-seat",
    "opus-seat-full",
    "opus-seat-local",
    "fork",
}
PRESERVED_SEATS = {
    "verifier",
    "verifier-local",
    "codex-verifier",
    "codex-verifier-local",
    "planner",
    "fable-orchestrator",
    "plan",
    "plan-reviewer",
    "security-reviewer",
}
BRAIN_CLASSES = {
    "plan",
    "lane",
    "scope",
    "spec",
    "contract",
    "architecture",
    "design",
    "review",
    "verdict",
    "judge",
    "audit",
    "synthesis",
    "lead",
    "ui-critical",
    "trust-boundary",
    "money-path",
    "judgment",
}
HANDS_CLASSES = {"implement", "mechanical", "fix", "land", "explore", "test", "probe", "sweep", "bulletin", "research"}
RESEARCH = re.compile(r"^(?:research|investigate|survey|compare)\b", re.IGNORECASE)
TAG = re.compile(r"^\s*\[class:([\w-]+)\]", re.IGNORECASE)
EXCEPTION = re.compile(
    r"^\s*OPUS-REASON:\s*(ui-critical|trust-boundary|money-path|twice-failed|judgment|contract)\b",
    re.IGNORECASE | re.MULTILINE,
)
BRAINS = re.compile(
    r"\b(?:lead|leader|leading|planner|planning|plan|plans|lane|scope|scoping|spec|specification|"
    r"contract|architecture|design|review|reviewer|verdict|judge|judgment|judgement|audit|synthesis|decision)\b",
    re.IGNORECASE,
)
HANDS = re.compile(
    r"studio_merge|\bfix-forward\b|\b(?:implement|fix|patch|commit|test|probe|reproduce|install|land|sweep|drain|bulletin)\b"
    r"|\bedit\s+(?:\S+\s+){0,3}(?:files?|[\w./-]+\.[\w]+)\b|\brun\s+\S+",
    re.IGNORECASE,
)
READ = re.compile(r"\b(?:read|find|trace|measure)\b", re.IGNORECASE)
EDIT = re.compile(r"\b(?:edit|fix|land|commit|implement|patch|install|studio_merge)\b", re.IGNORECASE)
STUDIO = re.compile(
    r"~/\.agent-rails/|/Users/[^/]+/\.agent-rails|~/\.claude/|/Users/[^/]+/\.claude|~/\.agent-lb"
    r"|studio_merge|127\.0\.0\.1|localhost:|log show|lane-post|agent-test-account|factory-install|plan-publish",
    re.IGNORECASE,
)


def lean_run(*args: str, prompt: str = "") -> subprocess.CompletedProcess | None:
    tool = os.environ.get("LEAN_STUDIO_BIN") or str(Path.home() / ".local" / "bin" / "lean-studio")
    try:
        return subprocess.run([tool, *args], input=prompt, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None


def local_warning(subagent: str, prompt: str) -> str | None:
    if not (subagent.endswith("-local") or "-local-" in subagent):
        return None
    done = lean_run("check-local", subagent, prompt=prompt)
    return (done.stdout.strip() or None) if done is not None and done.returncode == 1 else None


def lean_active() -> bool:
    import json

    done = lean_run("status")
    try:
        return done is not None and done.returncode == 0 and json.loads(done.stdout).get("active") is True
    except (ValueError, AttributeError):
        return False


def brief_header(prompt: str) -> tuple[str, str]:
    tag = ""
    for line in prompt.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("NEEDS:"):
            continue
        match = TAG.match(line)
        if match:
            tag = match.group(1).lower()
            line = line[match.end() :].strip()
            if not line:
                continue
        return tag, line
    return tag, ""


def preserve_seat(subagent: str, prompt: str) -> bool:
    tag, first = brief_header(prompt)
    return (
        subagent.lower() in PRESERVED_SEATS
        or tag in BRAIN_CLASSES
        or bool(EXCEPTION.search(prompt))
        or (
            tag not in HANDS_CLASSES
            and not HANDS.match(first)
            and not READ.match(first)
            and not RESEARCH.match(first)
            and bool(BRAINS.search(first))
        )
    )


def hands_warning(subagent: str, prompt: str) -> str | None:
    if (
        (subagent or "general-purpose").lower() in SEATS
        and os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") != "1"
        and not preserve_seat(subagent, prompt)
        and not hands_target(subagent, prompt)
    ):
        return "unclear whether this brief is hands work; keeping the requested seat unchanged"
    return None


def hands_target(subagent: str, prompt: str) -> tuple[str, str] | None:
    if (subagent or "general-purpose").lower() not in SEATS or os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") == "1":
        return None
    tag, first = brief_header(prompt)
    if preserve_seat(subagent, prompt):
        return None
    tagged = tag in HANDS_CLASSES
    research = tag == "research" or bool(RESEARCH.match(first))
    explore = subagent.lower() == "explore" or tag == "explore"
    if not (tagged or research or explore or HANDS.search(prompt[:3000])):
        return None
    if research:
        target = "codex-sol"
    elif explore or (READ.search(prompt[:3000]) and not EDIT.search(prompt[:3000])):
        target = "gpt-explorer"
    else:
        target = "gpt-implementer"
    if STUDIO.search(prompt) or re.search(r"\b(?:xcodebuild|simctl|herdr|aside|keychain|tcc)\b", prompt, re.IGNORECASE):
        target += "-local"
    return target, f"class:{tag}" if tagged else "hands markers without a brains marker"


def steer_input(tool_input: dict, target: str, prompt: str, anthropic_model: bool) -> dict:
    updated = dict(tool_input, subagent_type=target)
    if anthropic_model:
        updated.pop("model", None)
    if target.startswith("gpt-implementer") and not re.search(r"^﻿?[ \t]*NEEDS:", prompt, re.MULTILINE):
        land = "yes" if re.search(r"studio_merge|\bland\s", prompt, re.IGNORECASE) else "no"
        local = "yes" if target.endswith("-local") else "no"
        prompt = f"NEEDS: land={land} local={local}\n" + prompt
    updated["prompt"] = prompt
    return updated
