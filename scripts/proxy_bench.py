#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import cProfile
import gzip
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import AsyncExitStack, ExitStack
from pathlib import Path
from typing import Any, cast

import aiohttp
from aiohttp import http_parser, web, web_protocol

ROOT = Path(__file__).resolve().parents[1]
METRICS = ("ttfb_ms", "first_event_ms", "last_event_ms", "total_ms", "event_delivery_ms")
TEXT = "We measure each event carefully as the local server streams a steady sequence of small text updates. "


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--events", type=int, default=200)
    parser.add_argument("--gap-ms", type=float, default=2)
    parser.add_argument("--out", type=Path, default=Path("proxy-bench.json"))
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(args.runs, args.concurrency, args.events) < 1 or args.gap_ms < 0:
        parser.error("positive counts; nonnegative gap")
    return args


async def seed():
    from datetime import datetime, timedelta, timezone

    from app.core.crypto import TokenEncryptor
    from app.db.models import Account, AccountProxyBinding, AccountStatus, ProxyEndpoint, ProxyPool, ProxyPoolMember
    from app.db.session import SessionLocal, close_db, init_db

    await init_db()
    encryptor = TokenEncryptor()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as session:
        for provider in ("anthropic", "openai"):
            session.add(
                Account(
                    id=f"bench-{provider}",
                    provider=provider,
                    email=f"{provider}@bench.invalid",
                    plan_type="max" if provider == "anthropic" else "plus",
                    access_token_encrypted=encryptor.encrypt("bench-access"),
                    refresh_token_encrypted=encryptor.encrypt("bench-refresh"),
                    last_refresh=now,
                    access_expires_at=now + timedelta(days=30),
                    status=AccountStatus.ACTIVE,
                    chatgpt_account_id="bench-openai" if provider == "openai" else None,
                )
            )
        await session.flush()
        session.add(
            ProxyEndpoint(
                id="bench-endpoint",
                name="benchmark",
                scheme="http",
                host="127.0.0.1",
                port=int(os.environ["PROXY_BENCH_UPSTREAM_PORT"]),
            )
        )
        session.add(ProxyPool(id="bench-pool", name="benchmark"))
        await session.flush()
        session.add(ProxyPoolMember(pool_id="bench-pool", endpoint_id="bench-endpoint"))
        session.add(AccountProxyBinding(account_id="bench-openai", pool_id="bench-pool"))
        await session.commit()
    await close_db()


def serve(args):
    sys.path.insert(0, str(ROOT))
    allowed_port = int(os.environ["PROXY_BENCH_UPSTREAM_PORT"])

    def guard(event, values):
        if event == "socket.connect":
            address = values[1]
            if isinstance(address, tuple) and address[:2] != ("127.0.0.1", allowed_port):
                raise RuntimeError("egress blocked")

    sys.addaudithook(guard)
    asyncio.run(seed())
    import uvicorn

    profiler = cProfile.Profile(timer=time.process_time) if args.profile else None
    if profiler:
        signal.signal(signal.SIGUSR1, lambda *_: profiler.enable())
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.serve, log_level="error", access_log=False)
    finally:
        if profiler:
            profiler.disable()
            profiler.dump_stats(str(args.profile))


class Upstream(web.Server):
    def __init__(self, handler):
        super().__init__(handler)
        self.tcp = 0

    def connection_made(self, handler, transport):
        self.tcp += 1
        super().connection_made(handler, transport)


