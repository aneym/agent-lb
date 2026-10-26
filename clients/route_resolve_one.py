from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import route_cache  # pyrefly: ignore [missing-import]
from route_anthropic_models import (  # pyrefly: ignore [missing-import]
    allow_discovery,
    blocked_patterns,
    cursor_models,
    defer_discovery,
    devin_models,
    family_alias,
    is_retired,
    model_list_pending,
    readmitted_patterns,
    resolve_claude_family,
    retired_patterns,
    served_models,
    version_key,
)
from route_reserve_state import (  # pyrefly: ignore [missing-import]
    WORKFLOW_BRIEF_SEATS,
    WORKFLOW_EFFORT_BRIEF_SEATS,
    WORKFLOW_OPUS_STAGES,
    chain_effort,
    finish_text,
    same_model,
    seat_opts,
)
from route_seat_states import pinned_model  # pyrefly: ignore [missing-import]
from route_versioned_rules import ModelState, env_path, pool_ids, setup_error  # pyrefly: ignore [missing-import]


def resolve_alias(
    table: dict[str, Any], name: str, served: list[str], seat: str | None = None
) -> tuple[str | None, str]:

    name = family_alias(table, name)
    model, reason = resolve_one(table, name, served, seat)
    if route_cache.MODEL_CACHE_HITS and (
        not model
        or "pinned" in reason
        or "Claude Code alias" in reason
        or (reason == "not an alias" and model not in served)
    ):
        route_cache.FORCE_MODELS = True
        route_cache.MODEL_CACHE_HITS.clear()
        ModelState.ANTHROPIC_MODELS = None
        try:
            served[:] = served_models()[0]
            model, reason = resolve_one(table, name, served, seat)
        finally:
            route_cache.FORCE_MODELS = False
    aliases = table.get("aliases") if isinstance(table.get("aliases"), dict) else {}
    fallback = (aliases.get(name) or {}).get("fallback") if isinstance(aliases.get(name), dict) else None
    if (
        model
        or not isinstance(fallback, str)
        or fallback == name
        or reason.startswith(f"{name} is readmitted for")
        or reason.startswith("pending: ")
    ):
        return model, reason
    model, why = resolve_one(table, fallback, served, seat)
    return model, f"{reason}; fallback {fallback}: {why}"


def muse_catalog_pin(pinned: str, family: str) -> tuple[str | None, str]:
    if not re.fullmatch(rf"{re.escape(family)}-full-\d+(?:\.\d+)*", pinned):
        return pinned, f"pinned {family}; account eligibility is checked at dispatch"
    listed = cursor_models()
    if not listed:
        return pinned, f"pinned {family}; served catalog unavailable"
    if any(model == pinned or model.startswith(pinned + "-") for model in listed):
        return pinned, f"pinned {family}; served catalog lists {pinned}"
    bases = sorted(
        {
            re.sub(r"-(?:minimal|low|medium|high|xhigh|max)$", "", model)
            for model in listed
            if model.startswith(family + "-") and "-full-" not in model
        }
    )
    served_as = bases[-1] if bases else "none"
    return None, f"{pinned} is not available; served {family} is {served_as}"


