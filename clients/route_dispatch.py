from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import route_heal  # pyrefly: ignore [missing-import]
from route_anthropic_models import (  # pyrefly: ignore [missing-import]
    blocked_patterns,
    family_alias,
    http_get,
    is_retired,
    ledger_now_iso,
    read_state,
    served_models,
)
from route_class_candidates import (  # pyrefly: ignore [missing-import]
    DispatchContext,
    alias_vendor,
    class_candidates,
    decision_day,  # noqa: F401  # pyrefly: ignore [missing-import]
    read_decisions,
    table_digest,
)
from route_grok import (  # pyrefly: ignore [missing-import]
    constraint_choice,  # noqa: F401  # pyrefly: ignore [missing-import]
    hands_refusal,  # noqa: F401  # pyrefly: ignore [missing-import]
    seat_defaults,  # noqa: F401  # pyrefly: ignore [missing-import]
)
from route_ladder_walk import (  # pyrefly: ignore [missing-import]
    arm_block,  # noqa: F401  # pyrefly: ignore [missing-import]
    dispatch_wait,
    ladder_walk,
)
from route_learning_text import learning_outcomes  # noqa: F401  # pyrefly: ignore [missing-import]
from route_pace_state import (  # pyrefly: ignore [missing-import]
    append_ledger,
    pick_leases,  # noqa: F401  # pyrefly: ignore [missing-import]
    record_explicit_model,
    state_age_seconds,
)
from route_quota import authority_fallback, quota_swap_state, swapped_standard  # pyrefly: ignore [missing-import]
from route_reserve_state import (  # pyrefly: ignore [missing-import]
    EFFORTS,
    MENU_POOL_FIELDS,
    MERGE_REFUSED,
    OutcomeSources,
    dispatch_standard,
    new_ulid,
    outcome_event,
    pool_for_model,
    pools_by_id,
    read_jsonl,
    seat_opts,
    synthetic_decision,
)
from route_resolve_one import ServedModels, append_decision, five_hour_windows, ladder_entries, resolve_alias  # pyrefly: ignore [missing-import]
from route_seat import fetch_pools  # pyrefly: ignore [missing-import]
from route_seat_states import (  # pyrefly: ignore [missing-import]
    api_request_logs,
    attributed_outcome,  # noqa: F401  # pyrefly: ignore [missing-import]
    learning_window,  # noqa: F401  # pyrefly: ignore [missing-import]
    pinned_model,
)
from route_spread import dispatch_choice  # noqa: F401  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_OK,
    EXIT_UNROUTABLE,
    STATE_MAX_AGE_SECONDS,
    fail,
    ledger_path,  # noqa: F401  # pyrefly: ignore [missing-import]
    load_table,
    now_iso,
    parse_ts,
    setup_error,
)


