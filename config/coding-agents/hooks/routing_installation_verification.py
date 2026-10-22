from __future__ import annotations  # noqa: I001

# fmt: off

import argparse
import fnmatch
import json
import os
import re
from pathlib import Path

PARSER = argparse.ArgumentParser(description="Verify coding-agent routing policy")
PARSER.add_argument("--source-only", action="store_true", help="Check source without installed policy or an LB")
SOURCE_ONLY = PARSER.parse_args().source_only
HOME = Path(os.environ.get("AGENT_LB_USER_HOME", Path.home()))
SOURCE = Path(__file__).resolve().parent
SOURCE = SOURCE.parent
CANONICAL = HOME / ".agents" / "policy" / "coding-agents"
MARKER_START = "<!-- agent-lb:coding-agent-routing:start -->"
MARKER_END = "<!-- agent-lb:coding-agent-routing:end -->"
FORBIDDEN = ("fable", "claude-planner", "astra")

MODEL_PIN = re.compile(r"(?:--model[ =]+|\bmodel\s*[:=]\s*|\bmodel\s+)[`'\"]?([A-Za-z0-9][\w.\-\[\]*]*)", re.IGNORECASE)
GPT_ID = re.compile(r"^gpt-(\d+(?:\.\d+)*)-([a-z]+)$")
from policy_verification import MANAGED  # type: ignore[missing-import]

failures: list[str] = []



def check(condition: bool, message: str) -> None:
    print(f"{'PASS' if condition else 'FAIL'} {message}")
    if not condition:
        failures.append(message)


def read(path: Path) -> str:
    return path.read_text() if path.is_file() else ""


def forbidden(model: str) -> bool:
    return any(marker in model.lower() for marker in FORBIDDEN)


def unpinned(text: str) -> str:
    return re.sub(r"(?m)^(model|effort):.*\n?", "", text)


def readmitted(seat: str) -> tuple:

    if seat == "fable-orchestrator":
        return ("claude-fable-*", "fable-latest*")
    try:
        entry = (json.loads(read(SOURCE / "routing-table.json") or "{}").get("readmitted") or {}).get(seat)
    except (json.JSONDecodeError, AttributeError):
        return ()
    if not isinstance(entry, dict):
        return ()
    return (*(entry.get("patterns") or ()), *((entry["alias"] + "*",) if entry.get("alias") else ()))


