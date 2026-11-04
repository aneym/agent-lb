from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from uuid import uuid4


def main() -> None:
    base = os.environ.get("KIMI_PROOF_BASE_URL", "http://127.0.0.1:2455")
    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    key = os.environ.get("AGENT_LB_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    call = {"type": "tool_use", "id": "tool_proof", "name": "read", "input": {}}
    result = {"type": "tool_result", "tool_use_id": "tool_proof", "content": "hello"}
    cases = {
        "empty": [{"role": "user", "content": [{**result, "tool_use_id": ""}]}],
        "orphan": [{"role": "user", "content": [{**result, "tool_use_id": "lost"}]}],
        "valid": [
            {"role": "user", "content": "Read the file."},
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [result]},
        ],
        "missing-call-id": [
            {"role": "user", "content": "Read the file."},
            {"role": "assistant", "content": [{**call, "id": ""}]},
            {"role": "user", "content": [{**result, "tool_use_id": ""}]},
        ],
    }
    for name, messages in cases.items():
        request_id = f"kimi-history-{name}-{uuid4()}"
        payload = {
            "model": "k3.1-pro",
            "max_tokens": 128,
            "stream": False,
            "tools": [{"name": "read", "input_schema": {"type": "object", "properties": {}}}],
            "messages": [*messages, {"role": "user", "content": "Reply with OK; no tools."}],
        }
        request = urllib.request.Request(
            base + "/v1/messages", data=json.dumps(payload).encode(), headers={**headers, "x-request-id": request_id}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.load(response)
                if response.status != 200 or body.get("type") != "message":
                    raise SystemExit(f"FAIL {name} request={request_id} status={response.status}")
                print(f"PASS {name} request={response.headers.get('x-request-id', request_id)}", flush=True)
        except urllib.error.HTTPError as exc:
            error = json.load(exc).get("error", {})
            matched = "tool_call_id" in str(error.get("message", ""))
            raise SystemExit(
                f"FAIL {name} request={request_id} status={exc.code} tool_call_id_error={matched}"
            ) from None


if __name__ == "__main__":
    main()
