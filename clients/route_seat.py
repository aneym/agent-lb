from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import route_heal  # pyrefly: ignore [missing-import]
from route_anthropic_models import family_alias, http_get, request  # pyrefly: ignore [missing-import]
from route_cache import HttpResult  # pyrefly: ignore [missing-import]
from route_reserve_state import pools_by_id  # pyrefly: ignore [missing-import]
from route_resolve_one import ServedModels, ladder_entries, resolve_alias, rung_model  # pyrefly: ignore [missing-import]
from route_storage import atomic_write  # pyrefly: ignore [missing-import]
from route_versioned_rules import (  # pyrefly: ignore [missing-import]
    EXIT_OK,
    READ_TIMEOUT_SECONDS,
    ROUTABLE_STATUSES,
    SEAT_NAME,
    add_pool_refills,
    base_url,
    class_seat_entries,
    fixture_file,
    now_iso,
    policy_git,
    pool_ids,
    pool_snapshot_path,
    reduced_entry,
    remaining,
    setup_error,
    versioned_rules,
)


def command_seat(args: argparse.Namespace) -> int:
    table, rules, clone = versioned_rules(args.rules, args.json)
    seats = table["seats"]
    name = args.name
    if not SEAT_NAME.fullmatch(name) or name not in seats:
        setup_error("unknown_seat", f"unknown seat {name!r}", args.json)
    named = [(task_class, entry) for task_class, entry in class_seat_entries(table["classes"]) if entry["seat"] == name]
    rung = None
    if args.task_class is not None and args.task_class not in table["classes"]:
        setup_error("unknown_class", f"unknown class {args.task_class!r} for seat {name}", args.json)
    ladder = ladder_entries(table, args.task_class) if args.task_class is not None else None
    entry = next((item for item in ladder or [] if item["seat"] == name), None)
    if entry is not None:
        rung = entry.get("id")
        named = [(args.task_class, entry)]
    first: dict[Any, Any] = {}
    for task_class, item in named:
        first.setdefault(task_class, item)
    if args.task_class is not None:
        if args.task_class not in first and first:
            valid = ", ".join(first)
            setup_error(
                "unknown_class", f"class {args.task_class!r} does not name seat {name}; valid: {valid}", args.json
            )
        chosen = [(args.task_class, first[args.task_class])] if args.task_class in first else []
    else:
        chosen = list(first.items())
    if chosen:
        if len({(entry.get("model"), entry.get("effort")) for _, entry in chosen}) != 1:  # pyrefly: ignore [missing-attribute]
            options = "; ".join(f"{c} ({e.get('model')} {e.get('effort')})" for c, e in chosen)  # pyrefly: ignore [missing-attribute]
            setup_error(
                "ambiguous_class",
                f"seat {name} disagrees across classes; pass --class with one of: {options}",
                args.json,
            )
        task_class = "implement" if any(c == "implement" for c, _ in chosen) else chosen[0][0]
        entry = next(entry for c, entry in chosen if c == task_class)
        model_alias, effort = entry.get("model"), entry.get("effort")  # pyrefly: ignore [missing-attribute]
    else:
        task_class = None
        model_alias, effort = seats[name].get("model"), seats[name].get("effort")
        if not isinstance(model_alias, str):
            setup_error("unknown_rules", f"seat {name} has no model in seats map", args.json)
    served = ServedModels().get() if family_alias(table, model_alias) in table.get("aliases", {}) else []
    model, reason = (
        rung_model(table, entry, ServedModels()) if rung else resolve_alias(table, model_alias, served, name)  # pyrefly: ignore [unbound-name]
    )
    if model is None:
        setup_error("unresolved_model", f"alias {model_alias}: {reason}", args.json)
    if rules["commit"]:
        result = policy_git(clone, "show", f"{rules['commit']}:config/coding-agents/agents/{name}.md", binary=True)
        if result is None:
            setup_error("unknown_rules", f"missing definition agents/{name}.md at {rules['commit']}", args.json)
        definition = result.stdout
    else:
        definition_path = Path.home() / ".claude" / "agents" / f"{name}.md"
        try:
            definition = definition_path.read_bytes()
        except OSError:
            setup_error("unknown_rules", f"missing installed definition {definition_path}", args.json)
    seat = seats[name]
    answer = {
        "seat": name,
        "class": task_class,
        "vendor": seat["vendor"],
        "box": seat["box"],
        "model_alias": model_alias,
        "model": model,
        "effort": effort,
        "definition_sha256": hashlib.sha256(definition).hexdigest(),  # pyrefly: ignore [unbound-name]
        "rules": rules,
    }
    if rung:
        answer["rung"] = rung
    if args.json:
        print(json.dumps(answer, sort_keys=True))
    else:
        box = seat["box"]["adapter"] if isinstance(seat["box"], dict) else "studio only"
        revision = rules["commit"][:12] if rules["commit"] else "installed:" + rules["table_sha256"][:12]
        print(
            f"{name}: {model} ({model_alias}), effort {effort or '-'}, "
            f"vendor {seat['vendor']}, box {box}, rules {revision}"
        )
    return EXIT_OK


