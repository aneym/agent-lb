from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from route_anthropic_models import percent, read_state  # pyrefly: ignore [missing-import]
from route_class_candidates import DispatchContext, alias_vendor, class_candidates  # pyrefly: ignore [missing-import]
from route_grok import rung_block  # pyrefly: ignore [missing-import]
from route_pace_state import (  # pyrefly: ignore [missing-import]
    lease_unavailable,
    load_pool_history,
    pace_state,
    pick_leases,
    record_caller_choice,
    record_pool_history,
    seat_is_up,
    state_age_seconds,
)
from route_reserve_state import apply_overrides, pools_by_id, quota_mode, reserve_state, rung_pool, same_model  # pyrefly: ignore [missing-import]
from route_resolve_one import ServedModels, ladder_entries, rung_model  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools, fetch_stand_ins, print_stand_in  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_OK,
    EXIT_UNROUTABLE,
    EXIT_WAIT,
    STATE_MAX_AGE_SECONDS,
    env_path,
    pool_exhausted,
    setup_error,
)


def ladder_walk(
    table: dict,
    class_name: str,
    pools: dict,
    seats: Any,
    served: ServedModels,
    history: list[dict],
    now: datetime,
    author: str | None = None,
    skip_ids: list[str] | None = None,
    prefer: str | None = None,
    model: str | None = None,
    leases: dict | None = None,
) -> dict:

    wanted = model
    author = "cognition" if author == "devin" else author
    entries = apply_overrides(ladder_entries(table, class_name, author) or [], table.get("overrides"), class_name)
    policy = table.get("policy") or {}
    guarded_pools = (policy.get("pace") or {}).get("guarded_pools")
    pace = {
        pool: pace_state(pools.get(pool, {}), history, policy, now)
        for entry in entries
        if (pool := rung_pool(table, entry, pools))
    }
    for pool in pace:
        if pool_exhausted(pools.get(pool, {})):
            pace[pool]["state"] = "exhausted"
    reserve = reserve_state(table, pools, class_name)
    ordered = [
        entry
        for entry in entries
        if (entry["id"] != "sonnet-cursor-high" or (reserve and reserve["active"]))
        and entry.get("promote") == pace.get(rung_pool(table, entry, pools), {}).get("state")
        and entry.get("promote")
    ]
    ordered += [entry for entry in entries if entry not in ordered]
    rows, opened, soft, skipped = [], [], [], []
    intended = None
    for entry in ordered:
        pool = rung_pool(table, entry, pools)
        state = pace.get(pool, {}).get("state", "unknown")
        gate = entry.get("gate") or {}
        reason = None
        if "gate" in entry and gate.get("open") is not True:
            reason = f"gated: {gate.get('until')}"
        elif entry.get("when") and entry["when"] != state:
            reason = f"when {entry['when']}: {pool} is {state}"
        if intended is None and reason is None:
            intended = entry["id"]
        if reason is None and entry["id"] == "sonnet-cursor-high" and not (reserve and reserve["active"]):
            reason = "review adapter only while Claude is short"
        if (
            reason is None
            and entry.get("reserve_hard")
            and reserve
            and reserve["active"]
            and not reserve["admitted"]
            and pool == reserve["pool"]
        ):
            reason = f"reserved for orchestrators: {reserve['why']}"
        if reason is None and entry.get("stand_in_only"):
            unavailable = all(row["status"] == "skipped" for row in rows if not row.get("stand_in_only"))
            if not unavailable:
                reason = "stand-in only when the default is unavailable"
        waived = None
        if reason is not None and prefer in (entry["id"], entry["seat"]):
            waived, reason = reason, None
        reason = reason or rung_block(table, class_name, entry, pools)
        if reason is None and entry["id"] in (skip_ids or []):
            reason = "skipped by caller"
        if reason is None and entry.get("pool") and entry["pool"] not in pools and pool is None:
            reason = f"unresolvable: pool {entry['pool']} unavailable"
        if reason is None and pool_exhausted(pools.get(pool, {})):
            reason = f"pool {pool} exhausted"
        if reason is None:
            reason = lease_unavailable(table, {"seat": entry["seat"], "pool": pool}, pools, leases)
        model, why = rung_model(table, entry, served) if reason is None else (None, "")
        pending = model is not None and why.startswith("pending: ")
        if reason is None and model is None:
            reason = f"unresolvable: {why}"
        if reason is None and not seat_is_up(seats, entry["seat"]):
            record = seats.get(entry["seat"], {}) if isinstance(seats, dict) else {}
            reason = f"seat down: {record.get('error') or 'routing-state.json'}"
        if (
            reason is None
            and class_name == "verify"
            and entry["maker"] == author
            and not entry.get("same_vendor_fallback")
        ):
            reason = f"same maker {author}"
        audit = None
        audit_gap = None

        if reason is None and class_name in ("implement", "mechanical"):
            review = ladder_walk(table, "verify", pools, seats, served, history, now, entry["maker"], leases=leases)
            audit = review["chosen"]
            primary = pools.get("anthropic-general", {})
            eligible = primary.get("eligibleAccounts")
            critical = pool_exhausted(primary) or (
                isinstance(eligible, int) and not isinstance(eligible, bool) and eligible <= 1
            )
            if audit is None and entry["maker"] != "anthropic" and critical:
                legacy = table["classes"].get(class_name, {}).get("audit", {})
                candidate = {
                    **entry,
                    "vendor": {"xai": "cursor", "cognition": "devin"}.get(entry["maker"], entry["maker"]),
                }
                available, rejected, _ = class_candidates(
                    table,
                    class_name,
                    {"chain": [candidate], "audit": legacy},
                    author_vendor=None,
                    pools=pools,
                    seats=seats,
                    served=served,
                    leases=leases,
                )
                audit = available[0]["auditor"] if available else None
                if audit is not None:
                    audit.update(rung=None, maker="anthropic" if audit.get("via") else audit.get("vendor"))
                if audit is None:
                    audit_gap = (
                        available[0].get("audit_gap") if available else rejected[0]["reason"] if rejected else None
                    )
            if audit is None:
                gaps = "; ".join(f"{row['rung']}: {row['reason']}" for row in review["skipped"])
                audit_gap = audit_gap or f"no open reviewer for {entry['maker']}: {gaps}"
        row = {
            "rung": entry["id"],
            "seat": entry["seat"],
            "model": model,
            "alias": entry["model"],
            "effort": entry.get("effort"),
            "maker": entry["maker"],
            "vendor": entry["maker"],
            "stand_in_only": bool(entry.get("stand_in_only")),
            "pool": pool,
            "same_vendor": class_name == "verify" and entry["maker"] == author,
            "audit": audit,
            "auditor": audit,
            "audit_gap": audit_gap,
            "read_only": bool(entry.get("read_only")),
        }
        if pending:
            row["model_pending"] = True
        if waived:
            row["waived"] = waived
        if reason:
            row.update(status="skipped", reason=reason)
        else:
            reasons = []
            if state == "low" and (guarded_pools is None or pool in guarded_pools):
                facts = pace[pool]
                empties, reset = facts.get("empties_in_h"), facts.get("hours_left")
                reasons.append(
                    f"running low: empties in {empties:.3g} h, resets in {reset:.3g} h"
                    if empties is not None and reset is not None
                    else "running low: pace below policy"
                )
            if reserve and reserve["active"] and not reserve["admitted"] and pool == reserve["pool"]:
                reasons.append(f"reserved for orchestrators: {reserve['why']}")
            if entry.get("same_vendor_fallback"):
                reasons.append(f"same maker {entry['maker']}")
            row.update(
                status="soft" if reasons else "open", reason="; ".join(reasons) if reasons else "first open rung"
            )
            (soft if reasons else opened).append(row)
        rows.append(row)
        if row["status"] != "open":
            skipped.append({"rung": row["rung"], "reason": row["reason"]})
    chosen = next(iter(opened or soft), None)
    if chosen is not None and not opened:
        chosen = {**chosen, "reason": chosen["reason"] + "; no other rung open"}

    default = (
        chosen
        if prefer is None
        else ladder_walk(table, class_name, pools, seats, served, history, now, author, skip_ids, leases=leases)[
            "chosen"
        ]
    )
    refused = None
    if prefer is not None:
        preferred = [row for row in opened + soft if prefer in (row["rung"], row["seat"])]
        if wanted is not None and preferred:
            runs = sorted({row["alias"] for row in preferred})
            preferred = [row for row in preferred if same_model(wanted, row["model"], row["alias"])] or [
                row for row in preferred if row.get("model_pending")
            ]
            if not preferred:
                refused = f"no open {prefer} rung on the {class_name} ladder runs {wanted}; it runs {', '.join(runs)}"
        chosen = next(iter(preferred), None)
        if chosen is not None and chosen["same_vendor"] and default is not None and not default["same_vendor"]:
            refused = f"same maker {author} while {default['rung']} can review"
            chosen = None
        if chosen is not None:
            chosen = {
                **chosen,
                "reason": f"preferred by caller over {default['rung'] if default else 'none'}"
                + (f"; {chosen['reason']}" if chosen["status"] == "soft" else "")
                + (f"; waived {chosen['waived']}" if chosen.get("waived") else ""),
            }
    return {
        "chosen": chosen,
        "default": default["rung"] if default else None,
        "refused": refused,
        "rungs": rows,
        "routable": opened + soft,
        "fallbacks": [row for row in opened + soft if chosen is None or row["rung"] != chosen["rung"]],
        "skipped": skipped,
        "intended": intended,
        "pace": pace,
    }


