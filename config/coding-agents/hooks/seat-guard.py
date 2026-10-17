#!/usr/bin/env python3


from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


FORWARDER_SEATS = {
    "codex-sol",
    "codex-verifier",
    "codex-test-runner",
    "computer-use",
    "cursor-seat",
    "devin-seat",
    "gpt-implementer",
    "gpt-explorer",
    "sol-consult",
    "astra-consult",
}


SUBMIT_SEATS = {"gpt-implementer", "sonnet-implementer"}
WORKTREE_BRIEF = re.compile(r"worktree|-wt/", re.IGNORECASE)
NEEDS_LINE = re.compile(r"^\ufeff?[ \t]*NEEDS:", re.MULTILINE)
STUDIO_ONLY = re.compile(r"^\ufeff?[ \t]*PLACEMENT:[ \t]*studio-only(?![\w-])", re.MULTILINE)
NEEDS_MISSING = (
    "no NEEDS line: add one line `NEEDS: land=yes|no local=yes|no tools=a,b paths=/x,/y os=linux|mac` "
    "(land and local required); without it seat-submit treats the brief as needing nothing Mac-only and "
    "places it on the roomiest reachable box, never on Studio; with no box free it waits, then holds the "
    "contract for a retry"
)
ANTHROPIC_MODEL_MARKERS = ("opus", "sonnet", "fable", "haiku", "claude")
SNAPSHOT_MAX_AGE_SECONDS = 600
SNAPSHOT_MAX_FUTURE_SECONDS = 60
CLASS_TAG = re.compile(r"^\s*\[class:([a-z0-9_-]+)\]", re.IGNORECASE)
AGENT_MODEL = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)
DEFAULT_RETIRED = ("claude-fable-*", "fable", "claude-planner", "gpt-*-astra", "gpt-*-astra-*", "gpt-5.6*", "gpt-5.5*")
DEFAULT_BLOCKED = ("claude-planner", "gpt-5.4*", "gpt-5-*", "gpt-5")

BRIEF_CLASSES = {"scope", "spec", "contract", "design"}
BRIEF = re.compile(
    r"\b(?:write|draft|author|produce|rewrite)\s+(?:the\s+|a\s+|an\s+|this\s+|its\s+)?(?:[\w-]+\s+)?"
    r"(?:scopes?|specs?|specifications?|contracts?|design\s+(?:briefs?|specs?|docs?))\b"
    r"|\b(?:scope|spec|contract|design)\s+brief\b",
    re.IGNORECASE,
)
SOL_SEATS = {"codex-sol", "gpt-implementer", "sol-consult", "gpt-explorer", "luna-implementer"}
SOL_MODEL = re.compile(r"(?:^|[-/_])(?:sol|luna|gpt)(?:$|[-_.\d])", re.IGNORECASE)
MODEL_PIN = re.compile(r"(?:--model[ =]+|\bmodel\s*[:=]\s*|\bmodel\s+)[`'\"]?([A-Za-z0-9][\w.\-\[\]*]*)", re.IGNORECASE)


def emit_advisory(advisory: str, extra: str = "") -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "seat-guard advisory: " + advisory + ". "
                        "Capacity is status, not admission control. Check "
                        "`route pools` or `agent-lb status --provider anthropic --json`."
                        + ("\n" + extra if extra else "")
                    ),
                }
            }
        )
    )


def emit_warning(reason: str, extra: str = "") -> None:

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "seat-guard (warn only, not blocked): " + reason + "; logged in the routing ledger. "
                        "Default work names a family alias (`route pick <class>`)." + ("\n" + extra if extra else "")
                    ),
                }
            }
        )
    )


def emit_context(text: str, extra: str = "") -> None:
    text += "\n" + extra if extra else ""
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))


def is_brief(prompt: str) -> bool:
    tag = CLASS_TAG.match(prompt)
    if tag:
        return tag.group(1).lower() in BRIEF_CLASSES
    return bool(BRIEF.search(prompt[:600]))


def sol_choice(subagent: str, model: str) -> bool:
    return subagent in SOL_SEATS or bool(model and SOL_MODEL.search(model))


