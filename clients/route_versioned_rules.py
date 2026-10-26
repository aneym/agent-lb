from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import route_heal  # pyrefly: ignore [missing-import]
from route_quota import load_quota_policy  # pyrefly: ignore [missing-import]
from route_reserve_state import pool_for_model, pools_by_id, vendor_for  # pyrefly: ignore [missing-import]

DEFAULT_BASE_URL = "http://127.0.0.1:2455"

DEFAULT_CODEX_COMPANION = "~/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs"

EXIT_OK = 0

EXIT_DEGRADED = 1

EXIT_UNROUTABLE = 2

EXIT_SETUP = 3

POOL_OK_HEADROOM = 25.0

POOL_LOW_HEADROOM = 5.0

DOCTOR_BUDGET_SECONDS = 55.0

CURSOR_TIMEOUT_SECONDS = 10.0

DEVIN_TIMEOUT_SECONDS = 15.0

CODEX_TIMEOUT_SECONDS = 15.0

READ_TIMEOUT_SECONDS = 5.0

PROBE_BUDGET_SECONDS = 2.0

SESSION_WINDOW_MINUTES = 30

MESSAGE_HEAD_CHARS = 160

STATE_MAX_AGE_SECONDS = 7200

SEAT_PROBE = {"cursor-seat": "cursor-agent", "devin-seat": "devin", "muse-seat-local": "muse"}

CLI_SEAT_HOLD_AFTER_FAILURES = 2

CLI_SEAT_HOLD_SECONDS = 600

ANALYTICS_SESSION_CAP = 20


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fixture_file(path: str) -> Path | None:
    directory = (os.environ.get("ROUTE_FIXTURE_DIR") or "").strip()
    if not directory:
        return None
    slug = path.split("?", 1)[0].strip("/").replace("/", "_") or "root"
    return Path(directory).expanduser() / f"{slug}.json"


class ModelState:
    ANTHROPIC_MODELS: tuple[list[str], str] | None = None
    CURSOR_MODELS: list[str] | None = None
    DEFER_DISCOVERY = False
    DEFERRED_DISCOVERY: set[str] = set()
    DEVIN_MODELS: list[str] | None = None


def base_url() -> str:
    url = os.environ.get("AGENT_LB_URL") or client_env_url() or DEFAULT_BASE_URL
    return url.rstrip("/")


def client_env_url() -> str | None:

    try:
        text = (Path.home() / ".config" / "agent-lb" / "client.env").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?AGENT_LB_URL\s*=\s*['\"]?(https?://[^\s'\"]+)['\"]?\s*", line)
        if match:
            return match.group(1)
    return None


def env_path(name: str, default: Path) -> Path:
    value = (os.environ.get(name) or "").strip()
    return Path(value).expanduser() if value else default