def command_ladder_pick(args: argparse.Namespace, table: dict) -> int:
    if args.task_class == "verify" and not args.author_vendor:
        setup_error("unknown_author", "verify requires --author-vendor <maker>", args.json)
    state = read_state()
    age = state_age_seconds(state)
    stale = bool(state) and (age is None or age > STATE_MAX_AGE_SECONDS)
    document, error = fetch_pools(timeout=3.0)
    now = datetime.now(timezone.utc)
    history = load_pool_history(now)
    if document is not None:
        record_pool_history(document, now)
    elif isinstance(state.get("pools"), dict):
        document = state["pools"]
    pools = pools_by_id(document)
    leases = pick_leases()
    walk = ladder_walk(
        table,
        args.task_class,
        pools,
        None if stale else state.get("seats"),
        ServedModels(),
        history,
        now,
        args.author_vendor,
        args.skip,
        args.prefer,
        leases=leases,
    )
    walk["lease_availability"] = "known" if leases is not None else "unknown"
    chosen = walk.pop("chosen")
    refused = walk.pop("refused")
    rungs = walk.pop("rungs")
    walk.pop("routable")
    if args.prefer and not any(args.prefer in (row["rung"], row["seat"]) for row in rungs):
        setup_error(
            "unknown_rung", f"--prefer {args.prefer!r} is not a rung or seat on the {args.task_class} ladder", args.json
        )
    if chosen is None:
        message = f"nothing routable for class {args.task_class!r}"
        if args.prefer:
            why = refused or next(row.get("reason") for row in rungs if args.prefer in (row["rung"], row["seat"]))
            message = f"preferred {args.prefer!r} cannot run: {why}"
        print(f"route: {message}", file=sys.stderr)
        if args.json:
            print(json.dumps({"error": "unroutable", "message": message, **walk}))
        else:
            for entry in walk["skipped"]:
                print(f"  skipped {entry['rung']}: {entry['reason']}", file=sys.stderr)
        return EXIT_UNROUTABLE
    chosen.pop("status")
    result = {
        "class": args.task_class,
        "ladder": table["ladder"],
        "state_age_s": None if age is None else round(age, 1),
        "reserve": reserve_state(table, pools, args.task_class),
        **chosen,
        **walk,
    }
    fields = ("session_id", "lane", "intended", "running", "since", "expected_return")
    result["standing_in"] = [{key: record.get(key) for key in fields} for record in fetch_stand_ins()["active"]]
    if error:
        result["reason"] += f"; pool status unknown ({error})"  # pyrefly: ignore [unsupported-operation]
    record_caller_choice(args, result)
    if args.json:
        print(json.dumps(result))
    else:
        for pool, facts in result["pace"].items():  # pyrefly: ignore [missing-attribute]
            label = pool.replace("-general", "").replace("-codex", "")
            label = "cursor" if pool in ("cursor-models", "cursor-other") else label
            words = {
                "low": "running low",
                "rich": "running rich",
                "on_pace": "on pace",
                "exhausted": "empty",
                "unknown": "unknown",
            }[facts["state"]]
            detail = f"remaining {percent(facts['remaining'])}"
            print(f"{label:<12}{words:<14}{detail}")
        for entry in result["skipped"]:  # pyrefly: ignore [not-iterable]
            print(f"  skipped {entry['rung']}: {entry['reason']}")
        auditor = result.get("audit")
        if auditor and auditor.get("via"):
            print(f"audit     {auditor['seat']}/{auditor['model']} via {auditor['via']}")
        for record in result["standing_in"]:
            print_stand_in(record)
        print(f"→ {result['model']} ({result['pool']})")
    return EXIT_OK


