from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

_HASH_LEN = 12
_MAX_PARTS = 16
_MAX_KEY_LEN = 32


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_value(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()[:_HASH_LEN]


def _hash_optional(container: dict[str, Any], key: str) -> str | None:
    value = container.get(key)
    return _hash_value(value) if value is not None else None


def _checkpoints(count: int) -> set[int]:
    if count <= 0:
        return set()
    points: set[int] = set()
    depth = 1
    while depth < count:
        points.add(depth)
        depth *= 2
    points.add(count)
    return points


def _cumulative_content_hashes(contents: list[Any]) -> list[str]:
    checkpoints = _checkpoints(len(contents))
    if not checkpoints:
        return []
    hasher = sha256(b"[")
    hashes: list[str] = []
    for index, item in enumerate(contents):
        if index > 0:
            hasher.update(b",")
        hasher.update(_canonical_bytes(item))
        if index + 1 in checkpoints:
            snapshot = hasher.copy()
            snapshot.update(b"]")
            hashes.append(snapshot.hexdigest()[:_HASH_LEN])
    return hashes


def _part_hashes(content: object) -> list[str]:
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return []
    return [_hash_value(part) for part in parts[:_MAX_PARTS]]


def _part_keys(content: object) -> list[list[str]]:
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return []
    return [
        sorted(key for key in part if isinstance(key, str) and key.isidentifier() and len(key) <= _MAX_KEY_LEN)
        for part in parts[:_MAX_PARTS]
        if isinstance(part, dict)
    ]


def prefix_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("request")
    req = request if isinstance(request, dict) else {}
    raw_contents = req.get("contents")
    contents = raw_contents if isinstance(raw_contents, list) else []
    return {
        "sys": _hash_optional(req, "systemInstruction"),
        "sys_p": _part_hashes(req.get("systemInstruction")),
        "tools": _hash_optional(req, "tools"),
        "tcfg": _hash_optional(req, "toolConfig"),
        "gcfg": _hash_optional(req, "generationConfig"),
        "labels": _hash_optional(req, "labels"),
        "safety": _hash_optional(req, "safetySettings"),
        "cc": _hash_optional(req, "cachedContent"),
        "sid": _hash_optional(req, "session_id"),
        "model": _hash_optional(payload, "model"),
        "project": _hash_optional(payload, "project"),
        "upid": _hash_optional(payload, "user_prompt_id"),
        "n": len(contents),
        "c": _cumulative_content_hashes(contents),
        "c0_p": _part_hashes(contents[0]) if contents else [],
        "kinds": [_part_keys(item) for item in contents[:2]],
    }
