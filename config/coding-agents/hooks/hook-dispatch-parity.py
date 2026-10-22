#!/usr/bin/env python3

from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from parity_cases import NEVER_RUN, builtin_cases
from parity_comparison import run_case, timing_only
from parity_processes import counter_selftest, process_count
from parity_registry import group_key
from parity_replay import crash_cases, floor_replay
from parity_sandbox import OVERLAY, Sandbox


def install_lock(home, wait=600):
    import fcntl

    digest = hashlib.sha256(str(os.path.realpath(os.path.expanduser(home))).encode()).hexdigest()[:16]
    lock_dir = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
    handle = open(os.path.join(lock_dir, "agent-lb-install-policy-%d-%s.lock" % (os.getuid(), digest)), "a")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() > deadline:
                handle.close()
                raise SystemExit("error: an install-policy run held the install lock for %ss" % wait)
            time.sleep(1)



def main():
    parser = argparse.ArgumentParser(description="hook-dispatch.py parity fixture")
    parser.add_argument("--home", default=os.path.expanduser("~"))
    parser.add_argument("--registry")
    parser.add_argument("--dispatcher")
    parser.add_argument("--out")
    parser.add_argument("--workdir", help="sandbox parent (default: a new temp dir)")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox")
    parser.add_argument(
        "--overlay", action="append", default=[], metavar="REL=PATH", help="run PATH in place of HOME/REL (repeatable)"
    )
    parser.add_argument("--only", help="regex: run only cases whose name matches")
    parser.add_argument("--jobs", type=int, default=4, help="cases run at once")
    parser.add_argument("--require-process-count", action="store_true")
    parser.add_argument(
        "--no-process-count",
        action="store_true",
        help="skip the process count",
    )
    parser.add_argument("--lock-held", action="store_true", help="the caller holds the install lock")
    parser.add_argument(
        "--require-expectations",
        action="store_true",
        help="also require each expected decision",
    )
    args = parser.parse_args()
    home = os.path.abspath(args.home)
    for item in args.overlay:
        rel, _sep, path = item.partition("=")
        if not rel or not path or not os.path.isfile(path):
            parser.error("--overlay %r: want REL=PATH of an existing file" % item)
        OVERLAY[rel.strip("/")] = os.path.abspath(path)
    lock = None if args.lock_held else install_lock(home)
    registry_path = args.registry or os.path.join(home, ".claude", "hooks", "dispatch", "registry.json")
    dispatcher = args.dispatcher or os.path.join(home, ".claude", "hooks", "hook-dispatch.py")
    with open(registry_path) as handle:
        registry = json.load(handle)
    if args.workdir:
        os.makedirs(args.workdir, exist_ok=True)

        for name in os.listdir(args.workdir):
            stale = os.path.join(args.workdir, name)
            if (
                name.startswith("hook-parity-")
                and os.path.isdir(stale)
                and not os.path.islink(stale)
                and time.time() - os.path.getmtime(stale) > 3600
            ):
                shutil.rmtree(stale, ignore_errors=True)
    root = tempfile.mkdtemp(prefix="hook-parity-", dir=args.workdir)
    started = time.time()
    report = {
        "schema": 2,
        "registry": registry_path,
        "dispatcher": dispatcher,
        "home": home,
        "dispatcher_sha256": hashlib.sha256(open(dispatcher, "rb").read()).hexdigest(),
        "python": sys.executable,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
    }
    try:
        commands = [
            hook["command"]
            for groups in registry["per_hook"].values()
            for group in groups
            for hook in group.get("hooks", [])
        ]
        sandbox = Sandbox(os.path.join(root, "live"), home, registry, dispatcher, commands)
        cases = [case for case in builtin_cases() if case.get("tool") not in NEVER_RUN]
        if args.only:
            cases = [case for case in cases if re.search(args.only, case["name"])]
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            report["cases"] = list(pool.map(lambda case: run_case(sandbox, registry, case), cases))

        by_name = {case["name"]: case for case in cases}
        for index, outcome in enumerate(report["cases"]):
            attempts = 1
            while timing_only(outcome) and attempts < 3:
                attempts += 1
                outcome = dict(run_case(sandbox, registry, by_name[outcome["name"]]), attempts=attempts)
            report["cases"][index] = outcome
        report["crash_timeout"] = [] if args.only else crash_cases(root, dispatcher, home)
        report["floor_replay"] = [] if args.only else floor_replay(root, dispatcher, home, registry, args.jobs)
        skip_count = bool(args.only) or args.no_process_count
        report["process_counter_selftest"] = (
            None if skip_count else counter_selftest(sandbox.env(sandbox.fresh()), root)
        )
        report["process_count"] = [] if skip_count else process_count(sandbox, registry, args.jobs)
        guards = sorted(
            {
                hook["command"]
                for groups in registry["per_hook"].values()
                for group in groups
                for hook in group.get("hooks", [])
            }
        )
        report["guards"] = len(guards)
        report["not_run"] = sorted(
            {
                hook["command"]
                for group in registry["per_hook"].get("PreToolUse", [])
                if group_key(group) in NEVER_RUN
                for hook in group.get("hooks", [])
            }
        )
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            report["sandbox"] = root
        if lock is not None:
            lock.close()
    parity_ok = all(case["result"] == "pass" for case in report["cases"] + report["crash_timeout"])
    floor_ok = all(row["ok"] for row in report["floor_replay"])
    missed = [case["name"] for case in report["cases"] if case.get("expect_ok") is False]
    report["expectations_failed"] = missed
    rows = report["process_count"]

    selftest_ok = bool(report.get("process_counter_selftest") and report["process_counter_selftest"]["ok"])
    count_ok = bool(rows) and selftest_ok and all(row["ok"] for row in rows)
    report["parity"] = "pass" if parity_ok else "fail"
    report["floor"] = "pass" if floor_ok else "fail"
    layers = [row["hook_layer"] for row in rows]
    report["hook_layer_max"] = max(layers) if layers and all(isinstance(n, int) for n in layers) else None
    totals = [row["processes"] for row in rows]
    report["process_count_max"] = max(totals) if totals and all(isinstance(n, int) for n in totals) else None
    report["process_count_ok"] = count_ok
    report["seconds"] = round(time.time() - started, 1)
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(text + "\n")
    failed = [case["name"] for case in report["cases"] + report["crash_timeout"] if case["result"] != "pass"]
    floor_failed_rows = [row["name"] for row in report["floor_replay"] if not row["ok"]]
    print(
        "parity %s: %d cases, %d failed%s; floor replay %s (%d runs%s); hook-layer processes per Bash call max %s"
        % (
            report["parity"],
            len(report["cases"]) + len(report["crash_timeout"]),
            len(failed),
            (" (" + ", ".join(failed) + ")") if failed else "",
            report["floor"],
            len(report["floor_replay"]),
            (", failed: " + ", ".join(floor_failed_rows)) if floor_failed_rows else "",
            report["hook_layer_max"],
        )
    )
    if not parity_ok:
        return 1
    if not floor_ok:
        return 5
    if args.require_process_count and not count_ok:
        return 3
    if args.require_expectations and missed:
        print("expected decision missed: " + ", ".join(missed))
        return 4
    return 0



if __name__ == "__main__":
    sys.exit(main())