def arm_block(ctx: DispatchContext, arm: dict[str, Any]) -> str | None:
    explore = ctx.table.get("explore") or {}
    standard = ctx.table["standards"][ctx.class_name]
    if ctx.class_name in (explore.get("never_classes") or []) or getattr(ctx, "requested_class", ctx.class_name) in (
        explore.get("never_classes") or []
    ):
        return f"{ctx.class_name} never explores"
    if explore.get("enabled") is not True:
        return "explore disabled"
    if standard.get("explore") is not True:
        return f"{ctx.class_name} standard does not explore"
    if arm.get("paused"):
        return "paused"
    gate = arm.get("gate")
    if isinstance(gate, dict) and gate.get("open") is False:
        return f"gate closed until {gate.get('until') or 'reopened'}"
    wanted = arm.get("author_vendor")
    if wanted and wanted != ctx.author_vendor:
        return f"author vendor {ctx.author_vendor or 'unset'} is not {wanted}"
    if alias_vendor(ctx.table, arm.get("model")) == "anthropic":
        if quota_mode() == "tight":
            return "tight quota mode holds Claude arms"
        why = ctx.reserve_why()
        if why:
            return f"anthropic reserve: {why}"
    return None


def dispatch_wait(reason: str, class_name: str, job_id: str, as_json: bool) -> int:
    document = {"status": "wait", "class": class_name, "job_id": job_id, "reason": reason}
    print(json.dumps(document, sort_keys=True) if as_json else f"wait: {reason}")
    return EXIT_WAIT


def merge_receipts_path() -> Path:
    return env_path("STUDIO_MERGE_RECEIPTS", Path.home() / ".agent-rails" / "studio-merge" / "receipts.jsonl")


AUDIT_VERDICTS = ("pass", "fix-needed")
