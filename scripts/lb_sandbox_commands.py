from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import uuid
from pathlib import Path

from scripts.lb_sandbox_custody import (
    custody_paths,
    read_accounts,
)
from scripts.lb_sandbox_edge import (
    FaultState,
    make_edge_handler,
)
from scripts.lb_sandbox_filesystem import (
    append_in_root,
    check_own_volume,
    confine,
    confined,
    is_confined,
    on_own_volume,
    open_dir,
    read_root_json,
    root_entry,
    write_in_root,
    write_json,
)
from scripts.lb_sandbox_guards import (
    EDGES,
    EXIT_ASSERT,
    EXIT_OK,
    EXIT_UNHEALTHY,
    GATE_ALLOWED,
    HOLD_MAX_S,
    LIVE_PEER_URL,
    Refused,
    Unhealthy,
    check_ports,
    check_root,
    now_iso,
    root_for,
)
from scripts.lb_sandbox_runtime import (
    log_to_root,
    usable_counts,
)
from scripts.lb_sandbox_scan import (
    scan_paths,
    scan_processes,
)
from scripts.lb_sandbox_system import (
    emit,
    http,
    launchd_pid,
    listener_pids,
    live_federation_token,
    load_meta,
    pid_alive,
    read_key,
)


def load_secrets(root: Path | None) -> list[str]:

    secrets = []
    token = live_federation_token()
    if not token:
        raise Refused("live federation token not found; a scan without it would be incomplete")
    secrets.append(token)
    if root is not None and entry_of(root) == "dir":
        for account in read_accounts(root, with_tokens=True, strict=True):
            secrets.extend(s for s in account.pop("_secrets") if s)
            secrets.extend(s for s in account.pop("_ciphertexts") if s)
        key = load_key(root)
        if key:
            secrets.append(key)
    return secrets


def load_key(root: Path | None) -> str | None:

    if root is None or entry_of(root) != "dir":
        return None
    try:
        return read_key(root)
    except OSError as exc:
        raise Refused(f"sandbox store key is redirected or shared ({exc.__class__.__name__})") from None