def build_menu(class_names: list[str] | None = None) -> dict[str, Any]:

    table = load_table()
    classes = table.get("classes") if isinstance(table.get("classes"), dict) else {}
    state = read_state()
    age = state_age_seconds(state)
    stale = bool(state) and (age is None or age > STATE_MAX_AGE_SECONDS)
    seats = None if stale else state.get("seats")
    accounts = http_get("/api/accounts", timeout=3.0)
    document, pools_error = fetch_pools(timeout=3.0, accounts=accounts, refills=True)
    if document is None and isinstance(state.get("pools"), dict):
        document = state["pools"]
    pools = pools_by_id(document)
    windows = five_hour_windows(accounts.body) if accounts.json_ok else {}

    now = datetime.now(timezone.utc)
    served = ServedModels()
    menu_classes: dict[str, Any] = {}
    used_pools: set[str] = set()
    for class_name in class_names or list(classes):
        spec = classes.get(class_name)
        if not isinstance(spec, dict):
            fail(f"unknown class {class_name!r}; known classes: {', '.join(sorted(classes))}")
        if spec.get("driver"):  # pyrefly: ignore [missing-attribute]
            menu_classes[class_name] = {"driver": True, "crossVendor": False, "seats": [], "excluded": []}
            continue
        if ladder_entries(table, class_name) is not None:
            walk = ladder_walk(table, class_name, pools, seats, served, now)
            used_pools.update(walk["pace"])
            menu_classes[class_name] = {
                "driver": False,
                "crossVendor": class_name == "verify",
                "rungs": walk["rungs"],
                "seats": [{"id": f"{row['seat']}.{row['model']}", **row} for row in walk["routable"]],
                "excluded": [
                    {key: row.get(key) for key in ("seat", "model", "alias", "rung", "reason")}
                    for row in walk["rungs"]
                    if row["status"] != "open"
                ],
                "paced": [],
                "ladder": table["ladder"],
            }
            continue
        routable, skipped, paced = class_candidates(
            table, class_name, spec, author_vendor=None, pools=pools, seats=seats, served=served
        )
        entries = []
        for entry in routable:
            entries.append({"id": f"{entry['seat']}.{entry['model']}", **entry})
            if entry["pool"]:
                used_pools.add(entry["pool"])
        menu_classes[class_name] = {
            "driver": False,
            "crossVendor": bool(spec.get("cross_vendor")),  # pyrefly: ignore [missing-attribute]
            "seats": entries,
            "excluded": skipped,
            "paced": paced,
        }

    pool_facts = {}
    for pool_id in sorted(set(pools) | used_pools):
        pool = pools.get(pool_id, {})
        facts = {field: pool.get(field) for field in MENU_POOL_FIELDS}
        facts.update(windows.get(pool_id, {"fiveHourHeadroomPercent": None, "fiveHourResetAt": None}))
        pool_facts[pool_id] = facts
    return {
        "generatedAt": now_iso(),
        "poolsSource": (document or {}).get("source") if isinstance(document, dict) else None,
        "poolsError": pools_error,
        "stateAgeS": None if age is None else round(age, 1),
        "stateIgnored": stale,
        "pools": pool_facts,
        "classes": menu_classes,
    }


def arm_cap_block(ctx: DispatchContext, arm: dict[str, Any], today: list[dict[str, Any]]) -> str | None:
    explore = ctx.table.get("explore") or {}
    arm_jobs = {
        row.get("job_id") for row in today if row.get("class") == ctx.class_name and row.get("arm_id") == arm.get("id")
    }
    cap = explore.get("daily_cap_per_arm")
    if isinstance(cap, int) and len(arm_jobs) >= cap:
        return f"daily cap {cap} jobs reached"
    if alias_vendor(ctx.table, arm.get("model")) == "anthropic":
        claude_jobs = {
            row.get("job_id") for row in today if alias_vendor(ctx.table, row.get("model_alias")) == "anthropic"
        }
        claude_cap = explore.get("claude_daily_cap_jobs")
        if isinstance(claude_cap, int) and len(claude_jobs) >= claude_cap:
            return f"Claude explore cap {claude_cap} jobs reached"
    return None


