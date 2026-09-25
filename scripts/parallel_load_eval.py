#!/usr/bin/env python3
"""Parallel-capacity eval: a staircase of concurrent tiny requests per path.

Fires N requests at once (N = 25, 50, 100, 200 by default) on each path and
records, per step, client latency and time to first byte, errors by type, the
accounts that served the step, and how much of each request's time was spent
outside the LB's own request timer. Prompts are "reply ok" with a small token
cap, so a full staircase costs a few cents.

Each step models one Claude Code session fanning out N subagents: the message
paths share one session id per step, and every request carries its own prompt
("reply ok (agent i)"); --same-prompt sends the identical prompt N times instead.

Paths:
  ccgpt      POST /v1/messages, model luna-latest-low (Claude Code on GPT via the bridge)
  codex      websocket /backend-api/codex/responses, response.create (how Codex CLI calls it)
  anthropic  POST /v1/messages, model claude-haiku-4-5

  python3 scripts/parallel_load_eval.py                       # all paths, 25..200
  python3 scripts/parallel_load_eval.py --paths codex --steps 100,200

The account and LB-timer columns come from the request log; pass the LB's
database URL in AGENT_LB_DATABASE_URL (never on the command line) to get them.
Account ids are reported as short hashes. Receipts land in
~/.agent-lb/evals/parallel-load/receipts/<utc>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
DEFAULT_STEPS = (25, 50, 100, 200)
RECEIPTS = Path.home() / ".agent-lb" / "evals" / "parallel-load" / "receipts"
PROMPT = "reply ok"
USER_AGENT = "parallel-load-eval/1"
CODEX_MODEL = "gpt-6-luna"
MODELS = {"ccgpt": "luna-latest-low", "codex": CODEX_MODEL, "anthropic": "claude-haiku-4-5"}


@dataclass(slots=True)
class Result:
    path: str
    ok: bool = False
    error: str | None = None
    detail: str = ""
    status: int | None = None
    request_ids: list[str] = field(default_factory=list)
    total_s: float = 0.0
    ttfb_s: float | None = None
    first_token_s: float | None = None
    text: str = ""


def _now() -> float:
    return time.monotonic()


def _prompt(index: int, same: bool) -> str:
    return PROMPT if same else f"{PROMPT} (agent {index})"


async def _messages(
    session: aiohttp.ClientSession, base: str, path: str, timeout: float, *, index: int, same: bool, session_id: str
) -> Result:
    res = Result(path=path)
    body = {
        "model": MODELS[path],
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": _prompt(index, same)}],
        # One Claude Code session fanning out N subagents: shared session id.
        "metadata": {"user_id": json.dumps({"session_id": session_id})},
    }
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01", "user-agent": USER_AGENT}
    start = _now()
    try:
        async with asyncio.timeout(timeout):
            async with session.post(f"{base}/v1/messages", json=body, headers=headers) as resp:
                res.status = resp.status
                if rid := resp.headers.get("x-request-id"):
                    res.request_ids.append(rid)
                if resp.status != 200:
                    detail = (await resp.text())[:300]
                    res.error = f"http_{resp.status}:{_error_code(detail)}"
                    res.detail = detail
                    return res
                async for raw in resp.content:
                    if res.ttfb_s is None:
                        res.ttfb_s = _now() - start
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    kind = event.get("type")
                    if kind == "message_start":
                        message_id = str(event.get("message", {}).get("id", ""))
                        # The GPT bridge logs the upstream response id; its message id carries it.
                        if message_id.startswith("msg_") and path == "ccgpt":
                            res.request_ids.append("resp_" + message_id[4:])
                    elif kind == "content_block_delta":
                        if res.first_token_s is None:
                            res.first_token_s = _now() - start
                        res.text += event.get("delta", {}).get("text", "")
                    elif kind == "error":
                        res.error = "stream_" + str(event.get("error", {}).get("type", "error"))
                        return res
                    elif kind == "message_stop":
                        res.ok = True
                if not res.ok:
                    res.error = "stream_incomplete"
    except TimeoutError:
        res.error = "client_timeout"
    except aiohttp.ClientError as exc:
        res.error = type(exc).__name__
    finally:
        res.total_s = _now() - start
    return res


async def _codex(session: aiohttp.ClientSession, base: str, timeout: float, *, index: int, same: bool) -> Result:
    res = Result(path="codex")
    ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
    headers = {
        "user-agent": USER_AGENT,
        "originator": "codex_exec",
        "OpenAI-Beta": "responses_websockets=2026-02-06",
        "session_id": str(uuid.uuid4()),
    }
    request = {
        "type": "response.create",
        "model": CODEX_MODEL,
        "instructions": "Answer briefly.",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": _prompt(index, same)}]}
        ],
        "reasoning": {"effort": "low"},
        "store": False,
        "stream": True,
        "tools": [],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    start = _now()
    try:
        async with asyncio.timeout(timeout):
            async with session.ws_connect(f"{ws_base}/backend-api/codex/responses", headers=headers) as ws:
                await ws.send_str(json.dumps(request))
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        res.error = f"ws_{msg.type.name.lower()}"
                        return res
                    if res.ttfb_s is None:
                        res.ttfb_s = _now() - start
                    event = json.loads(msg.data)
                    kind = event.get("type")
                    if kind == "response.created":
                        if rid := event.get("response", {}).get("id"):
                            res.request_ids.append(str(rid))
                    elif kind == "response.output_text.delta":
                        if res.first_token_s is None:
                            res.first_token_s = _now() - start
                        res.text += event.get("delta", "")
                    elif kind in ("response.failed", "error"):
                        err = event.get("error") or event.get("response", {}).get("error") or {}
                        res.status = event.get("status") or event.get("status_code")
                        res.error = "event_" + str(err.get("code") or err.get("type") or kind)
                        res.detail = str(err.get("message") or "")[:300]
                        return res
                    elif kind == "response.completed":
                        res.ok = True
                        return res
                res.error = "ws_closed_early"
    except TimeoutError:
        res.error = "client_timeout"
    except aiohttp.WSServerHandshakeError as exc:
        res.status = exc.status
        res.error = f"ws_handshake_{exc.status}"
    except aiohttp.ClientError as exc:
        res.error = type(exc).__name__
    finally:
        res.total_s = _now() - start
    return res


def _error_code(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except ValueError:
        return "non_json"
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or "error")
    return "error"


async def _sample_loop_lag(base: str, session: aiohttp.ClientSession, stop: asyncio.Event, out: list[float]) -> None:
    """Poll the LB's event-loop lag gauge (metrics port 9090) while a step runs."""
    url = os.environ.get("AGENT_LB_METRICS_URL", "http://127.0.0.1:9090/metrics")
    while not stop.is_set():
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                for line in (await resp.text()).splitlines():
                    if line.startswith("agent_lb_event_loop_lag_seconds "):
                        out.append(float(line.split()[1]))
        except (aiohttp.ClientError, TimeoutError, ValueError):
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except TimeoutError:
            pass