def frontmatter_model(text: str) -> str | None:
    head = text.split("\n---", 1)[0]
    for line in head.splitlines():
        if line.startswith("model:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


settings = {}
if not SOURCE_ONLY:

    for name in ("claude-adapter.md", "routing-table.json"):
        check(read(CANONICAL / name) == read(SOURCE / name) != "", f"{CANONICAL / name} matches {SOURCE / name}")

    for section in sorted((SOURCE / "routing").glob("*.md")):
        check(read(HOME / ".claude/docs/models" / section.name) == read(section), f"installed routing section matches {section.name}")
    claude_doc = HOME / ".claude" / "CLAUDE.md"
    claude_text = read(claude_doc)
    check(
        claude_text.count(MARKER_START) == 1 and claude_text.count(MARKER_END) == 1,
        f"{claude_doc} has exactly one managed routing block",
    )
    check(read(SOURCE / "claude-adapter.md").strip() in claude_text, f"{claude_doc} routing block matches the adapter")
    check(
        MARKER_START not in read(HOME / ".codex" / "AGENTS.md"),
        "~/.codex/AGENTS.md carries no managed routing block",
    )

    settings_path = HOME / ".claude" / "settings.json"
    try:
        settings = json.loads(read(settings_path) or "{}")
    except json.JSONDecodeError:
        settings = {}
        check(False, f"{settings_path} contains valid JSON")
    check(settings.get("model") == "opus", "direct Claude Code default model is opus (Opus 5.5)")
    check(settings.get("effortLevel") == "medium", "direct Claude Code default effort is medium")
    sonnet_pin = (settings.get("env") or {}).get("ANTHROPIC_DEFAULT_SONNET_MODEL") if isinstance(settings.get("env"), dict) else None
    check(isinstance(sonnet_pin, str) and re.fullmatch(r"claude-sonnet-\d+(?:-\d+)?", sonnet_pin) is not None,
          f"settings.json pins Claude Code's sonnet alias (ANTHROPIC_DEFAULT_SONNET_MODEL={sonnet_pin})")
    guard_registered = any(
        "seat-guard.py" in hook.get("command", "")
        for group in settings.get("hooks", {}).get("PreToolUse", [])
        if group.get("matcher") == "Agent"
        for hook in group.get("hooks", [])
    )
    check(guard_registered, "seat guard is registered as the Agent PreToolUse hook")


    for name, installed, source in MANAGED:
        check(read(SOURCE / source) != "" and unpinned(read(HOME / installed)) == unpinned(read(SOURCE / source)),
              f"{installed} matches {source} apart from snapshot-resolved model and effort")
        owner = HOME / ".agent-lb" / "managed" / "coding-agents" / name
        check(read(owner) == f"agent-lb:{name}:v1\n", f"{owner} records installer ownership")

    for retired_seat in ("astra", "implementer"):
        installed = (HOME / ".claude" / "agents" / f"{retired_seat}.md").exists()
        check(not installed, f"the retired {retired_seat} seat is not installed")


    for path in sorted((HOME / ".claude" / "agents").glob("*.md")):
        model = frontmatter_model(read(path)) or ""
        allowed = any(fnmatch.fnmatchcase(model.lower(), pattern) for pattern in readmitted(path.stem))
        check(not forbidden(model) or allowed, f"{path.name} does not pin a Fable model ({model or 'unset'})")
for name in ("planner", "plan-reviewer", "frontend-designer"):
    model = frontmatter_model(read(SOURCE / "agents" / f"{name}.md"))
    check(model == "opus", f"{name} runs on the newest Opus via `model: opus` (got {model})")
check(
    'seat-exec --seat codex-verifier --effort "$dispatch_effort"' in read(SOURCE / "agents" / "codex-verifier.md"),
    "codex-verifier forwards to its seat at dispatch-selected effort",
)


try:
    source_table = json.loads(read(SOURCE / "routing-table.json") or "{}")
except json.JSONDecodeError:
    source_table = {}
    check(False, "routing tables are valid JSON")
if not SOURCE_ONLY:
    table_path = HOME / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    try:
        live_table = json.loads(read(table_path) or "{}")
    except json.JSONDecodeError:
        live_table = {}
        check(False, "routing tables are valid JSON")
    strip = lambda table: {key: value for key, value in table.items() if key != "overrides"}  # noqa: E731
    check(bool(source_table) and strip(live_table) == strip(source_table), f"{table_path} matches the source table")
classes = source_table.get("classes", {})
chain_models = [entry.get("model", "") for spec in classes.values() for entry in spec.get("chain", [])]
check(bool(chain_models) and not any(forbidden(m) for m in chain_models), "no routing-table class runs on Fable")
check(not any(forbidden(key) for key in source_table.get("pools", {})), "no routing-table pool maps a Fable model")

if not SOURCE_ONLY:
    chain_seats = sorted({entry.get("seat", "") for spec in classes.values() for entry in spec.get("chain", [])}
        | set(source_table.get("seats", {})))
    missing_seats = [seat for seat in chain_seats if not (HOME / ".claude" / "agents" / f"{seat}.md").is_file()]
    check(
        not missing_seats,
        "every routing-table seat has a definition in ~/.claude/agents"
        + (f": missing {missing_seats}" if missing_seats else ""),
    )
plan_chain = [entry.get("model") for entry in classes.get("plan", {}).get("chain", [])]
check(
    plan_chain == ["opus-latest"], f"plan uses only opus-latest, without a Sol fallback ({plan_chain})"
)
for name in ("implement", "mechanical"):
    spec = classes.get(name, {})
    unpaced_opus = [
        entry for entry in spec.get("chain", []) if entry.get("model") == "opus-latest" and "min_pace" not in entry
    ]
    check(not unpaced_opus, f"{name} puts Opus only behind a pace gate (min_pace), else on cheaper seats")
    audit = spec.get("audit", {})
    by_vendor = audit.get("by_author_vendor", {})
    check(
        audit.get("required") is False
        and audit.get("mode") == "on_request"
        and by_vendor.get("anthropic", {}).get("model") == "sol-latest"
        and all(
            by_vendor.get(v, {}).get("model") == "sonnet-latest"
            for v in ("openai", "cursor", "devin", "glm", "kimi")
        ),
        f"{name} names its on-request cross-vendor auditors (Sonnet / newest Sol); review is optional pre-launch",
    )