def command_dispatch(args: argparse.Namespace, table: dict[str, Any] | None = None) -> int:
    table = load_table() if table is None else table
    standards = table.get("standards") if isinstance(table.get("standards"), dict) else {}
    requested_class = args.task_class
    class_name = (table.get("class_aliases") or {}).get(requested_class, requested_class)
    if class_name not in standards:
        print(f"route: unknown dispatch class {args.task_class!r}; known: {', '.join(standards)}", file=sys.stderr)
        return EXIT_UNROUTABLE
    if args.effort is not None and args.effort not in EFFORTS:
        print(f"route: unknown effort {args.effort!r}", file=sys.stderr)
        return EXIT_UNROUTABLE
    args.task_class = class_name
    if requested_class == "fix" and "fix" not in args.tag:
        args.tag = [*args.tag, "fix"]
    job_id = args.job or f"job_{new_ulid()}"
    history = read_decisions()
    job_prior = [row for row in history if row.get("job_id") == job_id]
    prior = [row for row in job_prior if row.get("class") == class_name]
    attempt = max((row.get("attempt") or 1 for row in job_prior), default=0) + 1
    parent_id = args.parent
    if parent_id is None and job_prior and job_prior[-1].get("class") != class_name:
        parent_id = job_prior[-1].get("decision_id")
    standard = dispatch_standard(table, class_name, args.author_vendor)
    if args.pools_file and not args.dry_run:
        setup_error("pools_file", "--pools-file needs --dry-run", args.json)
    pools_doc = json.loads(Path(args.pools_file).read_text()) if args.pools_file else fetch_pools()[0]
    mode_state = (
        {"mode": pools_doc.get("quotaMode", "anthropic")}
        if args.pools_file
        else quota_swap_state(
            table, fetch_pools, api_request_logs, append_ledger if not args.dry_run else lambda _row: None
        )
    )
    explicit = any(value is not None for value in (args.seat, args.model, args.effort))
    if not explicit:
        standard = authority_fallback(standard, mode_state) or standard
    if not prior and not explicit:
        standard = swapped_standard(table, class_name, standard, mode_state)
    ctx = DispatchContext(table, class_name, args.author_vendor)
    ctx.requested_class = requested_class
    choice, eligible, sticky = route_heal.select(
        sys.modules[__name__], args, table, standard, ctx, prior, history, mode_state, pools_doc, job_id
    )
    if choice is None:
        return dispatch_wait("all dispatch seats excluded or unavailable", class_name, job_id, args.json)
    seat, model_alias = choice["seat"], choice["model_alias"]
    served = ServedModels()
    model = None
    if model_alias:
        needs_list = alias_vendor(table, model_alias) == "openai"
        model, _ = resolve_alias(table, model_alias, served.get() if needs_list else [], seat)
        unaliased = family_alias(table, model_alias) not in (table.get("aliases") or {})
        if model is None and choice["arm"] == "explicit" and unaliased:
            if not is_retired(model_alias, blocked_patterns(table)):
                model = model_alias
                record_explicit_model(model_alias, f"route dispatch {class_name}")
    wrapper_alias = standard.get("wrapper_model")
    if wrapper_alias and wrapper_alias == standard.get("model"):
        wrapper_alias = model_alias
    wrapper_model = resolve_alias(table, wrapper_alias, [], None)[0] if wrapper_alias else None
    effort = choice["effort"]
    row = {
        "v": 1,
        "decision_id": f"dcn_{new_ulid()}",
        "ts": ledger_now_iso(),
        "class": class_name,
        "requested_class": requested_class,
        "job_id": job_id,
        "attempt": attempt,
        "parent_id": parent_id,
        "caller": args.caller,
        "arm": choice["arm"],
        "arm_id": choice["arm_id"],
        "seat": seat,
        "model_alias": model_alias,
        "model": model,
        "effort": effort,
        "requested_effort": args.effort,
        "wrapper_model": wrapper_model,
        "pool": choice["pool"] or (pool_for_model(table, model_alias) if model_alias else None),
        "opts": seat_opts(seat, model, model_alias, effort),
        "explicit": choice["arm"] == "explicit",
        "sticky": sticky,
        "propensity": float(choice["propensity"]),
        "eligible_arms": eligible,
        "reason": choice["reason"],
        "table_sha256": table_digest(),
        "policy_rev": str(table.get("policy_rev") or "unversioned"),
    }
    if synthetic_decision(job_id, args.caller):
        row["synthetic"] = True
    if not args.dry_run:
        append_decision(row)
    if args.json:
        print(json.dumps(row, sort_keys=True))
    else:
        print(f"{class_name} -> {seat} {model or model_alias} {effort or ''} ({row['arm']}:{row['arm_id']})".rstrip())
    return EXIT_OK


def snapshot_resolved_models(
    table: dict[str, Any], standards: dict[str, Any], seats: dict[str, dict[str, Any]], background: Any
) -> dict[str, str]:
    wanted = {"opus-latest", "sonnet-latest", "haiku-latest", "sol-latest"}
    wanted.update(spec.get("model") for spec in seats.values())  # pyrefly: ignore [bad-argument-type]
    for spec in standards.values():
        if isinstance(spec, dict):
            wanted.update((spec.get("model"), spec.get("wrapper_model")))  # pyrefly: ignore [bad-argument-type]
    wanted.add(background)
    served: list[str] | None = None
    resolved: dict[str, str] = {}
    for alias in sorted(name for name in wanted if isinstance(name, str) and name.endswith("-latest")):
        model = pinned_model(table, alias)
        if not model:
            if served is None:
                served, _ = served_models()
            model, _ = resolve_alias(table, alias, served) if served else (None, "")
        if model:
            resolved[alias] = model
    return resolved


