from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

CHUNK = 1 << 20
MAX_USED_FRACTION = 0.70


class ArchiveError(RuntimeError):
    pass


def off_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    if os.stat(resolved).st_dev == os.stat("/").st_dev:
        raise ArchiveError(f"{resolved} is on the root volume; pick an archive volume with room")
    return resolved


def room(path: Path, needed: int) -> dict:
    usage = shutil.disk_usage(path)
    after = (usage.used + needed) / usage.total
    return {
        "path": str(path),
        "free_bytes": usage.free,
        "used_fraction_after": round(after, 4),
        "fits": needed < usage.free and after < MAX_USED_FRACTION,
    }


def copy_out(cursor, sql: str, params: dict, root: Path, name: str) -> dict:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = hashlib.sha256()
    raw_bytes = 0
    with gzip.open(target, "wb", compresslevel=6) as handle:
        with cursor.copy(f"COPY ({cursor.mogrify(sql, params)}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
            for chunk in copy:
                data = bytes(chunk)
                raw.update(data)
                raw_bytes += len(data)
                handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "file": name,
        "sha256": file_sha256(target),
        "raw_sha256": raw.hexdigest(),
        "raw_bytes": raw_bytes,
        "bytes": target.stat().st_size,
    }


def raw_export_sha256(cursor, sql: str, params: dict) -> str:
    raw = hashlib.sha256()
    with cursor.copy(f"COPY ({cursor.mogrify(sql, params)}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
        for chunk in copy:
            raw.update(bytes(chunk))
    return raw.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def read_raw(path: Path) -> Iterator[bytes]:
    with gzip.open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            yield block


def verify_file(root: Path, entry: dict) -> None:
    path = root / entry["file"]
    if file_sha256(path) != entry["sha256"]:
        raise ArchiveError(f"{path} does not match its manifest sha256")
    raw = hashlib.sha256()
    for block in read_raw(path):
        raw.update(block)
    if raw.hexdigest() != entry["raw_sha256"]:
        raise ArchiveError(f"{path} decompresses to bytes that do not match the manifest")


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_manifest(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise ArchiveError(f"{path} is missing")
    return json.loads(path.read_text())


def sha256_sums(root: Path, entries: list[dict]) -> None:
    lines = [f"{entry['sha256']}  {entry['file']}" for entry in entries]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
