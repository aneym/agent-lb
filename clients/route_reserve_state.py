from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def pools_by_id(document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict):
        return {}
    pools = document.get("pools")
    if not isinstance(pools, list):
        return {}
    return {pool["id"]: pool for pool in pools if isinstance(pool, dict) and isinstance(pool.get("id"), str)}


def pool_for_model(table: dict[str, Any], model: str) -> str | None:
    pools = table.get("pools")
    if not isinstance(pools, dict):
        return None
    exact = pools.get(model)
    if isinstance(exact, str):
        return exact
    for key, value in pools.items():
        if key.endswith("*") and model.startswith(key[:-1]) and isinstance(value, str):
            return value
    return None


def vendor_for(entry: dict[str, Any], pool: str | None) -> str | None:
    declared = entry.get("vendor")
    if isinstance(declared, str) and declared:
        return declared
    if pool:
        return pool.split("-")[0]
    return None


def override_matches(override: Any, class_name: str, entry: dict[str, Any]) -> bool:
    if not isinstance(override, dict) or override.get("class") != class_name:
        return False
    demote = override.get("demote")
    if not isinstance(demote, dict):
        return False
    if demote.get("seat") != entry.get("seat"):
        return False
    model = demote.get("model")
    return model is None or model == entry.get("model")


def apply_overrides(chain: list[dict[str, Any]], overrides: Any, class_name: str) -> list[dict[str, Any]]:
    if not isinstance(overrides, list) or not overrides:
        return list(chain)
    kept: list[dict[str, Any]] = []
    demoted: list[dict[str, Any]] = []
    for entry in chain:
        target = demoted if any(override_matches(o, class_name, entry) for o in overrides) else kept
        target.append(entry)
    return kept + demoted


def reserve_state(table: dict[str, Any], pools: dict[str, dict[str, Any]], class_name: str) -> dict[str, Any] | None:
    policy = table.get("policy")
    reserve = policy.get("reserve") if isinstance(policy, dict) else None
    if not isinstance(reserve, dict):
        return None
    pool = reserve.get("pool")
    min_eligible = reserve.get("min_eligible")
    min_headroom = reserve.get("headroom_min_percent")
    admit_classes = reserve.get("admit_classes")
    if not isinstance(pool, str) or not isinstance(admit_classes, list):
        return None
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in (min_eligible, min_headroom)):
        return None
    pool_doc = pools.get(pool, {})
    eligible = pool_doc.get("eligibleAccounts")
    headroom = pool_doc.get("headroomPercent")
    why = None
    if isinstance(eligible, (int, float)) and not isinstance(eligible, bool) and eligible < min_eligible:  # pyrefly: ignore [unsupported-operation]
        noun = "account" if eligible == 1 else "accounts"
        why = f"{eligible:g} eligible {noun} (keep {min_eligible:g})"
    elif isinstance(headroom, (int, float)) and not isinstance(headroom, bool) and headroom < min_headroom:  # pyrefly: ignore [unsupported-operation]
        why = f"headroom {headroom:g}% (keep {min_headroom:g}%)"
    return {
        "pool": pool,
        "active": why is not None,
        "admitted": class_name in admit_classes,
        "eligible": eligible,
        "headroom": headroom,
        "why": why,
    }


def rung_pool(table: dict, entry: dict, pools: dict) -> str | None:
    return entry["pool"] if entry.get("pool") in pools else pool_for_model(table, entry["model"])


EFFORT_SUFFIX = re.compile(r"-(?:low|medium|high|xhigh|max)$")


def same_model(wanted: str, *names: str | None) -> bool:

    base = EFFORT_SUFFIX.sub("", wanted)
    return any(name and (wanted == name or base == EFFORT_SUFFIX.sub("", name)) for name in names)


def reservation_capacity(row: dict, pools: dict, policy: dict) -> tuple[str, int]:
    pool = row.get("pool")
    facts = pools.get(pool, {})
    provider = facts.get("provider")
    key = (
        provider
        if isinstance(provider, str) and provider.strip()
        else (pool.split("-", 1)[0] if pool else f"seat:{row['seat']}")
    )
    eligible = next(
        (
            facts[field]
            for field in ("eligibleAccounts", "usableAccounts")
            if isinstance(facts.get(field), int) and not isinstance(facts[field], bool)
        ),
        1,
    )
    multiplier = (policy.get("per_account") or {}).get(key, policy.get("default", 2))
    return key, max(0, eligible) * multiplier


