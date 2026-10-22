from __future__ import annotations
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from lb_sandbox_faults import (
    FaultState,
    make_edge_handler,
)
from lb_sandbox_filesystem import (
    append_in_root,
    read_root_json,
    write_json,
)
from lb_sandbox_paths import (
    EDGES,
    EXIT_OK,
    GATE_ALLOWED,
    LIVE_PEER_URL,
    Refused,
    Unhealthy,
    check_ports,
    now_iso,
)
from lb_sandbox_private_storage import (
    check_root,
)
from lb_sandbox_processes import (
    listener_pids,
)
from lb_sandbox_protection import (
    confine,
)
from lb_sandbox_runtime import (
    log_to_root,
)
from lb_sandbox_volumes import (
    check_own_volume,
)


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
