from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import socket
import subprocess
from pathlib import Path
from typing import Any

from route_anthropic_models import family_alias  # pyrefly: ignore [missing-import]
from route_class_candidates import alias_vendor, table_digest  # pyrefly: ignore [missing-import]
from route_dispatch import command_dispatch  # pyrefly: ignore [missing-import]
from route_pick import command_reserve  # pyrefly: ignore [missing-import]
from route_reserve_state import new_ulid, seat_opts  # pyrefly: ignore [missing-import]
from route_resolve_one import append_decision  # pyrefly: ignore [missing-import]
from route_versioned_rules import EXIT_OK, EXIT_UNROUTABLE, EXIT_WAIT, load_table, now_iso, setup_error  # pyrefly: ignore [missing-import]


def t3_settings(table: dict[str, Any]) -> dict[str, Any]:
    if isinstance(table.get("t3"), dict):
        return table["t3"]
    path = Path(__file__).resolve().parent.parent / "config/coding-agents/routing-table/t3.json"
    return json.loads(path.read_text(encoding="utf-8"))["t3"]


def provider_for(settings: dict, table: dict, alias: str, seat: str) -> dict | None:
    vendor = (table.get("seats", {}).get(seat) or {}).get("vendor") or alias_vendor(table, alias)
    if (settings.get("seat_vendors", {}).get(vendor) or {}).get("not_t3"):
        return None
    providers = settings["providers"]
    name = family_alias(table, alias)
    base, _, effort = name.rpartition("-")
    if effort in ("low", "medium", "high", "xhigh", "max") and base in providers:
        name = base
    spec = providers.get(name)
    return spec or next((entry for entry in providers.values() if entry["model"] == alias), None)


def local_environment(environments: dict) -> str:
    try:
        result = subprocess.run(["whereami", "--json"], capture_output=True, text=True, timeout=5, check=False)
        name = json.loads(result.stdout).get("host", {}).get("name")
        if result.returncode == 0 and name in environments:
            return name
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    hostname = socket.gethostname().lower().split(".")[0]
    matches = [name for name in environments if name == hostname or name in hostname]
    if len(matches) == 1:
        return matches[0]
    return setup_error("unknown_environment", f"local host {hostname!r} has no T3 environment mapping", True)


def placement(args: argparse.Namespace, settings: dict) -> dict:
    environments = settings["environments"]
    local = local_environment(environments)
    if args.here and args.here not in environments:
        setup_error("unknown_environment", f"unknown --here environment {args.here!r}", args.json)
    if args.on and args.on not in environments:
        setup_error("unknown_environment", f"unknown --on environment {args.on!r}", args.json)
    if args.on and args.here and args.on != args.here:
        setup_error("conflicting_environment", "--on and --here must name the same environment", args.json)
    if args.on and args.mac and args.on != "studio":
        setup_error("conflicting_environment", "--mac requires --on studio", args.json)
    remote_write = args.write and args.here is not None and args.here != local
    if args.on:
        name, reason = args.on, "caller pinned --on"
    elif args.mac:
        name, reason = "studio", "mac required: studio"
    elif args.write:
        name, reason = (
            args.here or local,
            "write stays on the caller environment; linked-environment worktrees unsupported",
        )
    else:
        name, reason = local, "no per-box headroom signal in route; using the local environment"
    if args.write and name != local:
        remote_write = True
    return {
        "environment": name,
        "ssh_host": environments[name]["ssh_host"],
        "placement_reason": reason,
        **({"remote_write": "unsupported"} if remote_write else {}),
    }


def dispatch_target(args: argparse.Namespace, table: dict, settings: dict) -> tuple[int, dict, list[str]]:
    filtered = copy.deepcopy(table)
    class_name = (table.get("class_aliases") or {}).get(args.task_class, args.task_class)
    standard = filtered.get("standards", {}).get(class_name, {})
    fallback = []
    if "chain" in standard:
        chain = []
        for alias in standard["chain"]:
            if provider_for(settings, table, alias, standard["seat"]):
                chain.append(alias)
            else:
                fallback.append(f"{alias}: no T3 provider")
        standard["chain"] = chain
        if not chain:
            return EXIT_WAIT, {}, fallback
    dispatch = argparse.Namespace(
        task_class=args.task_class,
        job=args.job or f"job_{new_ulid()}",
        parent=None,
        caller=args.caller,
        author_vendor=args.author_vendor,
        seat=None,
        exclude=[],
        model=None,
        effort=None,
        seed=None,
        dry_run=True,
        pools_file=None,
        tag=[],
        json=True,
    )
    while True:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = command_dispatch(dispatch, table=filtered)
        row = json.loads(output.getvalue() or "{}")
        if code != EXIT_OK:
            return code, row, fallback
        if provider_for(settings, table, row["model_alias"], row["seat"]):
            return code, row, fallback
        fallback.append(f"{row['seat']} ({row['model_alias']}): no T3 provider")
        if row["seat"] in dispatch.exclude:
            return EXIT_WAIT, row, fallback
        dispatch.exclude.append(row["seat"])