def command_seats(args: argparse.Namespace) -> int:
    table, rules, _ = versioned_rules(args.rules, args.json)
    ladder_entries(table, "verify")
    for name in table.get("ladders", {}):
        ladder_entries({**table, "ladder": name}, "verify")
    classes = {
        name: {"chain": [reduced_entry(entry, table) for entry in spec["chain"] if isinstance(entry, dict)]}
        for name, spec in table["classes"].items()
        if isinstance(spec, dict) and isinstance(spec.get("chain"), list)
    }
    implement = table["classes"].get("implement", {})
    audit = implement.get("audit", {}).get("by_author_vendor", {})
    escalation = implement.get("escalation")
    if (
        not isinstance(escalation, dict)
        or not isinstance(escalation.get("seat"), str)
        or type(escalation.get("at_fix_round")) is not int
    ):
        setup_error("unknown_rules", f"invalid classes.implement.escalation: {escalation!r}", args.json)
    answer = {
        "rules": rules,
        "seats": table["seats"],
        "classes": classes,
        "audit": {"by_author_vendor": {name: reduced_entry(entry, table) for name, entry in audit.items()}},
        "escalation": {"implement": {"seat": escalation["seat"], "at_fix_round": escalation["at_fix_round"]}},
    }
    if "ladders" in table:
        answer["ladder"] = table.get("ladder")
        answer["ladders"] = json.loads(json.dumps(table["ladders"]))
        for ladder in answer["ladders"].values():
            verify = ladder.get("verify")
            if isinstance(verify, dict):
                verify["by_author_maker"] = {
                    maker: [{"id": ref[1:], **verify["rungs"][ref[1:]]} for ref in refs]
                    for maker, refs in verify["by_author_maker"].items()
                }
    if args.json:
        print(json.dumps(answer))
    else:
        for name, spec in table["seats"].items():
            box = spec.get("box")
            print(f"{name} vendor={spec['vendor']} box={box['adapter'] if isinstance(box, dict) else 'studio'}")
    return EXIT_OK


def http_post(path: str, payload: dict[str, Any], *, timeout: float) -> HttpResult:
    return request("POST", path, payload=payload, timeout=timeout)


def pools_from_accounts(body: Any) -> dict[str, Any]:

    import statistics

    accounts = body.get("accounts") if isinstance(body, dict) else body
    if not isinstance(accounts, list):
        accounts = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        if not isinstance(account, dict):
            continue
        for pool_id in pool_ids(account):
            grouped.setdefault(pool_id, []).append(account)

    pools = []
    for pool_id, members in sorted(grouped.items()):
        routable = [account for account in members if account.get("status") in ROUTABLE_STATUSES]
        usable = [account for account in routable if remaining(account) > 0.0]
        headroom = max((remaining(account) for account in usable), default=0.0)
        aggregate = statistics.fmean(remaining(account) for account in routable) if routable else 0.0
        resets = sorted(
            value
            for value in (account.get("resetAtSecondary") for account in (usable or members))
            if isinstance(value, str) and value
        )
        provider = members[0].get("provider") if members else None
        weekly_members = [account for account in routable if account.get("status") in ROUTABLE_STATUSES]
        pools.append(
            {
                "id": pool_id,
                "provider": provider,
                "kind": "fable_scoped" if pool_id == "anthropic-fable" else "weekly",
                "accounts": len(members),
                "eligibleAccounts": len(usable),
                "headroomPercent": round(headroom, 1),
                "aggregateRemainingPercent": round(aggregate, 1),
                "resetAt": resets[0] if resets else None,
                "status": route_heal.pool_status(headroom),
                **(
                    {"unavailableReason": "no balance"}
                    if not routable
                    and any(
                        account.get("status") == "paused"
                        and str(account.get("deactivationReason") or "").startswith("balance_exhausted:")
                        for account in members
                    )
                    else {}
                ),
                "source": "accounts_fallback",
                "weeklyRemainingPercent": (
                    round(
                        statistics.fmean(
                            route_heal.window(a.get("usage"), "secondaryRemainingPercent", 100.0)
                            for a in weekly_members
                        ),
                        1,
                    )
                    if weekly_members
                    else None
                ),
                "pace": None,
            }
        )
    return {"generatedAt": now_iso(), "pools": pools, "source": "accounts_fallback"}


