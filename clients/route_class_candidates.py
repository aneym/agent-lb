from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from route_anthropic_models import LOW_BAND_MAX_ELIGIBLE, cursor_models, family_alias  # pyrefly: ignore [missing-import]
from route_pace import TIERS, shadow_pace  # pyrefly: ignore [missing-import]
from route_pace_state import lease_unavailable, reservation_request, seat_is_up  # pyrefly: ignore [missing-import]
from route_reserve_state import apply_overrides, pool_for_model, pools_by_id, reserve_state, vendor_for  # pyrefly: ignore [missing-import]
from route_resolve_one import ServedModels, decisions_path, resolve_alias, workflow_seat  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools, fetch_stand_ins, print_stand_in  # pyrefly: ignore [missing-import]
from route_seat_states import pinned_model  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_OK,
    READ_TIMEOUT_SECONDS,
    env_path,
    parse_ts,
    pool_exhausted,
    table_bytes,
    table_path,
)


def class_candidates(
    table: dict[str, Any],
    class_name: str,
    spec: dict[str, Any],
    *,
    author_vendor: str | None,
    pools: dict[str, dict[str, Any]],
    seats: Any,
    served: ServedModels,
    leases: dict | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:

    chain = spec.get("chain") if isinstance(spec.get("chain"), list) else []
    entries = [entry for entry in chain if isinstance(entry, dict)]
    ordered = apply_overrides(entries, table.get("overrides"), class_name)
    routable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    aliases = table.get("aliases") if isinstance(table.get("aliases"), dict) else {}
    paced: list[str] = []
    cross_vendor = bool(spec.get("cross_vendor"))

    def skip(seat: str, alias: str, reason: str) -> None:
        skipped.append({"seat": seat, "alias": alias, "reason": reason})

    for entry in ordered:
        seat = entry.get("seat")
        alias = entry.get("model")
        if not isinstance(seat, str) or not isinstance(alias, str):
            continue
        model, why = resolve_alias(table, alias, served.get() if family_alias(table, alias) in aliases else [], seat)
        if model is None:
            skip(seat, alias, why)
            continue
        pool = pool_for_model(table, alias)
        vendor = vendor_for(entry, pool)
        if cross_vendor and author_vendor and vendor == author_vendor:
            skip(seat, alias, f"same vendor as author: {vendor}")
            continue
        if not seat_is_up(seats, seat):
            skip(seat, alias, "seat down per routing-state.json")
            continue
        pool_doc = pools.get(pool, {}) if pool else {}
        status = pool_doc.get("status")
        if pool_exhausted(pool_doc):
            skip(seat, alias, f"pool {pool} exhausted")
            continue
        lease_reason = lease_unavailable(table, {"seat": seat, "pool": pool}, pools, leases)
        if lease_reason:
            skip(seat, alias, lease_reason)
            continue
        min_tier = entry.get("min_tier")
        if min_tier in TIERS:
            eligible = pool_doc.get("eligibleAccounts")
            if isinstance(eligible, int) and eligible <= LOW_BAND_MAX_ELIGIBLE:
                band = "critical" if eligible <= 1 else "low"
                skip(seat, alias, f"pool {pool} {band}: {eligible} eligible account(s)")
                continue
            tier = shadow_pace(pool_doc)["tier"]
            verdict = "meets" if tier in TIERS and TIERS.index(tier) >= TIERS.index(min_tier) else "below"
            paced.append(f"{seat} admitted: shadow pace, pool {pool} tier {tier or 'unknown'} {verdict} {min_tier}")

        codex_pool = pools.get("openai-codex", {})
        codex_eligible = codex_pool.get("eligibleAccounts")
        if (
            spec.get("codex_empty_fallback")
            and vendor == "anthropic"
            and (
                pool_exhausted(codex_pool)
                or (isinstance(codex_eligible, int) and not isinstance(codex_eligible, bool) and codex_eligible <= 1)
            )
        ):
            band = "exhausted" if pool_exhausted(codex_pool) else f"critical: {codex_eligible} eligible"
            skip(seat, alias, f"Codex stand-in admission: its auditor's pool openai-codex is {band}")
            continue

        def resolve_auditor() -> tuple[dict[str, Any] | None, str | None]:

            auditor = None
            audit_spec = spec.get("audit")
            if isinstance(audit_spec, dict) and isinstance(audit_spec.get("by_author_vendor"), dict):
                audit_entry = audit_spec["by_author_vendor"].get(vendor)
                if not isinstance(audit_entry, dict) or not isinstance(audit_entry.get("model"), str):
                    return None, f"no auditor declared for author vendor {vendor}"
                audit_alias = audit_entry["model"]
                audit_models = served.get() if family_alias(table, audit_alias) in aliases else []
                audit_model, audit_why = resolve_alias(
                    table, audit_alias, audit_models, str(audit_entry.get("seat") or "")
                )
                if audit_model is None:
                    return None, f"its auditor {audit_entry['model']} is unavailable: {audit_why}"
                audit_pool = pool_for_model(table, audit_entry["model"])
                audit_lease_reason = lease_unavailable(
                    table, {"seat": audit_entry["seat"], "pool": audit_pool}, pools, leases
                )
                audit_seat = str(audit_entry.get("seat") or "")
                audit_pool_doc = pools.get(audit_pool, {}) if audit_pool else {}
                audit_eligible = audit_pool_doc.get("eligibleAccounts")
                audit_unavailable = f"its auditor: {audit_lease_reason}" if audit_lease_reason else None
                if pool_exhausted(audit_pool_doc):
                    audit_unavailable = f"its auditor's pool {audit_pool} is exhausted"
                elif isinstance(audit_eligible, int) and not isinstance(audit_eligible, bool) and audit_eligible <= 1:
                    audit_unavailable = f"its auditor's pool {audit_pool} is critical: {audit_eligible} eligible"
                alternate = None
                if audit_unavailable:
                    policy = table.get("policy")
                    via = policy.get("auditor_via") if isinstance(policy, dict) else None
                    alternate = via.get(audit_pool) if isinstance(via, dict) else None
                    if isinstance(alternate, dict) and all(
                        isinstance(alternate.get(key), str) and alternate[key] for key in ("pool", "vendor", "model")
                    ):
                        alternate_doc = pools.get(alternate["pool"], {})
                        alternate_eligible = alternate_doc.get("eligibleAccounts")
                        critical = (
                            isinstance(alternate_eligible, int)
                            and not isinstance(alternate_eligible, bool)
                            and alternate_eligible <= 1
                        )
                        if (
                            not alternate_doc
                            or pool_exhausted(alternate_doc)
                            or critical
                            or lease_unavailable(
                                table, {"seat": "cursor-seat", "pool": alternate["pool"]}, pools, leases
                            )
                            or not seat_is_up(seats, "cursor-seat")
                            or alternate["model"] not in cursor_models()
                        ):
                            alternate = None
                        else:
                            audit_seat = "cursor-seat"
                    else:
                        alternate = None
                    if alternate is None:
                        return None, audit_unavailable
                if audit_seat and not seat_is_up(seats, audit_seat):
                    return None, f"its auditor seat {audit_seat} is down per routing-state.json"
                auditor = {**audit_entry, "alias": audit_entry["model"], "model": audit_model, "reason": audit_why}
                if alternate is not None:
                    auditor.update(
                        {
                            "seat": audit_seat,
                            "via": alternate["vendor"],
                            "pool": alternate["pool"],
                            "model": alternate["model"],
                            "reason": f"{audit_unavailable}; auditor via {alternate['vendor']} pool {alternate['pool']}",
                        }
                    )
            return auditor, None

        auditor, audit_gap = resolve_auditor()
        if audit_gap:
            paced.append(f"{seat} advisory: {audit_gap}; review is on request only")
        routable.append(
            {
                "seat": seat,
                "model": model,
                "alias": alias,
                "pool": pool,
                "vendor": vendor,
                "poolStatus": status,
                "auditor": auditor,
                "audit_gap": audit_gap,
            }
        )

    reserve = reserve_state(table, pools, class_name)
    if reserve and reserve["active"] and not reserve["admitted"]:
        kept = []
        reserved = []
        for entry in routable:
            if entry["pool"] == reserve["pool"]:
                entry["reserved"] = f"reserved for orchestrators: {reserve['why']}"
                reserved.append(entry)
            else:
                kept.append(entry)
        routable = kept + reserved
    return routable, skipped, paced


def release_job_hold(job: str, until: float) -> str | None:

    why = "not attempted"
    while True:
        left = until - time.monotonic()
        if left < 0.2:
            return why
        code, listing = reservation_request("GET", "", timeout=min(READ_TIMEOUT_SECONDS, left))
        if code != EXIT_OK:
            why = f"listing holds failed: {listing.get('message') or listing.get('status')}"
        else:
            held = [row for row in listing.get("live") or [] if row.get("job") == job]
            if not held:
                return None
            for row in held:
                left = until - time.monotonic()
                if left < 0.2:
                    return why
                _code, body = reservation_request(
                    "POST",
                    f"/{row['reservation_id']}/release",
                    {"outcome": "cancelled"},
                    timeout=min(READ_TIMEOUT_SECONDS, left),
                )
                why = f"releasing {row['reservation_id']} failed: {body.get('message') or body.get('status')}"
        time.sleep(max(0.0, min(0.5, until - time.monotonic() - 0.2)))


def command_reservations(args: argparse.Namespace) -> int:
    code, result = reservation_request("GET", "")
    print(json.dumps(result))
    return code


def print_pick(result: dict[str, Any], *, as_json: bool) -> None:
    fields = ("session_id", "lane", "intended", "running", "since", "expected_return")
    result["standing_in"] = [{key: record.get(key) for key in fields} for record in fetch_stand_ins()["active"]]
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"seat      {result['seat']}")
    alias = result.get("alias")
    shown = result["model"] or "-"
    if alias and alias != result["model"]:
        shown = f"{shown} (from {alias})"
    print(f"model     {shown}")
    print(f"pool      {result['pool'] or '-'}")
    age = result.get("state_age_s")
    print(f"state age {'no state file' if age is None else f'{age}s'}")
    print(f"reason    {result['reason']}")
    reserve = result.get("reserve")
    if reserve and reserve["active"] and not reserve["admitted"]:
        print(f"reserve: {reserve['why']}")
    fallbacks = ", ".join(f"{entry['seat']}/{entry['model']}" for entry in result["fallbacks"]) or "none"
    print(f"fallbacks {fallbacks}")
    auditor = result.get("audit")
    if isinstance(auditor, dict):
        shown = auditor.get("model") or f"unresolved {auditor.get('alias')}: {auditor.get('reason')}"
        via = f" via {auditor['via']}" if auditor.get("via") else ""
        print(f"audit     {auditor.get('seat')}/{shown}{via} audits this seat's closeout before acceptance")
    for record in result["standing_in"]:
        print_stand_in(record)


