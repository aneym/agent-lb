from __future__ import annotations

import re
import sqlite3
import struct
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "agent-lb-gemini-meter/1"
BUCKET = timedelta(minutes=5)
KEEP = timedelta(days=8)
SETTLE = timedelta(minutes=5)
SPANS = {"5h": timedelta(hours=5), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
LOG_READ_LIMIT = 8 * 1024 * 1024
FIELD_RECORD = 1
FIELD_MODEL = 19
FIELD_USAGE = 4
FIELD_IN = 2
FIELD_CACHED = 5
FIELD_OUT = 3
FIELD_THINK = 9
FIELD_TEXT = 10
UNKNOWN_MODEL = "unknown"
UNATTRIBUTED = "unattributed"
COUNTERS = ("requests", "tokensIn", "tokensCached", "tokensOut", "quota", "lowQuota", "capacity")
VALID_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{1,80}")
QUOTA_LINE = re.compile(r"RESOURCE_EXHAUSTED|QUOTA_EXHAUSTED|\(code 429\)|exhausted your capacity")
CAPACITY_LINE = re.compile(r"\(code 503\)|No capacity available")
LOW_QUOTA_LINE = re.compile(r"is low on quota")
LINE_MODEL = re.compile(r"for model ([A-Za-z0-9._-]+)|model ([A-Za-z0-9._-]+) is low on quota")
WAL_MAGIC = (0x377F0682, 0x377F0683)
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def empty_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "stores": {}, "logs": {}, "buckets": {}, "layoutMisses": 0, "gaps": 0}


def _bucket_key(at: datetime) -> str:
    at = at.astimezone(timezone.utc)
    return (at - timedelta(minutes=at.minute % 5, seconds=at.second, microseconds=at.microsecond)).strftime(STAMP)


def _bucket_start(key: str) -> datetime:
    return datetime.strptime(key, STAMP).replace(tzinfo=timezone.utc)


def _add(state: dict[str, Any], key: str, model: str, **counts: int) -> None:
    row = state["buckets"].setdefault(key, {}).setdefault(model, dict.fromkeys(COUNTERS, 0))
    for name, value in counts.items():
        row[name] = row.get(name, 0) + value


