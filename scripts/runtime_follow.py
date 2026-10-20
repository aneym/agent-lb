#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import runtime_bin
from scripts import runtime_tree as tree
from scripts.runtime_tree import Paths, log_line, utc

DRIFT_ALERT_S = 30 * 60
HAND_DEPLOY_GRACE_S = 30 * 60
RETRY_AFTER_S = 30 * 60
MAX_ATTEMPTS = 3
LABEL = "follow"


def epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def load(paths: Paths) -> dict:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return {}


def save(paths: Paths, state: dict) -> None:
    temp = paths.state.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    os.replace(temp, paths.state)


def alert(paths: Paths, state: dict, key: str, text: str) -> None:
    if state.get("alerted") == key:
        return
    state["alerted"] = key
    log_line(paths, f"ALERT runtime-follow: {text}")
    target = os.environ.get("AGENT_LB_FOLLOW_ALERT_TO")
    if target:
        try:
            subprocess.run(
                ["lane-post", "post", "--to", target, "--kind", "info", "--topic", "agent-lb-runtime-follow", text],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def restart_lock_busy(paths: Paths) -> bool:
    lock = paths.root / "lb-restart.lock"
    try:
        fh = lock.open("a")
    except OSError:
        return False
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        fh.close()
    return False


def prepare_stage(paths: Paths, state: dict, want: str) -> bool:
    if tree.staged_rev(paths) != want:
        tree.export(paths, want, paths.stage)
    front = tree.tree_of(paths, want, "frontend")
    if front and state.get("frontend_built") != front and state.get("frontend_failed") != front:
        try:
            tree.build_frontend(paths)
            state["frontend_built"] = front
            state.pop("frontend_failed", None)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            state["frontend_failed"] = front
            alert(
                paths,
                state,
                f"frontend:{front}",
                f"dashboard build failed for origin/main {want[:12]} ({exc}); runtime keeps its current app/static",
            )
    if state.get("frontend_built") == front:
        return tree.stage_static(paths)
    return False


def deploy(paths: Paths, state: dict, want: str, manage_static: bool, bin_todo: list, why: str) -> bool:
    attempts = state.setdefault("attempts", {})
    entry = attempts.setdefault(want, {"count": 0})
    entry["count"] += 1
    entry["last"] = utc()
    before = state.get("deployed") or "unknown"
    saved = tree.backup(paths, LABEL)
    runtime_bin.save(bin_todo, saved / "bin")
    old_lock = tree.lock_sha(paths.runtime)
    report = runtime_bin.BinReport()
    try:
        tree.apply(paths, manage_static=manage_static)
        tree.sync_venv(paths)
        runtime_bin.install(bin_todo, report)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return failed(paths, state, want, saved, old_lock, before, f"install step failed: {exc}")
    log_line(
        paths,
        f"sync ok {want[:10]} (runtime-follow{', bin ' + ','.join(report.refreshed) if report.refreshed else ''})",
    )
    cmd = [
        str(paths.bin_dir / "lb-restart"),
        "--reason",
        f"runtime-follow: origin/main {want[:12]} ({why})",
        "--backup",
        str(saved),
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        rc, tail = done.returncode, (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
    except (OSError, subprocess.TimeoutExpired) as exc:
        rc, tail = 1, [str(exc)]
    if rc != 0:
        return failed(paths, state, want, saved, old_lock, before, f"lb-restart rc={rc}: {tail[0][:200]}")
    state["deployed"] = want
    state["deployed_at"] = utc()
    state["in_sync"] = True
    state["drift_paths"] = 0
    state["drift_sample"] = []
    state.pop("drift_since", None)
    attempts.pop(want, None)
    state.pop("alerted", None)
    tree.prune_backups(paths, LABEL)
    return True


def failed(paths: Paths, state: dict, want: str, saved: Path, old_lock: str, before: str, why: str) -> bool:
    restored = "restored"
    try:
        tree.restore(paths, saved)
        runtime_bin.put_back(saved / "bin", paths.bin_dir)
        if tree.lock_sha(paths.runtime) == old_lock:
            tree.sync_venv(paths)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        restored = f"RESTORE FAILED ({exc}); backup {saved}"
    entry = state["attempts"][want]
    entry["error"] = why
    alert(
        paths,
        state,
        f"deploy:{want}:{entry['count']}",
        f"deploy of origin/main {want[:12]} failed (attempt {entry['count']}/{MAX_ATTEMPTS}): {why}; live primary kept serving {before[:12]}; disk {restored}",
    )
    return False


def may_attempt(state: dict, want: str, now: float) -> bool:
    entry = (state.get("attempts") or {}).get(want)
    if not entry:
        return True
    if entry.get("count", 0) >= MAX_ATTEMPTS:
        return False
    last = epoch(entry.get("last"))
    return last is None or now - last >= RETRY_AFTER_S


def tick(paths: Paths, *, force: bool) -> int:
    paths.root.mkdir(parents=True, exist_ok=True)
    lock = (paths.root / "follow.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    state = load(paths)
    now = time.time()
    state["ticked_at"] = utc()
    try:
        return step(paths, state, now, force=force)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        state["error"] = str(exc)[:300]
        alert(paths, state, f"tick:{type(exc).__name__}", f"follower tick failed: {str(exc)[:200]}")
        return 1
    finally:
        save(paths, state)


def step(paths: Paths, state: dict, now: float, *, force: bool) -> int:
    if (paths.root.parent / "follow.pause").exists() and not force:
        state["paused"] = True
        return 0
    state.pop("paused", None)
    state.pop("error", None)
    if (paths.root / ".sync.lock").is_dir():
        return 0
    if not (paths.runtime / "pyproject.toml").is_file():
        raise RuntimeError(f"{paths.runtime} is not an installed runtime")
    fetch_error = tree.fetch_main(paths)
    if fetch_error:
        state["fetch_error"] = fetch_error
    else:
        state.pop("fetch_error", None)
    want = tree.origin_main(paths)
    if state.get("main") != want:
        state["main"] = want
        state["main_seen_at"] = utc()
    manage_static = prepare_stage(paths, state, want)
    changes = tree.diff(paths, manage_static=manage_static)
    bin_todo, bin_report = runtime_bin.plan(paths, state.setdefault("bin_verdicts", {}))
    state["bin_drift"] = bin_report.drift
    state["drift_paths"] = len(changes)
    state["drift_sample"] = changes[:5]
    if not changes:
        if bin_todo:
            runtime_bin.install(bin_todo, bin_report)
            log_line(
                paths, f"runtime-follow: refreshed bin {','.join(bin_report.refreshed)} to origin/main {want[:10]}"
            )
        if state.get("deployed") != want:
            state["deployed"] = want
            state["deployed_at"] = utc()
        state.pop("drift_since", None)
        state["in_sync"] = True
        return 0
    state["in_sync"] = False
    state.setdefault("drift_since", utc())
    hand_deploy = state.get("deployed") == want
    if hand_deploy and not force:
        since = epoch(state["drift_since"]) or now
        if now - since < HAND_DEPLOY_GRACE_S:
            return 0
    if not force and not may_attempt(state, want, now):
        return 0
    if restart_lock_busy(paths):
        return 0
    why = "runtime edited by hand since the last deploy" if hand_deploy else f"{len(changes)} paths behind"
    return 0 if deploy(paths, state, want, manage_static, bin_todo, why) else 1


def age_min(stamp: str | None, now: float) -> int | None:
    then = epoch(stamp)
    return None if then is None else int((now - then) // 60)


def summary(paths: Paths) -> tuple[str, bool]:
    state = load(paths)
    now = time.time()
    try:
        main = tree.origin_main(paths)
    except (RuntimeError, subprocess.TimeoutExpired):
        main = state.get("main") or ""
    deployed = state.get("deployed") or ""
    tick_age = age_min(state.get("ticked_at"), now)
    in_sync = bool(state.get("in_sync")) and deployed == main
    if in_sync:
        drift_age = 0
        text = f"runtime {deployed[:12]} = origin/main"
    else:
        start = state.get("drift_since") if deployed == main else state.get("main_seen_at") or state.get("drift_since")
        drift_age = age_min(start, now) or 0
        relation = "differs from" if deployed == main else "behind"
        text = f"runtime {deployed[:12] or 'unknown'} {relation} origin/main {main[:12]} ({state.get('drift_paths', '?')} paths) for {drift_age}m"
        entry = (state.get("attempts") or {}).get(main)
        if entry:
            text += f"; {entry.get('count')} failed deploy(s), last: {entry.get('error', '')[:160]}"
    text += f"; follower tick {tick_age}m ago" if tick_age is not None else "; follower never ran"
    if state.get("bin_drift"):
        text += f"; bin copies edited by hand: {','.join(state['bin_drift'])}"
    stale = tick_age is None or tick_age > DRIFT_ALERT_S // 60
    bad = stale or (not in_sync and drift_age * 60 >= DRIFT_ALERT_S)
    return text, bad


def check(paths: Paths) -> int:
    text, bad = summary(paths)
    state = load(paths)
    key = f"check:{text.split(' for ')[0]}" if bad else ""
    if bad and state.get("check_alerted") != key:
        log_line(paths, f"ALERT runtime-follow: {text}")
        state["check_alerted"] = key
        save(paths, state)
    elif not bad and state.get("check_alerted"):
        state.pop("check_alerted", None)
        save(paths, state)
    print(("DRIFT " if bad else "ok ") + text)
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="runtime-follow")
    parser.add_argument("command", nargs="?", default="tick", choices=("tick", "status", "check"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    paths = Paths.from_env()
    if args.command == "tick":
        return tick(paths, force=args.force)
    if args.command == "status":
        print(summary(paths)[0])
        return 0
    return check(paths)


if __name__ == "__main__":
    raise SystemExit(main())