class ReserveStopped(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum
        self.at = time.monotonic()


def stop_reserve(signum: int, _frame: Any) -> None:
    import signal

    for each in (signal.SIGTERM, signal.SIGINT):
        signal.signal(each, signal.SIG_IGN)
    raise ReserveStopped(signum)


MENU_POOL_FIELDS = (
    "status",
    "accounts",
    "eligibleAccounts",
    "headroomPercent",
    "weeklyRemainingPercent",
    "admissionPacePercent",
    "pace",
    "weeklyResetAt",
    "resetAt",
    "refills",
)

WORKFLOW_BRIDGE_SEATS = frozenset({"gpt-implementer", "grok-implementer", "gpt-explorer", "sol-consult"})

WORKFLOW_BRIEF_SEATS = frozenset({"cursor-seat", "devin-seat", "muse-seat", "muse-seat-local"})

WORKFLOW_EFFORT_BRIEF_SEATS = frozenset({"codex-sol"})

WORKFLOW_FIXED_FORWARDERS = frozenset({"codex-verifier", "codex-test-runner", "computer-use"})

WORKFLOW_AUTHOR_VENDORS = ("anthropic", "openai", "xai", "cursor", "cognition", "devin", "glm", "kimi")

CCGPT_EFFORTS = ("low", "medium", "high", "xhigh")

WORKFLOW_OPUS_STAGES = ("plan", "review", "judge", "synthesis", "orchestrator")


def chain_effort(table: dict[str, Any], class_name: str, seat: str) -> str | None:
    spec = (table.get("classes") or {}).get(class_name)
    for entry in (spec.get("chain") or []) if isinstance(spec, dict) else []:
        if isinstance(entry, dict) and entry.get("seat") == seat:
            return entry.get("effort")
    return None


def finish_text() -> str:
    for path in (
        Path(__file__).resolve().parent.parent / "config/coding-agents/FINISH.md",
        Path.home() / ".agents/policy/coding-agents/FINISH.md",
        Path.home() / ".agent-lb/managed/coding-agents/FINISH.md",
    ):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    print("route: warning: FINISH.md unavailable; omitting finish contract", file=sys.stderr)
    return ""


def seat_opts(seat: str, model: str | None, alias: str | None, effort: str | None) -> dict[str, Any]:
    opts: dict[str, Any] = {"agentType": seat}
    if seat in WORKFLOW_BRIDGE_SEATS:
        if alias:
            base = next((alias[: -len(f"-{e}")] for e in CCGPT_EFFORTS if alias.endswith(f"-{e}")), alias)
            opts["model"] = f"{base}-{effort}" if effort in CCGPT_EFFORTS else alias
    elif seat in WORKFLOW_BRIEF_SEATS:
        opts["effort"] = "low"
    elif seat not in WORKFLOW_EFFORT_BRIEF_SEATS and seat not in WORKFLOW_FIXED_FORWARDERS:
        if model:
            opts["model"] = model
        if effort:
            opts["effort"] = effort
    return opts


def workflow_quota():
    import tomllib

    path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "open-factory/quota.toml"
    try:
        mode = tomllib.loads(path.read_text()).get("mode", "tight")
    except FileNotFoundError:
        mode = "tight"
    if mode not in ("tight", "normal"):
        raise ValueError("invalid factory quota mode")
    return {
        "mode": mode,
        "default_model": "sol-latest",
        "opus_stages": list(WORKFLOW_OPUS_STAGES),
        "opus_effort": "medium",
        "excluded_work": ["status", "wake", "polling", "single-file"],
        "maintenance_model": "sol-latest",
        "auto_tighten_used_percent": 70,
    }


def workflow_stage_effort(table: dict[str, Any]) -> dict[str, Any]:
    standards = table.get("standards") if isinstance(table.get("standards"), dict) else {}
    efforts = {
        name: spec["effort"]
        for name, spec in standards.items()
        if isinstance(spec, dict) and spec.get("effort") in EFFORTS
    }
    if "verify" in efforts:
        efforts["facts"] = efforts["verify"]
    bands = ((table.get("policy") or {}).get("stage_effort") or {}).get("bands") or {}
    money = bands.get("money_path") or ["xhigh"]
    efforts["money_path"] = money[-1]
    return efforts


OPUS_AUTHORITY = frozenset({"verdict", "architecture"})

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

EFFORTS = ("low", "medium", "high", "xhigh", "max")


def new_ulid() -> str:
    import secrets

    value = (int(time.time() * 1000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    return "".join(CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


SYNTHETIC_PREFIXES = ("test", "probe", "synthetic", "smoke", "eval-")


def synthetic_decision(job_id: Any, caller: Any) -> bool:
    if os.environ.get("ROUTE_SYNTHETIC") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return any(isinstance(value, str) and value.lower().startswith(SYNTHETIC_PREFIXES) for value in (job_id, caller))


def quota_mode() -> str:
    import tomllib

    try:
        return workflow_quota()["mode"]
    except (ValueError, OSError, tomllib.TOMLDecodeError):
        return "tight"


def dispatch_standard(table: dict[str, Any], class_name: str, author_vendor: str | None) -> dict[str, Any]:
    standard = dict(table["standards"][class_name])
    override = (standard.get("by_author_vendor") or {}).get(author_vendor or "")
    if isinstance(override, dict):
        standard.update(override)
    return standard


MERGE_CLASSES = frozenset({"implement", "mechanical", "ui"})

VERDICT_CLASSES = frozenset({"verify", "review", "audit", "judge"})

METRIC_CLASSES = frozenset({"decide"})

REPAIR_WINDOW = timedelta(hours=72)

REDISPATCH_WINDOW = timedelta(hours=2)

SUBSCRIPTION_VENDORS = frozenset({"muse", "devin", "cognition"})

CLOSEOUT_DATA_KEYS = (
    "ok",
    "error",
    "subagent_type",
    "worker_model",
    "worker_effort",
    "wrapper_model",
    "fallback",
    "session_ids",
    "pool_pct",
    "exit_code",
    "infra_reason",
    "wall_s",
    "tokens_in",
    "tokens_out",
)

REVERT_LINE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")

MERGE_REFUSED = frozenset({"refused", "conflict", "failed", "error", "rejected"})


def outcome_event_id(source: str, key: str) -> str:
    return "evt_" + hashlib.sha256(f"{source}|{key}".encode("utf-8")).hexdigest()[:32]


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_since(value: str) -> timedelta:
    found = re.fullmatch(r"\s*(\d+)\s*([dhm])\s*", value or "")
    if not found:
        raise ValueError(f"--since wants <n>d, <n>h or <n>m, got {value!r}")
    amount, unit = int(found.group(1)), found.group(2)
    return {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]


def number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class OutcomeSources:
    def __init__(
        self,
        *,
        decisions: list[dict[str, Any]],
        ledger: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
        reverts: list[dict[str, Any]],
        request_logs: list[dict[str, Any]],
        seeds: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        self.decisions = [row for row in decisions if row.get("v") == 1 and row.get("decision_id")]
        self.ledger = ledger
        self.receipts = receipts
        self.reverts = reverts
        self.request_logs = request_logs
        self.seeds = seeds
        self.now = now


def git_reverts(receipts: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:

    repos = sorted({str(row.get("repo")) for row in receipts if isinstance(row.get("repo"), str)})
    reverts = []
    for repo in repos:
        if not Path(repo).expanduser().is_dir():
            continue
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(Path(repo).expanduser()),
                    "log",
                    "--all",
                    f"--since={utc_stamp(since)}",
                    "--grep=This reverts commit",
                    "--format=%H%x1f%cI%x1f%B%x1e",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for chunk in result.stdout.split("\x1e"):
            parts = chunk.strip().split("\x1f")
            if len(parts) < 3:
                continue
            for reverted in REVERT_LINE.findall(parts[2]):
                reverts.append({"sha": parts[0], "ts": parts[1], "reverts": reverted, "repo": repo})
    return reverts


def outcome_event(
    kind: str,
    source: str,
    key: str,
    ts: datetime,
    decision: dict[str, Any],
    data: dict[str, Any],
    job_id: str | None = None,
) -> dict[str, Any]:
    return {
        "v": 1,
        "event_id": outcome_event_id(source, key),
        "job_id": job_id or decision.get("job_id"),
        "decision_id": decision.get("decision_id"),
        "attempt": decision.get("attempt"),
        "parent_id": decision.get("parent_id"),
        "kind": kind,
        "ts": utc_stamp(ts),
        "source": source,
        "data": data,
    }


def log_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def summed_part(part: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    costs = []
    for row in rows:
        cost = number_or_none(log_value(row, "cost_usd", "costUsd"))
        tokens = number_or_none(row.get("tokens")) or sum(
            number_or_none(log_value(row, *keys)) or 0
            for keys in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"))
        )
        costs.append(None if cost == 0 and tokens > 0 else cost)
    if not rows or any(cost is None for cost in costs):
        return {"part": part, "source": "unknown", "usd": None}
    return {"part": part, "source": "request_logs", "usd": round(sum(costs), 6)}  # pyrefly: ignore [no-matching-overload]


LB_HEALTH_ATTEMPTS = 3

LB_HEALTH_RETRY_SECONDS = 5.0