def table_path() -> Path:
    managed = Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    repo = Path(__file__).resolve().parent.parent / "config" / "coding-agents" / "routing-table.json"
    configured = (os.environ.get("ROUTE_TABLE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return managed if managed.is_file() else repo


def state_path() -> Path:
    return env_path("ROUTE_STATE", Path.home() / ".claude" / "routing-state.json")


def alert_path() -> Path:
    return env_path("ROUTE_ALERT", Path.home() / ".claude" / "routing-ALERT")


def ledger_path() -> Path:
    return env_path("ROUTE_LEDGER", Path.home() / ".claude" / "logs" / "dispatch.jsonl")


def cursor_command() -> list[str]:
    import shlex

    return shlex.split(os.environ.get("ROUTE_CURSOR_CMD") or "cursor-agent status")


def devin_command() -> list[str]:
    import shlex

    return shlex.split(os.environ.get("ROUTE_DEVIN_CMD") or "devin auth status")


def codex_command() -> list[str]:
    import shlex

    configured = os.environ.get("ROUTE_CODEX_CMD")
    if configured:
        return shlex.split(configured)
    return ["node", str(Path(DEFAULT_CODEX_COMPANION).expanduser()), "status"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_table(*, apply_default: bool = True) -> dict[str, Any]:
    path = table_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"routing table not found at {path}")
    except (OSError, json.JSONDecodeError) as error:
        fail(f"routing table at {path} is unreadable: {error}")
    if not isinstance(value, dict):  # pyrefly: ignore [unbound-name]
        fail(f"routing table at {path} is not an object")
    value = load_quota_policy(value, path)
    return apply_implement_default(value) if apply_default else value


def apply_implement_default(table: dict[str, Any]) -> dict[str, Any]:

    seat = table.get("implement_default")
    classes = table.get("classes")
    if not isinstance(seat, str) or not isinstance(classes, dict):
        return table
    for name in ("implement", "mechanical"):
        spec = classes.get(name)
        chain = spec.get("chain") if isinstance(spec, dict) else None
        if isinstance(chain, list):
            lead = [entry for entry in chain if isinstance(entry, dict) and entry.get("seat") == seat]
            spec["chain"] = lead + [entry for entry in chain if not any(entry is item for item in lead)]  # pyrefly: ignore [unsupported-operation]
    return table


def fail(message: str) -> Any:
    print(f"route: {message}", file=sys.stderr)
    raise SystemExit(EXIT_UNROUTABLE)


SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

COMMIT_NAME = re.compile(r"^[0-9a-f]{7,64}$")

FULL_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


def setup_error(kind: str, message: str, as_json: bool) -> Any:
    print(f"route: {message}", file=sys.stderr)
    if as_json:
        print(json.dumps({"error": kind, "message": message}, sort_keys=True))
    raise SystemExit(EXIT_SETUP)


def policy_paths() -> tuple[Path, Path, Path]:
    clone = env_path("AGENT_LB_POLICY_SRC", Path.home() / ".agent-lb" / "policy-src")
    managed = clone.parent / "managed" / "coding-agents"
    return clone, managed / "APPLIED", managed / "routing-table.json"


def policy_git(clone: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess[Any] | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(clone), *args], capture_output=True, text=not binary, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result if result.returncode == 0 else None


def applied_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (
        value
        if isinstance(value, dict) and isinstance(value.get("commit"), str) and FULL_COMMIT.fullmatch(value["commit"])
        else None
    )


def versioned_rules(pin: str | None, as_json: bool) -> tuple[dict[str, Any], dict[str, Any], Path]:
    clone, applied_path, installed_path = policy_paths()
    git_ready = clone.is_dir() and policy_git(clone, "rev-parse", "--git-dir") is not None
    source = "installed"
    commit = None
    if pin is not None:
        if not git_ready:
            setup_error(
                "no_rules", f"no policy clone {clone}; APPLIED {applied_path}; installed {installed_path}", as_json
            )
        if not COMMIT_NAME.fullmatch(pin):
            setup_error("unknown_rules", f"invalid rules commit {pin!r}", as_json)
        source = "pinned"
        revision = policy_git(clone, "rev-parse", "--verify", "--quiet", f"{pin}^{{commit}}")
        if revision is None:
            setup_error("unknown_rules", f"unknown rules commit {pin}", as_json)
        commit = revision.stdout.strip()  # pyrefly: ignore [missing-attribute]
    elif git_ready:
        applied = applied_record(applied_path)
        if applied:
            revision = policy_git(clone, "rev-parse", "--verify", "--quiet", f"{applied['commit']}^{{commit}}")
            if revision is not None:
                source = "applied"
                commit = revision.stdout.strip()
    if commit:
        result = policy_git(clone, "show", f"{commit}:config/coding-agents/routing-table.json", binary=True)
        if result is None:
            setup_error("unknown_rules", f"routing table missing at commit {commit}", as_json)
        data = result.stdout  # pyrefly: ignore [missing-attribute]
    else:
        try:
            data = installed_path.read_bytes()
        except OSError:
            setup_error(
                "no_rules",
                f"no policy clone/APPLIED {clone}, {applied_path} or installed table {installed_path}",
                as_json,
            )
    try:
        table = json.loads(data)  # pyrefly: ignore [unbound-name]
    except (UnicodeError, ValueError):
        table = None
    if (
        not isinstance(table, dict)
        or not isinstance(table.get("classes"), dict)
        or not isinstance(table.get("seats"), dict)
    ):
        setup_error("unknown_rules", f"invalid routing table at {commit or installed_path}", as_json)
    return (
        apply_implement_default(table),  # pyrefly: ignore [bad-argument-type]
        {"commit": commit, "source": source, "table_sha256": hashlib.sha256(data).hexdigest()},
        clone,
    )


def class_seat_entries(classes: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    entries = []
    for task_class, spec in classes.items():
        if not isinstance(spec, dict):
            continue
        candidates = list(spec.get("chain", [])) if isinstance(spec.get("chain"), list) else []
        candidates.append(spec.get("second_opinion"))
        audit = spec.get("audit", {})
        if isinstance(audit, dict) and isinstance(audit.get("by_author_vendor"), dict):
            candidates.extend(audit["by_author_vendor"].values())
        entries.extend(
            (task_class, entry)
            for entry in candidates
            if isinstance(entry, dict) and isinstance(entry.get("seat"), str)
        )
    return entries


def reduced_entry(entry: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    seat = entry.get("seat")
    model = entry.get("model")
    vendor = entry.get("vendor")
    if not isinstance(vendor, str):
        vendor = table["seats"].get(seat, {}).get("vendor") or vendor_for(entry, pool_for_model(table, model))
    return {"seat": seat, "model": model, "vendor": vendor, "effort": entry.get("effort")}


def rules_status() -> dict[str, Any]:
    clone, applied_path, _ = policy_paths()
    state: dict[str, Any] = {
        "applied": None,
        "applied_at": None,
        "result": None,
        "origin": None,
        "behind": None,
        "diverged": None,
        "error": "no_rules",
    }
    applied = applied_record(applied_path) if clone.is_dir() and policy_git(clone, "rev-parse", "--git-dir") else None
    if not applied:
        return state
    revision = policy_git(clone, "rev-parse", "--verify", "--quiet", f"{applied['commit']}^{{commit}}")
    if not revision:
        return state
    state.update(applied=revision.stdout.strip(), applied_at=applied.get("applied_at"), result=applied.get("result"))
    origin = policy_git(clone, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main")
    if not origin:
        state["error"] = "no_origin"
        return state
    state.update(origin=origin.stdout.strip(), error=None)
    ancestor = policy_git(clone, "merge-base", "--is-ancestor", state["applied"], state["origin"])
    state["diverged"] = ancestor is None
    if ancestor is not None:
        count = policy_git(clone, "rev-list", "--count", f"{state['applied']}..{state['origin']}")
        state["behind"] = int(count.stdout.strip()) if count else None
    return state


def pool_exhausted(pool_doc: Any) -> bool:

    if not isinstance(pool_doc, dict):
        return False
    eligible = pool_doc.get("eligibleAccounts")
    if isinstance(eligible, int) and not isinstance(eligible, bool):
        return eligible <= 0
    return pool_doc.get("status") == "exhausted"


def remaining(account: dict[str, Any]) -> float:

    if account.get("status") != "active":
        return 0.0
    usage = account.get("usage")

    return min(
        route_heal.window(usage, "secondaryRemainingPercent", 0.0),
        route_heal.window(usage, "primaryRemainingPercent", 100.0),
    )


ROUTABLE_STATUSES = frozenset({"active", "rate_limited", "quota_exceeded"})

WEEKLY_CYCLE_HOURS = 168.0


def pool_ids(account: dict[str, Any]) -> list[str]:
    provider = account.get("provider")
    if provider == "anthropic":
        ids = ["anthropic-general"]
        if account.get("fableEligible"):
            ids.append("anthropic-fable")
        return ids
    if provider == "openai":
        return ["openai-codex"]
    if isinstance(provider, str) and provider:
        return [provider]
    return []


def add_pool_refills(document: dict[str, Any], body: Any) -> None:

    accounts = body.get("accounts") if isinstance(body, dict) else body
    if not isinstance(accounts, list):
        return
    for pool in pools_by_id(document).values():
        if pool.get("windowLabel") == "month" or pool.get("id") == "anthropic-fable":
            continue
        resets = []
        now = datetime.now(timezone.utc)
        for account in accounts:
            if not isinstance(account, dict) or account.get("status") not in ROUTABLE_STATUSES:
                continue
            subscription = account.get("subscription")
            if isinstance(subscription, dict) and str(subscription.get("status") or "").strip().lower() == "canceled":
                continue
            if pool.get("id") not in pool_ids(account):
                continue
            stamp = parse_ts(account.get("resetAtSecondary"))
            remaining = route_heal.window(account.get("usage"), "secondaryRemainingPercent", 100.0)
            if stamp is not None and stamp.tzinfo is not None and stamp > now:
                resets.append((stamp.astimezone(timezone.utc), remaining))
        if not resets:
            continue
        cohorts = []
        low_resets = []
        for stamp, remaining in sorted(resets):
            if not cohorts or (stamp - cohorts[-1][0]).total_seconds() > 3600:
                cohorts.append([stamp, 0, 0.0])
            cohorts[-1][1] += 1
            cohorts[-1][2] += remaining
            if remaining < 10 and stamp > now:
                low_resets.append(stamp)
        if "refills" not in pool:
            pool["refills"] = [
                {
                    "at": stamp.isoformat().replace("+00:00", "Z"),
                    "accounts": count,
                    "remainingPercent": round(remaining, 1),
                }
                for stamp, count, remaining in cohorts
            ]
        if low_resets:
            pool.setdefault("pace", {})["next_refill_h"] = (min(low_resets) - now).total_seconds() / 3600


def pool_snapshot_path() -> Path:
    return env_path("ROUTE_POOL_SNAPSHOT", Path.home() / ".cache" / "agent-lb" / "route-pools.json")


EXIT_WAIT = 3
