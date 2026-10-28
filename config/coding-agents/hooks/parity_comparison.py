import json
import os
import signal

import parity_offload  # type: ignore[missing-import]
from parity_cases import payload_for, setup_case  # type: ignore[missing-import]

from parity_execution import direct_argv, effect, effective, floor_failed, floor_of, hook_timeout, run_direct, run_shell, with_exec_timer, with_floor  # type: ignore[missing-import]  # isort: skip  # fmt: skip
from parity_presentation import expected_presentation, same_effect  # type: ignore[missing-import]
from parity_registry import new_hooks, old_hooks  # type: ignore[missing-import]
from parity_sandbox import TEST_PANE, files_of  # type: ignore[missing-import]

RTK_STATE = "home/Library/Application Support/rtk/"


def run_path(sandbox, case, hooks, extra, trace):
    run = sandbox.fresh()
    setup_case(case, run)
    payload = payload_for(case, run)
    trace_path = os.path.join(run, "trace.jsonl")
    extra = dict(extra, **parity_offload.env(case, run))
    env = sandbox.env(run, dict(extra, HOOK_DISPATCH_TRACE=trace_path) if trace else extra)
    cwd = os.path.join(run, case.get("cwd_rel", "work"))
    results = []
    for hook in hooks:
        result = run_shell(hook["command"], payload, env, cwd, hook_timeout(hook))
        result["trace"] = []
        if not trace and case.get("event") == "PreToolUse" and floor_of(hook["command"]):
            result["floor"] = floor_of(hook["command"])
            argv = direct_argv(hook["command"], result["floor"], env["HOME"])
            result["raw"] = (
                run_direct(argv, result["floor"], payload, env, cwd, hook_timeout(hook))
                if argv
                else dict(result, opaque=True, receipt=False)
            )
        if trace and os.path.exists(trace_path):
            with open(trace_path) as handle:
                result["trace"] = [json.loads(line) for line in handle if line.strip()]
            os.unlink(trace_path)
        results.append(result)
    return run, results, files_of(run)


def npx_calls(run):
    try:
        with open(os.path.join(run, "npx-calls.log")) as handle:
            return [line.rstrip("\n").replace(run, "<run>") for line in handle]
    except OSError:
        return []


def same(value, run):
    return json.loads(json.dumps(value).replace(run, "<run>"))