def workflow_relay_seat(table: dict[str, Any]) -> dict[str, Any]:
    standard = (table.get("standards") or {}).get("relay")
    if not isinstance(standard, dict) or not standard.get("seat"):
        raise ValueError("no relay standard")
    alias = standard.get("wrapper_model") or standard.get("model")
    model = pinned_model(table, alias) or alias
    entry = {
        "seat": standard["seat"],
        "model": model,
        "alias": alias,
        "effort": standard.get("effort") or "low",
        "vendor": alias_vendor(table, alias) or "anthropic",
    }
    return workflow_seat(table, "relay", entry)


def read_decisions() -> list[dict[str, Any]]:
    try:
        lines = decisions_path().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("v") == 1 and row.get("kind") != "route.run.outcome":
            rows.append(row)
    return rows


def table_digest() -> str:
    path = table_path()
    return hashlib.sha256(table_bytes(path) if path.exists() else b"").hexdigest()


def alias_vendor(table: dict[str, Any], alias: str | None) -> str | None:
    if not alias:
        return None
    spec = (table.get("aliases") or {}).get(family_alias(table, alias))
    if isinstance(spec, dict):
        return spec.get("vendor")
    return "anthropic" if alias.startswith("claude-") else None


def decision_day(ts: Any) -> str | None:
    from zoneinfo import ZoneInfo

    stamp = parse_ts(ts)
    return stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() if stamp else None


class DispatchContext:
    def __init__(self, table: dict[str, Any], class_name: str, author_vendor: str | None) -> None:
        self.table = table
        self.class_name = class_name
        self.author_vendor = author_vendor
        self._reserve: dict[str, Any] | None | bool = False

    def reserve(self) -> dict[str, Any] | None:
        if self._reserve is False:
            document, _ = fetch_pools()
            self._reserve = reserve_state(self.table, pools_by_id(document), self.class_name)
        return self._reserve if isinstance(self._reserve, dict) else None

    def reserve_why(self) -> str | None:
        state = self.reserve()
        return state["why"] if state and state.get("active") else None


def outcome_events_path() -> Path:
    return env_path("OUTCOME_EVENTS", Path.home() / ".claude" / "logs" / "outcome-events.jsonl")


def outcomes_path() -> Path:
    return env_path("ROUTE_OUTCOMES", Path.home() / ".agent-lb/audits/routing/outcomes.jsonl")