class Fake:
    def __init__(self, args):
        self.args = args
        self.emissions = {}
        self.requests = []

    async def handle(self, request):
        raw = await request.read()
        if request.headers.get("Content-Encoding") == "gzip" and raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        if not raw:
            return web.json_response({"error": f"unexpected {request.method} {request.path}"}, status=404)
        body = json.loads(raw)
        match = re.search(r"bench-[a-z]+-[0-9]+", json.dumps(body))
        if match is None:
            return web.json_response({"error": "unexpected request"}, status=400)
        key = match.group()
        self.requests.append({"key": key, "path": request.path})
        anthropic = request.path.endswith("/messages")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        sent = []
        self.emissions[key] = sent

        async def emit(event):
            payload = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            stamp = time.perf_counter()
            await response.write(payload)
            if event["type"] in ("content_block_delta", "response.output_text.delta"):
                sent.append(stamp)

        model = body["model"]
        usage = {"input_tokens": 20, "output_tokens": self.args.events * 20}
        message = {
            "id": "msg_bench",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_seq": None,
            "usage": {"input_tokens": 20, "output_tokens": 0},
        }
        result = {
            "id": "resp_bench",
            "object": "response",
            "created_at": 1,
            "status": "in_progress",
            "model": model,
            "output": [],
        }
        if anthropic:
            await emit({"type": "message_start", "message": message})
            await emit({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        else:
            await emit({"type": "response.created", "sequence_number": 0, "response": result})
        for index in range(self.args.events):
            await asyncio.sleep(self.args.gap_ms / 1000)
            if anthropic:
                event = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": TEXT}}
            else:
                event = {
                    "type": "response.output_text.delta",
                    "sequence_number": index + 1,
                    "item_id": "msg_bench",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": TEXT,
                }
            await emit(event)
        if anthropic:
            await emit({"type": "content_block_stop", "index": 0})
            await emit(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_seq": None},
                    "usage": {"output_tokens": usage["output_tokens"]},
                }
            )
            await emit({"type": "message_stop"})
        else:
            result.update(
                status="completed",
                output=[
                    {
                        "id": "msg_bench",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": TEXT * self.args.events, "annotations": []}],
                    }
                ],
                usage={
                    **usage,
                    "total_tokens": sum(usage.values()),
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            )
            await emit({"type": "response.completed", "sequence_number": self.args.events + 1, "response": result})
        await response.write_eof()
        return response


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        raise RuntimeError("empty sample")

    def percentile(q):
        position = (len(ordered) - 1) * q
        low = math.floor(position)
        high = math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    return {"median": percentile(0.5), "p90": percentile(0.9)}


async def drive(session, base, path, kind, key, args, fake):
    model = "claude-sonnet-4-5" if kind == "anthropic" else "gpt-5.4"
    body: dict[str, Any] = {"model": model, "stream": True}
    if kind == "anthropic":
        body.update(max_tokens=8192, messages=[{"role": "user", "content": key}])
    else:
        body.update(input=[{"role": "user", "content": [{"type": "input_text", "text": key}]}], store=False)
    started = time.perf_counter()
    first_byte = None
    arrivals = []
    deltas = []
    types = []
    buffer = b""
    headers = {"Content-Type": "application/json"}
    async with session.post(
        base + path, data=json.dumps(body), headers=headers, skip_auto_headers={"Content-Type"}
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"{kind} {path}: HTTP {response.status}: {(await response.text())[:1000]}")
        async for chunk in response.content.iter_any():
            now = time.perf_counter()
            if first_byte is None:
                first_byte = now
            buffer += chunk.replace(b"\r\n", b"\n")
            while b"\n\n" in buffer:
                block, buffer = buffer.split(b"\n\n", 1)
                data = b"\n".join(line[5:].strip() for line in block.splitlines() if line.startswith(b"data:"))
                if not data or data == b"[DONE]":
                    continue
                event = json.loads(data)
                event_type = event.get("type", "")
                types.append(event_type)
                arrivals.append(now)
                if event_type in ("content_block_delta", "response.output_text.delta"):
                    deltas.append(now)
        ended = time.perf_counter()
    terminal = "message_stop" if kind == "anthropic" else "response.completed"
    if len(deltas) != args.events or terminal not in types or len(fake.emissions.get(key, [])) != args.events:
        raise RuntimeError(f"incomplete {kind}: {len(deltas)} deltas; terminal={terminal in types}")
    assert first_byte is not None
    delivery = [(arrived - sent) * 1000 for arrived, sent in zip(deltas, fake.emissions[key], strict=True)]
    return {
        "ttfb_ms": (first_byte - started) * 1000,
        "first_event_ms": (arrivals[0] - started) * 1000,
        "last_event_ms": (arrivals[-1] - started) * 1000,
        "total_ms": (ended - started) * 1000,
        "event_delivery_ms": distribution(delivery)["median"],
        "delivery_ms": delivery,
        "events": len(arrivals),
        "delta_events": len(deltas),
    }


def environment(data, upstream):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_LB_", "OTEL_", "PROMETHEUS_"))
        and key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")
    }
    values = {
        "data_dir": str(data),
        "database_url": f"sqlite+aiosqlite:///{data / 'store.db'}",
        "encryption_key_file": str(data / "encryption.key"),
        "dashboard_bootstrap_token": "bench-bootstrap",
        "upstream_base_url": upstream,
        "anthropic_upstream_base_url": upstream,
        "auth_base_url": upstream,
        "anthropic_auth_base_url": upstream,
        "glm_anthropic_upstream_base_url": upstream,
        "kimi_anthropic_upstream_base_url": upstream,
        "proxy_unauthenticated_client_cidrs": "127.0.0.1/32",
        "identity_enabled": "false",
        "upstream_stream_transport": "http",
        "http_responses_session_bridge_enabled": "false",
    }
    for name in (
        "account_pulse",
        "usage_refresh",
        "model_registry",
        "auth_guardian",
        "reset_credit_auto_redeem",
        "reset_credit_expiry_redeem",
        "accounts_cache_warmer",
        "quota_planner_scheduler",
        "metrics",
        "otel",
        "leader_election",
    ):
        values[f"{name}_enabled"] = "false"
    env.update({f"AGENT_LB_{key.upper()}": value for key, value in values.items()})
    env["PROXY_BENCH_UPSTREAM_PORT"] = upstream.rsplit(":", 1)[1]
    env["NO_PROXY"] = "*"
    env["HOME"] = str(data)
    return env


def cpu_seconds(pid):
    value = subprocess.check_output(["ps", "-p", str(pid), "-o", "time="], text=True).strip()
    parts = [float(part) for part in value.split(":")]
    return sum(part * 60**index for index, part in enumerate(reversed(parts)))


async def benchmark(args, data, stack, async_stack):
    http_parser.SINGLETON_HEADERS = http_parser.SINGLETON_HEADERS - {"content-type"}
    web_protocol.HttpRequestParser = http_parser.HttpRequestParserPy
    fake = Fake(args)
    upstream = Upstream(fake.handle)
    runner = web.ServerRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    upstream_url = f"http://127.0.0.1:{cast(asyncio.Server, site._server).sockets[0].getsockname()[1]}"
    child = None
    try:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        if port == 2455:
            raise RuntimeError("live port refused")
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(port)]
        if args.profile:
            args.profile = args.profile.resolve()
            args.profile.parent.mkdir(parents=True, exist_ok=True)
            command += ["--profile", str(args.profile)]
        log = stack.enter_context((data / "server.log").open("w+"))
        child = subprocess.Popen(command, cwd=data, env=environment(data, upstream_url), stdout=log, stderr=log)
        process = child.pid
        proxy_url = f"http://127.0.0.1:{port}"
        session = await async_stack.enter_async_context(
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120), trust_env=False)
        )
        deadline = time.monotonic() + 240
        while True:
            if child.poll() is not None or time.monotonic() > deadline:
                log.seek(0)
                raise RuntimeError(f"startup rc={child.poll()}: " + log.read()[-5000:])
            try:
                async with session.get(proxy_url + "/health/ready", timeout=aiohttp.ClientTimeout(total=2)) as response:
                    ready = response.status == 200
                if ready:
                    break
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.1)
        if args.profile:
            child.send_signal(signal.SIGUSR1)
        summary = {
            "runs": args.runs,
            "concurrency": args.concurrency,
            "delta_events_per_request": args.events,
            "gap_ms": args.gap_ms,
            "profile": str(args.profile) if args.profile else None,
            "method": "Delivery=delta arrival minus pre-write; added=paired via-direct. CPU=ps (10ms), startup excluded. Profile=CPU, shares overlap. TTFB=body; cold first; archive off; duplicate Content-Type accepted.",
            "paths": {},
        }
        seq = 0
        for kind, path, direct_path in (
            ("anthropic", "/v1/messages", "/v1/messages"),
            ("codex", "/v1/responses", "/codex/responses"),
        ):
            batches: dict[str, Any] = {}
            for mode, base, endpoint in (
                ("direct", upstream_url, direct_path),
                ("via_lb", proxy_url, path),
            ):
                semaphore = asyncio.Semaphore(args.concurrency)
                keys = []
                for _ in range(args.runs):
                    seq += 1
                    keys.append(f"bench-request-{seq}")

                async def one(key):
                    async with semaphore:
                        return await drive(session, base, endpoint, kind, key, args, fake)

                connections = upstream.tcp
                cpu = cpu_seconds(process)
                samples = await asyncio.gather(*(one(key) for key in keys))
                used_cpu = cpu_seconds(process) - cpu
                tcp = upstream.tcp - connections
                metrics: dict[str, Any] = {name: distribution([sample[name] for sample in samples]) for name in METRICS}
                metrics["all_delivery_ms"] = distribution(
                    [value for sample in samples for value in sample["delivery_ms"]]
                )
                metrics.update(
                    connections=tcp,
                    tcp_per_request=tcp / args.runs,
                    cpu_seconds=used_cpu,
                    cpu_seconds_per_request=used_cpu / args.runs,
                    cpu_seconds_per_event=used_cpu / sum(sample["events"] for sample in samples),
                    cpu_seconds_per_delta=used_cpu / (args.runs * args.events),
                    samples=samples,
                )
                batches[mode] = metrics
            batches["proxy_added"] = {
                name: distribution(
                    [
                        via[name] - direct[name]
                        for via, direct in zip(batches["via_lb"]["samples"], batches["direct"]["samples"], strict=True)
                    ]
                )
                for name in METRICS
            }
            summary["paths"][kind] = batches
        summary["upstream_requests"] = fake.requests
        child.send_signal(signal.SIGTERM)
        await asyncio.to_thread(child.wait, 30)
        if child.returncode not in (0, -signal.SIGTERM):
            raise RuntimeError(f"server exited {child.returncode}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2) + "\n")
        print("path route: TTFB, first SSE, last SSE, total, event delivery (ms median/p90)")
        for kind, batches in summary["paths"].items():
            for mode in ("direct", "via_lb", "proxy_added"):
                values = "  ".join(
                    f"{batches[mode][name]['median']:7.2f}/{batches[mode][name]['p90']:6.2f}" for name in METRICS
                )
                print(f"{kind:10} {mode:11} {values}")
            via = batches["via_lb"]
            print(
                f"  TCP/request direct={batches['direct']['tcp_per_request']:.3f} via={via['tcp_per_request']:.3f}; server CPU/request={via['cpu_seconds_per_request']:.6f}s CPU/event={via['cpu_seconds_per_event']:.9f}s"
            )
        print(f"JSON: {args.out.resolve()}")
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                await asyncio.to_thread(child.wait, 15)
            except subprocess.TimeoutExpired:
                child.kill()
                await asyncio.to_thread(child.wait)
        await runner.cleanup()


async def run(args):
    with tempfile.TemporaryDirectory(prefix="proxy-bench-") as temporary, ExitStack() as stack:
        async with AsyncExitStack() as async_stack:
            await benchmark(args, Path(temporary), stack, async_stack)


if __name__ == "__main__":
    options = arguments()
    if options.serve:
        serve(options)
    else:
        asyncio.run(run(options))