async def _log_rows(request_ids: list[str]) -> dict[str, dict[str, Any]]:
    dsn = os.environ.get("AGENT_LB_DATABASE_URL", "")
    if not dsn or not request_ids:
        return {}
    import asyncpg  # the LB's own venv has it

    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    for _ in range(10):  # request logs are written asynchronously after the stream ends
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "select request_id, account_id, latency_ms, latency_first_token_ms, status, error_code "
                "from request_logs where request_id = any($1::varchar[])",
                request_ids,
            )
        finally:
            await conn.close()
        found = {r["request_id"]: dict(r) for r in rows}
        if len(found) >= len(set(request_ids)) * 0.95:
            return found
        await asyncio.sleep(1.0)
    return found


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(q * (len(values) - 1))))], 3)


def _acct_label(account_id: str | None) -> str:
    return hashlib.sha256((account_id or "none").encode()).hexdigest()[:8]


async def run_step(base: str, path: str, n: int, timeout: float, same: bool) -> dict[str, Any]:
    connector = aiohttp.TCPConnector(limit=0, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        lag: list[float] = []
        stop = asyncio.Event()
        sampler = asyncio.create_task(_sample_loop_lag(base, session, stop, lag))
        session_id = str(uuid.uuid4())
        if path == "codex":
            coros = [_codex(session, base, timeout, index=i, same=same) for i in range(n)]
        else:
            coros = [
                _messages(session, base, path, timeout, index=i, same=same, session_id=session_id) for i in range(n)
            ]
        wall = _now()
        results = await asyncio.gather(*coros)
        wall = _now() - wall
        stop.set()
        await sampler
    ids = [rid for r in results for rid in r.request_ids]
    logs = await _log_rows(ids)
    ok = [r for r in results if r.ok]
    errors = collections.Counter(r.error for r in results if not r.ok)
    accounts: collections.Counter[str] = collections.Counter()
    outside_lb: list[float] = []
    lb_latency: list[float] = []
    for r in results:
        row = next((logs[i] for i in r.request_ids if i in logs), None)
        if row is None:
            continue
        accounts[_acct_label(row["account_id"])] += 1
        if row["latency_ms"] is not None and r.ok:
            lb_latency.append(row["latency_ms"] / 1000)
            outside_lb.append(max(0.0, r.total_s - row["latency_ms"] / 1000))
    return {
        "path": path,
        "model": MODELS[path],
        "n": n,
        "prompts": "same" if same else "distinct",
        "wall_s": round(wall, 3),
        "ok": len(ok),
        "errors": dict(errors),
        "latency_p50_s": _pct([r.total_s for r in ok], 0.5),
        "latency_p95_s": _pct([r.total_s for r in ok], 0.95),
        "ttfb_p50_s": _pct([r.ttfb_s for r in ok if r.ttfb_s is not None], 0.5),
        "ttfb_p95_s": _pct([r.ttfb_s for r in ok if r.ttfb_s is not None], 0.95),
        "first_token_p50_s": _pct([r.first_token_s for r in ok if r.first_token_s is not None], 0.5),
        "first_token_p95_s": _pct([r.first_token_s for r in ok if r.first_token_s is not None], 0.95),
        "lb_timer_p50_s": _pct(lb_latency, 0.5),
        "lb_timer_p95_s": _pct(lb_latency, 0.95),
        "outside_lb_timer_p50_s": _pct(outside_lb, 0.5),
        "outside_lb_timer_p95_s": _pct(outside_lb, 0.95),
        "log_rows_matched": sum(1 for r in results if any(i in logs for i in r.request_ids)),
        "accounts": dict(accounts.most_common()),
        "loop_lag_max_s": round(max(lag), 3) if lag else None,
        "sample_errors": [asdict(r) | {"text": r.text[:40]} for r in results if not r.ok][:3],
    }


def _print_step(step: dict[str, Any]) -> None:
    print(
        f"{step['path']:<9} {step['prompts']:<8} N={step['n']:<4} ok={step['ok']:<4} "
        f"p50={step['latency_p50_s']}s p95={step['latency_p95_s']}s "
        f"ttfb p50={step['ttfb_p50_s']}s p95={step['ttfb_p95_s']}s "
        f"outside-lb p95={step['outside_lb_timer_p95_s']}s lag_max={step['loop_lag_max_s']}s "
        f"accounts={len(step['accounts'])} errors={step['errors']}",
        flush=True,
    )


async def main_async(args: argparse.Namespace) -> int:
    started = datetime.now(UTC)
    steps: list[dict[str, Any]] = []
    for path in args.paths:
        for n in args.steps:
            step = await run_step(args.base_url.rstrip("/"), path, n, args.timeout, args.same_prompt)
            steps.append(step)
            _print_step(step)
            await asyncio.sleep(args.pause)
    receipt = {
        "eval": "parallel-load",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "label": args.label,
        "steps": steps,
    }
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    out = RECEIPTS / f"{started.strftime('%Y%m%dT%H%M%SZ')}{('-' + args.label) if args.label else ''}.json"
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"receipt {out}")
    return 0 if all(s["ok"] == s["n"] for s in steps) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("AGENT_LB_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--paths", default="ccgpt,codex,anthropic", type=lambda s: [p for p in s.split(",") if p])
    parser.add_argument(
        "--steps", default=",".join(map(str, DEFAULT_STEPS)), type=lambda s: [int(p) for p in s.split(",")]
    )
    parser.add_argument("--timeout", type=float, default=240.0, help="per-request client timeout, seconds")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between steps")
    parser.add_argument(
        "--same-prompt",
        action="store_true",
        help="send the identical prompt N times (best-of-N fan-out) instead of N distinct subagent prompts",
    )
    parser.add_argument("--label", default="", help="suffix for the receipt file name, e.g. baseline")
    args = parser.parse_args()
    unknown = [p for p in args.paths if p not in MODELS]
    if unknown:
        parser.error(f"unknown path(s): {unknown}")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
