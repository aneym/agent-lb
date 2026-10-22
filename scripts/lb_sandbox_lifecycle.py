from __future__ import annotations
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from lb_sandbox_account_reader import (
    read_accounts,
)
from lb_sandbox_cleanup import (
    teardown,
)
from lb_sandbox_credentials import (
    create_run_key,
)
from lb_sandbox_filesystem import (
    append_in_root,
    confined,
    read_root_json,
)
from lb_sandbox_launchd import (
    find_node,
    live_identity,
    log_failure_classes,
    submit_job,
    usable_counts,
)
from lb_sandbox_ownership import (
    CREATED,
)
from lb_sandbox_paths import (
    EXIT_ASSERT,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNHEALTHY,
    LIVE_PLIST,
    LIVE_RUNTIME,
    LIVE_VENV,
    Refused,
    SANDBOXES,
    Unhealthy,
    bootstrap_script,
    check_launchctl,
    check_ports,
    check_run_id,
    labels_for,
    now_iso,
    root_for,
)
from lb_sandbox_private_storage import (
    make_private,
    make_root,
    private_names,
    root_entry,
    sandboxes_fd,
    sandboxes_id,
)
from lb_sandbox_processes import (
    emit,
    http,
    label_loaded,
    load_meta,
    pick_ports,
    pid_alive,
    port_free,
    ports_in_use_by_sandboxes,
    sandbox_roots,
    stamped_meta,
)
from lb_sandbox_protection import (
    check_bootstrap,
    check_interpreter,
    interpreter_for,
    run_confined,
)
from lb_sandbox_runtime import (
    cleanup_unpublished,
    plist_bodies,
    populate_root,
    ref_tree,
)
from lb_sandbox_volumes import (
    attach_volume,
    check_own_volume,
)


