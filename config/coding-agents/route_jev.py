"""TypeSafe System One wire contract, following rails/jev.py choice/score/noul.

Only caller-supplied task text and path names enter state. No files are opened.
The environment key is used only as a transport header, never as model input.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
CLASSES = {
    "plan": "Architecture, requirements, decomposition; driver planning",
    "review": "Review a plan or proposal before implementation",
    "explore": "Read-only code inventory and investigation",
    "implement": "Build or fix behavior in code",
    "mechanical": "Precisely specified repetitive edits or commands",
    "verify": "Validate implementation, adversarial audit or testing",
    "computer": "Interact with a desktop, browser or device",
    "research": "Research external information and sources",
}
QUESTIONS = {
    "class": {"type": "choice", "instructions": "Choose the task's primary work class", "criteria": CLASSES},
    "difficulty": {"type": "score", "instructions": "Rate implementation difficulty, easiest level first",
                   "criteria": ["1: trivial", "2: straightforward", "3: ordinary multi-file",
                                "4: difficult cross-file reasoning", "5: architecture or high complexity"]},
    "money_path": {"type": "noul", "instructions": (
        "The task concerns auth, RLS, billing, migrations, receipts, idempotency or a tool boundary"
    )},
    "needs_write": {"type": "noul", "instructions": "Completing the task requires modifying files or external state"},
}


class JevUnavailable(RuntimeError):
    pass


def request_body(task: str, paths: list[str]) -> dict[str, Any]:
    return {"model": "jev-latest", "state": {"task": task, "paths": paths}, "questions": QUESTIONS}


def ask(task: str, paths: list[str]) -> dict[str, Any]:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevUnavailable("auth: TYPESAFE_API_KEY is not configured")
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps(request_body(task, paths)).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        reasons = {401: "auth", 403: "auth", 402: "quota", 429: "rate_limited", 529: "overloaded"}
        reason = reasons.get(error.code, "server" if error.code >= 500 else None)
        if reason:
            raise JevUnavailable(f"{reason}: HTTP {error.code}") from None
        raise ValueError(f"Jev rejected the question set: HTTP {error.code}") from None
    except (OSError, urllib.error.URLError) as error:
        reason = "timeout" if isinstance(error, TimeoutError) else "network"
        raise JevUnavailable(reason) from None
    return parse(payload)


def number(value: Any, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Jev returned an invalid number")
    if not 0 <= value <= maximum:
        raise ValueError("Jev returned an out-of-range number")
    return float(value)


def parse(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise ValueError("Jev returned no answers object")
    answers = payload["answers"]
    for name, question in QUESTIONS.items():
        given = answers.get(name)
        if not isinstance(given, dict) or given.get("type") not in (None, question["type"]):
            raise ValueError(f"Jev returned an invalid {name} answer")
        if question["type"] == "noul":
            number(given.get("noul"), 1)
            continue
        expected = set(CLASSES) if name == "class" else {str(i) for i in range(5)}
        probs = given.get("probabilities")
        if not isinstance(probs, dict) or set(probs) != expected:
            raise ValueError(f"Jev returned incomplete {name} probabilities")
        if abs(sum(number(value, 1) for value in probs.values()) - 1) > 0.02:
            raise ValueError(f"Jev returned unnormalized {name} probabilities")
        number(given.get("confidence"), 1)
    selected = answers["class"]
    if selected.get("choice") not in CLASSES:
        raise ValueError("Jev chose an unknown class")
    if selected["probabilities"][selected["choice"]] < max(selected["probabilities"].values()) - 1e-6:
        raise ValueError("Jev choice contradicts probabilities")
    return {"class": selected["choice"], "difficulty": number(answers["difficulty"].get("score"), 4) + 1,
            "money_path": answers["money_path"]["noul"] > 0.5,
            "needs_write": answers["needs_write"]["noul"] > 0.5,
            "jev_confidence": selected["confidence"]}