def run_case(sandbox, registry, case):
    event, tool = case["event"], case.get("tool")
    olds = old_hooks(registry["per_hook"], event, tool)
    news = new_hooks(registry["dispatch"], event, tool)
    extra = {"T3_THREAD_ID": TEST_PANE} if case.get("pane") else {}
    outcome = {"name": case["name"], "event": event, "tool": tool, "mismatches": []}
    run, old_results, old_files = run_path(sandbox, case, olds, extra, False)
    run2, new_results, new_files = run_path(sandbox, case, news, extra, True)

    unfolded = {result["command"] for result in new_results if not result["trace"]}
    execd = {record["exec"] for result in new_results for record in result["trace"] if record.get("exec")}
    old_plain = same(effect(event, old_results), run)
    expected = with_exec_timer(old_results, execd)
    old_view = same(expected_presentation(effect(event, with_floor(event, expected, unfolded)), run, run2), run2)
    new_view = same(effect(event, new_results), run2)

    if not same_effect(old_view, new_view):
        outcome["mismatches"].append(
            {
                "what": "effect",
                "old": old_view,
                "new": new_view,
                "floor_raw": [
                    {
                        key: (result.get("raw") or {}).get(key)
                        for key in ("command", "code", "receipt", "timed_out", "opaque", "err")
                    }
                    for result in old_results
                    if result.get("floor")
                ],
            }
        )

    new_by_command, modes = {}, []
    for result in new_results:
        if not result["trace"]:
            new_by_command[result["command"]] = (effective(result), "per-hook")
            continue
        for record in result["trace"]:
            for row in record.get("hooks", []):
                modes.append([row["command"][:70], row.get("kind"), row.get("mode"), row.get("reason")])
                if record.get("exec") == row["command"] and "code" not in row:
                    new_by_command[row["command"]] = (effective(result), "exec")
                elif str(row.get("mode", "")).startswith("skipped ("):
                    new_by_command[row["command"]] = (None, row["mode"])
                elif "code" in row:
                    new_by_command[row["command"]] = (effective(row), row.get("mode"))
    for old in expected:
        if old["command"] not in new_by_command:
            outcome["mismatches"].append({"what": "guard did not run on the new path", "command": old["command"]})
            continue
        new_eff, mode = new_by_command[old["command"]]
        if mode.startswith("skipped ("):
            continue
        raw = old.get("raw") or old
        if (
            old.get("floor")
            and mode != "per-hook"
            and not case.get("replay")
            and not raw.get("opaque")
            and not raw["timed_out"]
            and raw["code"] == 0
            and not raw.get("receipt")
        ):
            outcome["mismatches"].append(
                {"what": "floor guard exited 0 without its allow receipt", "command": old["command"]}
            )
        if mode == "per-hook":
            raw = old
        elif old.get("floor") and floor_failed(raw):
            continue
        elif old.get("floor") and raw["code"] == 2 and not raw["timed_out"]:
            old = dict(old, code=2, out=raw["out"], err=raw["err"])
        old_eff = same(effective(old), run)
        new_eff = same(new_eff, run2)
        if old_eff != new_eff:
            outcome["mismatches"].append(
                {"what": "guard result", "command": old["command"], "mode": mode, "old": old_eff, "new": new_eff}
            )
    extra_new = set(new_by_command) - {old["command"] for old in old_results}
    if extra_new:
        outcome["mismatches"].append({"what": "guards only on the new path", "commands": sorted(extra_new)})
    old_npx, new_npx = npx_calls(run), npx_calls(run2)
    outcome["npx_calls"] = {"old": old_npx, "new": new_npx}
    if new_npx != old_npx and (new_npx or any("--find-config-path" not in call for call in old_npx)):
        outcome["mismatches"].append({"what": "npx calls", "old": old_npx, "new": new_npx})

    if any(str(mode).startswith("skipped (") for _c, _k, mode, _r in modes):
        old_files = {f for f in old_files if not f.startswith(RTK_STATE)}
        new_files = {f for f in new_files if not f.startswith(RTK_STATE)}
    if old_files != new_files:
        outcome["mismatches"].append(
            {"what": "files", "only_old": sorted(old_files - new_files), "only_new": sorted(new_files - old_files)}
        )
    outcome["decision"] = old_plain["decision"] or "allow"
    outcome["block_message"] = old_plain["block"][:400]
    outcome["new_decision"] = new_view["decision"] or "allow"
    outcome["new_block_message"] = new_view["block"][:400]

    outcome["new_updated_input"] = new_view["updatedInput"]
    outcome["new_shown"] = new_view["shown"][:400]
    outcome["new_codes"] = [result["code"] for result in new_results]
    outcome["new_seconds"] = max([result["seconds"] for result in new_results] or [0])
    outcome["new_timed_out"] = any(result["timed_out"] for result in new_results)
    outcome["execd"] = sorted(execd)
    if case.get("expect"):
        outcome["expect"] = case["expect"]
        outcome["expect_ok"] = outcome["decision"] == case["expect"]
    outcome["modes"] = modes

    outcome["timeouts"] = (
        any(r["timed_out"] for r in old_results + new_results)
        or any((r.get("raw") or {}).get("timed_out") for r in old_results)
        or any(row.get("timed_out") for r in new_results for record in r["trace"] for row in record.get("hooks", []))
        or any(
            r["code"] == 128 + signal.SIGALRM and any(record.get("exec") for record in r["trace"]) for r in new_results
        )
    )
    outcome["mismatches"] = parity_offload.verdict(case, outcome)
    outcome["result"] = "pass" if not outcome["mismatches"] else "fail"
    return outcome


def timing_only(case):
    return case["result"] != "pass" and case.get("timeouts")