def retired_patterns(table: Path, subagent: str = "") -> tuple:

    try:
        loaded = json.loads(table.read_text())
    except Exception:
        return DEFAULT_RETIRED
    configured = loaded.get("retired") if isinstance(loaded, dict) else None
    if not (isinstance(configured, list) and configured and all(isinstance(item, str) for item in configured)):
        return DEFAULT_RETIRED
    readmitted = loaded.get("readmitted") if isinstance(loaded.get("readmitted"), dict) else {}

    names = [
        entry["alias"] + "*"
        for entry in readmitted.values()
        if isinstance(entry, dict) and isinstance(entry.get("alias"), str) and entry["alias"]
    ]
    entry = readmitted.get(subagent) if subagent else None
    exempt = list(entry.get("patterns") or []) if isinstance(entry, dict) else []
    if isinstance(entry, dict) and isinstance(entry.get("alias"), str) and entry["alias"]:
        exempt.append(entry["alias"] + "*")
    return tuple(item for item in (*configured, *names) if item not in exempt)


def blocked_patterns(table: Path) -> tuple:

    try:
        configured = json.loads(table.read_text()).get("blocked")
    except Exception:
        return DEFAULT_BLOCKED
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return tuple(configured)
    return DEFAULT_BLOCKED


def forbidden_model(model: str, patterns: tuple = DEFAULT_RETIRED) -> bool:
    bare = model.strip().strip("`'\"").lower().split("[", 1)[0]
    return bool(bare) and any(fnmatch.fnmatchcase(bare, pattern) for pattern in patterns)


REMOVAL_CONTEXT = re.compile(
    r"\b(?:remove|removes|removing|replace|replaces|replacing|retire|drop|delete|strip|"
    r"instead of|no longer|never|used to|avoid|avoids|forbid|forbids|ban|bans)\b[^.;:!?\n]{0,40}$",
    re.IGNORECASE,
)
PROHIBITION = re.compile(r"(?:\b(?:do not|don't|dont|never|must not|should not|avoid)\s+use)\s*$", re.IGNORECASE)
CLAUSE_BREAK = re.compile(r"[.;:!?\n]|\b(?:use|set|pass|switch to|then)\b", re.IGNORECASE)


def retired_pins(prompt: str, patterns: tuple) -> list:
    found = []
    for match in MODEL_PIN.finditer(prompt):
        candidate = match.group(1).rstrip(".,;:)")
        if not forbidden_model(candidate, patterns) or candidate in found:
            continue
        before = prompt[max(0, match.start() - 80) : match.start()]
        breaks = list(CLAUSE_BREAK.finditer(before))
        clause = before[breaks[-1].end() :] if breaks else before
        if REMOVAL_CONTEXT.search(clause):
            continue

        if breaks and PROHIBITION.search(before[: breaks[-1].end()]):
            continue
        found.append(candidate)
    return found


def definition_model(subagent: str, agents_dir: Path) -> str | None:
    if not subagent or "/" in subagent or subagent.startswith("."):
        return None
    for path in agents_dir.glob("*.md"):
        if path.stem.lower() != subagent:
            continue
        try:
            head = path.read_text().split("\n---", 1)[0]
        except OSError:
            return None
        match = AGENT_MODEL.search(head)
        return match.group(1).strip().strip("'\"").lower() if match else None
    return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def task_class(prompt: str, subagent: str, table: Path) -> str | None:
    match = CLASS_TAG.match(prompt or "")
    if match:
        return match.group(1).lower()
    if not subagent:
        return None
    try:
        classes = json.loads(table.read_text()).get("classes") or {}
    except Exception:
        return None
    fallback = None
    for name, spec in classes.items():
        if not isinstance(spec, dict):
            continue
        seats = [str(entry.get("seat") or "") for entry in (spec.get("chain") or []) if isinstance(entry, dict)]
        if seats and seats[0].lower() == subagent:
            return name
        if fallback is None and subagent in [seat.lower() for seat in seats]:
            fallback = name
    return fallback