def resolve_one(table: dict[str, Any], name: str, served: list[str], seat: str | None = None) -> tuple[str | None, str]:

    retired = retired_patterns(table)
    aliases = table.get("aliases") if isinstance(table.get("aliases"), dict) else {}
    spec = aliases.get(name)
    if isinstance(spec, dict):
        readmitted = table.get("readmitted") if isinstance(table.get("readmitted"), dict) else {}
        owners = [key for key, entry in readmitted.items() if isinstance(entry, dict) and entry.get("alias") == name]
        if owners and seat is not None and seat not in owners:
            return None, f"{name} is readmitted for {', '.join(owners)} only"
        exempt = readmitted_patterns(table, name)
        retired = tuple(pattern for pattern in retired if pattern not in exempt)

    blocked = blocked_patterns(table)
    retired = (*retired, *(pattern for pattern in blocked if pattern not in retired))
    if not isinstance(spec, dict):
        if re.search(r"-latest(?:-(?:low|medium|high|xhigh))?$", name):
            return None, f"unknown alias {name}"
        if is_retired(name, blocked):
            return None, f"{name} is no longer served (blocked)"
        if is_retired(name, retired):
            return None, f"{name} is retired"
        if "*" in name or "?" in name:
            return None, f"{name} is a wildcard, not a served model"
        if re.fullmatch(r"muse-.+-full-\d+(?:\.\d+)*", name):
            return muse_catalog_pin(name, name.rsplit("-full-", 1)[0])
        return name, "not an alias"
    if spec.get("vendor") == "anthropic" or (spec.get("harness") and not spec.get("family")):
        return resolve_claude_family(spec, retired)
    family = spec.get("family")
    if not isinstance(family, str) or not family:
        return None, f"alias {name} has no family"
    if spec.get("vendor") == "openrouter":
        return spec["pinned"], "pinned OpenRouter model"
    if spec.get("vendor") == "muse":
        pinned = spec.get("pinned")
        if not isinstance(pinned, str) or not re.fullmatch(rf"{re.escape(family)}-(?:full-)?\d+(?:\.\d+)*", pinned):
            return None, f"alias {name} has no valid {family} pin"
        if is_retired(pinned, retired):
            return None, f"{pinned} is retired or blocked"
        return muse_catalog_pin(pinned, family)
    if spec.get("vendor") == "cursor":
        variant = spec.get("variant")
        suffix = "-" + re.escape(variant) if variant else ""
        version = r"\d+(?:-\d{1,2})?" if family.startswith("claude-") else r"\d+(?:\.\d+)*"
        pattern = re.compile(rf"^(?:cursor-)?{re.escape(family)}-({version}){suffix}$")
        served = cursor_models()
        if not served:
            if "cursor" in ModelState.DEFERRED_DISCOVERY:
                return None, model_list_pending("cursor")
            return None, "Cursor model list unavailable (cursor-agent --list-models)"
    elif spec.get("vendor") == "devin":
        variant = spec.get("variant") or "high"
        pattern = re.compile(rf"^{re.escape(family)}-(\d+)-{re.escape(variant)}$")
        served = devin_models()
        if not served:
            if "devin" in ModelState.DEFERRED_DISCOVERY:
                return None, model_list_pending("devin")
            return None, "Devin model list unavailable (devin models list)"
    else:
        pattern = re.compile(rf"^gpt-(\d+(?:\.\d+)*)-{re.escape(family)}$")
    candidates = []
    for model in served:
        match = pattern.match(model)
        if match and not is_retired(model, retired):
            candidates.append((version_key(match.group(1).replace("-", ".")), model))
    if not candidates:
        return None, f"no non-retired {family} model is served"
    return max(candidates)[1], f"newest served {family}"


class ServedModels:
    def __init__(self) -> None:
        self._models: list[str] | None = None

    def get(self) -> list[str]:
        if self._models is None:
            self._models, _ = served_models()
        return self._models