def policy_snapshot(table: dict[str, Any]) -> dict[str, Any]:
    standards = table.get("standards") if isinstance(table.get("standards"), dict) else {}
    seats: dict[str, dict[str, Any]] = {}
    for spec in standards.values():
        seat = spec.get("seat")
        if seat and seat not in seats:
            seats[seat] = {
                "model": spec.get("model"),
                "model_id": pinned_model(table, spec.get("model")),
                "effort": spec.get("effort"),
            }
    for seat, spec in (table.get("seats") or {}).items():
        if seat not in seats and isinstance(spec, dict) and spec.get("model"):
            seats[seat] = {
                "model": spec.get("model"),
                "model_id": pinned_model(table, spec.get("model")),
                "effort": spec.get("effort"),
            }
    background = table.get("background_model")
    return {
        "resolved_models": snapshot_resolved_models(table, standards, seats, background),
        "policy_rev": table.get("policy_rev"),
        "autocompact_window": table.get("autocompact_window"),
        "table_sha256": table_digest(),
        "precedence": table.get("precedence"),
        "standards": standards,
        "background_model": pinned_model(table, background) or background,
        "background_model_alias": background,
        "explore_enabled": bool((table.get("explore") or {}).get("enabled")),
        "seats": dict(sorted(seats.items())),
    }


def row_time(row: dict[str, Any]) -> datetime | None:
    stamp = parse_ts(row.get("ts"))
    if stamp is None:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def replay_sources(directory: Path) -> OutcomeSources:
    meta = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    now = parse_ts(meta.get("now")) if isinstance(meta, dict) else None
    return OutcomeSources(
        decisions=read_jsonl(directory / "route-decisions.jsonl"),
        ledger=read_jsonl(directory / "dispatch.jsonl"),
        receipts=read_jsonl(directory / "receipts.jsonl"),
        reverts=read_jsonl(directory / "reverts.jsonl"),
        request_logs=read_jsonl(directory / "request-logs.jsonl"),
        seeds=read_jsonl(directory / "events.jsonl"),
        now=now or datetime.now(timezone.utc),
    )


class DecisionIndex:
    def __init__(self, decisions: list[dict[str, Any]]) -> None:
        self.by_id = {row["decision_id"]: row for row in decisions}
        self.by_job: dict[str, list[dict[str, Any]]] = {}
        for row in decisions:
            self.by_job.setdefault(str(row.get("job_id")), []).append(row)
        for rows in self.by_job.values():
            rows.sort(key=lambda row: (row.get("attempt") or 1, str(row.get("ts") or "")))

    def lookup(self, ref: Any) -> dict[str, Any] | None:
        if not isinstance(ref, str) or not ref:
            return None
        if ref in self.by_id:
            return self.by_id[ref]
        rows = self.by_job.get(ref)
        return rows[-1] if rows else None

    def parent(self, row: dict[str, Any]) -> dict[str, Any] | None:
        parent = self.lookup(row.get("parent_id"))
        if parent is None or parent.get("job_id") == row.get("job_id"):
            return None
        return self.by_job[str(parent.get("job_id"))][0]

    def root(self, row: dict[str, Any]) -> dict[str, Any]:
        seen = {row.get("job_id")}
        current = row
        while True:
            parent = self.parent(current)
            if parent is None or parent.get("job_id") in seen:
                return current
            seen.add(parent.get("job_id"))
            current = parent


def merge_events(src: OutcomeSources, index: DecisionIndex, start: datetime) -> list[dict[str, Any]]:
    events = []
    for receipt in src.receipts:
        ts = row_time(receipt)
        status = str(receipt.get("status") or "merged").lower()
        if ts is None or ts < start or status in MERGE_REFUSED:
            continue
        merge_sha = receipt.get("merged_sha")
        if not isinstance(merge_sha, str) or not merge_sha:
            continue
        commits = [commit for commit in receipt.get("commits") or [] if isinstance(commit, dict)]
        if not commits and receipt.get("dispatch_ids"):
            commits = [{"sha": merge_sha, "dispatch_ids": receipt.get("dispatch_ids")}]
        for commit in commits:
            commit_sha = str(commit.get("sha") or merge_sha)
            for ref in commit.get("dispatch_ids") or []:
                decision = index.lookup(ref)
                if decision is None:
                    continue
                data = {
                    "merge_sha": merge_sha,
                    "commit_sha": commit_sha,
                    "branch": receipt.get("branch"),
                    "repo": receipt.get("repo"),
                }
                events.append(
                    outcome_event("merge", "studio-merge", f"{merge_sha}|{commit_sha}|{ref}", ts, decision, data)
                )
    return events