def append(record: dict, ledger: Path) -> bool:
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return False
    return True


def is_anthropic_model(model: str) -> bool:
    return any(marker in model for marker in ANTHROPIC_MODEL_MARKERS)


def snapshot_advisory(snapshot_path: Path) -> str | None:
    try:
        snapshot = json.loads(snapshot_path.read_text())
    except FileNotFoundError:
        return "missing snapshot"
    except (OSError, json.JSONDecodeError):
        return "unparseable snapshot"
    try:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not an object")
        polled_at = str(snapshot["polled_at"])
        observed_at = datetime.fromisoformat(polled_at[:-1] + "+00:00" if polled_at.endswith("Z") else polled_at)
        if observed_at.tzinfo is None:
            raise ValueError("polled_at is not timezone-aware")
        age_seconds = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -SNAPSHOT_MAX_FUTURE_SECONDS:
            return "snapshot dated in the future"
        if age_seconds > SNAPSHOT_MAX_AGE_SECONDS:
            return "stale snapshot"
        if snapshot["reachable"] is not True:
            return "snapshot reports unreachable"
        if snapshot["providers"]["anthropic"]["usable_count"] < 2:
            return "anthropic usable_count < 2"
    except Exception:
        return "internal error reading snapshot"
    return None


def emit_refusal(why: str) -> None:

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "seat-guard: " + why + ", so this dispatch is refused (the seat guard "
                    "fails closed). Retry the dispatch; if it repeats, check ~/.claude/hooks/seat-guard.py.",
                }
            }
        )
    )