def ladder_entries(table: dict[str, Any], class_name: str, author: str | None = None) -> list[dict] | None:
    ladders = table.get("ladders", {})
    name = table.get("ladder")

    def invalid(message: str) -> Any:
        setup_error("invalid_ladder", message, "--json" in sys.argv)

    if not isinstance(ladders, dict):
        invalid("ladders must be an object")
    if name is not None and (not isinstance(name, str) or name not in ladders):
        invalid("unknown or malformed ladder name")
    ladder = ladders.get(name, {})
    if not isinstance(ladder, dict):
        invalid("ladder must be an object")
    for task, definition in ladder.items():
        if task == "verify":
            if not isinstance(definition, dict):
                invalid("verify ladder must be an object")
            rungs = definition.get("rungs")
            authors = definition.get("by_author_maker")
            if not isinstance(rungs, dict) or not rungs or not isinstance(authors, dict):
                invalid("verify ladder requires rungs and by_author_maker")
            for refs in authors.values():
                if not isinstance(refs, list) or any(
                    not isinstance(ref, str) or not ref.startswith("@") or ref[1:] not in rungs for ref in refs
                ):
                    invalid("invalid verify rung reference")
            values = list(rungs.values())
        else:
            if not isinstance(definition, list) or not definition:
                invalid(f"{task} ladder must be a list")
            values = definition
        for entry in values:
            if (
                not isinstance(entry, dict)
                or any(not isinstance(entry.get(key), str) for key in ("seat", "model", "maker"))
                or (task != "verify" and not isinstance(entry.get("id"), str))
            ):
                invalid(f"malformed rung in {task}")
            if "gate" in entry and not isinstance(entry["gate"], dict):
                invalid(f"gate in {task} must be an object")
            if "pool" in entry and not isinstance(entry["pool"], str):
                invalid(f"pool in {task} must be a string")
    spec = ladder.get(class_name)
    if isinstance(spec, list):
        return [dict(entry) for entry in spec]
    if isinstance(spec, dict) and class_name == "verify":
        rungs = spec.get("rungs", {})
        author = "cognition" if author == "devin" else author
        references = spec.get("by_author_maker", {}).get(author)
        if author is not None and references is None:
            setup_error("unknown_author", f"unknown author maker {author!r}", "--json" in sys.argv)
        if references is None:
            references = ["@" + key for key in rungs]
        return [{"id": ref[1:], **rungs[ref[1:]]} for ref in references]
    return None


def rung_model(table: dict, entry: dict, served: ServedModels) -> tuple[str | None, str]:

    alias = entry["model"]
    models = served.get() if family_alias(table, alias) in table.get("aliases", {}) else []
    model, why = resolve_alias(table, alias, models, entry.get("seat") or "")
    if model is None and why.startswith("pending: "):
        return alias, why
    if model and family_alias(table, alias) not in table.get("aliases", {}):
        vendor = {"cursor-seat": "cursor", "devin-seat": "devin", "muse-seat-local": "muse"}.get(entry["seat"])
        listed = cursor_models() if vendor == "cursor" else (devin_models() if vendor == "devin" else [])
        if not listed and vendor in ModelState.DEFERRED_DISCOVERY:
            return model, model_list_pending(vendor)
        if listed and model not in listed:
            return None, f"{model} is not listed for {entry['seat']}"
    return model, why


def settle_pending(table: dict, row: dict, wanted: str | None) -> tuple[dict | None, str | None]:

    allow_discovery()
    resolved: dict = {}
    try:
        if row.get("model_pending"):
            model, why = rung_model(table, {"model": row["alias"], "seat": row["seat"]}, ServedModels())
            if not model:
                return None, f"{row['rung']} unresolvable once held: {why}"
            if wanted is not None and not same_model(wanted, model, row["alias"]):
                return None, f"{row['rung']} runs {model}, not {wanted}"
            resolved["model"] = model
        audit = row.get("audit")
        if isinstance(audit, dict) and audit.get("model_pending"):
            model, why = rung_model(table, {"model": audit["alias"], "seat": audit["seat"]}, ServedModels())
            if not model:
                resolved.update(
                    audit=None,
                    auditor=None,
                    audit_gap=f"{row['rung']}'s reviewer {audit['rung']} unresolvable once held: {why}",
                )
            else:
                resolved["audit"] = {**{k: v for k, v in audit.items() if k != "model_pending"}, "model": model}
                resolved["auditor"] = resolved["audit"]
        return resolved, None
    finally:
        defer_discovery()


