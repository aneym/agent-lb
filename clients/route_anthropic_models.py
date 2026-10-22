from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import route_cache  # pyrefly: ignore [missing-import]
from route_cache import HttpResult  # pyrefly: ignore [missing-import]
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_DEGRADED,
    EXIT_OK,
    READ_TIMEOUT_SECONDS,
    ModelState,
    base_url,
    env_path,
    fixture_file,
    now_iso,
    parse_ts,
    state_path,
)


def request(
    method: str, path: str, *, payload: dict[str, Any] | None, timeout: float, fixture: bool = True
) -> HttpResult:
    return route_cache.request(
        method,
        path,
        payload=payload,
        timeout=timeout,
        fixture=fixture,
        url=base_url(),
        cache_dir=models_cache_path().parent,
    )


def http_get(path: str, *, timeout: float = READ_TIMEOUT_SECONDS) -> HttpResult:
    return request("GET", path, payload=None, timeout=timeout)


def percent(value: Any) -> str:

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.1f}%"


def command_plan(args: argparse.Namespace) -> int:
    result = http_get("/api/pools/plan")
    if not result.json_ok or not isinstance(result.body, dict):
        print(f"route: /api/pools/plan unavailable ({result.error or result.status})", file=sys.stderr)
        return EXIT_DEGRADED
    document = result.body
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
        return EXIT_OK
    level = args.level or document.get("recommended", "balanced")
    plan = document.get("levels", {}).get(level)
    if not isinstance(plan, dict):
        print(f"route: plan level {level} unavailable", file=sys.stderr)
        return EXIT_DEGRADED
    print(plan["title"])
    print(plan["why"])
    for account in plan["accounts"]:
        unit = "plan" if account["pool"] == "cursor" else "account"
        print(
            f"{account['label']}, {account['count']} {unit}{'' if account['count'] == 1 else 's'} "
            f"[{account['change']}, delta {account['delta']:+d}] — {account['reason']}"
        )
    labels = {"implement": "Write code", "mechanical": "Small edits", "explore": "Read and explore", "review": "Review"}
    for job, rungs in plan["ladders"].items():
        names = {"opus-every-piece": "Claude Opus on every piece", "sol-claude-work": "GPT-6.1 Sol for Claude work"}
        print(
            f"{labels[job]}: "
            + " → ".join(names.get(rung["id"], f"{rung['model']} ({rung['harness']})") for rung in rungs)
        )
    print(plan["risk"])
    return EXIT_OK


MODELS_CACHE_MAX_AGE_SECONDS = 86400

LOW_BAND_MAX_ELIGIBLE = 2

DEFAULT_RETIRED = ("claude-fable-*", "fable", "claude-planner", "gpt-*-astra", "gpt-*-astra-*", "gpt-5.6*", "gpt-5.5*")


def models_cache_path() -> Path:
    return env_path("ROUTE_MODELS_CACHE", Path.home() / ".cache" / "agent-lb" / "route-models.json")


def retired_patterns(table: dict[str, Any]) -> tuple[str, ...]:
    configured = table.get("retired")
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return tuple(configured)
    return DEFAULT_RETIRED


DEFAULT_BLOCKED = ("claude-planner", "gpt-5.4*", "gpt-5-*", "gpt-5")


def blocked_patterns(table: dict[str, Any]) -> tuple[str, ...]:

    configured = table.get("blocked")
    if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
        return tuple(configured)
    return DEFAULT_BLOCKED


def readmitted_patterns(table: dict[str, Any], alias: str) -> tuple[str, ...]:

    readmitted = table.get("readmitted") if isinstance(table.get("readmitted"), dict) else {}
    found: list[str] = []
    for entry in readmitted.values():
        if not isinstance(entry, dict) or entry.get("alias") != alias:
            continue
        found.extend(item for item in entry.get("patterns") or () if isinstance(item, str))
    return tuple(found)


def is_retired(model: str, patterns: tuple[str, ...]) -> bool:
    bare = model.lower().split("[", 1)[0]
    return any(fnmatch.fnmatchcase(bare, pattern) for pattern in patterns)


def model_ids(body: Any) -> list[str]:
    items = body.get("models") or body.get("data") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    ids = [item.get("id") or item.get("slug") for item in items if isinstance(item, dict)]
    return [value for value in ids if isinstance(value, str) and value]


