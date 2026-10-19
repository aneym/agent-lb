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

        async def emit(event):
            payload = f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
            stamp = time.perf_counter()
            await response.write(payload)
            if event["type"] in ("content_block_delta", "response.output_text.delta"):
                sent.append(stamp)

        model = body["model"]
        request_index = int(key.rsplit("-", 1)[1])
        usage = {"input_tokens": 100 + request_index * 7, "output_tokens": self.opts.events * 20 + request_index * 11}
        cached = request_index * 3
        reasoning = request_index * 2
        message = {
            "id": "msg_bench",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_seq": None,
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": 0,
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": request_index,
            },
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
