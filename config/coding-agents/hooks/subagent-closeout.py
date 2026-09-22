#!/usr/bin/env python3
"""SubagentStop hook: close the C2 dispatch ledger line a seat opened.

The payload carries ``session_id``, ``agent_id``, ``agent_type`` (the seat's
frontmatter name) and ``last_assistant_message`` — but not the ``name`` the
caller passed to the Agent tool, so the pair needs a join key. A subagent's own
transcript opens with the Agent prompt verbatim (verified 2026-09-19 against
``…/<session>/subagents/agent-*.jsonl``), so hashing that first user message
reproduces the ``prompt_sha256`` seat-guard wrote: an exact join, recorded as
``"match": "prompt_hash"``. When the transcript is missing or unparsable this
falls back to the newest open dispatch for the seat, recorded as
``"match": "fallback"``. ``ok`` is read off the last message, the only outcome
signal the payload has.

The same transcript supplies the cost: the model that answered, input and
output token totals, the cache-read share of input, and wall-clock seconds from
first to last entry. All of these are null when the transcript is unreadable.

Appends one C2 ``closeout`` line. Fail-open: it never blocks a subagent.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Same precedence as seat-guard, so both halves of a dispatch land in one ledger.
LEDGER = Path(
    os.environ.get("ROUTE_LEDGER")
    or os.environ.get("DISPATCH_LEDGER")
    or Path.home() / ".claude" / "logs" / "dispatch.jsonl"
)
TAIL_LINES = 4000
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
    """The first user message in a subagent transcript, as a string.

    A transcript interleaves `attachment` rows (no role, no content) with the
    user rows, so rows are selected on `message.role`, never on position.
    """
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
        # Only the subagent's own transcript will do; the session transcript
        # opens with the user's prompt, not the Agent tool's.
        candidate = str(payload.get("transcript_path") or "")
        path = candidate if "/subagents/" in candidate else ""
    return path


def usage_count(usage: dict, key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def transcript_cost(path: str) -> dict:
    """Model, token totals and wall-clock span of a subagent transcript.

    A streamed reply is written as several rows sharing one `message.id`, each
    with the usage so far, so only the last row per id is counted. Rows with no
    id count on their own. Every field is null when the file cannot be read.
    """
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
    """Candidate sha256s of the prompt this subagent was launched with.

    A named dispatch is delivered as a teammate, and its transcript stores the
    prompt inside a `<teammate-message …>` envelope (verified 2026-09-19 against
    a live closeout: the envelope hashed to cc2052dc…, the prompt inside it to
    1f07ce8e…, which is what seat-guard recorded). An unnamed dispatch stores
    the prompt bare. Hash both readings and let the ledger say which was real.
    """
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
    """One dispatch out of those still open, newest first, and how it was found.

    Order: the prompt hash, then the caller-given name, then the seat. Claude
    Code puts the NAME in `agent_type` when the dispatch had one, so a payload's
    `agent_type` is tried as both (verified live: `agent_type` arrived as
    "opus-liveness-probe" for a dispatch whose subagent_type was "opus-seat").
    """
    # Identical prompts dispatched concurrently share a hash; the caller-given name
    # tells them apart, so a hash match that also carries the name wins.
    wanted = [name.lower() for name in names if name]
    for digest in digests:
        for record in reversed(open_dispatches):
            if digest and record.get("prompt_sha256") == digest and str(record.get("name") or "").lower() in wanted:
                return record, "prompt_hash"
    for digest in digests:
        candidates = [
            record for record in reversed(open_dispatches) if digest and record.get("prompt_sha256") == digest
        ]
        if candidates:
            # Several open dispatches with one prompt and no telling name: any pick
            # may be the sibling's, so say so rather than claim an exact join.
            return candidates[0], "prompt_hash" if len(candidates) == 1 else "prompt_hash_ambiguous"
    for name in names:
        for record in reversed(open_dispatches):
            if name and str(record.get("name") or "").lower() == name.lower():
                return record, "name"
    for record in reversed(open_dispatches):
        if seat and str(record.get("subagent_type") or "").lower() == seat.lower():
            return record, "seat"
    return None, "none"


def match(records: list, session_id, agent_type: str, agent_name: str, digests: list):
    """The dispatch this stop belongs to, and how it was found.

    Dispatches go on one open list; replaying each past closeout through the
    same `resolve` removes exactly the dispatch that closeout was attributed to.
    Separate per-key stacks used to diverge here: a closeout matched by prompt
    hash still popped the newest dispatch off its seat stack, so a sibling seat
    was closed twice and its real dispatch never at all.
    """
    open_dispatches: list = []
    for record in records:
        if record.get("session_id") != session_id:
            continue
        if record.get("event") == "dispatch" and not record.get("denied"):
            open_dispatches.append(record)
        elif record.get("event") == "closeout":
            if record.get("match") == "prompt_hash_ambiguous":
                # It claimed no dispatch, so replaying it must not close one either.
                continue
            closed, _ = resolve(
                open_dispatches,
                [str(record.get("prompt_sha256") or "")],
                [str(record.get("name") or "")],
                str(record.get("subagent_type") or record.get("agent_type") or ""),
            )
            if closed is not None:
                open_dispatches.remove(closed)
    return resolve(open_dispatches, digests, [agent_name, agent_type], agent_type)


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

    digests = prompt_digests(payload)
    cost = transcript_cost(transcript_path(payload))
    dispatch, how = match(tail(), session_id, agent_type, agent_name, digests)
    shared = None
    if how == "prompt_hash_ambiguous":
        # Siblings with one prompt share class, seat and model, but which of them
        # stopped is unknown: keep those shared facts and attribute to none of them.
        shared, dispatch = dispatch, None
    ok = bool(last.strip()) and not FAILURE.search(last)
    record = {
        "ts": stamp(now()),
        "event": "closeout",
        "session_id": session_id,
        # agent_type is what the payload calls it, which is the NAME whenever the
        # dispatch had one. The seat, model and class are the dispatch's own, so
        # S3's `route report` can group closeouts by seat at all.
        "agent_type": agent_type or None,
        "subagent_type": (dispatch or shared or {}).get("subagent_type") or agent_type.lower() or None,
        "name": (dispatch or {}).get("name") or agent_name or None,
        "agent_id": payload.get("agent_id"),
        "task_class": (dispatch or shared or {}).get("task_class"),
        # `model` is what actually answered, read off the transcript; the
        # dispatch's requested model (often an alias) is kept beside it.
        "model": cost["model"],
        "dispatch_model": (dispatch or shared or {}).get("model"),
        # The hash of the dispatch this closeout was ATTRIBUTED to, so replaying
        # the ledger resolves it back to the same row. A digest that matched
        # nothing is kept separately rather than written here as if it had.
        "prompt_sha256": (dispatch or {}).get("prompt_sha256"),
        "digest_seen": None if how == "prompt_hash" else (digests[0] if digests else None),
        "duration_s": duration(dispatch),
        "wall_s": cost["wall_s"],
        "tokens_in": cost["tokens_in"],
        "tokens_out": cost["tokens_out"],
        "cache_read_tokens": cost["cache_read_tokens"],
        "ok": ok,
        "error": None if ok else (last.strip()[:300] or "no final message"),
        "matched": bool(dispatch),
        "match": how,
    }
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    main()