def served_models() -> tuple[list[str], str]:

    for path in ("/api/models", "/v1/models"):
        result = http_get(path, timeout=3.0)
        ids = model_ids(result.body) if result.ok else []
        if ids:
            cache = models_cache_path()
            try:
                if not result.from_cache and fixture_file(path) is None:
                    atomic_write(cache, json.dumps({"ts": now_iso(), "models": ids}))
            except OSError:
                pass
            return ids, f"lb {path}"
    try:
        cached = json.loads(models_cache_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return [], "no LB model list and no cache"
    stamp = parse_ts(cached.get("ts")) if isinstance(cached, dict) else None
    ids = cached.get("models") if isinstance(cached, dict) else None
    if stamp is None or not isinstance(ids, list):
        return [], "model cache unreadable"
    if (datetime.now(timezone.utc) - stamp).total_seconds() > MODELS_CACHE_MAX_AGE_SECONDS:
        return [], "LB unreachable and the model cache is over a day old"
    return [value for value in ids if isinstance(value, str)], "cache (LB unreachable)"


def anthropic_models() -> tuple[list[str], str]:

    if ModelState.ANTHROPIC_MODELS is not None:
        return ModelState.ANTHROPIC_MODELS
    cache = models_cache_path().with_name("route-anthropic-models.json")
    result = http_get("/api/models/anthropic", timeout=10.0)
    ids = (
        model_ids({"models": [{"id": value} for value in result.body.get("models", [])]})
        if (result.ok and isinstance(result.body, dict) and isinstance(result.body.get("models"), list))
        else []
    )
    if ids:
        try:
            if not result.from_cache and fixture_file("/api/models/anthropic") is None:
                atomic_write(cache, json.dumps({"ts": now_iso(), "models": ids}))
        except OSError:
            pass
        ModelState.ANTHROPIC_MODELS = (ids, "lb /api/models/anthropic")
        return ModelState.ANTHROPIC_MODELS
    ModelState.ANTHROPIC_MODELS = ([], "no Anthropic model list from the LB and no cache")
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ModelState.ANTHROPIC_MODELS
    stamp = parse_ts(cached.get("ts")) if isinstance(cached, dict) else None
    if stamp is not None and stamp.tzinfo is None:
        stamp = None
    listed = cached.get("models") if isinstance(cached, dict) else None
    if (
        stamp is not None
        and isinstance(listed, list)
        and (datetime.now(timezone.utc) - stamp).total_seconds() <= MODELS_CACHE_MAX_AGE_SECONDS
    ):
        ModelState.ANTHROPIC_MODELS = (
            [value for value in listed if isinstance(value, str)],
            "cache (LB list unavailable)",
        )
    return ModelState.ANTHROPIC_MODELS


def resolve_claude_family(spec: dict[str, Any], retired: tuple[str, ...]) -> tuple[str | None, str]:

    family = spec.get("family")
    if isinstance(family, str) and family:
        pattern = re.compile(rf"^claude-{re.escape(family)}-(\d+)(?:-(\d{{1,2}}))?$")
        listed, source = anthropic_models()
        candidates = []
        for model in listed:
            match = pattern.match(model)
            if match and not is_retired(model, retired):
                candidates.append(((int(match.group(1)), int(match.group(2) or 0)), model))
        if candidates:
            return max(candidates)[1], f"newest listed {family} ({source})"
        if listed and spec.get("fallback"):
            return None, f"no listed {family} ({source})"
    pinned = spec.get("pinned")
    if isinstance(pinned, str) and pinned and not is_retired(pinned, retired):
        return pinned, f"pinned fallback {pinned} (no Anthropic model list)"
    harness = spec.get("harness")
    if isinstance(harness, str) and harness:
        return harness, f"Claude Code alias {harness!r} (its newest {harness})"
    return None, f"alias for {family} has no model list, pin or harness"


def model_list_pending(vendor: str) -> str:
    return f"pending: the {vendor} model list is listed once capacity is held"


def defer_discovery() -> None:
    ModelState.DEFER_DISCOVERY = True


def allow_discovery() -> None:

    ModelState.DEFER_DISCOVERY = False
    if "cursor" in ModelState.DEFERRED_DISCOVERY:
        ModelState.CURSOR_MODELS = None
    if "devin" in ModelState.DEFERRED_DISCOVERY:
        ModelState.DEVIN_MODELS = None
    ModelState.DEFERRED_DISCOVERY.clear()


def cursor_models() -> list[str]:

    if ModelState.CURSOR_MODELS is None:
        ModelState.CURSOR_MODELS = fetch_cursor_models()
    return ModelState.CURSOR_MODELS


def model_list_failed_recently(cache: Path) -> bool:
    failure = cache.with_suffix(".failed.json")
    try:
        cached = json.loads(failure.read_text(encoding="utf-8"))
        stamp = parse_ts(cached.get("ts"))
        return bool(stamp and stamp.tzinfo and (datetime.now(timezone.utc) - stamp).total_seconds() < 300)
    except (OSError, ValueError, AttributeError):
        return False


def cache_model_list(cache: Path, ids: list[str]) -> None:
    failure = cache.with_suffix(".failed.json")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        if ids:
            cache.write_text(json.dumps({"ts": now_iso(), "models": ids}), encoding="utf-8")
            failure.unlink(missing_ok=True)
        else:
            failure.write_text(json.dumps({"ts": now_iso()}), encoding="utf-8")
    except OSError:
        pass


def fetch_cursor_models() -> list[str]:
    import shlex

    cache = models_cache_path().with_name("route-cursor-models.json")
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        stamp = parse_ts(cached.get("ts"))
        if (
            stamp
            and stamp.tzinfo
            and (datetime.now(timezone.utc) - stamp).total_seconds() < MODELS_CACHE_MAX_AGE_SECONDS
        ):
            return [value for value in cached.get("models", []) if isinstance(value, str)]
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass
    if model_list_failed_recently(cache):
        return []
    if ModelState.DEFER_DISCOVERY:
        ModelState.DEFERRED_DISCOVERY.add("cursor")
        return []
    command = shlex.split(os.environ.get("ROUTE_CURSOR_MODELS_CMD") or "cursor-agent --list-models")
    output = run_listing(command, timeout=10)
    ids = [line.split(" - ", 1)[0].strip() for line in output.splitlines() if " - " in line]
    ids = [value for value in ids if re.fullmatch(r"[\w.\-]+", value)]
    cache_model_list(cache, ids)
    return ids


LISTING_TERM_GRACE_SECONDS = 1.0

LISTING_DRAIN_SECONDS = 3.0

STOP_BUDGET_SECONDS = 8.0

UNDRAINED: list[str] = []


def run_listing(command: list[str], timeout: float) -> str:

    import subprocess

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return ""
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except BaseException as interrupted:
        if not end_group(process, LISTING_DRAIN_SECONDS):
            UNDRAINED.append(f"model listing group {process.pid} outlived SIGKILL for {LISTING_DRAIN_SECONDS:.0f}s")
        if process.stdout is not None:
            process.stdout.close()
        if isinstance(interrupted, subprocess.TimeoutExpired):
            return ""
        raise
    return stdout if process.returncode == 0 else ""


def signal_group(pgid: int, signum: int) -> bool:

    try:
        os.killpg(pgid, signum)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def end_group(process: subprocess.Popen, within: float) -> bool:

    import signal
    import subprocess

    pgid = process.pid
    deadline = time.monotonic() + within
    signal_group(pgid, signal.SIGTERM)
    try:
        process.wait(timeout=max(0.0, min(LISTING_TERM_GRACE_SECONDS, deadline - time.monotonic())))
    except subprocess.TimeoutExpired:
        pass
    while True:
        signal_group(pgid, signal.SIGKILL)
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
        if process.returncode is not None and not signal_group(pgid, 0):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def devin_models() -> list[str]:

    if ModelState.DEVIN_MODELS is None:
        ModelState.DEVIN_MODELS = fetch_devin_models()
    return ModelState.DEVIN_MODELS


def fetch_devin_models() -> list[str]:
    import shlex

    cache = models_cache_path().with_name("route-devin-models.json")
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        stamp = parse_ts(cached.get("ts"))
        if (
            stamp
            and stamp.tzinfo
            and (datetime.now(timezone.utc) - stamp).total_seconds() < MODELS_CACHE_MAX_AGE_SECONDS
        ):
            return [value for value in cached.get("models", []) if isinstance(value, str)]
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass
    if model_list_failed_recently(cache):
        return []
    if ModelState.DEFER_DISCOVERY:
        ModelState.DEFERRED_DISCOVERY.add("devin")
        return []
    command = shlex.split(os.environ.get("ROUTE_DEVIN_MODELS_CMD") or "devin models list")
    output = run_listing(command, timeout=10)

    ids = [line.split()[0] for line in output.splitlines() if line.startswith("  ") and line.split()]
    ids = [value for value in ids if re.fullmatch(r"[a-z0-9][\w.\-]*", value)]
    cache_model_list(cache, ids)
    return ids


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def family_alias(table: dict[str, Any], name: str) -> str:

    aliases = table.get("aliases") or {}
    if name in aliases:
        return name
    base, _, effort = name.rpartition("-")
    if effort in ("low", "medium", "high", "xhigh") and base in aliases:
        return base
    return name


def ledger_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state() -> dict[str, Any]:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