def lean_studio_refusal(subagent: str, prompt: str) -> str | None:
    if not (subagent.endswith("-local") or "-local-" in subagent):
        return None
    tool = os.environ.get("LEAN_STUDIO_BIN") or str(Path.home() / ".local" / "bin" / "lean-studio")
    if not os.access(tool, os.X_OK):
        return None
    try:
        done = subprocess.run([tool, "check-local", subagent], input=prompt, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 1 else None


def emit_lean_refusal(why: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "seat-guard: " + why,
                }
            }
        )
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError as error:
        emit_refusal(f"could not read the Agent hook input (not JSON: {error})")
        return
    if not isinstance(payload, dict):
        emit_refusal("could not read the Agent hook input (not a JSON object)")
        return
    if payload.get("tool_name") != "Agent":
        emit_advisory("invalid Agent hook input; dispatch was not blocked")
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or any(
        field in tool_input and not isinstance(tool_input[field], str) for field in ("subagent_type", "model")
    ):
        emit_refusal("could not read the Agent hook input (tool_input is not an object of text fields)")
        return
    subagent = (tool_input.get("subagent_type") or "").strip().lower()
    model = (tool_input.get("model") or "").strip().lower()
    prompt = str(tool_input.get("prompt") or "")
    ledger = Path(
        os.environ.get("ROUTE_LEDGER")
        or os.environ.get("DISPATCH_LEDGER")
        or Path.home() / ".claude" / "logs" / "dispatch.jsonl"
    )
    table = Path(
        os.environ.get("ROUTING_TABLE")
        or os.environ.get("ROUTE_TABLE")
        or Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    )
    record = {
        "ts": now(),
        "event": "dispatch",
        "session_id": payload.get("session_id"),
        "subagent_type": subagent or None,
        "model": model or None,
        "name": str(tool_input.get("name") or "") or None,
        "task_class": task_class(prompt, subagent, table),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "cwd": payload.get("cwd"),
    }
    open_ended = not re.search(r"^(?:One check:|check:)", prompt, re.IGNORECASE | re.MULTILINE) and re.search(
        r"fix (every|all|each) (gap|issue|bug|problem)s?|end to end and fix|until (it|everything) (works|passes)",
        prompt,
        re.IGNORECASE,
    )
    budget_advisory = (
        "seat-guard advisory: open-ended brief; name one check and a bounded deliverable, "
        "or the seat runs as a coordinator"
        if open_ended
        else ""
    )
    advisories: list[str] = []
    if (
        subagent in SUBMIT_SEATS
        and WORKTREE_BRIEF.search(prompt)
        and not NEEDS_LINE.search(prompt)
        and not STUDIO_ONLY.search(prompt)
    ):
        record["needs_missing"] = True
        advisories.append(NEEDS_MISSING)
    agents_dir = Path(os.environ.get("SEAT_GUARD_AGENTS_DIR") or Path.home() / ".claude" / "agents")
    pinned = definition_model(subagent, agents_dir)
    retired = retired_patterns(table, subagent)
    blocked = blocked_patterns(table)
    brief_pins = retired_pins(prompt, retired)
    blocked_pins = retired_pins(prompt, blocked)
    warn_reason = None

    if forbidden_model(model, blocked):
        warn_reason = f"this dispatch pins {model!r}, which is no longer served"
    elif not model and pinned and forbidden_model(pinned, blocked):
        warn_reason = f"subagent type {subagent!r} is defined on {pinned!r}, which is no longer served"
    elif not model and pinned and forbidden_model(pinned, retired) and pinned not in [p.lower() for p in brief_pins]:
        warn_reason = (
            f"subagent type {subagent!r} is defined on the retired model {pinned!r} and nothing asked "
            "for it; name the model on the dispatch or in the brief to request it"
        )
    explicit = [{"model": model, "source": "dispatch"}] if model and forbidden_model(model, retired) else []
    explicit += [
        {"model": pin, "source": "brief", **({"blocked": True} if pin in blocked_pins else {})}
        for pin in dict.fromkeys(brief_pins + blocked_pins)
    ]
    if explicit and not warn_reason:
        record["explicit_models"] = explicit
        record["why"] = str(tool_input.get("description") or tool_input.get("name") or "") or None
        advisories.append(
            "explicit request for a model off the default ladder ("
            + ", ".join(item["model"] for item in explicit)
            + "), recorded in the routing ledger"
        )
        if blocked_pins:
            advisories.append(
                "the brief names "
                + ", ".join(repr(pin) for pin in blocked_pins)
                + ", no longer served upstream; prose is not enforced, the seat may fail on it"
            )
    if warn_reason:
        record["warned"] = warn_reason
        append(record, ledger)
        emit_warning(warn_reason + ("; " + NEEDS_MISSING if record.get("needs_missing") else ""), budget_advisory)
        return
    lean = lean_studio_refusal(subagent, prompt)
    if lean:
        record["lean_studio_refused"] = True
        append(record, ledger)
        emit_lean_refusal(lean)
        return
    is_fork = subagent == "fork"
    if is_fork:
        record["fork"] = True
    requires_snapshot = (is_anthropic_model(model) or subagent not in FORWARDER_SEATS) and (
        not is_fork or is_anthropic_model(model)
    )
    if requires_snapshot and os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") != "1":
        capacity_advisory = snapshot_advisory(
            Path(os.environ.get("LIMIT_WATCH_SNAPSHOT") or Path.home() / ".agent-lb" / "state" / "limit-watch.json")
        )
        if capacity_advisory:
            record["capacity_advisory"] = capacity_advisory
            advisories.append(capacity_advisory)
    elif requires_snapshot:
        record["anthropic_override"] = True
    brief_on_sol = is_brief(prompt) and sol_choice(subagent, model)
    if brief_on_sol:
        record["brief_on_sol"] = True
    if not append(record, ledger):
        emit_advisory("could not record routing telemetry; dispatch was not blocked", budget_advisory)
    elif brief_on_sol:
        emit_context(
            "seat-guard warning: scope, spec, contract and design briefs default to opus-latest "
            "(models.md 2026-10-14); this Sol dispatch still runs."
            + (" Capacity: " + "; ".join(advisories) + "." if advisories else ""),
            budget_advisory,
        )
    elif advisories:
        emit_advisory("; ".join(advisories), budget_advisory)
    elif budget_advisory:
        emit_context(budget_advisory)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit_refusal(f"failed ({type(error).__name__})")
    else:
        if os.environ.get("HOOK_FLOOR_RECEIPT"):
            print("floor-ok seat-guard.py")
