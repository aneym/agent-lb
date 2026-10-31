#!/usr/bin/env python3


import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from transcript_navigation import transcript_nav  # type: ignore[missing-import]

LEDGER = Path(
    os.environ.get("ROUTE_LEDGER")
    or os.environ.get("DISPATCH_LEDGER")
    or Path.home() / ".claude" / "logs" / "dispatch.jsonl"
)
DECISIONS = Path(os.environ.get("ROUTE_DECISIONS_LOG") or Path.home() / ".claude" / "logs" / "route-decisions.jsonl")
EVENTS = Path(os.environ.get("OUTCOME_EVENTS") or Path.home() / ".claude" / "logs" / "outcome-events.jsonl")
TAIL_LINES = 4000
DISPATCH_LINE = re.compile(r"(?im)^[\s>*`-]*Dispatch:\s*`?(dcn_[0-9A-HJKMNP-TV-Z]{26})\b")
INFRA = (
    ("exit 75", re.compile(r"\bexit(?:ed)?(?:\s+(?:code|status|rc))?[\s:=]+75\b|\brc[\s:=]+75\b", re.IGNORECASE)),
    ("not admitted", re.compile(r"\bnot admitted\b", re.IGNORECASE)),
    ("no box", re.compile(r"\bno (?:box|host) (?:admits|admitted|available)|\bno box\b", re.IGNORECASE)),
    ("governor refusal", re.compile(r"\bgovernor (?:refus|denied|declin)", re.IGNORECASE)),
    ("usage limit", re.compile(r"\busage[- ]limit|\b429\b|rate limit reached", re.IGNORECASE)),
)
FORWARD_PREFIXES = ("gpt-", "codex-", "cursor-", "grok-", "devin-", "muse-", "computer-use", "host-relay")
IN_PROCESS_SEATS = frozenset({"gpt-explorer"})
SEAT_RUN_FORWARDERS = {"opus-seat", "opus-seat-full", "sonnet-implementer", "verifier"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FAILURE = re.compile(
    r"\b(fabrication|cross-vendor-violation|verdict:?\s*fail|blocked|i (?:cannot|can't|was unable)"
    r"|error:|failed to|traceback \(most recent)",
    re.IGNORECASE,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def tail() -> list:
    try:
        with LEDGER.open() as handle:
            lines = handle.readlines()[-TAIL_LINES:]
    except Exception:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def first_prompt(path: str):

    try:
        with open(path) as handle:
            for line in handle:
                try:
                    message = (json.loads(line) or {}).get("message") or {}
                except Exception:
                    continue
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        block.get("text") or ""
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if isinstance(content, str) and content:
                    return content
    except Exception:
        return None
    return None


def transcript_path(payload: dict) -> str:
    path = str(payload.get("agent_transcript_path") or "")
    if not path:
        candidate = str(payload.get("transcript_path") or "")
        path = candidate if "/subagents/" in candidate else ""
    return path


def structured_result(path: str) -> str:

    if not path:
        return ""
    found = ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if '"StructuredOutput"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                for block in (row.get("message") or {}).get("content") or []:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") == "StructuredOutput"
                    ):
                        found = json.dumps(block.get("input"))[:300]
    except OSError:
        return ""
    return found


def usage_count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def transcript_cost(path: str) -> dict:

    cost = {"model": None, "tokens_in": None, "tokens_out": None, "cache_read_tokens": None, "wall_s": None}
    if not path:
        return cost
    try:
        by_id: dict = {}
        anonymous: list = []
        model = None
        first_ts = last_ts = None
        stamps = 0
        with open(path) as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                try:
                    moment = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
                except Exception:
                    moment = None
                if moment is not None:
                    first_ts = first_ts or moment
                    last_ts = moment
                    stamps += 1
                message = entry.get("message")
                if entry.get("type") != "assistant" and not (
                    isinstance(message, dict) and message.get("role") == "assistant"
                ):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("model"):
                    model = str(message.get("model"))
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                if message.get("id"):
                    by_id[message["id"]] = usage
                else:
                    anonymous.append(usage)
        usages = list(by_id.values()) + anonymous
        cache_read = sum(usage_count(u, "cache_read_input_tokens") for u in usages)
        cost["model"] = model
        cost["tokens_in"] = cache_read + sum(
            usage_count(u, "input_tokens") + usage_count(u, "cache_creation_input_tokens") for u in usages
        )
        cost["tokens_out"] = sum(usage_count(u, "output_tokens") for u in usages)
        cost["cache_read_tokens"] = cache_read
        if stamps >= 2:
            cost["wall_s"] = round((last_ts - first_ts).total_seconds(), 1)
    except Exception:
        return {key: None for key in cost}
    return cost


def prompt_digests(payload: dict) -> list:

    path = transcript_path(payload)
    content = first_prompt(path) if path else None
    if not content:
        return []
    readings = [content]
    envelope = re.match(r"^<teammate-message\b[^>]*>\n(.*)\n</teammate-message>\s*$", content, re.S)
    if envelope:
        readings.insert(0, envelope.group(1))
    return [hashlib.sha256(reading.encode("utf-8")).hexdigest() for reading in readings]


def resolve(open_dispatches: list, digests: list, names: list, seat: str):

    wanted = [name.lower() for name in names if name]
    for digest in digests:
        for record in reversed(open_dispatches):
            if digest and record.get("prompt_sha256") == digest and str(record.get("name") or "").lower() in wanted:
                return record, "prompt_hash"
    for digest in digests:
        candidates = [
            record for record in reversed(open_dispatches) if digest and record.get("prompt_sha256") == digest
        ]

        if len(candidates) == 1:
            return candidates[0], "prompt_hash"
        if candidates:
            shared = {
                key: candidates[0].get(key)
                for key in ("task_class", "subagent_type", "model")
                if all(record.get(key) == candidates[0].get(key) for record in candidates)
            }
            shared["_newest"] = candidates[0]
            return shared, "prompt_hash_ambiguous"
    for name in names:
        for record in reversed(open_dispatches):
            if name and str(record.get("name") or "").lower() == name.lower():
                return record, "name"
    for record in reversed(open_dispatches):
        if seat and str(record.get("subagent_type") or "").lower() == seat.lower():
            return record, "seat"
    return None, "none"


def replayed_dispatch(open_dispatches: list, closeout: dict):

    digest = str(closeout.get("prompt_sha256") or "")
    seat = str(closeout.get("subagent_type") or "").lower()
    name = str(closeout.get("name") or "").lower()
    if digest:
        candidates = [record for record in reversed(open_dispatches) if record.get("prompt_sha256") == digest]
        for key, wanted in (("subagent_type", seat), ("name", name)):
            narrowed = [record for record in candidates if wanted and str(record.get(key) or "").lower() == wanted]
            if narrowed:
                candidates = narrowed
        if candidates:
            return candidates[0]
    closed, how = resolve(open_dispatches, [""], [name], seat)
    return None if how == "prompt_hash_ambiguous" else closed


def match(records: list, session_id, agent_type: str, agent_name: str, digests: list):

    open_dispatches: list = []
    for record in records:
        if record.get("session_id") != session_id:
            continue
        if record.get("event") == "dispatch" and not record.get("denied"):
            open_dispatches.append(record)
        elif record.get("event") == "closeout":
            if record.get("match") == "prompt_hash_ambiguous" and not record.get("matched"):
                continue
            closed = replayed_dispatch(open_dispatches, record)
            if closed is not None:
                open_dispatches.remove(closed)
    return resolve(open_dispatches, digests, [agent_name, agent_type], agent_type)


def event_id(source: str, key: str) -> str:
    return "evt_" + hashlib.sha256(f"{source}|{key}".encode("utf-8")).hexdigest()[:32]


def json_objects(text: str) -> list:
    found = []
    candidates = [text.strip()] + [line.strip() for line in text.splitlines()]
    for candidate in candidates:
        if not (candidate.startswith("{") and candidate.endswith("}")):
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def seat_result(text: str) -> dict:
    for value in reversed(json_objects(text)):
        if any(key in value for key in ("worker_model", "worker_effort", "rc", "exit_code", "fallback")):
            return value
    return {}


def decision_ref(last: str, prompt: str) -> str:
    for text in (last, prompt):
        found = DISPATCH_LINE.search(text or "")
        if found:
            return found.group(1)
    value = (os.environ.get("AGENT_LB_DISPATCH") or "").strip()
    return value if value.startswith("dcn_") else ""


def decision_row(decision_id: str) -> dict:
    if not decision_id:
        return {}
    try:
        with DECISIONS.open(encoding="utf-8") as handle:
            lines = handle.readlines()[-TAIL_LINES:]
    except OSError:
        return {}
    for line in reversed(lines):
        if decision_id not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("decision_id") == decision_id:
            return row
    return {}


def in_process(seat: str) -> bool:
    seat = seat.lower()
    if seat in IN_PROCESS_SEATS:
        return True
    return not seat.startswith(FORWARD_PREFIXES) and (seat.endswith("-local") or seat not in SEAT_RUN_FORWARDERS)


def exit_code(result: dict):
    for key in ("rc", "exit_code", "exit"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def infra_reason(result: dict, text: str):
    if exit_code(result) == 75:
        return "exit 75"
    reason = result.get("infra_reason")
    if isinstance(reason, str) and reason:
        return reason
    for name, pattern in INFRA:
        if pattern.search(text or ""):
            return name
    return None


def effort_value(value):
    return value if value in EFFORTS else None


def string_list(value) -> list:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def append_event(row: dict) -> None:
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        return


def duration(dispatch) -> float:
    if not dispatch:
        return None
    try:
        started = datetime.fromisoformat(str(dispatch.get("ts")).replace("Z", "+00:00"))
    except Exception:
        return None
    return round((now() - started).total_seconds(), 1)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id")
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    agent_name = str(payload.get("agent_name") or payload.get("name") or "").strip()
    last = str(payload.get("last_assistant_message") or "")
    if not last.strip():
        last = structured_result(transcript_path(payload))

    digests = prompt_digests(payload)
    cost = transcript_cost(transcript_path(payload))
    dispatch, how = match(tail(), session_id, agent_type, agent_name, digests)
    shared = None
    if how == "prompt_hash_ambiguous":
        shared, dispatch = {k: v for k, v in dispatch.items() if k != "_newest"}, None
    ok = bool(last.strip()) and not FAILURE.search(last)
    path = transcript_path(payload)
    decision_id = decision_ref(last, first_prompt(path) if path else "")
    decision = decision_row(decision_id)
    result = seat_result(last)
    seat = str(decision.get("seat") or agent_type)
    local = in_process(seat) and not result
    infra = infra_reason(result, last if not ok or result else "")
    if infra:
        ok = False
    fallback = result.get("fallback") if isinstance(result.get("fallback"), dict) else None
    pool_pct = result.get("pool_pct")
    moment = stamp(now())
    record = {
        "ts": moment,
        "event": "closeout",
        "host": os.environ.get("FACTORY_LOCAL_HOST") or os.environ.get("FACTORY_ADMIT_HOST") or os.uname().nodename,
        "session_id": session_id,
        "agent_type": agent_type or None,
        "subagent_type": (
            shared.get("subagent_type")
            if shared is not None
            else (dispatch or {}).get("subagent_type") or agent_type.lower() or None
        ),
        "name": (dispatch or {}).get("name") or agent_name or None,
        "agent_id": payload.get("agent_id"),
        "task_class": (dispatch or shared or {}).get("task_class"),
        "model": cost["model"],
        "dispatch_model": (dispatch or shared or {}).get("model"),
        "prompt_sha256": (dispatch or {}).get("prompt_sha256"),
        "digest_seen": None if how == "prompt_hash" else (digests[0] if digests else None),
        "duration_s": duration(dispatch),
        "wall_s": cost["wall_s"],
        "tokens_in": cost["tokens_in"],
        "tokens_out": cost["tokens_out"],
        "cache_read_tokens": cost["cache_read_tokens"],
        "nav": transcript_nav(transcript_path(payload)),
        "ok": ok,
        "error": None if ok else (last.strip()[:300] or "no final message"),
        "matched": bool(dispatch),
        "match": how,
        "decision_id": decision_id or None,
        "job_id": decision.get("job_id"),
        "attempt": decision.get("attempt"),
        "parent_id": decision.get("parent_id"),
        "worker_model": result.get("worker_model") or (cost["model"] if local else None),
        "worker_effort": effort_value(result.get("worker_effort")),
        "wrapper_model": None if local else cost["model"],
        "fallback": fallback,
        "session_ids": string_list(result.get("session_ids")),
        "pool_pct": pool_pct if isinstance(pool_pct, (int, float)) and not isinstance(pool_pct, bool) else None,
        "exit_code": exit_code(result),
        "infra_reason": infra,
    }
    record["event_id"] = event_id("subagent-closeout", f"{session_id}|{record['agent_id'] or record['name']}|{moment}")
    if decision_id or infra:
        append_event(
            {
                "v": 1,
                "event_id": record["event_id"],
                "job_id": record["job_id"],
                "decision_id": record["decision_id"],
                "attempt": record["attempt"],
                "parent_id": record["parent_id"],
                "kind": "infra" if infra else "closeout",
                "ts": moment,
                "source": "subagent-closeout",
                "data": {
                    key: record[key]
                    for key in (
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
                },
            }
        )
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    main()