def cmd_start(args) -> int:  # noqa: C901 - one sequence, each step torn down on failure
    run_id = check_run_id(args.run_id)
    if not (args.from_live or args.from_ref):
        raise Refused("start needs --from-live or --from-ref REF (accounts always come from live)")
    if args.transport != "http":
        raise Refused("only --transport http is supported (the edge must see and cut streams)")
    check_launchctl()
    root = root_for(run_id)
    try:
        if root_entry(root) is not None:
            raise Refused(f"sandbox root {root} already exists")
        if private_names(run_id):
            raise Refused(f"run {run_id} already has a private dir (an earlier start); stop it first")
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise Refused(f"sandboxes dir is a link or not private ({exc})") from None
    label, aux_label = labels_for(run_id)
    if label_loaded(label) or label_loaded(aux_label):
        raise Refused(f"label {label} is already loaded")
    if not LIVE_PLIST.is_file() or not (LIVE_RUNTIME / "app").is_dir() or not (LIVE_VENV / "bin" / "python").exists():
        raise Unhealthy("live runtime, venv or plist missing")


    tree = None
    runtime_source: dict = {"kind": "live", "path": str(LIVE_RUNTIME)}
    if args.from_ref:
        sha, tree = ref_tree(Path(args.repo), args.from_ref)
        runtime_source = {"kind": "ref", "ref": args.from_ref, "sha": sha, "repo": str(args.repo)}
    taken = ports_in_use_by_sandboxes()
    if args.port is not None:
        check_ports([args.port])
        if args.port in taken or not port_free(args.port):
            raise Refused(f"--port {args.port} is in use")
        taken.add(args.port)
    picked = pick_ports(6 if args.port is None else 5, taken)
    front = args.port if args.port is not None else picked.pop(0)
    names = ("primary", "standby", "gate", "edge_anthropic", "edge_openai")
    ports = {"front": front, **dict(zip(names, picked, strict=True))}
    check_ports(list(ports.values()))
    started = time.monotonic()
    out: dict = {"run_id": run_id, "root": str(root), "label": label, "aux_label": aux_label}
    out["runtime"] = runtime_source

    stopping: list[int] = []

    def interrupted(signum, frame):
        if not stopping:
            stopping.append(signum)
            raise Unhealthy(f"interrupted by signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    priv: Private | None = None
    published = False
    try:


        try:
            sandboxes_fd(create=True)
        except OSError as exc:
            raise Refused(f"{SANDBOXES} is not a real private directory ({exc})") from None
        priv = make_private(run_id)
        CREATED[run_id] = priv
        root_fd, root_ident = make_root(root, priv)
        published = True
        try:
            attach_volume(root, run_id, priv, root_ident)
        finally:
            os.close(root_fd)
        meta = {
            "run_id": run_id,
            "label": label,
            "aux_label": aux_label,
            "ports": ports,
            "caller_pid": os.getppid(),
            "created_at": now_iso(),
            "created_epoch": time.time(),
            "transport": args.transport,
            "node": find_node(),
            "runtime": runtime_source,

            "sandboxes_id": list(sandboxes_id()),
        }
        source = str(bootstrap_script())
        check_bootstrap(Path(source), root)
        check_interpreter(interpreter_for(root), root)
        run_confined(
            root,
            lambda: populate_root(root, run_id, ports, meta, args.transport, source, tree),
            reads=False,
            timeout_s=300,
        )
        create_run_key(root, priv)


        primary_job, aux_job = plist_bodies(root, run_id, ports, args.transport, Path(source))
        out["live"] = live_identity()

        submit_job(aux_job)
        deadline = time.monotonic() + 30
        while not read_root_json(root, "state/aux.json").get("ready"):
            if time.monotonic() > deadline:
                raise Unhealthy("aux (gate, edges, front) did not become ready in 30 s")
            time.sleep(0.25)
        submit_job(primary_job)
        deadline = time.monotonic() + 90
        while not (
            http(ports["primary"], "/health/ready")[0] == 200 and http(ports["front"], "/health/ready")[0] == 200
        ):
            if time.monotonic() > deadline:
                raise Unhealthy("sandbox primary or front not healthy in 90 s")
            time.sleep(0.5)
        out["boot_s"] = round(time.monotonic() - started, 1)
        deadline = time.monotonic() + 120
        while True:
            counts = usable_counts(read_accounts(root))
            if all(n >= args.min_accounts for n in counts.values()):
                break
            if time.monotonic() > deadline:
                raise Unhealthy(f"mirror gave {counts} usable accounts, need {args.min_accounts} per vendor in 120 s")
            time.sleep(1)
        out.update(
            {
                "front_port": ports["front"],
                "primary_port": ports["primary"],
                "standby_port": ports["standby"],
                "gate_port": ports["gate"],
                "edge_ports": {"anthropic": ports["edge_anthropic"], "openai": ports["edge_openai"]},
                "accounts_per_vendor": counts,
                "ready_s": round(time.monotonic() - started, 1),
            }
        )
        return emit(out, EXIT_OK)
    except Exception as exc:
        if priv is None:
            if isinstance(exc, Refused):
                raise
            out["error"] = f"{exc.__class__.__name__}: {exc}"
            return emit(out, EXIT_UNHEALTHY)
        out["refused" if isinstance(exc, Refused) else "error"] = f"{exc.__class__.__name__}: {exc}"
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        if published:
            out["failure"] = log_failure_classes(root)
            out["teardown"] = teardown(run_id, Path(args.logs_to) if args.logs_to else None, created=True)
        else:
            out["teardown"] = cleanup_unpublished(priv)
        return emit(out, EXIT_REFUSED if isinstance(exc, Refused) else EXIT_UNHEALTHY)


def cmd_restart(args) -> int:
    check_launchctl()
    root, _ = load_meta(args.run_id)


    check_own_volume(root)
    lb_restart = Path(__file__).resolve().parent / "lb-restart"
    if not lb_restart.is_file():
        raise Unhealthy(f"{lb_restart} missing")
    log_path = root / "logs" / "lb-restart.log"


    argv = isolated_python(
        sys.executable, lb_restart, ["--sandbox", str(root / "lb-restart.json"), "--skip-exchange-wait", "--skip-exit-timeout-gate", "--reason", args.reason]
    )
    started = time.monotonic()

    with confined(lambda: append_in_root(root, "logs/lb-restart.log")) as log:
        log.write(f"==== {now_iso()} lb-sandbox restart: {' '.join(argv[1:])}\n")
        log.flush()

        env = {**isolated_env(dict(os.environ)), "LB_SANDBOX_ROOT": str(root)}
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(root), env=env
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            sys.stderr.write(line)
        code = proc.wait()
    out = {"lb_restart_exit": code, "log": str(log_path), "seconds": round(time.monotonic() - started, 1)}
    return emit(out, EXIT_OK if code == 0 else EXIT_UNHEALTHY)


def cmd_stop(args) -> int:
    check_launchctl()
    run_id = check_run_id(args.run_id)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    result = teardown(run_id, Path(args.logs_to) if args.logs_to else None)
    result["run_id"] = run_id
    if result.get("refused"):
        return emit(result, EXIT_REFUSED)
    safe_logs = result.get("logs", {})
    safe = (
        safe_logs.get("scan_found") is False
        and safe_logs.get("scan_complete") is True
        and not safe_logs.get("secret_load_error")
        and not safe_logs.get("copy_error")
        and not safe_logs.get("export_found")
        and result.get("custody", {}).get("key_unlinked") is not False
    )
    return emit(result, EXIT_OK if result["clean"] and safe else EXIT_ASSERT)


def cmd_gc(args) -> int:
    check_launchctl()
    stopped, kept, skipped = [], [], []
    now = time.time()
    for root in sandbox_roots():
        run_id = root.name
        meta = stamped_meta(root, run_id)
        created = meta.get("created_epoch")
        if not meta or isinstance(created, bool) or not isinstance(created, (int, float)):

            skipped.append({"run_id": run_id, "reason": "no sandbox.json stamped with this run id"})
            continue
        if now - float(created) > args.older_than and not pid_alive(meta.get("caller_pid")):
            result = teardown(run_id, None)
            stopped.append(run_id) if result["clean"] else kept.append({"run_id": run_id, "teardown": result})
    return emit({"stopped": stopped, "not_clean": kept, "skipped": skipped}, EXIT_OK if not kept else EXIT_ASSERT)


def isolated_env(env: dict[str, str]) -> dict[str, str]:

    return {k: v for k, v in env.items() if not k.startswith(("PYTHON", "DYLD_"))}


def isolated_python(python: str, script: Path, argv: list[str]) -> list[str]:

    return [python, "-I", str(script), *argv]