def reserve_target(args: argparse.Namespace, table: dict, row: dict, vendor: str, model: str) -> tuple[int, dict]:
    admission = copy.deepcopy(table)
    admission.setdefault("classes", {}).setdefault(row["class"], {})
    ladder = admission.setdefault("ladders", {}).setdefault(admission.setdefault("ladder", "t3-target"), {})
    rung = {
        "id": "t3-target",
        "seat": row["seat"],
        "model": model,
        "maker": vendor,
        "effort": row["effort"],
        **({"pool": row["pool"]} if row.get("pool") else {}),
    }
    if row["class"] == "verify":
        author = "cognition" if args.author_vendor == "devin" else args.author_vendor
        ladder["verify"] = {"rungs": {"t3-target": rung}, "by_author_maker": {author: ["@t3-target"]}}
    else:
        ladder[row["class"]] = [rung]
    reservation = argparse.Namespace(
        task_class=row["class"],
        job=f"{row['job_id']}:{row['decision_id']}",
        author_vendor=args.author_vendor,
        skip=[],
        prefer="t3-target",
        model=model,
        reason="route target dispatch admission",
        ttl=600,
        json=True,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = command_reserve(reservation, table=admission)
    return code, json.loads(output.getvalue() or "{}")


def resolve_target(args: argparse.Namespace) -> tuple[int, dict]:
    table = load_table()
    settings = t3_settings(table)
    class_name = (table.get("class_aliases") or {}).get(args.task_class, args.task_class)
    if class_name not in table.get("standards", {}):
        setup_error("unknown_class", f"unknown dispatch class {args.task_class!r}", args.json)
    if class_name == "verify" and not args.author_vendor:
        setup_error("unknown_author", "verify requires --author-vendor <vendor>", args.json)
    placed = placement(args, settings)
    code, row, fallback = dispatch_target(args, table, settings)
    if code not in (EXIT_OK, EXIT_WAIT):
        return code, row
    if not row.get("decision_id"):
        row.update(
            v=1,
            decision_id=f"dcn_{new_ulid()}",
            ts=now_iso(),
            **{"class": class_name},
            requested_class=args.task_class,
            caller=args.caller,
            job_id=args.job or f"job_{new_ulid()}",
            table_sha256=table_digest(),
            policy_rev=str(table.get("policy_rev") or "unversioned"),
        )
    row.setdefault("seat", None)
    row.setdefault("vendor", None)
    row.setdefault("reason", "no T3-compatible dispatch candidate")
    row.update(
        kind="route.target",
        status="queued",
        target=None,
        lease=None,
        lease_id=None,
        fallback_reason="; ".join(fallback) or None,
        table_sha=row["table_sha256"],
        **placed,
    )
    if code == EXIT_OK:
        provider = provider_for(settings, table, row["model_alias"], row["seat"])
        if provider is None:
            return EXIT_UNROUTABLE, row
        model = row.get("model") or provider["model"]
        vendor = alias_vendor(table, row["model_alias"])
        effort = args.effort or row.get("effort") or "medium"
        row.update(
            vendor=vendor,
            model=model,
            effort=effort,
            requested_effort=args.effort,
            opts=seat_opts(row["seat"], model, row["model_alias"], effort),
        )
        target = {
            "providerInstanceId": provider["providerInstanceId"],
            "model": model,
            "options": {provider["effort_option"]: effort},
            "environmentId": settings["environments"][placed["environment"]]["environmentId"],
        }
        if args.write and row.get("remote_write"):
            row["reason"] = "remote writes unsupported; run route target on the write environment"
        elif args.dry_run:
            row.update(status="ok", target=target)
        else:
            reserve_code, lease = reserve_target(args, table, row, vendor, model)
            if reserve_code == EXIT_OK and lease.get("status") == "reserved":
                row.update(
                    status="ok",
                    target=target,
                    lease_id=lease["reservation_id"],
                    lease={"id": lease["reservation_id"], "expires_at": lease["expires_at"]},
                )
            else:
                row["reason"] = lease.get("reason") or lease.get("message") or "admission unavailable"
    if not args.dry_run:
        append_decision(row)
    return EXIT_OK, row


def command_target(args: argparse.Namespace) -> int:
    code, row = resolve_target(args)
    if args.json:
        print(json.dumps(row, sort_keys=True))
    else:
        print(
            f"{row['status']}: {row.get('class', args.task_class)} -> {row.get('seat') or 'none'} on {row['environment']}"
        )
    return code
