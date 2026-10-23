from dataclasses import replace

import pytest

from app.core.identity import RequestIdentity
from app.core.openai.requests import ResponsesRequest
from app.modules.proxy.http_bridge_forwarding import (
    HTTP_BRIDGE_IDENTITY_HEADER,
    HTTP_BRIDGE_IDENTITY_SIGNATURE_HEADER,
    HTTP_BRIDGE_SIGNATURE_HEADER,
    HTTPBridgeForwardContext,
    build_owner_forward_headers,
    parse_forwarded_request,
)


def _context(*, original_request_unanchored: bool = False) -> HTTPBridgeForwardContext:
    return HTTPBridgeForwardContext(
        origin_instance="origin",
        target_instance="target",
        codex_session_affinity=True,
        downstream_turn_state="turn-a",
        identity=RequestIdentity("nikhil", "member", "laptop", "tailnet"),
        original_request_unanchored=original_request_unanchored,
    )


@pytest.mark.parametrize("unanchored", [False, True])
def test_forward_identity_authenticates_with_modern_signatures(unanchored: bool) -> None:
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hello"})
    ctx = _context(original_request_unanchored=unanchored)
    headers = build_owner_forward_headers(headers={}, payload=payload, context=ctx)
    forwarded, error = parse_forwarded_request(headers, payload=payload, current_instance="target")
    assert error is None
    assert forwarded is not None
    assert forwarded.context.identity == ctx.identity


def test_forward_identity_rejects_cross_request_primary_and_identity_substitution() -> None:
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hello"})
    unrelated = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "other"})
    ctx = _context()
    valid = build_owner_forward_headers(headers={}, payload=payload, context=ctx)
    other = build_owner_forward_headers(
        headers={},
        payload=unrelated,
        context=replace(ctx, identity=RequestIdentity("other", "member", "laptop", "tailnet")),
    )
    for name in (HTTP_BRIDGE_SIGNATURE_HEADER, HTTP_BRIDGE_IDENTITY_HEADER, HTTP_BRIDGE_IDENTITY_SIGNATURE_HEADER):
        valid[name] = other[name]
    forwarded, error = parse_forwarded_request(valid, payload=payload, current_instance="target")
    assert forwarded is None
    assert error is not None


def test_forward_identity_rejects_forged_identity_before_tools_signature_fast_path() -> None:
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hello"})
    headers = build_owner_forward_headers(headers={}, payload=payload, context=_context())
    headers[HTTP_BRIDGE_IDENTITY_HEADER] = '["other","member","laptop","tailnet"]'
    forwarded, error = parse_forwarded_request(headers, payload=payload, current_instance="target")
    assert forwarded is None
    assert error is not None
