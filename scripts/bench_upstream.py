from __future__ import annotations

import asyncio
import gzip
import json
import re
import time

from aiohttp import web

TEXT = "We measure each event carefully as the local server streams a steady sequence of small text updates. "


class Upstream(web.Server):
    def __init__(self, handler):
        super().__init__(handler)
        self.tcp = 0

    def connection_made(self, handler, transport):
        self.tcp += 1
        super().connection_made(handler, transport)


class Fake:
    def __init__(self, args):
        self.opts = args
        self.sent_at = {}
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
            return web.json_response({"error": "bad request"}, status=400)
        key = match.group()
        self.requests.append({"key": key, "path": request.path})
        anthropic = request.path.endswith("/messages")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        sent = []
        self.sent_at[key] = sent

        request_index = int(key.rsplit("-", 1)[1])
        crlf = anthropic and request_index % 2 == 0
        split = anthropic and request_index % 3 == 0

        async def emit(event):
            payload = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            if crlf:
                payload = payload.replace(b"\n", b"\r\n")
            stamp = time.perf_counter()
            if split:
                boundary = payload.index(b"\r\n") + 1 if crlf else len(payload) // 2
                await response.write(payload[:boundary])
                await asyncio.sleep(0.001)
                await response.write(payload[boundary:])
            else:
                await response.write(payload)
            if event["type"] in ("content_block_delta", "response.output_text.delta"):
                sent.append(stamp)

        model = body["model"]
        usage = {"input_tokens": 100 + request_index * 7, "output_tokens": self.opts.events * 20 + request_index * 11}
        cached = request_index * 3
        reasoning = request_index * 2
        if anthropic:
            usage = {
                "input_tokens": 20 + request_index,
                "output_tokens": self.opts.events * 20 + request_index,
                "cache_read_input_tokens": request_index * 3,
                "cache_creation_input_tokens": request_index * 2,
            }
        message = {
            "id": "msg_bench",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_seq": None,
            "usage": {**usage, "output_tokens": 0},
        }
        result = {
            "id": key,
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
        for index in range(self.opts.events):
            await asyncio.sleep(self.opts.gap_ms / 1000)
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
            if request_index == self.opts.runs + 1:
                await emit({"type": "error", "error": {"type": "overloaded_error", "message": "bench error"}})
            else:
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
                        "content": [{"type": "output_text", "text": TEXT * self.opts.events, "annotations": []}],
                    }
                ],
                usage={
                    **usage,
                    "total_tokens": sum(usage.values()),
                    "input_tokens_details": {"cached_tokens": cached},
                    "output_tokens_details": {"reasoning_tokens": reasoning},
                },
            )
            await emit({"type": "response.completed", "sequence_number": self.opts.events + 1, "response": result})
        await response.write_eof()
        return response
