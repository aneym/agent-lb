import json
import re

NAV_TOOLS = {"Read", "Grep", "Glob", "LS"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NAV_BASH = re.compile(
    r"(^|[;&|(]\s*|\s)(grep|rg|find|ls|cat|sed -n|head|tail|wc|git (log|show|grep|ls-files|diff)|tree|fd|rtk read)\b"
)
EDIT_BASH = re.compile(r"(apply_patch|sed -i|cat\s*>\s*[^&]|tee\s+[^|]|python3? - <<.*open\(.*['\"]w)")


RTK_VERBS = {"cat", "head", "tail", "ls", "grep", "find", "git", "wc", "gh", "tree", "diff", "curl", "ps"}
RTK_GIT = {"status", "show", "log", "diff", "add", "commit", "push", "pull", "branch", "fetch", "stash", "worktree"}
RTK_SKIP = re.compile(r"\$\(|`|(?:^|[;&|]\s*)(?:for|while|until|if|case)\s")
RTK_SHAPES = {
    "head": re.compile(r"head(?:\s+-\d+)?\s+[^-\s]"),
    "tail": re.compile(r"tail\s+(?:-\d+|-n\s*\d+)\s"),
    "git": re.compile(r"git(?:\s+-C\s+\S+)?\s+(" + "|".join(sorted(RTK_GIT)) + r")\b"),
}


FAIL_LINE = {
    "zsh_glob": re.compile(r"(?:\(eval\)|zsh):\d+: no matches found"),
    "missing_path": re.compile(
        r"(?im)^(?:(?:\(eval\)|[a-z][\w.-]*)(?::\w+)?(?::\d+)?: [^\n]*(?:no such file or directory|cannot access)"
        r"|(?:<tool_use_error>)?(?:File|Path) does not exist)"
    ),
    "cmd_not_found": re.compile(r": command not found|command not found: "),
    "too_large": re.compile(
        r"Output too large \(|exceeds maximum allowed tokens|truncated output \(original token count"
    ),
}
STATUS = re.compile(
    r"gh (?:pr (?:view|checks|status)|run (?:view|list|watch))|queue_pr\.py\s+(?:status|show|list)|result\.json"
    r"|/folds?/|fold-status|lane-status|seat-run\s+--wait|RESUME\.md|BRIEF\.md|PROTOCOL\.md"
)
RESULT_PARSER = re.compile(r"json\.loads?\([^\n]*result\.json|result\.json[\s\S]*json\.loads?\(")
READ_ARG = re.compile(
    r"(?:^|[;&|]\s*)(?:rtk read|cat|head|tail|sed -n \S+)(?:\s+-\S+(?:\s+\d+)?)*\s+"
    r"([~/][^\s;&|>]+|[\w.-]+/[^\s;&|>]+)"
)
LOG_TARGET = re.compile(r"^/tmp/|\.(?:log|out)$")
BIG_OUTPUT = 20000
COMMAND_CAP = 20000
INLINE_OUTPUT = 15000


def shell_segments(command: str) -> list:

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

    if RTK_SKIP.search(command):
        return command
    out = []
    for sep, segment in shell_segments(command):
        lead = segment.lstrip()
        words = lead.split()
        while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
            words = words[1:]
        verb = words[0] if words else ""
        body = lead[lead.find(verb) :] if verb else lead
        if (
            sep != "|"
            and verb in RTK_VERBS
            and not re.search(r"(?<![0-9&])>", body)
            and (verb not in RTK_SHAPES or RTK_SHAPES[verb].match(body))
        ):
            new = ("rtk read" + body[len(verb) :]) if verb in ("cat", "head", "tail") else "rtk " + body
            segment = segment[: len(segment) - len(body)] + new
        out.append(sep + segment)
    return "".join(out)


def classify_call(name: str, tool_input, output: str, is_error: bool) -> dict:

    tool_input = tool_input if isinstance(tool_input, dict) else {}

    command = (str(tool_input.get("command") or "") if name == "Bash" else "")[:COMMAND_CAP]
    ran = rtk_rewrite(command) if command else ""
    edit = name in EDIT_TOOLS or bool(command and EDIT_BASH.search(command))
    nav = not edit and (name in NAV_TOOLS or bool(ran and NAV_BASH.search(ran)))
    head = (output or "")[:3000]
    fails = []

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
        return "\n".join(str(block.get("text") or "") if isinstance(block, dict) else str(block) for block in content)
    return "" if content is None else str(content)


def transcript_calls(path: str) -> list:

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

        counts["failed_lookups"] += bool({"missing_path", "zsh_glob"} & set(facts["fails"]))
        target = facts["read_target"]
        if target:
            if target in seen_reads:
                counts["status_polls" if LOG_TARGET.search(target) else "repeat_reads"] += 1
            seen_reads.add(target)
        counts["outputs_over_20k"] += facts["out_len"] > BIG_OUTPUT
        counts["outputs_over_15k"] += facts["out_len"] > INLINE_OUTPUT
        counts["status_polls"] += facts["status"]
        counts["result_parsers"] += facts["result_parser"]
    return counts


def transcript_nav(path: str):

    if not path:
        return None
    try:
        return nav_counters(
            classify_call(row["name"], row["input"], row["output"], row["is_error"]) for row in transcript_calls(path)
        )
    except Exception:
        return None
