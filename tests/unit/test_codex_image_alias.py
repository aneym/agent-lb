from __future__ import annotations

import pytest

from app.core.middleware.path_rewrite import BackendApiCodexV1AliasMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generations", "edits"])
@pytest.mark.parametrize("prefix", ["/backend-api/codex/", "/backend-api/codex/v1/"])
async def test_image_alias_preserves_auth_body_query_and_original_scope(operation, prefix):
    path = f"{prefix}images/{operation}"
    headers = [(b"authorization", b"Bearer test-only"), (b"content-type", b"multipart/form-data; boundary=x")]
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"probe=1",
        "headers": headers,
    }
    body = {"type": "http.request", "body": b"original multipart or json payload"}
    observed = {}

    async def receive():
        return body

    async def send(message):
        pass

    async def downstream(s, r, t):
        observed.update(scope=s, body=await r())

    await BackendApiCodexV1AliasMiddleware(downstream)(scope, receive, send)
    assert observed["scope"]["path"] == f"/v1/images/{operation}"
    assert observed["scope"]["raw_path"] == f"/v1/images/{operation}".encode()
    assert observed["scope"]["headers"] is headers
    assert observed["scope"]["query_string"] == b"probe=1"
    assert observed["body"] is body
    assert scope["path"] == path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/v1/images/generations", "/backend-api/codex/alpha/search", "/backend-api/codex/images/other"]
)
async def test_unrelated_routes_are_not_remapped(path):
    scope = {"type": "http", "path": path, "raw_path": path.encode()}
    observed = []

    async def downstream(s, r, t):
        observed.append(s)

    await BackendApiCodexV1AliasMiddleware(downstream)(scope, None, None)
    assert observed == [scope]
    assert observed[0] is scope