def cached_pool_snapshot() -> dict[str, Any] | None:
    if fixture_file("/api/pools") is not None:
        return None
    try:
        record = json.loads(pool_snapshot_path().read_text(encoding="utf-8"))
        age = time.time() - record["savedAt"]
        document = record["document"]
        if (
            record["baseUrl"] != base_url()
            or not 0 <= age < 60
            or not isinstance(document, dict)
            or not isinstance(document.get("pools"), list)
        ):
            return None
        return {**document, "source": "cache", "cacheAgeSeconds": age}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def fetch_pools(
    *, timeout: float = READ_TIMEOUT_SECONDS, accounts: HttpResult | None = None, refills: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    deadline = time.monotonic() + timeout

    def read_accounts() -> HttpResult:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return HttpResult(ok=False, status=None, body=None, error="snapshot deadline exceeded")
        return http_get("/api/accounts", timeout=remaining)

    direct = http_get("/api/pools", timeout=timeout)
    if direct.json_ok and isinstance(direct.body, dict) and isinstance(direct.body.get("pools"), list):
        document = dict(direct.body)
        route_heal.balance_pool(document, read_accounts)
        document.setdefault("source", "lb")
        if not direct.from_cache and fixture_file("/api/pools") is None:
            try:
                atomic_write(
                    pool_snapshot_path(),
                    json.dumps({"baseUrl": base_url(), "savedAt": time.time(), "document": document}),
                )
            except OSError:
                pass
        if not refills:
            return document, None
        accounts = accounts or read_accounts()
        if accounts.json_ok:
            try:
                add_pool_refills(document, accounts.body)
            except Exception:
                for pool in pools_by_id(document).values():
                    pool.pop("refills", None)
                    pool.pop("pace", None)
        return document, None
    cached = cached_pool_snapshot()
    if cached is not None:
        route_heal.balance_pool(cached, read_accounts)
        return cached, None
    accounts = accounts or read_accounts()
    if accounts.json_ok:
        document = pools_from_accounts(accounts.body)
        route_heal.balance_pool(document, lambda: accounts)
        if refills:
            try:
                add_pool_refills(document, accounts.body)
            except Exception:
                for pool in pools_by_id(document).values():
                    pool.pop("refills", None)
                    pool.pop("pace", None)
        return document, None
    reason = direct.error or f"HTTP {direct.status}"
    return None, f"/api/pools unavailable ({reason}); /api/accounts unavailable ({accounts.error})"


def fetch_stand_ins(timeout: float = 2.0) -> dict:

    empty = {"active": [], "recent": []}
    try:
        response = http_get("/api/pools/stand-ins", timeout=timeout)
        body = response.body
        if not response.json_ok or not isinstance(body, dict):
            return empty
        if any(
            not isinstance(body.get(key), list) or any(not isinstance(row, dict) for row in body[key])
            for key in ("active", "recent")
        ):
            return empty
        return body
    except Exception:
        return empty


def print_stand_in(record: dict[str, Any]) -> None:
    print(
        f"standing in: {record.get('lane') or record.get('session_id')} "
        f"on {record.get('running')} for {record.get('intended')} since {record.get('since')}, "
        f"back ~{record.get('expected_return') or 'unknown'}"
    )


def command_stand_ins(args: argparse.Namespace) -> int:
    document = fetch_stand_ins()
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        for record in document["active"]:
            print_stand_in(record)
        print(f"recent: {len(document['recent'])} returned")
    return EXIT_OK


def pace_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def pool_remaining(pool: dict[str, Any]) -> float | None:
    key = "monthlyRemainingPercent" if pool.get("windowLabel") == "month" else "weeklyRemainingPercent"
    remaining = pace_number(pool.get(key))
    return remaining if remaining is not None else pace_number(pool.get("aggregateRemainingPercent"))