def five_hour_windows(accounts_body: Any) -> dict[str, dict[str, Any]]:

    accounts = accounts_body.get("accounts") if isinstance(accounts_body, dict) else accounts_body
    windows: dict[str, dict[str, Any]] = {}
    for account in accounts if isinstance(accounts, list) else []:
        if not isinstance(account, dict) or account.get("status") != "active":
            continue
        remaining = (account.get("usage") or {}).get("primaryRemainingPercent")
        reset = account.get("resetAtPrimary")
        for pool_id in pool_ids(account):
            window = windows.setdefault(pool_id, {"fiveHourHeadroomPercent": None, "fiveHourResetAt": None})
            if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                best = window["fiveHourHeadroomPercent"]
                window["fiveHourHeadroomPercent"] = max(float(remaining), best if best is not None else 0.0)
            if isinstance(reset, str) and reset and (window["fiveHourResetAt"] or "~") > reset:
                window["fiveHourResetAt"] = reset
    return windows


def workflow_seat(table: dict[str, Any], class_name: str, entry: dict[str, Any]) -> dict[str, Any]:

    seat = entry["seat"]
    model = entry.get("model")
    alias = entry.get("alias") or model
    effort = entry.get("effort") if "effort" in entry else chain_effort(table, class_name, seat)
    if model and is_retired(model, retired_patterns(table)):
        raise ValueError(f"{seat} picked retired model {model}")
    opts = seat_opts(seat, model, alias, effort)
    brief = ""
    if seat in WORKFLOW_BRIEF_SEATS:
        brief = f"Seat model: {model or alias}\n"
        if class_name == "verify":
            brief += "Seat mode: ask\nReviewer instructions: read ~/.claude/agents/verifier.md and follow it.\n"
        elif entry.get("read_only"):
            brief += "Seat mode: ask\n"
    elif seat in WORKFLOW_EFFORT_BRIEF_SEATS:
        brief = f"Effort: {effort}\n" if effort else ""
    out = {key: entry.get(key) for key in ("rung", "seat", "model", "alias", "vendor", "pool") if entry.get(key)}
    seat_spec = (table.get("seats") or {}).get(seat)
    if "vendor" not in out and isinstance(seat_spec, dict) and seat_spec.get("vendor"):
        out["vendor"] = seat_spec["vendor"]
    if class_name != "verify":
        finish = finish_text()
        if finish:
            brief += "\n\n" + finish
    out.update(effort=effort, opts=opts, brief=brief)
    return out


def workflow_table(table):
    for name, spec in table["classes"].items():
        if name == "verify" or name in WORKFLOW_OPUS_STAGES or spec.get("driver"):
            continue
        entries = ladder_entries(table, name) or spec.get("chain", [])
        sol = [dict(entry) for entry in entries if str(entry.get("model", "")).startswith("sol-latest")]
        if not sol:
            seat = "gpt-explorer" if name == "explore" else "sol-consult" if name == "plan" else "gpt-implementer"
            sol = [
                {
                    "seat": seat,
                    "model": "sol-latest",
                    "pool": "openai-codex",
                    "effort": "high" if name == "plan" else "medium",
                }
            ]
        spec["chain"] = sol
        if table.get("ladder"):
            table["ladders"][table["ladder"]][name] = [
                dict(entry, id=entry.get("id", "sol-default"), maker="openai") for entry in sol
            ]
    return table


def workflow_opus_stage(table: dict[str, Any], stage: str) -> dict[str, Any]:
    standard = (table.get("standards") or {}).get(stage)
    seat = standard.get("seat") if isinstance(standard, dict) and standard.get("seat") else "general-purpose"
    model, _ = resolve_alias(table, "opus-latest", [], seat)
    entry = {
        "seat": seat,
        "model": model or pinned_model(table, "opus-latest"),
        "alias": "opus-latest",
        "vendor": "anthropic",
        "pool": "anthropic-general",
        "effort": "medium",
    }
    return workflow_seat(table, stage, entry)


def decisions_path() -> Path:
    return env_path("ROUTE_DECISIONS_LOG", Path.home() / ".claude" / "logs" / "route-decisions.jsonl")


def append_decision(row: dict[str, Any]) -> None:
    path = decisions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


RowKey = tuple[str, str, str]

IMPLEMENT_EVENT = "implement_result"
