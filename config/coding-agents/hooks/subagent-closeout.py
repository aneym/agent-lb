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


def structured_result(path: str) -> str:
    """The StructuredOutput payload of a schema-bound subagent, or "".

    A Workflow agent() with a schema ends by calling StructuredOutput, so its
    last assistant message has no text. Without this, every such agent was
    logged as "no final message" and route learn demoted a working seat.
    """
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
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "StructuredOutput":
                        found = json.dumps(block.get("input"))[:300]
    except OSError:
        return ""
    return found


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


# Navigation counters (nav retro 2026-10-02, workboard #1000334). The token-audit
# lane's nav_audit.py imports this file, so the hook and the daily section count
# one way. A navigation call is Read, Grep, Glob, LS, or Bash running one of the
# NAV_BASH verbs (the retro analyzer's set plus `rtk read`).
NAV_TOOLS = {"Read", "Grep", "Glob", "LS"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NAV_BASH = re.compile(
    r"(^|[;&|(]\s*|\s)(grep|rg|find|ls|cat|sed -n|head|tail|wc|git (log|show|grep|ls-files|diff)|tree|fd|rtk read)\b"
)
EDIT_BASH = re.compile(r"(apply_patch|sed -i|cat\s*>\s*[^&]|tee\s+[^|]|python3? - <<.*open\(.*['\"]w)")
# rtk 0.42.3's PreToolUse rewrite, mirrored so counts reflect what ran (probed
# with `rtk rewrite`, 2026-10-02): a segment opened by one of these verbs runs as
# `rtk <verb>`, with cat, `head [-N]` and `tail -N|-n N` becoming `rtk read`.
# rg, sed and fd pass through raw, as do later pipe stages, a segment that
# writes stdout to a file, and the whole command when it has $(...), backticks or
# a for/while/if block. Transcripts keep the command as typed, so the rewrite is
# replayed here; `nav_audit.py --verify-rtk N` checks it against `rtk rewrite`.
RTK_VERBS = {"cat", "head", "tail", "ls", "grep", "find", "git", "wc", "gh", "tree", "diff", "curl", "ps"}
RTK_GIT = {"status", "show", "log", "diff", "add", "commit", "push", "pull", "branch", "fetch", "stash", "worktree"}
RTK_SKIP = re.compile(r"\$\(|`|(?:^|[;&|]\s*)(?:for|while|until|if|case)\s")
RTK_SHAPES = {
    "head": re.compile(r"head(?:\s+-\d+)?\s+[^-\s]"),
    "tail": re.compile(r"tail\s+(?:-\d+|-n\s*\d+)\s"),
    "git": re.compile(r"git(?:\s+-C\s+\S+)?\s+(" + "|".join(sorted(RTK_GIT)) + r")\b"),
}
# Failure classes. Program errors are matched only on lines a program wrote
# ("rg: path: No such file ..."), so file contents that quote an error don't count.
FAIL_LINE = {
    "zsh_glob": re.compile(r"(?:\(eval\)|zsh):\d+: no matches found"),
    "missing_path": re.compile(
        r"(?im)^(?:(?:\(eval\)|[a-z][\w.-]*)(?::\w+)?(?::\d+)?: [^\n]*(?:no such file or directory|cannot access)"
        r"|(?:<tool_use_error>)?(?:File|Path) does not exist)"
    ),
    "cmd_not_found": re.compile(r": command not found|command not found: "),
    # Codex marks its own cap (tool_output_token_limit) as "truncated output".
    "too_large": re.compile(
        r"Output too large \(|exceeds maximum allowed tokens|truncated output \(original token count"
    ),
}
STATUS = re.compile(
    r"gh (?:pr (?:view|checks|status)|run (?:view|list|watch))|queue_pr\.py\s+(?:status|show|list)|result\.json"
    r"|/folds?/|fold-status|lane-status|seat-run\s+--wait|RESUME\.md|BRIEF\.md|PROTOCOL\.md|herdr pane read"
)
RESULT_PARSER = re.compile(r"json\.loads?\([^\n]*result\.json|result\.json[\s\S]*json\.loads?\(")
READ_ARG = re.compile(
    r"(?:^|[;&|]\s*)(?:rtk read|cat|head|tail|sed -n \S+)(?:\s+-\S+(?:\s+\d+)?)*\s+"
    r"([~/][^\s;&|>]+|[\w.-]+/[^\s;&|>]+)"
)
BIG_OUTPUT = 20000
COMMAND_CAP = 20000
INLINE_OUTPUT = 15000


def shell_segments(command: str) -> list:
    """(separator before, segment) pairs, split on ; && || | outside quotes."""
    pairs, current, quote, sep, i = [], "", "", "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif command.startswith(("&&", "||"), i) or ch in ";|":
            pairs.append((sep, current))
            sep = command[i : i + 2] if command.startswith(("&&", "||"), i) else ch
            current, i = "", i + len(sep)
            continue
        current += ch
        i += 1
    pairs.append((sep, current))
    return pairs


def rtk_rewrite(command: str) -> str:
    """The command as rtk's hook would run it (see RTK_VERBS)."""
    if RTK_SKIP.search(command):
        return command
    out = []
    for sep, segment in shell_segments(command):
        lead = segment.lstrip()
        words = lead.split()
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
            words = words[1:]
        verb = words[0] if words else ""
        body = lead[lead.find(verb):] if verb else lead
        if (
            sep != "|"
            and verb in RTK_VERBS
            and not re.search(r"(?<![0-9&])>", body)
            and (verb not in RTK_SHAPES or RTK_SHAPES[verb].match(body))
        ):
            new = ("rtk read" + body[len(verb):]) if verb in ("cat", "head", "tail") else "rtk " + body
            segment = segment[: len(segment) - len(body)] + new
        out.append(sep + segment)
    return "".join(out)


def classify_call(name: str, tool_input, output: str, is_error: bool) -> dict:
    """One call's navigation facts. `output` is the tool_result text as stored,
    which is after rtk filtering and Claude's own large-output spill."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    # EDIT_BASH and RESULT_PARSER backtrack quadratically; a 100 KB heredoc took 8 s,
    # past the hook timeout. The verb and target sit at the start of a command.
    command = (str(tool_input.get("command") or "") if name == "Bash" else "")[:COMMAND_CAP]
    ran = rtk_rewrite(command) if command else ""
    edit = name in EDIT_TOOLS or bool(command and EDIT_BASH.search(command))
    nav = not edit and (name in NAV_TOOLS or bool(ran and NAV_BASH.search(ran)))
    head = (output or "")[:3000]
    fails = []
    # A successful Read, Grep or Glob returns file text, which may quote an error.
    errored = is_error or head.lstrip().startswith(("Path does not exist", "No such file"))
    quoted = name in NAV_TOOLS and not errored
    for kind, pattern in FAIL_LINE.items():
        if (kind == "too_large" or not quoted) and pattern.search(head):
            fails.append(kind)
    target = ""
    if name == "Read":
        target = str(tool_input.get("file_path") or "")
    elif command:
        found = READ_ARG.search(command)
        target = found.group(1) if found else ""
    probe = command or str(tool_input.get("file_path") or tool_input.get("path") or "")
    return {
        "nav": nav,
        "edit": edit,
        "rtk": bool(nav and ran and "rtk " in ran),
        "fails": fails,
        "read_target": target,
        "status": bool(STATUS.search(probe)),
        "result_parser": bool(command and RESULT_PARSER.search(command)),
        "out_len": len(output or ""),
    }


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "") if isinstance(block, dict) else str(block) for block in content
        )
    return "" if content is None else str(content)


def transcript_calls(path: str) -> list:
    """Every tool call in a Claude transcript, oldest first, with its result.

    A streamed reply is written as several rows sharing one `message.id`, and a
    resumed transcript can repeat rows, so calls are keyed by (message id, tool
    use id) and results by tool use id: each call counts once.
    """
    calls: dict = {}
    order: list = []
    results: dict = {}
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if '"tool_use"' not in line and '"tool_result"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and message.get("role") == "assistant":
                    key = (message.get("id"), block.get("id"))
                    if key in calls:
                        continue
                    calls[key] = {
                        "id": block.get("id"),
                        "name": str(block.get("name") or ""),
                        "input": block.get("input"),
                        "ts": entry.get("timestamp"),
                    }
                    order.append(key)
                elif block.get("type") == "tool_result" and block.get("tool_use_id") not in results:
                    results[block.get("tool_use_id")] = (bool(block.get("is_error")), result_text(block.get("content")))
    rows = []
    for key in order:
        call = calls[key]
        is_error, output = results.get(call["id"], (False, ""))
        rows.append({**call, "is_error": is_error, "output": output})
    return rows


def nav_counters(calls) -> dict:
    """The per-seat navigation block from classified calls, oldest first."""
    counts = {
        "calls": 0,
        "nav": 0,
        "nav_rtk": 0,
        "nav_before_write": None,
        "failed": {kind: 0 for kind in FAIL_LINE},
        "failed_lookups": 0,
        "repeat_reads": 0,
        "outputs_over_20k": 0,
        "outputs_over_15k": 0,
        "status_polls": 0,
        "result_parsers": 0,
    }
    seen_reads: set = set()
    nav_so_far = 0
    for facts in calls:
        counts["calls"] += 1
        if facts["edit"] and counts["nav_before_write"] is None:
            counts["nav_before_write"] = nav_so_far
        if facts["nav"]:
            nav_so_far += 1
            counts["nav"] += 1
            counts["nav_rtk"] += facts["rtk"]
        for kind in facts["fails"]:
            counts["failed"][kind] += 1
        # The retro's KPI: a call that died on a missing path or a dead zsh glob.
        counts["failed_lookups"] += bool({"missing_path", "zsh_glob"} & set(facts["fails"]))
        target = facts["read_target"]
        if target:
            if target in seen_reads:
                counts["repeat_reads"] += 1
            seen_reads.add(target)
        counts["outputs_over_20k"] += facts["out_len"] > BIG_OUTPUT
        counts["outputs_over_15k"] += facts["out_len"] > INLINE_OUTPUT
        counts["status_polls"] += facts["status"]
        counts["result_parsers"] += facts["result_parser"]
    return counts


def transcript_nav(path: str):
    """Navigation counters for a subagent transcript; None when unreadable."""
    if not path:
        return None
    try:
        return nav_counters(
            classify_call(row["name"], row["input"], row["output"], row["is_error"]) for row in transcript_calls(path)
        )
    except Exception:
        return None


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
        # No narrowing by the payload's agent_type: it may be a caller name that
        # happens to equal a sibling's seat, which would fake an exact join.
        if len(candidates) == 1:
            return candidates[0], "prompt_hash"
        if candidates:
            # Several open dispatches with one prompt and no telling name or seat:
            # any pick may be the sibling's. Return only the facts they all share
            # (the newest is kept aside for replaying ledgers written before this).
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
    """The open dispatch a PAST closeout claimed, identified by what it recorded.

    A past closeout stores its dispatch's own hash, seat and name, so replay may
    narrow by all three (unlike a live stop, whose agent_type can be a caller
    name). Among exact equals the newest wins, as the hook that wrote it chose.
    """
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
            if record.get("match") == "prompt_hash_ambiguous" and not record.get("matched"):
                # It claimed no dispatch, so replaying it must not close one either.
                continue
            closed = replayed_dispatch(open_dispatches, record)
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
    if not last.strip():
        last = structured_result(transcript_path(payload))

    digests = prompt_digests(payload)
    cost = transcript_cost(transcript_path(payload))
    dispatch, how = match(tail(), session_id, agent_type, agent_name, digests)
    shared = None
    if how == "prompt_hash_ambiguous":
        # Siblings with one prompt share class, seat and model, but which of them
        # stopped is unknown: keep those shared facts and attribute to none of them.
        shared, dispatch = {k: v for k, v in dispatch.items() if k != "_newest"}, None
    ok = bool(last.strip()) and not FAILURE.search(last)
    record = {
        "ts": stamp(now()),
        "event": "closeout",
        "host": os.environ.get("FACTORY_LOCAL_HOST") or os.environ.get("FACTORY_ADMIT_HOST") or os.uname().nodename,
        "session_id": session_id,
        # agent_type is what the payload calls it, which is the NAME whenever the
        # dispatch had one. The seat, model and class are the dispatch's own, so
        # S3's `route report` can group closeouts by seat at all.
        "agent_type": agent_type or None,
        # An ambiguous join reports only the seat its candidates share (none when they
        # differ); the payload's agent_type may be a caller name, not a seat.
        "subagent_type": (
            shared.get("subagent_type")
            if shared is not None
            else (dispatch or {}).get("subagent_type") or agent_type.lower() or None
        ),
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
        "nav": transcript_nav(transcript_path(payload)),
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
