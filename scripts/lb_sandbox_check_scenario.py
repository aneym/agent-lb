from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

from scripts.lb_sandbox_check_runtime import (
    HOME,
    LIVE_SYNC_LOG,
    Check,
    leftover_pids,
    listener_pids,
    live_identity,
    live_restart_holder,
    live_restarts_since,
    live_untouched,
    resolve_model,
    sh,
    sync_log_size,
    utc,
)
from scripts.lb_sandbox_check_turns import anthropic_turn, codex_turn, dashboard_export_status, in_flight, within_budget
from scripts.lb_sandbox_check_validation import (
    custody_problems,
    parse_cutover,
    process_scan_problems,
    restart_problems,
    scan_problems,
    stop_problems,
)

GATE_ALLOWED = {("GET", "/api/federation/mirror"), ("GET", "/api/federation/status")}


HOLD_S = 120


CUTOVER_WAIT_S = 60


RESTART_MARKS = {
    "standby_boot": "standby healthy on",
    "front_switch": "front -> :",
    "drain": "old primary exited",
    "restore": "new primary healthy",
    "standby_stopped": "stopped listening",
}


def edge_lines(path: Path, skip: int) -> list[dict]:
    try:
        lines = path.read_text().splitlines()[skip:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text().splitlines())
    except OSError:
        return 0