def cmd_aux(root_arg: str) -> int:  # noqa: C901 - one event loop with three listeners and a child
    import asyncio
    import ssl

    import aiohttp
    from aiohttp import web

    root = check_root(Path(root_arg))
    log_to_root(root, "logs/aux.log")
    if os.environ.get("LB_SANDBOX_ROOT") != str(root):
        raise Refused("LB_SANDBOX_ROOT does not match the sandbox root")

    check_own_volume(root)
    confine(root, reads=True)
    meta = read_root_json(root, "sandbox.json")
    if meta.get("run_id") != root.name or not isinstance(meta.get("ports"), dict):
        raise Refused(f"{root} has no sandbox.json stamped with its run id")
    ports = meta["ports"]
    check_ports(list(ports.values()))
    state_file = root / "state" / "aux.json"
    edge_log = root / "logs" / "edge.jsonl"
    gate_log = root / "logs" / "gate.jsonl"
    counters = {
        "gate_forwarded": {},
        "gate_refused": 0,
        "gate_refused_by": {},
        "edge_requests": {"anthropic": 0, "openai": 0},
        "faults_applied": {"anthropic": 0, "openai": 0},
    }
    faults = {edge: FaultState(root, f"state/fault-{edge}.json") for edge in EDGES}
    front_proc: dict[str, subprocess.Popen | None] = {"proc": None}
    ready = {"value": False}

    def append(path: Path, record: dict) -> None:
        with append_in_root(root, path.relative_to(root)) as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")

    def save_state() -> None:
        proc = front_proc["proc"]
        write_json(
            root,
            state_file.relative_to(root),
            {
                "pid": os.getpid(),
                "front_pid": proc.pid if proc else None,
                "front_alive": bool(proc and proc.poll() is None),
                "ready": ready["value"],
                "updated_at": now_iso(),
                "gate": {
                    "forwarded": [
                        {"method": k.split(" ", 1)[0], "path": k.split(" ", 1)[1], "count": v}
                        for k, v in sorted(counters["gate_forwarded"].items())
                    ],
                    "refused": counters["gate_refused"],
                    "refused_by": counters["gate_refused_by"],
                },
                "edge": {
                    "requests": counters["edge_requests"],
                    "faults_applied": counters["faults_applied"],
                    "log": str(edge_log),
                },
            },
        )

    try:
        import certifi

        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_context = ssl.create_default_context()

    async def main() -> None:
        connector = aiohttp.TCPConnector(ssl=ssl_context, limit=0)
        session = aiohttp.ClientSession(
            connector=connector,
            auto_decompress=False,
            trust_env=False,
            skip_auto_headers=("User-Agent", "Accept-Encoding"),
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=900),
        )

        async def gate(request: web.Request) -> web.StreamResponse:
            key = (request.method, request.path)
            name = f"{request.method} {request.path}"
            if key not in GATE_ALLOWED or request.query_string:
                counters["gate_refused"] += 1
                counters["gate_refused_by"][name] = counters["gate_refused_by"].get(name, 0) + 1
                append(gate_log, {"ts": now_iso(), "method": request.method, "path": request.path, "status": 403})
                save_state()
                return web.json_response({"detail": "lb-sandbox peer gate: read-only mirror only"}, status=403)
            headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "accept")}
            async with session.get(f"{LIVE_PEER_URL}{request.path}", headers=headers) as upstream:
                body = await upstream.read()
                status = upstream.status
                content_type = upstream.headers.get("content-type", "application/json")
            counters["gate_forwarded"][name] = counters["gate_forwarded"].get(name, 0) + 1
            append(gate_log, {"ts": now_iso(), "method": request.method, "path": request.path, "status": status})
            save_state()
            return web.Response(status=status, body=body, headers={"content-type": content_type})

        def edge_handler(edge: str):
            def log(entry: dict) -> None:
                append(edge_log, entry)
                save_state()

            return make_edge_handler(edge, EDGES[edge], faults[edge], session, counters, log)

        runners = []
        gate_app = web.Application(client_max_size=1 << 20)
        gate_app.router.add_route("*", "/{tail:.*}", gate)
        apps = [(gate_app, ports["gate"])]
        for edge in EDGES:
            app = web.Application(client_max_size=256 << 20)
            app.router.add_route("*", "/{tail:.*}", edge_handler(edge))
            apps.append((app, ports[f"edge_{edge}"]))
        for app, port in apps:
            runner = web.AppRunner(app, auto_decompress=False, access_log=None, handle_signals=False)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            runners.append(runner)

        front_env = {
            "HOME": str(root / "home"),
            "PATH": "/usr/bin:/bin",
            "AGENT_LB_FRONT_HOST": "127.0.0.1",
            "AGENT_LB_FRONT_LISTEN_PORT": str(ports["front"]),
            "AGENT_LB_FRONT_UPSTREAM_PORTS": f"{ports['primary']},{ports['standby']}",
            "AGENT_LB_FRONT_PREFERRED_FILE": str(root / "state" / "front-preferred-port"),
            "AGENT_LB_FRONT_STATE_FILE": str(root / "state" / "front.json"),
            "LB_SANDBOX_ROOT": str(root),
        }
        front_log = append_in_root(root, "logs/front.log")
        front_proc["proc"] = subprocess.Popen(
            [meta["node"], str(root / "runtime" / "scripts" / "agent-lb-front.mjs"), str(root)],
            env=front_env,
            stdout=front_log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(root),
        )
        deadline = time.monotonic() + 15
        while not listener_pids(ports["front"]):
            if time.monotonic() > deadline or front_proc["proc"].poll() is not None:
                raise Unhealthy("sandbox front did not listen")
            await asyncio.sleep(0.2)
        ready["value"] = True
        save_state()

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, stop.set)
        while not stop.is_set():
            if front_proc["proc"].poll() is not None:
                break
            save_state()
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        ready["value"] = False
        for runner in runners:
            await runner.cleanup()
        await session.close()

    try:
        asyncio.run(main())
    finally:
        proc = front_proc["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        save_state()
    return EXIT_OK


def cmd_status(args) -> int:
    root, meta = load_meta(args.run_id)
    ports = meta["ports"]
    code, body = http(ports["primary"], "/internal/drain/status", timeout=2)
    in_flight = None
    if code == 200:
        try:
            in_flight = int(json.loads(body)["checks"]["in_flight"])
        except (ValueError, KeyError, TypeError):
            in_flight = None
    aux = read_root_json(root, "state/aux.json")
    accounts = read_accounts(root)
    for account in accounts:
        account.pop("usable", None)
    primary_pid = launchd_pid(meta["label"])
    healthy = (
        http(ports["primary"], "/health/ready")[0] == 200
        and http(ports["front"], "/health/ready")[0] == 200
        and bool(aux.get("ready"))
    )
    out = {
        "run_id": meta["run_id"],
        "healthy": healthy,
        "in_flight_primary": in_flight,
        "preferred_port": read_root_json(root, "state/front.json").get("preferred"),
        "primary_pid": primary_pid,
        "aux_pid": aux.get("pid"),
        "front_pid": aux.get("front_pid"),
        "own_volume": on_own_volume(root),
        "confined": {
            "primary": is_confined(primary_pid),
            "aux": is_confined(aux.get("pid")),
            "front": is_confined(aux.get("front_pid")),
        },
        "accounts": accounts,
        "accounts_per_vendor": usable_counts(read_accounts(root)),
        "gate": aux.get("gate", {"forwarded": [], "refused": 0}),
        "edge": {k: v for k, v in aux.get("edge", {}).items() if k != "log"},
        "edge_log": str(root / "logs" / "edge.jsonl"),
        "request_log_store": str(root / "data" / "store.db"),
        "ports": ports,
    }
    return emit(out, EXIT_OK if healthy else EXIT_UNHEALTHY)


CODEX_PLACEHOLDER = "lb-sandbox-placeholder-not-a-credential"


def cmd_client_env(args) -> int:
    root, meta = load_meta(args.run_id)
    check_own_volume(root)
    front = meta["ports"]["front"]
    if args.vendor == "claude":
        os.close(confined(lambda: open_dir(root, "clients/claude", create=True)))
        env = {
            "CLAUDE_LB_LOCAL_URL": f"http://127.0.0.1:{front}",
            "CLAUDE_LB_BASE_URL": f"http://127.0.0.1:{front}",
            "CLAUDE_LB_LAUNCHD_LABEL": meta["label"],
        }
    else:
        home = root / "clients" / "codex"
        config = (
            "\n".join(
                [
                    'model_provider = "agent-lb"',
                    "",
                    "[model_providers.agent-lb]",
                    'name = "OpenAI"',
                    f'base_url = "http://127.0.0.1:{front}/backend-api/codex"',
                    'wire_api = "responses"',
                    "supports_websockets = false",
                    "requires_openai_auth = true",
                    "",
                ]
            )
        ).encode()

        confined(lambda: write_in_root(root, "clients/codex/config.toml", config))
        confined(
            lambda: write_json(
                root, "clients/codex/auth.json", {"OPENAI_API_KEY": CODEX_PLACEHOLDER, "auth_mode": "apikey"}
            )
        )
        env = {"CODEX_SOURCE_HOME": str(home)}
    return emit({"run_id": meta["run_id"], "vendor": args.vendor, "env": env}, EXIT_OK)


def parse_fault(spec: str) -> tuple[str, int | None]:
    if spec == "account_429":
        return "account_429", None
    match = re.fullmatch(r"cut_after_bytes:([0-9]{1,9})", spec)
    if match:
        return "cut_after_bytes", int(match.group(1))
    match = re.fullmatch(r"hold_stream:([0-9]{1,3})", spec)
    if match and 1 <= int(match.group(1)) <= HOLD_MAX_S:
        return "hold_stream", int(match.group(1))
    raise Refused(f"unknown fault {spec!r}; use account_429, cut_after_bytes:N or hold_stream:S (S 1-{HOLD_MAX_S})")


def cmd_fault(args) -> int:
    root, _ = load_meta(args.run_id)
    check_own_volume(root)
    path = f"state/fault-{args.edge}.json"
    if args.clear:
        confined(lambda: write_json(root, path, {"armed": False, "nonce": uuid.uuid4().hex, "at": now_iso()}))
        return emit({"armed": False, "edge": args.edge, "fault": None}, EXIT_OK)
    fault, n = parse_fault(args.arm)
    if fault == "hold_stream" and args.edge != "anthropic":
        raise Refused("hold_stream is served by the anthropic edge only")
    confined(
        lambda: write_json(
            root, path, {"armed": True, "fault": fault, "n": n, "nonce": uuid.uuid4().hex, "at": now_iso()}
        )
    )
    return emit({"armed": True, "edge": args.edge, "fault": args.arm}, EXIT_OK)


def cmd_scan(args) -> int:
    root = root_for(args.run_id)
    if not args.paths and not args.processes:
        raise Refused("scan needs paths, --processes or both")
    secrets = load_secrets(root if entry_of(root) == "dir" else None)
    key = load_key(root)
    newer = Path(args.newer_than).stat().st_mtime if args.newer_than else None
    result = (
        scan_paths(
            [Path(p) for p in args.paths],
            secrets,
            newer_than=newer,
            exclude=[Path(p) for p in args.exclude],
            skip_exact=custody_paths(root),
            keys=[key] if key else None,
            root=root,
        )
        if args.paths
        else {"found": False, "complete": True, "files_scanned": 0, "hits": []}
    )
    result.pop("_files", None)
    if args.processes:
        procs = scan_processes(root, args.run_id, secrets)
        result["processes"] = procs
        result["found"] = result["found"] or procs["found"]
        result["complete"] = result["complete"] and procs["complete"]
    result["secrets_loaded"] = len(secrets)
    result["ciphertext_keys"] = 1 if key else 0
    result["run_id"] = args.run_id
    return emit(result, EXIT_OK if result["complete"] and not result["found"] else EXIT_ASSERT)


def stop_pid(pid: int, bound: float = 15) -> None:
    for sig, wait in ((signal.SIGTERM, bound), (signal.SIGKILL, 5)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while pid_alive(pid) and time.monotonic() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            time.sleep(0.2)
        if not pid_alive(pid):
            return


def entry_of(root: Path) -> str | None:

    try:
        return root_entry(root)
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown"
