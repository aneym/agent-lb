"""One routing decision: menu from `route`, a pick from the decider, a host re-check, a receipt.

The host owns the move (keel's decision architecture): the decider chooses among seats the
host already vetted or abstains, the host re-checks the pick against a fresh menu, and every
fallback is the host's. Each decision is one `of_decision` row in the dispatch ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .common import PKG, resolve_bin, utc_now

POLICY_PATH = PKG / "decider.json"
DECISION_EVENT = "of_decision"
DECIDERS = ("jev", "static")


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text())


def ledger_path() -> Path:
    return Path(os.environ.get("ROUTE_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl").expanduser()


def run_route(*args: str, timeout: float = 30.0) -> tuple[int, Any, str]:
    route = resolve_bin("route")
    if not route:
        return 127, None, "route not found"
    proc = subprocess.run([route, *args], capture_output=True, text=True, timeout=timeout, check=False)
    try:
        body = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        body = None
    return proc.returncode, body, proc.stderr.strip()


def fetch_menu(task_class: str) -> dict[str, Any]:
    code, menu, err = run_route("menu", "--class", task_class, "--json")
    if menu is None:
        raise SystemExit(f"open-factory: route menu failed (exit {code}): {err}")
    return menu


def _hours_until(stamp: Any) -> str | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        from datetime import datetime, timezone

        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    hours = (when - datetime.now(timezone.utc)).total_seconds() / 3600
    return f"{max(hours, 0):.0f}h"


def describe(seat: dict[str, Any], pools: dict[str, Any], policy: dict[str, Any]) -> str:
    capability = policy.get("capability", {}).get(seat.get("alias"), f"{seat.get('model')} ({seat.get('vendor')}).")
    pool = pools.get(seat.get("pool") or "", {})
    facts = []
    weekly = pool.get("weeklyRemainingPercent")
    if isinstance(weekly, (int, float)):
        reset = _hours_until(pool.get("weeklyResetAt"))
        facts.append(f"weekly quota {weekly:.0f}% left" + (f", resets in {reset}" if reset else ""))
    five = pool.get("fiveHourHeadroomPercent")
    if isinstance(five, (int, float)):
        facts.append(f"best account has {five:.0f}% of its 5-hour window")
    eligible = pool.get("eligibleAccounts")
    if isinstance(eligible, int):
        facts.append(f"{eligible} usable account(s)")
    text = f"{capability} Seat {seat['seat']} runs {seat['model']}."
    if facts:
        text += f" Pool {seat.get('pool')}: " + "; ".join(facts) + "."
    return text[:2000]


def candidates_for(menu: dict[str, Any], task_class: str, policy: dict[str, Any]) -> list[dict[str, str]]:
    entry = menu.get("classes", {}).get(task_class, {})
    seats = entry.get("seats") or []
    limit = int(policy.get("max_candidates", 16))
    return [{"id": seat["id"], "description": describe(seat, menu.get("pools", {}), policy)} for seat in seats][:limit]


def ask_jev(task: str, context: str | None, candidates: list[dict[str, str]], timeout: float) -> dict[str, Any]:
    """Jev's answer, or {"error": ...} when it is missing, unavailable, slow or malformed."""
    jev = resolve_bin("jev")
    if not jev:
        return {"error": "jev_missing"}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"task": task[:12000], "context": (context or "")[:40000], "candidates": candidates}, handle)
        menu_file = handle.name
    try:
        proc = subprocess.run([jev, "pick", menu_file], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"error": "jev_timeout"}
    finally:
        os.unlink(menu_file)
    if proc.returncode == 3:
        return {"error": "jev_unavailable", "detail": proc.stderr.strip()[:200]}
    try:
        answer = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": f"jev_exit_{proc.returncode}", "detail": proc.stderr.strip()[:200]}
    return answer if isinstance(answer, dict) else {"error": "jev_malformed"}


def decide(task: str, task_class: str, *, decider: str, context: str | None = None) -> dict[str, Any]:
    policy = load_policy()
    started = time.monotonic()
    menu = fetch_menu(task_class)
    menu_ms = round((time.monotonic() - started) * 1000)
    candidates = candidates_for(menu, task_class, policy)
    receipt: dict[str, Any] = {
        "ts": utc_now(),
        "event": DECISION_EVENT,
        "decision_id": uuid.uuid4().hex[:16],
        "session_id": os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "cwd": os.getcwd(),
        "task_class": task_class,
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "task_head": task[:160],
        "decider": decider,
        "policy_version": policy.get("version"),
        "candidates": [{"id": c["id"], "summary": c["description"][:96]} for c in candidates],
        "excluded": menu.get("classes", {}).get(task_class, {}).get("excluded", []),
        "menu_ms": menu_ms,
        "pick": None,
        "abstain": None,
        "validation": None,
        "confidence": None,
        "fit": None,
        "decider_ms": None,
        "decider_usd": None,
        "fallback": None,
    }

    chosen: str | None = None
    if not candidates:
        receipt["abstain"] = "empty_menu"
    elif decider == "jev":
        asked = time.monotonic()
        answer = ask_jev(task, context, candidates, float(policy.get("jev_timeout_s", 5)))
        receipt["decider_ms"] = round((time.monotonic() - asked) * 1000)
        if "error" in answer:
            receipt["abstain"] = answer["error"]
        else:
            receipt.update(
                confidence=answer.get("confidence"),
                fit=answer.get("fit"),
                decider_usd=answer.get("usd"),
                abstain=answer.get("abstain"),
            )
            chosen = answer.get("pick")
            if chosen is not None and chosen not in {c["id"] for c in candidates}:
                receipt["validation"] = "unknown_id"
                chosen = None
    else:
        receipt["abstain"] = "static_decider"

    if chosen is not None:
        receipt["pick"] = chosen
        if time.monotonic() - started > float(policy.get("pick_ttl_s", 45)):
            receipt["validation"] = "expired"
        else:
            fresh = fetch_menu(task_class)
            fresh_ids = {seat["id"] for seat in fresh.get("classes", {}).get(task_class, {}).get("seats", [])}
            receipt["validation"] = "accepted" if chosen in fresh_ids else "stale"

    if receipt["validation"] == "accepted":
        seat = next(s for s in menu["classes"][task_class]["seats"] if s["id"] == chosen)
    else:
        code, picked, err = run_route("pick", task_class, "--json")
        if code == 0 and isinstance(picked, dict):
            receipt["fallback"] = "route_pick"
            seat = {"seat": picked["seat"], "model": picked.get("model"), "pool": picked.get("pool")}
        else:
            receipt["fallback"] = "driver"
            receipt["fallback_reason"] = err[:200]
            seat = {"seat": "driver", "model": None, "pool": None}
    receipt.update(seat=seat["seat"], model=seat.get("model"), pool=seat.get("pool"))
    receipt["total_ms"] = round((time.monotonic() - started) * 1000)

    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def read_decisions(since_cwd: Path | None = None) -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if DECISION_EVENT not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != DECISION_EVENT:
                continue
            if since_cwd is not None and not str(row.get("cwd") or "").startswith(str(since_cwd)):
                continue
            rows.append(row)
    return rows
