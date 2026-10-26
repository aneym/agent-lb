#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
import time

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.realpath(__file__))]
try:
    from hook_dispatch_answers import (  # type: ignore[missing-import]
        Result,
        answer,
        finish,
        merge,
        refusal,
        subagent_bash,
        suppress_subagent_owner,
        updates_input,
    )
    from hook_dispatch_planning import (  # type: ignore[missing-import]
        DEFAULT_TIMEOUT,
        FLOOR_EXEC,
        FLOOR_TEXT,
        FLOOR_TIMEOUT,
        INLINE_FLOOR,
        REV_SHA,
        REWRITERS,
        SHAPE,
        cached,
        env_sensitive,
        expand_word,
        hazardous,
        interpreter_of,
        python_like,
        real_tool,
        same_interpreter,
        sha256_file,
        sibling_sources,
        utf8_locale,
        which,
    )
    from hook_dispatch_processes import (  # type: ignore[missing-import]
        SPAWNS,
        External,
        Payload,
        audit,
        exec_into,
        log_failures,
        run_inproc,
        trace,
        trace_hooks,
    )
    from hook_dispatch_registry import load_entry  # type: ignore[missing-import]
    from hook_failure_recovery import deny_reason, parse_json  # type: ignore[missing-import]
    from hook_prefilters import (  # type: ignore[missing-import]
        COMMAND_PREFILTERS,
        SCRIPT_NEEDS,
        PYTHON_PREFILTERED,
        SCRIPT_PREFILTERS,
        escapes_in_input,
        never,
    )
except ImportError as missing:
    pre = sys.argv[1:2] == ["PreToolUse"]
    why = (
        "hook-dispatch: a helper beside it is missing (%s); its guards did not run%s. Restore the per-hook config: "
        "python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
        % (missing, ", so this call is refused" if pre else "")
    )
    sys.stderr.write(why)
    if pre:
        verdict = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why.strip()}
        sys.stdout.write(json.dumps({"hookSpecificOutput": verdict}))
    sys.exit(2 if pre else 1)
IF_READABLE = re.compile(
    r'^if \[ -r "(?P<path>\$HOME/[^"$`\\]+)" \]; then (?P<run>(?:/usr/bin/python3|python3) "(?P=path)"); '
    r"else printf '%s\\n' \"hook warning: missing (?P=path); continuing\" >&2; fi(?: #[^\n]*)?$"
)
FLOOR_SCRIPTS = (
    "branch-guard.py",
    "rm-dynamic-deny",
    "stash-guard",
    "railway-vars-guard.sh",
    "link-cli-guard.sh",
    "plutil-guard.sh",
    "kill-guard",
    "agent-lb-bootout-guard.sh",
    "dangerous-command-guard.sh",
    "no-chrome-guard.sh",
    "host-credential-guard.py",
    "gh-write-guard.py",
    "kill-scope-guard.py",
)
FLOOR_WORD = re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in FLOOR_SCRIPTS))

EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
DISPATCH_TIMEOUT = 8.0
REV = re.compile(r"[0-9a-f]{12}")

LEGACY_FLOOR_COMMANDS = {
    "0fbf6582d067534e0cf7179a1801b12c675c75397170a48d36aec72171483e7c": '"$HOME/.claude/hooks/dangerous-command-guard.sh"',
}


class Plan(object):
    __slots__ = (
        "hook",
        "command",
        "timeout",
        "kind",
        "script",
        "argv",
        "interp",
        "devnull",
        "fallback_true",
        "fallback_text",
        "source",
        "reason",
        "prefilter",
        "floor",
        "exec_argv",
        "opaque",
        "target",
    )

    def __init__(self, hook):
        self.hook = hook
        self.command = hook.get("command", "") if isinstance(hook.get("command"), str) else ""
        timeout = hook.get("timeout")
        self.timeout = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else DEFAULT_TIMEOUT
        self.kind, self.script, self.argv, self.interp = "external", None, None, None
        self.devnull, self.fallback_true, self.fallback_text, self.source = False, False, None, None
        self.reason, self.prefilter = "", None
        self.floor = floor_name(self.command)
        self.timeout = (
            min(float(timeout), FLOOR_TIMEOUT)
            if self.floor and isinstance(timeout, (int, float)) and timeout > 0
            else (FLOOR_TIMEOUT if self.floor else self.timeout)
        )
        self.exec_argv = None
        self.opaque = None
        self.target = None