def run_check(c: Check, args) -> None:  # noqa: C901 - one scenario, steps in order
    out = c.out
    run_id = f"lbsbx-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dt%H%M%Sz')}-{os.getpid()}"
    c.run_id = run_id
    anthropic_model = resolve_model("sonnet-latest", "claude-sonnet-5-5")
    codex_model = resolve_model("sol-latest", "gpt-6.1-sol")

    marker = out / "marker"
    marker.write_text(f"{run_id} {utc()}\n")
    time.sleep(1.1)
    sync_mark = sync_log_size()
    holder_before = live_restart_holder()
    before = live_identity()
    (out / "live-before.json").write_text(json.dumps(before, indent=1))
    c.step(
        "live-identity-before",
        bool(before["primary_pid"] and before["front_pid"] and before["listeners_2455"]),
        f"live primary pid {before['primary_pid']}, front pid {before['front_pid']}, "
        f"2455 {before['listeners_2455']}, 2457 {before['listeners_2457']}, preferred {before['preferred_port']}",
        [str(out / "live-before.json"), str(marker)],
    )

    source = ["--from-ref", args.from_ref] if args.from_ref else ["--from-live"]
    rc, start, start_out = c.sb("start", "start", *source, "--run-id", run_id, timeout=300)
    c.started = rc == 0 or (HOME / ".agent-lb" / "sandboxes" / run_id).exists()
    if not c.step(
        "start",
        rc == 0 and bool(start.get("front_port")),
        f"lb-sandbox start exit {rc}; front {start.get('front_port')}, primary {start.get('primary_port')}, "
        f"standby {start.get('standby_port')}, gate {start.get('gate_port')}, edges {start.get('edge_ports')}; "
        f"usable accounts {start.get('accounts_per_vendor')}; ready in {start.get('ready_s')} s; "
        f"runtime {start.get('runtime')}" + (f"; error {start.get('error')}" if rc else ""),
        [str(start_out), str(start_out.with_suffix(".err"))],
    ):
        c.infra = f"sandbox never got healthy: {start.get('error')}"
        return
    front, primary = start["front_port"], start["primary_port"]
    root = Path(start["root"])

    rc, status, status_out = c.sb("status", "status", "--run-id", run_id)
    counts = status.get("accounts_per_vendor", {})
    ports = status.get("ports", {})
    pids = sorted({p for port in ports.values() for p in listener_pids(port)})
    listen = sh("/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN") if pids else ""
    addresses = [line.split()[8] for line in listen.splitlines()[1:] if len(line.split()) >= 9]
    (out / "sandbox-listeners.txt").write_text(listen)

    export_status = dashboard_export_status(front)
    forged_status = dashboard_export_status(front, forged_user="admin")
    c.step(
        "status-accounts-and-listeners",
        rc == 0
        and counts.get("anthropic", 0) >= 2
        and counts.get("openai", 0) >= 2
        and bool(addresses)
        and all(a.startswith("127.0.0.1:") for a in addresses)
        and export_status == 401
        and forged_status == 401,
        f"healthy {status.get('healthy')}; usable per vendor {counts}; run pids {pids} listen on {addresses}; "
        f"account export API answered {export_status}, with forged Remote-User and X-Forwarded-For "
        f"{forged_status} (want 401, 401)",
        [str(status_out), str(out / "sandbox-listeners.txt")],
    )

    problems = custody_problems(root.parent, root)

    if status.get("own_volume") is not True:
        problems.append(f"root on its own volume: {status.get('own_volume')}")
    confined = status.get("confined") or {}
    if any(confined.get(role) is not True for role in ("primary", "aux", "front")):
        problems.append(f"confined: {confined}")
    c.step(
        "custody-parity",
        not problems,
        f"sandboxes dir and root 0700, store key 0600 with one link, root its own volume, primary/aux/front "
        f"confined {confined}: {problems or 'yes'}",
        [str(status_out)],
    )

    rc_p, procs, procs_out = c.sb("scan-processes", "scan", "--run-id", run_id, "--processes")
    must_see = {"primary": status.get("primary_pid"), "aux": status.get("aux_pid"), "front": status.get("front_pid")}
    problems = process_scan_problems(rc_p, procs, must_see)
    p = procs.get("processes") or {}
    c.step(
        "process-env-scan",
        not problems,
        f"ps eww of {p.get('processes_scanned')} run processes {p.get('pids')}: environments read "
        f"{p.get('visible_pids')}, hits {p.get('hits')}; must read {must_see}: {problems or 'ok'}",
        [str(procs_out)],
    )

    a = anthropic_turn(front, anthropic_model)
    x = codex_turn(front, codex_model)
    (out / "turns-baseline.json").write_text(json.dumps({"anthropic": a, "codex": x}, indent=1))
    c.step(
        "baseline-turns",
        a["status"] == 200
        and bool(a["text"])
        and x["status"] == 200
        and bool(x["text"])
        and x["complete"]
        and within_budget("openai", x),
        f"Anthropic {anthropic_model}: {a['status']} {a['text']!r}; Codex {codex_model}: {x['status']} {x['text']!r} "
        f"output_tokens {x.get('output_tokens')}",
        [str(out / "turns-baseline.json")],
    )

    edge_log = root / "logs" / "edge.jsonl"
    for edge, turn in (
        ("anthropic", lambda: anthropic_turn(front, anthropic_model)),
        ("openai", lambda: codex_turn(front, codex_model)),
    ):
        skip = count_lines(edge_log)
        rc_arm, _, arm_out = c.sb(f"fault-{edge}", "fault", "--run-id", run_id, "--edge", edge, "--arm", "account_429")
        result = turn()
        time.sleep(1.5)
        lines = [ln for ln in edge_lines(edge_log, skip) if ln.get("edge") == edge]
        failed = [ln for ln in lines if ln.get("status") == 429 and ln.get("fault") == "account_429"]
        h1 = failed[0]["auth_hash12"] if failed else None
        served = [ln for ln in lines if ln.get("status") == 200 and ln.get("auth_hash12") not in (None, "", h1)]
        rc_st, st, st_out = c.sb(f"status-after-429-{edge}", "status", "--run-id", run_id)
        cooled = [acc for acc in st.get("accounts", []) if acc.get("auth_hash12") == h1]
        acct = cooled[0] if cooled else {}
        rc_clr, _, clr_out = c.sb(f"fault-clear-{edge}", "fault", "--run-id", run_id, "--edge", edge, "--clear")
        (out / f"turn-429-{edge}.json").write_text(json.dumps({"turn": result, "edge_lines": lines}, indent=1))
        c.step(
            f"account-429-{edge}",
            rc_arm == 0
            and result["status"] == 200
            and bool(result["text"])
            and bool(h1)
            and bool(served)
            and bool(acct.get("in_cooldown"))
            and rc_clr == 0
            and within_budget(edge, result),
            f"turn {result['status']} {result['text']!r} output_tokens {result.get('output_tokens')}; "
            f"edge 429 on hash {h1}, then 200 on "
            f"{served[0]['auth_hash12'] if served else None}; account {acct.get('id')} status {acct.get('status')} "
            f"in_cooldown {acct.get('in_cooldown')} until {acct.get('cooldown_until')}",
            [str(arm_out), str(out / f"turn-429-{edge}.json"), str(st_out), str(clr_out), str(edge_log)],
        )

    rc_hold, _, hold_out = c.sb(
        "fault-hold", "fault", "--run-id", run_id, "--edge", "anthropic", "--arm", f"hold_stream:{HOLD_S}"
    )
    hold_deadline = time.monotonic() + HOLD_S
    skip = count_lines(edge_log)
    restart_log = root / "logs" / "lb-restart.log"
    log_start = restart_log.stat().st_size if restart_log.exists() else 0
    holder: dict = {}
    stream_thread = threading.Thread(
        target=lambda: holder.update(anthropic_turn(front, anthropic_model, stream=True)), daemon=True
    )
    stream_thread.start()
    seen, deadline = -1, time.monotonic() + 20
    while time.monotonic() < deadline and stream_thread.is_alive():
        seen = in_flight(primary)
        if seen >= 1:
            break
        time.sleep(0.05)
    rc_flight, flight, flight_out = c.sb("status-in-flight", "status", "--run-id", run_id)
    restarted: dict = {}

    def do_restart() -> None:
        rc, data, path = c.sb("restart", "restart", "--run-id", run_id, "--reason", f"lb-sandbox-check {run_id}")
        restarted.update({"rc": rc, "data": data, "out": path})

    restart_thread = threading.Thread(target=do_restart, daemon=True)
    restart_thread.start()
    cutover, deadline = None, time.monotonic() + CUTOVER_WAIT_S
    while cutover is None and time.monotonic() < deadline and restart_thread.is_alive():
        try:
            with restart_log.open("rb") as fh:
                fh.seek(log_start)
                cutover = parse_cutover(fh.read().decode(errors="replace"))
        except OSError:
            pass
        if cutover is None:
            time.sleep(0.1)

    procs_mid, rc_pm, pm_out = {}, None, None
    if cutover is not None:
        scan_bound = max(5.0, hold_deadline - time.monotonic() - 10)
        rc_pm, procs_mid, pm_out = c.sb(
            "scan-processes-at-cutover", "scan", "--run-id", run_id, "--processes", timeout=scan_bound
        )

    rc_rel, _, rel_out = c.sb("release-hold", "fault", "--run-id", run_id, "--edge", "anthropic", "--clear")
    released_after_cutover = cutover is not None and rc_rel == 0
    restart_thread.join(timeout=300)
    stream_thread.join(timeout=60)
    restart = restarted.get("data", {})
    restart_out = restarted.get("out", out / "restart-missing")
    log_text = ""
    try:
        with restart_log.open("rb") as fh:
            fh.seek(log_start)
            log_text = fh.read().decode(errors="replace")
    except OSError:
        pass
    (out / "lb-restart.log").write_text(log_text)
    marks = {name: needle in log_text for name, needle in RESTART_MARKS.items()}
    standby = re.search(r"standby healthy on :\d+ in [0-9.]+s \(pid (\d+)\)", log_text)
    front_log = ""
    try:
        front_log = (root / "logs" / "front.log").read_text()
    except OSError:
        pass
    to_standby = front_log.rfind(f"preferred upstream now :{start['standby_port']}")
    switched = to_standby >= 0 and front_log.find(f"preferred upstream now :{primary}", to_standby) > to_standby
    (out / "front.log").write_text(front_log)
    holds = [ln for ln in edge_lines(edge_log, skip) if ln.get("fault") == "hold_stream"]
    hold = holds[0] if len(holds) == 1 else None
    (out / "turn-restart.json").write_text(
        json.dumps({"stream": holder, "in_flight_seen": seen, "cutover": cutover, "hold": holds}, indent=1)
    )
    problems = restart_problems(
        cutover,
        hold,
        released_after_cutover and rc_hold == 0,
        restarted.get("rc"),
        restart,
        marks,
        switched,
        holder,
        not stream_thread.is_alive(),
    )
    c.step(
        "restart-with-stream",
        not problems,
        f"cutover {cutover}; in_flight_primary {seen} (status call: {flight.get('in_flight_primary')}) before "
        f"restart; held stream {hold}; lb-restart exit {restart.get('lb_restart_exit')} in "
        f"{restart.get('seconds')} s; log marks {marks}; front switched to :{start['standby_port']} and back to "
        f":{primary}: {switched}; stream {holder.get('status')} complete {holder.get('complete')} "
        f"{holder.get('text', '')[:40]!r}; problems {problems or 'none'}",
        [
            str(hold_out),
            str(flight_out),
            str(restart_out),
            str(rel_out),
            str(out / "lb-restart.log"),
            str(out / "front.log"),
            str(out / "turn-restart.json"),
            str(edge_log),
        ],
    )
    must_see = {
        "draining old primary": cutover.get("old_pid") if cutover else None,
        "standby": int(standby.group(1)) if standby else None,
        "aux": status.get("aux_pid"),
        "front": status.get("front_pid"),
    }
    problems = process_scan_problems(rc_pm, procs_mid, must_see) if cutover else ["no cutover, no scan"]
    p = procs_mid.get("processes") or {}
    c.step(
        "process-env-scan-at-cutover",
        not problems,
        f"ps eww of {p.get('processes_scanned')} run processes {p.get('pids')} during the drain: environments "
        f"read {p.get('visible_pids')}, hits {p.get('hits')}; must read {must_see}: {problems or 'ok'}",
        [str(pm_out)] if pm_out else [],
    )

    rc_g, gate_status, gate_out = c.sb("status-gate", "status", "--run-id", run_id)
    gate = gate_status.get("gate", {})
    forwarded = [(f.get("method"), f.get("path")) for f in gate.get("forwarded", [])]
    c.step(
        "gate-read-only",
        bool(forwarded) and all(f in GATE_ALLOWED for f in forwarded),
        f"forwarded {gate.get('forwarded')}; refused {gate.get('refused')} {gate.get('refused_by')}",
        [str(gate_out)],
    )

    rc_s0, scan_root, s0_out = c.sb("scan-root", "scan", "--run-id", run_id, str(root))
    rc_s1, scan_out_dir, s1_out = c.sb("scan-out", "scan", "--run-id", run_id, str(out))
    tmpdir = os.environ.get("TMPDIR") or "/tmp"
    rc_s2, scan_new, s2_out = c.sb(
        "scan-newer",
        "scan",
        "--run-id",
        run_id,
        "--newer-than",
        str(marker),
        "--exclude",
        str(root),
        str(HOME / ".agent-rails"),
        str(HOME / "Library" / "LaunchAgents"),
        tmpdir,
        str(HOME / ".agent-lb"),
        timeout=1500,
    )
    problems = (
        scan_problems("sandbox root", rc_s0, scan_root, keyed=True)
        + scan_problems("--out", rc_s1, scan_out_dir, keyed=True)
        + scan_problems("shared trees", rc_s2, scan_new, keyed=True)
    )
    if not scan_root.get("files_scanned"):
        problems.append("the sandbox root scan read no file")
    c.step(
        "scan",
        not problems,
        f"sandbox root (store and key at their own paths excepted): found {scan_root.get('found')} in "
        f"{scan_root.get('files_scanned')} files; --out: found {scan_out_dir.get('found')} in "
        f"{scan_out_dir.get('files_scanned')} files; newer than marker under ~/.agent-rails, ~/Library/LaunchAgents, "
        f"$TMPDIR, ~/.agent-lb: found {scan_new.get('found')} in {scan_new.get('files_scanned')} files; unreadable "
        f"{scan_root.get('unreadable')}+{scan_out_dir.get('unreadable')}+{scan_new.get('unreadable')} "
        f"{scan_new.get('unreadable_paths', [])[:5]}; secrets (tokens, key, ciphertexts) held by lb-sandbox "
        f"{scan_out_dir.get('secrets_loaded')}; problems {problems or 'none'}",
        [str(s0_out), str(s1_out), str(s2_out)],
    )

    stop_and_verify(c, run_id)

    after = live_identity()
    holders = sorted({h for h in (holder_before, live_restart_holder()) if h})
    restarts = live_restarts_since(sync_mark)
    (out / "live-after.json").write_text(json.dumps(after, indent=1))
    (out / "live-restarts.json").write_text(
        json.dumps({"sync_log": str(LIVE_SYNC_LOG), "offset": sync_mark, "logged": restarts, "lock_holders": holders})
    )
    verdict, detail = live_untouched(before, after, restarts, holders)
    evidence = [str(out / "live-before.json"), str(out / "live-after.json"), str(out / "live-restarts.json")]
    if verdict == "infra_error":
        c.env_infra = detail
        c.unverified.append(f"Live agent-lb untouched: not provable this run; {detail}")
        c.steps.append({"name": "live-untouched", "result": "unverified", "detail": detail, "evidence": evidence})
        print(f"[{utc()}] INFRA live-untouched: {detail}", flush=True)
    else:
        c.step("live-untouched", verdict == "pass", detail, evidence)


def stop_and_verify(c: Check, run_id: str) -> None:
    rc, stop, stop_out = c.sb("stop", "stop", "--run-id", run_id, "--logs-to", str(c.out / "logs"))
    c.stopped = True
    leftover = leftover_pids(run_id)
    labels = sh("/bin/launchctl", "list")
    # type: ignore[arg-type]
    problems = stop_problems(rc, stop, leftover, labels, run_id)

    rc_x, exported, x_out = c.sb("scan-exported-logs", "scan", "--run-id", run_id, str(c.out / "logs"))
    problems += scan_problems("exported logs after stop", rc_x, exported, min_secrets=1)
    c.step(
        "stop-clean",
        not problems,
        f"stop exit {rc} clean {stop.get('clean')}: labels {stop.get('labels_loaded')}, listeners "
        f"{stop.get('listeners')}, processes {stop.get('processes')}, root exists {stop.get('root_exists')}; "
        f"logs {stop.get('logs')}; custody {stop.get('custody')}; independent ps leftover pids {leftover}; "
        f"exported logs rescanned for the live token: found {exported.get('found')} in "
        f"{exported.get('files_scanned')} files; problems {problems or 'none'}",
        [str(stop_out), str(x_out), str(c.out / "logs")],
    )
