from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from route_storage import atomic_write  # pyrefly: ignore [missing-import]


class HttpResult:
    def __init__(self, *, ok: bool, status: int | None, body: Any, error: str | None) -> None:
        self.ok = ok
        self.status = status
        self.body = body
        self.error = error

    from_cache = False

    @property
    def json_ok(self) -> bool:
        return self.ok and self.status == 200 and self.body is not None


def _fixture_file(path: str) -> Path | None:
    directory = (os.environ.get("ROUTE_FIXTURE_DIR") or "").strip()
    if not directory:
        return None
    slug = path.split("?", 1)[0].strip("/").replace("/", "_") or "root"
    return Path(directory).expanduser() / f"{slug}.json"


def _fixture_result(path: str) -> HttpResult:
    fixture = _fixture_file(path)
    assert fixture is not None
    try:
        body = json.loads(fixture.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HttpResult(ok=False, status=404, body=None, error="HTTP 404 (fixture missing)")
    except (OSError, json.JSONDecodeError) as error:
        return HttpResult(ok=False, status=None, body=None, error=f"fixture unreadable: {error}")
    if isinstance(body, dict) and "__status" in body:
        status = body["__status"]
        return HttpResult(ok=status == 200, status=status, body=body.get("__body"), error=f"HTTP {status}")
    return HttpResult(ok=True, status=200, body=body, error=None)


def _live_request(
    method: str, path: str, *, payload: dict[str, Any] | None, timeout: float, fixture: bool = True, url: str
) -> HttpResult:
    if fixture and _fixture_file(path) is not None:
        return _fixture_result(path)
    result = _live_attempt(method, path, payload=payload, timeout=timeout, url=url)
    for delay in (0.2, 0.5) if method == "GET" else ():
        if not (result.error or "").startswith("truncated"):
            break
        time.sleep(delay)
        result = _live_attempt(method, path, payload=payload, timeout=timeout, url=url)
    return result


def _live_attempt(method: str, path: str, *, payload: dict[str, Any] | None, timeout: float, url: str) -> HttpResult:
    import http.client
    import urllib.error
    import urllib.request

    headers = {"accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
        headers["anthropic-version"] = "2023-06-01"
    request = urllib.request.Request(f"{url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        body = _decode(raw)
        return HttpResult(ok=False, status=error.code, body=body, error=f"HTTP {error.code}")
    except http.client.IncompleteRead as error:
        return HttpResult(ok=False, status=None, body=None, error=f"truncated: {error!r}")
    except http.client.HTTPException as error:
        return HttpResult(ok=False, status=None, body=None, error=f"truncated: {type(error).__name__}: {error}")
    except TimeoutError:
        return HttpResult(ok=False, status=None, body=None, error="timeout")
    except (OSError, urllib.error.URLError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            return HttpResult(ok=False, status=None, body=None, error="timeout")
        return HttpResult(ok=False, status=None, body=None, error=f"{type(error).__name__}: {reason}")
    return HttpResult(ok=True, status=status, body=_decode(raw), error=None)


def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


TTLS = {
    "/api/models": 60,
    "/v1/models": 60,
    "/api/models/anthropic": 60,
    "/api/pools": 5,
    "/api/accounts": 5,
    "/api/request-logs": 5,
}
MODEL_PATHS = {"/api/models", "/v1/models", "/api/models/anthropic"}
MODEL_CACHE_HITS: set[str] = set()
FORCE_MODELS = False


def _prune(cache_dir: Path) -> None:
    marker = cache_dir / ".route-get-pruned"
    now = time.time()
    try:
        if now - marker.stat().st_mtime < 60:
            return
    except FileNotFoundError:
        pass
    marker.touch()
    for path in cache_dir.glob("route-get-*.json"):
        try:
            if path.is_file() and path.stat().st_mtime < now - 600:
                path.unlink()
        except OSError:
            pass


def request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None,
    timeout: float,
    url: str,
    cache_dir: Path,
    fixture: bool = True,
) -> HttpResult:
    endpoint = path.split("?", 1)[0]
    try:
        scale = float(os.environ.get("ROUTE_CACHE_TTL_SCALE", "1"))
    except ValueError:
        scale = 1.0
    ttl = TTLS.get(endpoint, 0) * scale
    enabled = method == "GET" and _fixture_file(path) is None and math.isfinite(ttl) and ttl > 0
    cache = None
    if enabled:
        key = hashlib.sha256((url + path).encode()).hexdigest()
        cache = cache_dir / f"route-get-{key}.json"
        try:
            if FORCE_MODELS and endpoint in MODEL_PATHS:
                raise ValueError("live model refresh")
            saved = json.loads(cache.read_text(encoding="utf-8"))
            age = time.time() - saved["saved_at"]
            if saved["url"] == url + path and 0 <= age < ttl and saved["body"] is not None:
                result = HttpResult(ok=True, status=saved["status"], body=saved["body"], error=None)
                result.from_cache = True
                if endpoint in MODEL_PATHS:
                    MODEL_CACHE_HITS.add(endpoint)
                return result
        except (OSError, ValueError, KeyError, TypeError):
            pass
    result = _live_request(method, path, payload=payload, timeout=timeout, fixture=fixture, url=url)
    if cache is not None and result.ok and result.body is not None:
        try:
            atomic_write(
                cache,
                json.dumps({"url": url + path, "saved_at": time.time(), "status": result.status, "body": result.body}),
            )
            _prune(cache_dir)
        except OSError:
            pass
    return result