def legacy_floor(command):
    return LEGACY_FLOOR_COMMANDS.get(hashlib.sha256(command.encode("utf-8")).hexdigest()) if command else None


def floor_name(command):
    if not command:
        return None
    match = FLOOR_WORD.search(legacy_floor(command) or command)
    if match:
        return match.group(1)
    return INLINE_FLOOR if FLOOR_TEXT in command else None


def floor_exec(command, name):
    match = FLOOR_EXEC.match(legacy_floor(command) or command)
    if not match:
        return None
    script = expand_word(match.group("script"))
    if os.path.basename(script) != name:
        return None
    return ([match.group("interp")] if match.group("interp") else []) + [script] + match.group("args").split()


def plan_hook(hook, inproc_names, script_pins=None):
    plan = Plan(hook)
    command = plan.command
    if hook.get("type", "command") != "command" or not command:
        plan.reason = "not a command hook"
        return plan
    if plan.floor:
        plan.exec_argv = floor_exec(command, plan.floor)
        if plan.exec_argv is None:
            plan.reason = "floor guard"
            plan.opaque = (
                "is not registered as a direct exec of its own script (`[python3|bash] <script> [args]`; "
                "a floor guard never runs through a shell, S44)"
            )
            return plan
        command = legacy_floor(command) or command
    if command.strip() in REWRITERS:
        plan.target = real_tool(command.split()[0])
        if not plan.target:
            plan.kind, plan.reason = "absent", "rewriter is not on PATH, so it cannot rewrite"
            return plan
        plan.kind, plan.reason = "rewriter", "side-effect free rewriter"
        return plan
    prefilter = COMMAND_PREFILTERS.get(hashlib.sha256(command.encode("utf-8")).hexdigest())
    if prefilter is not None:
        sensitive = env_sensitive()
        if sensitive:
            plan.reason = "pinned command, but %s is set" % sensitive
        else:
            plan.kind, plan.prefilter, plan.reason = "filtered", prefilter, "pinned command"
        return plan
    guarded = IF_READABLE.match(command)
    if guarded:
        if not os.access(expand_word(guarded.group("path")), os.R_OK):
            plan.kind, plan.prefilter, plan.reason = "filtered", never, "its script is missing, so it only warns"
            return plan
        command = guarded.group("run")
    match = SHAPE.match(command)
    if not match:
        plan.reason = "shape"
        return plan
    plan.devnull = bool(match.group("devnull"))
    plan.fallback_true = bool(match.group("true"))
    plan.fallback_text = match.group("fb")
    script = expand_word(match.group("script"))
    name = os.path.basename(os.path.realpath(script))
    if match.group("interp") is None or (name in PYTHON_PREFILTERED and python_like(match.group("interp"))):
        pinned = SCRIPT_PREFILTERS.get(name)
        if pinned is not None:
            sensitive = env_sensitive()
            hashes = pinned[0] if isinstance(pinned[0], tuple) else (pinned[0],)
            derived = (script_pins or {}).get(name) if isinstance(script_pins, dict) else None
            if isinstance(derived, str) and REV_SHA.fullmatch(derived):
                hashes = hashes + (derived,)
            missing = [need[0] for need in SCRIPT_NEEDS.get(name, ()) if not any(which(tool) for tool in need)]
            if match.group("fb") is not None:
                plan.reason = "pinned script with a fallback answer"
            elif sensitive:
                plan.reason = "pinned script, but %s is set" % sensitive
            elif missing:
                plan.reason = "pinned script, but %s is not on PATH" % ", ".join(missing)
            elif sha256_file(os.path.realpath(script)) in hashes:
                plan.kind, plan.prefilter, plan.reason = "filtered", pinned[1], "pinned script"
            else:
                plan.reason = "script changed since its prefilter was reviewed"
            return plan
    if name not in inproc_names:
        plan.reason = "not on the in-process list"
        return plan
    if not os.path.isfile(script):
        plan.reason = "missing script"
        return plan
    interp = match.group("interp")
    if interp is None:
        interp = interpreter_of(script)
    if not python_like(interp):
        plan.reason = "not python"
        return plan
    if not utf8_locale() and not same_interpreter(interp):
        plan.reason = "locale"
        return plan
    try:
        with open(script, "rb") as handle:
            data = handle.read()
        source = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        plan.reason = "unreadable"
        return plan
    pinned = inproc_names.get(name) if isinstance(inproc_names, dict) else None
    if pinned and hashlib.sha256(data).hexdigest() != pinned:
        plan.reason = "changed since the parity fixture passed on it"
        return plan
    texts = [source] + sibling_sources(os.path.dirname(os.path.realpath(script)), source)
    key = hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()
    if cached("hazard-" + key, lambda: any(hazardous(text) for text in texts), bool):
        plan.reason = "hazard"
        return plan
    plan.script, plan.interp, plan.source = script, interp, source
    plan.argv = [script] + match.group("args").split()
    plan.kind = "inproc"
    plan.reason = "same interpreter" if same_interpreter(interp) else "other interpreter"
    return plan


