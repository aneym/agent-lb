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
        "orphan-before-valid": [
            {"role": "user", "content": "Read the file."},
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [{**result, "tool_use_id": "lost"}, result]},
        ],
        "rich-error-orphan": [
            {
                "role": "user",
                "content": [
                    {
                        **result,
                        "tool_use_id": "lost",
                        "is_error": True,
                        "content": [
                            {
                                "type": "document",
                                "source": {"type": "text", "media_type": "text/plain", "data": "DOC_SENTINEL"},
                            },
                            {"type": "tool_reference", "tool_name": "TOOL_SENTINEL"},
                            {"type": "search_result", "source": "local", "title": "SEARCH_SENTINEL", "content": []},
                        ],
                    }
                ],
            }
        ],
        "duplicate-call-id": [
            {"role": "user", "content": "Read twice."},
            {"role": "assistant", "content": [call, call]},
            {"role": "user", "content": [result, result]},
        ],
        "empty-result-content": [
            {"role": "user", "content": "Read twice."},
            {"role": "assistant", "content": [call, {**call, "id": "second"}]},
            {
                "role": "user",
                "content": [{**result, "content": ""}, {**result, "tool_use_id": "second", "content": []}],
            },
        ],
        "missing-history-result": [
            {"role": "user", "content": "Read the file."},
            {"role": "assistant", "content": [call]},
            {"role": "assistant", "content": "The result was lost."},
        ],
        "empty-text-result": [
            {"role": "user", "content": "Read the file."},
            {"role": "assistant", "content": [call]},
            {"role": "user", "content": [{**result, "content": [{"type": "text", "text": ""}]}]},
        ],
    }
    for name, messages in cases.items():
        request_id = f"kimi-history-{name}-{uuid4()}"
        payload = {
            "model": "k3.1-pro",
            "max_tokens": 4096 if name == "rich-error-orphan" else 128,
            "stream": False,
            "tools": [{"name": "read", "input_schema": {"type": "object", "properties": {}}}],
            "messages": [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Reply with the three SENTINEL values from the previous result and say whether it was an error. No tools."
                        if name == "rich-error-orphan"
                        else "Reply with OK; no tools."
                    ),
                },
            ],
        }
        request = urllib.request.Request(
            base + "/v1/messages", data=json.dumps(payload).encode(), headers={**headers, "x-request-id": request_id}
        )
        try:
            with urllib.request.urlopen(request, timeout=180 if name == "rich-error-orphan" else 60) as response:
                body = json.load(response)
                if response.status != 200 or body.get("type") != "message":
                    raise SystemExit(f"FAIL {name} request={request_id} status={response.status}")
                if name == "rich-error-orphan":
                    answer = " ".join(part.get("text", "") for part in body.get("content", []))
                    if (
                        not all(value in answer for value in ("DOC_SENTINEL", "TOOL_SENTINEL", "SEARCH_SENTINEL"))
                        or "error" not in answer.lower()
                    ):
                        raise SystemExit(f"FAIL {name} request={request_id} result content or error marker missing")
                print(f"PASS {name} request={response.headers.get('x-request-id', request_id)}", flush=True)
        except urllib.error.HTTPError as exc:
            error = json.load(exc).get("error", {})
            matched = "tool_call_id" in str(error.get("message", ""))
            raise SystemExit(
                f"FAIL {name} request={request_id} status={exc.code} tool_call_id_error={matched}"
            ) from None


if __name__ == "__main__":
    main()