def _varint(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def _fields(data: bytes) -> dict[int, Any]:
    at, out = 0, {}
    while at < len(data):
        key, at = _varint(data, at)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, at = _varint(data, at)
        elif wire == 1:
            value, at = data[at : at + 8], at + 8
        elif wire == 5:
            value, at = data[at : at + 4], at + 4
        elif wire == 2:
            size, at = _varint(data, at)
            value, at = data[at : at + size], at + size
        else:
            raise ValueError("wire type")
        if at > len(data):
            raise ValueError("truncated")
        out.setdefault(number, value)
    return out


def read_row(data: bytes) -> tuple[str, int, int, int] | None:
    try:
        record = _fields(_fields(data)[FIELD_RECORD])
        usage = _fields(record[FIELD_USAGE])
        model = record[FIELD_MODEL].decode("utf-8")
    except (KeyError, ValueError, IndexError, UnicodeDecodeError, AttributeError, TypeError):
        return None
    numbers = {k: usage.get(k, 0) for k in (FIELD_IN, FIELD_CACHED, FIELD_OUT, FIELD_THINK, FIELD_TEXT)}
    if not all(isinstance(v, int) for v in numbers.values()) or not VALID_MODEL.fullmatch(model):
        return None
    if numbers[FIELD_THINK] + numbers[FIELD_TEXT] != numbers[FIELD_OUT]:
        return None
    return model, numbers[FIELD_IN], numbers[FIELD_CACHED], numbers[FIELD_OUT]


def _checksum(data: bytes, big: bool, s0: int, s1: int) -> tuple[int, int]:
    words = struct.unpack(f"{'>' if big else '<'}{len(data) // 4}I", data)
    for i in range(0, len(words), 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def merge_wal(db: bytes, wal: bytes) -> bytes:
    if len(wal) < 32:
        return db
    magic, _, page_size, _, salt1, salt2, c0, c1 = struct.unpack(">8I", wal[:32])
    if magic not in WAL_MAGIC or page_size < 512 or page_size & (page_size - 1):
        return db
    big = bool(magic & 1)
    if _checksum(wal[:24], big, 0, 0) != (c0, c1):
        return db
    s0, s1, at, size = c0, c1, 32, 0
    pending: dict[int, bytes] = {}
    committed: dict[int, bytes] = {}
    while at + 24 + page_size <= len(wal):
        page_no, commit, f1, f2, k0, k1 = struct.unpack(">6I", wal[at : at + 24])
        page = wal[at + 24 : at + 24 + page_size]
        if (f1, f2) != (salt1, salt2):
            break
        s0, s1 = _checksum(page, big, *_checksum(wal[at : at + 8], big, s0, s1))
        if (s0, s1) != (k0, k1):
            break
        pending[page_no] = page
        if commit:
            committed.update(pending)
            pending, size = {}, commit
        at += 24 + page_size
    if not size:
        return db
    image = bytearray(db[: size * page_size].ljust(size * page_size, b"\0"))
    for page_no, page in committed.items():
        if page_no <= size:
            image[(page_no - 1) * page_size : page_no * page_size] = page
    return bytes(image)


def _snapshot(path: Path) -> sqlite3.Connection | None:
    wal_path = Path(f"{path}-wal")
    wal = wal_path.read_bytes() if wal_path.exists() else b""
    db = path.read_bytes()
    if wal_path.exists():
        with wal_path.open("rb") as handle:
            head = handle.read(32)
        if head != wal[:32] or wal_path.stat().st_size < len(wal):
            return None
    image = bytearray(merge_wal(db, wal))
    if len(image) < 100:
        return None
    image[18:20] = b"\x01\x01"
    conn = sqlite3.connect(":memory:")
    conn.deserialize(bytes(image))
    return conn


def _last_write(path: Path) -> datetime:
    wal = Path(f"{path}-wal")
    stamp = max(path.stat().st_mtime, wal.stat().st_mtime if wal.exists() else 0.0)
    return datetime.fromtimestamp(stamp, timezone.utc)


def _scan_store(path: Path, state: dict[str, Any], now: datetime) -> bool:
    name = path.name
    cursor = state["stores"].get(name)
    try:
        written = _last_write(path)
        conn = _snapshot(path)
        if conn is None:
            return False
        try:
            top = conn.execute("select max(idx) from gen_metadata").fetchone()[0]
            if top is None:
                state["stores"].setdefault(name, -1)
                return True
            if cursor is not None and top < cursor:
                state["stores"][name] = top
                state["gaps"] += 1
                return True
            limit = top - 1 if now - written < SETTLE else top
            rows = conn.execute(
                "select idx, data from gen_metadata where idx > ? and idx <= ? order by idx",
                (-1 if cursor is None else cursor, limit),
            ).fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return False
    key = _bucket_key(written if cursor is None else now)
    for idx, data in rows:
        parsed = read_row(bytes(data)) if isinstance(data, (bytes, bytearray, memoryview)) else None
        if parsed is None:
            state["layoutMisses"] += 1
            _add(state, key, UNKNOWN_MODEL, requests=1)
        else:
            model, tokens_in, cached, tokens_out = parsed
            _add(state, key, model, requests=1, tokensIn=tokens_in, tokensCached=cached, tokensOut=tokens_out)
        state["stores"][name] = idx
    return True


def _line_class(line: str) -> str | None:
    if QUOTA_LINE.search(line):
        return "quota"
    if CAPACITY_LINE.search(line):
        return "capacity"
    if LOW_QUOTA_LINE.search(line):
        return "lowQuota"
    return None


def _scan_log(path: Path, state: dict[str, Any], now: datetime) -> None:
    offset = state["logs"].get(path.name)
    try:
        stat = path.stat()
        start = 0 if offset is None or stat.st_size < offset else offset
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(min(stat.st_size - start, LOG_READ_LIMIT))
    except OSError:
        return
    end = chunk.rfind(b"\n") + 1
    key = _bucket_key(datetime.fromtimestamp(stat.st_mtime, timezone.utc) if offset is None else now)
    for raw in chunk[:end].splitlines():
        line = raw.decode("utf-8", "replace")
        kind = _line_class(line)
        if kind:
            found = LINE_MODEL.search(line)
            model = (found.group(1) or found.group(2)) if found else UNATTRIBUTED
            _add(state, key, model, **{kind: 1})
    state["logs"][path.name] = start + end


def scan(agy_dir: Path, state: dict[str, Any] | None, now: datetime) -> tuple[dict[str, Any], bool]:
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        state = empty_state()
    stores = sorted((agy_dir / "conversations").glob("*.db"))
    logs = sorted((agy_dir / "log").glob("*.log"))
    ok = any([_scan_store(path, state, now) for path in stores])
    for path in logs:
        _scan_log(path, state, now)
    store_names = {p.name for p in stores}
    log_names = {p.name for p in logs}
    state["stores"] = {k: v for k, v in state["stores"].items() if k in store_names}
    state["logs"] = {k: v for k, v in state["logs"].items() if k in log_names}
    cutoff = now - KEEP
    state["buckets"] = {k: v for k, v in state["buckets"].items() if _bucket_start(k) >= cutoff}
    state["scannedAt"] = now.astimezone(timezone.utc).strftime(STAMP)
    state["ok"] = ok
    return state, ok


def _totals(rows: list[dict[str, int]]) -> dict[str, int]:
    return {name: sum(r.get(name, 0) for r in rows) for name in ("requests", "tokensIn", "tokensCached", "tokensOut")}


def _counts_errors(model: str, headline: Callable[[str], bool]) -> bool:
    return headline(model) or model == UNATTRIBUTED


def view(state: dict[str, Any], now: datetime, headline: Callable[[str], bool]) -> dict[str, Any]:
    buckets: dict[str, dict[str, dict[str, int]]] = state.get("buckets") or {}
    windows: dict[str, Any] = {}
    for name, span in SPANS.items():
        cutoff = now - span - BUCKET
        picked = [(m, r) for k, ms in buckets.items() if _bucket_start(k) > cutoff for m, r in ms.items()]
        errors = [r for m, r in picked if _counts_errors(m, headline)]
        windows[name] = {
            **_totals([r for m, r in picked if headline(m)]),
            "quotaErrors": sum(r.get("quota", 0) for r in errors),
            "lowQuota": sum(r.get("lowQuota", 0) for r in errors),
            "capacityErrors": sum(r.get("capacity", 0) for r in errors),
        }
    cutoff = now - SPANS["7d"] - BUCKET
    per_model: dict[str, list[dict[str, int]]] = {}
    for key, models in buckets.items():
        if _bucket_start(key) > cutoff:
            for model, row in models.items():
                per_model.setdefault(model, []).append(row)
    by_model = [{"model": m, **_totals(rows)} for m, rows in per_model.items()]
    requested = [k for k, ms in buckets.items() if any(headline(m) and r.get("requests") for m, r in ms.items())]
    refused = [
        k for k, ms in buckets.items() if any(_counts_errors(m, headline) and r.get("quota") for m, r in ms.items())
    ]
    return {
        "source": "agy_store",
        "scannedAt": state.get("scannedAt"),
        "ok": bool(state.get("ok")),
        "layoutMisses": int(state.get("layoutMisses") or 0),
        "gaps": int(state.get("gaps") or 0),
        "lastRequestAt": max(requested) if requested else None,
        "lastQuotaErrorAt": max(refused) if refused else None,
        "windows": windows,
        "byModel7d": sorted(
            (r for r in by_model if r["requests"]), key=lambda r: (-int(r["requests"]), str(r["model"]))
        ),
    }