def run_entry(event, hooks, raw, inproc_names, key=None, script_pins=None):
    deadline = time.monotonic() + DISPATCH_TIMEOUT
    record = {"event": event, "pid": os.getpid(), "ppid": os.getppid()}
    plans = []
    for hook in hooks:
        try:
            plans.append(plan_hook(hook, inproc_names, script_pins))
        except Exception as exc:
            plan = Plan(hook)
            plan.reason = "plan error %s" % exc.__class__.__name__
            plans.append(plan)
    try:
        payload_obj = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload_obj = None
    payload = Payload(raw)
    results: list[Result | None] = [None] * len(plans)
    started = {}
    try:
        for index, plan in enumerate(plans):
            if plan.opaque and event == "PreToolUse":
                plan.kind, plan.reason = "opaque", plan.reason + ", " + plan.opaque
                results[index] = Result(
                    1,
                    b"",
                    ("hook-dispatch: %s\n" % plan.command[:300]).encode("utf-8", "backslashreplace"),
                    False,
                    "opaque",
                )
                results[index].floor_failure = plan.opaque
                continue
            if plan.kind == "absent":
                results[index] = Result(0, b"", b"", False, "skipped")
                continue
            if plan.kind == "inproc" and plan.prefilter is not None and not plan.prefilter(payload_obj):
                plan.kind = "skipped"
                results[index] = Result(0, b"", b"", False, "skipped")
            if plan.kind == "filtered":
                try:
                    needed = escapes_in_input(payload_obj) or plan.prefilter(payload_obj)
                except Exception:
                    needed = True
                if needed:
                    plan.kind = "external"
                    plan.reason += ", prefilter matched"
                else:
                    results[index] = Result(0, b"", b"", False, "skipped")
        for index, plan in enumerate(plans):
            if plan.kind == "external":
                started[index] = External(plan, payload)
        for index, plan in enumerate(plans):
            if plan.kind != "inproc":
                continue
            plan.timeout = min(plan.timeout, max(0.001, deadline - time.monotonic()))
            try:
                result, crashed = run_inproc(plan, payload)
            except Exception as exc:
                plan.kind, plan.reason = "external", "in-process setup failed (%s)" % exc.__class__.__name__
                started[index] = External(plan, payload)
                continue
            if crashed and not same_interpreter(plan.interp):
                plan.kind, plan.reason = "external", "crashed under another interpreter"
                started[index] = External(plan, payload)
                continue
            results[index] = finish(plan, result)
        for index, plan in enumerate(plans):
            if plan.kind != "rewriter":
                continue
            plan.timeout = min(plan.timeout, max(0.001, deadline - time.monotonic()))
            known = [(i, r) for i, r in enumerate(results) if r is not None]
            blocked = any(r.code == 2 or deny_reason(parse_json(r.out)) for _i, r in known if not r.timed_out)
            superseded = any(i > index and updates_input(r) for i, r in known)
            if blocked or superseded:
                results[index] = Result(0, b"", b"", False, "skipped (%s)" % ("blocked" if blocked else "superseded"))
                continue
            others_quiet = (
                not started
                and all(r.empty() and not r.floor_failure for _i, r in known)
                and all(p.kind == "rewriter" or results[i] is not None for i, p in enumerate(plans) if i != index)
            )
            if (
                others_quiet
                and sum(1 for p in plans if p.kind == "rewriter") == 1
                and not subagent_bash(payload_obj, event)
            ):
                record["hooks"] = trace_hooks(plans, results)
                record["spawned"] = SPAWNS[0]
                exec_into(plan, payload, record)
            started[index] = External(plan, payload)
        for index, external in started.items():
            results[index] = finish(plans[index], external.result(deadline))
        log_failures(event, key, plans, results)
        record["hooks"] = trace_hooks(plans, results)
        record["spawned"] = SPAWNS[0]
        answer = suppress_subagent_owner(merge(event, plans, results), payload_obj, event)
        record["answer"] = {
            "code": answer.code,
            "out": answer.out.decode("utf-8", "replace"),
            "err": answer.err.decode("utf-8", "replace"),
        }
        trace(record)
        return answer
    finally:
        payload.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] not in EVENTS:
        sys.stderr.write(
            "hook-dispatch: usage: hook-dispatch.py <%s> <matcher> [<rev>]; its guards did not run\n" % "|".join(EVENTS)
        )
        return 2 if argv[:1] == ["PreToolUse"] else 1
    event, key = argv[0], argv[1]
    rev = argv[2] if len(argv) > 2 else None
    raw = b""
    try:
        raw = sys.stdin.buffer.read()
        try:
            if rev is not None and not REV.fullmatch(rev):
                raise ValueError("bad rev %r" % rev)
            registry, hooks, _path = load_entry(event, key, rev)
        except ValueError as exc:
            note = (
                "hook-dispatch: registry unreadable (%s); the %s '%s' guards did not run. Restore the per-hook "
                "config: python3 ~/.agents/policy/coding-agents/install-policy.py --hook-dispatcher off\n"
                % (exc, event, key)
            )
            return answer(refusal(event, note))
        if os.environ.get("HOOK_DISPATCH_TRACE"):
            sys.addaudithook(audit)
        inproc_names = (
            registry.get("inproc_sha")
            if isinstance(registry.get("inproc_sha"), dict)
            else dict.fromkeys(name for name in registry.get("inproc") or [] if isinstance(name, str))
        )
        script_pins = registry.get("script_sha") if isinstance(registry.get("script_sha"), dict) else None
        result = run_entry(event, hooks, raw, inproc_names, key, script_pins)
    except BaseException as exc:
        note = "hook-dispatch: failed (%s: %s); the %s '%s' guards did not all run%s\n" % (
            exc.__class__.__name__,
            str(exc)[:200],
            event,
            key,
            ", so this call is refused" if event == "PreToolUse" else "",
        )
        try:
            return answer(refusal(event, note))
        except BaseException:
            return 2 if event == "PreToolUse" else 1
    return answer(result)


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
