from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import mmap
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

MATCHER_PATH = Path(__file__).resolve().parent / "hooks/claim_codes.py"
STATE = Path(".local/state/codex-rollout-scrub")
HISTORY_TABLES = ("thread_items", "thread_realtime_items")
PENDING_SECONDS = 600
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR


def load_matcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_scrub_claim_codes", MATCHER_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"claim-code matcher missing at {MATCHER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATCHER = load_matcher()
CODE = re.compile(MATCHER.CODE.pattern.encode())
MASK = MATCHER.MASK.encode()


def pad(code: bytes) -> bytes:
    return MASK + b"*" * (len(code) - len(MASK))


def mask_text(text: str) -> tuple[str, int]:
    found = MATCHER.CODE.findall(text)
    if not found:
        return text, 0
    return MATCHER.CODE.sub(lambda m: pad(m[0].encode()).decode(), text), len(found)


def codex_homes(home: Path) -> list[Path]:
    named = [Path(os.environ["CODEX_HOME"])] if os.environ.get("CODEX_HOME") else []
    jobs = sorted(home.glob(".agent-rails/jobs/codex-homes/*"))
    found = [*named, home / ".codex", *sorted(home.glob(".codex-*")), *jobs]
    return list(dict.fromkeys(p.resolve() for p in found if (p / "sessions").is_dir()))


def rollouts(codex: Path) -> list[Path]:
    return sorted(
        p.resolve()
        for folder in ("sessions", "archived_sessions")
        if (codex / folder).is_dir()
        for p in (codex / folder).rglob("rollout-*.jsonl")
        if p.is_file() and not p.is_symlink()
    )


def proc_writers() -> set[str]:
    held = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if not target.endswith(".jsonl"):
                continue
            try:
                with open(f"/proc/{pid}/fdinfo/{fd}") as handle:
                    flags = int(handle.read().split("flags:", 1)[1].split()[0], 8)
            except (OSError, IndexError, ValueError):
                flags = os.O_WRONLY
            if flags & WRITE_FLAGS:
                held.add(target)
    return held


def lsof_writers(paths: list[Path]) -> set[str]:
    if not paths:
        return set()
    out = subprocess.run(
        ["lsof", "-nP", "-Fan", "--", *map(str, paths)], capture_output=True, text=True, timeout=60
    ).stdout
    held, mode = set(), ""
    for line in out.splitlines():
        if line.startswith("a"):
            mode = line[1:]
        elif line.startswith("n") and mode in ("w", "u"):
            held.add(line[1:])
    return held


def writers(paths: list[Path]) -> set[str]:
    return proc_writers() if Path("/proc/self/fd").is_dir() else lsof_writers(paths)


def find_edits(mm: mmap.mmap, start: int, limit: int) -> list[tuple[int, bytes]]:
    edits = []
    at = mm.find(b"rac_", start, limit)
    while at != -1:
        match = CODE.match(mm, at, limit)
        if match:
            edits.append((at, pad(match[0])))
            at = match.end()
        else:
            at += 4
        at = mm.find(b"rac_", at, limit) if at < limit else -1
    return edits


def lines_stay_json(mm: mmap.mmap, edits: list[tuple[int, bytes]], limit: int) -> bool:
    lines: dict[int, list[tuple[int, bytes]]] = {}
    for offset, new in edits:
        lines.setdefault(mm.rfind(b"\n", 0, offset) + 1, []).append((offset, new))
    for begin, line_edits in lines.items():
        end = mm.find(b"\n", begin, limit)
        end = limit if end == -1 else end
        raw = bytearray(mm[begin:end])
        try:
            json.loads(bytes(raw))
        except ValueError:
            continue
        for offset, new in line_edits:
            raw[offset - begin : offset - begin + len(new)] = new
        try:
            json.loads(bytes(raw))
        except ValueError:
            return False
    return True


def write_in_place(path: Path, edits: list[tuple[int, bytes]], before: os.stat_result) -> None:
    with open(path, "r+b") as handle:
        for offset, new in edits:
            handle.seek(offset)
            handle.write(new)
        handle.flush()
        os.fsync(handle.fileno())
    now = path.stat()
    if now.st_size == before.st_size:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def write_replacement(path: Path, mm: mmap.mmap, edits: list[tuple[int, bytes]], before: os.stat_result) -> bool:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".scrub")
    try:
        with os.fdopen(fd, "wb") as handle:
            at = 0
            for offset, new in edits:
                handle.write(mm[at:offset])
                handle.write(new)
                at = offset + len(new)
            handle.write(mm[at:])
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, stat.S_IMODE(before.st_mode))
        os.utime(temp, ns=(before.st_atime_ns, before.st_mtime_ns))
        now = path.stat()
        if (now.st_ino, now.st_size, now.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
            return False
        if str(path) in writers([path]):
            return False
        os.replace(temp, path)
        temp = ""
        return True
    finally:
        if temp:
            os.unlink(temp)


def scrub_rollout(path: Path, start: int, live: bool) -> tuple[int, int, int]:
    before = path.stat()
    if before.st_size == 0:
        return 0, 0, before.st_ino
    with open(path, "rb") as handle, mmap.mmap(handle.fileno(), before.st_size, access=mmap.ACCESS_READ) as mm:
        limit = mm.rfind(b"\n", start) + 1 if live else before.st_size
        if limit <= start:
            return start, 0, before.st_ino
        edits = find_edits(mm, start, limit)
        if not edits:
            return limit, 0, before.st_ino
        if not lines_stay_json(mm, edits, limit):
            print(f"skip {path.name}: masking would break a JSON line", file=sys.stderr)
            return start, 0, before.st_ino
        if live:
            write_in_place(path, edits, before)
            return limit, len(edits), before.st_ino
        if write_replacement(path, mm, edits, before):
            return limit, len(edits), path.stat().st_ino
        return start, 0, before.st_ino


def scrub_rollouts(codex: Path, state: dict, full: bool) -> tuple[int, int, set[str]]:
    todo = []
    for path in rollouts(codex):
        info = path.stat()
        seen = state.get(str(path))
        start = seen[1] if seen and not full and seen[0] == info.st_ino and seen[1] <= info.st_size else 0
        if start == info.st_size:
            continue
        todo.append((path, start))
    held = writers([path for path, _ in todo])
    files = codes = 0
    threads = set()
    for path, start in todo:
        done, masked, inode = scrub_rollout(path, start, str(path) in held)
        state[str(path)] = [inode, done]
        files += masked > 0
        codes += masked
        if masked:
            threads.add(path.stem[-36:])
    return files, codes, threads


def history_signature(db: Path) -> list[int]:
    return [
        value
        for part in (db, db.with_name(db.name + "-wal"))
        if part.exists()
        for value in (part.stat().st_mtime_ns, part.stat().st_size)
    ]


def history_rows(con: sqlite3.Connection, table: str, entry: dict, threads: list[str], full: bool) -> dict:
    query = f"select rowid, item_json from {table} where instr(item_json, 'rac_') > 0"
    if full or "rowids" not in entry:
        return dict(con.execute(query))
    found = dict(con.execute(query + " and rowid > ?", (entry["rowids"].get(table, 0),)))
    for thread in threads:
        found.update(con.execute(query + " and thread_id = ?", (thread,)))
    return found


def scrub_history(codex: Path, state: dict, full: bool, fresh: set[str]) -> tuple[int, int]:
    rows = codes = 0
    now = time.time()
    for db in sorted(codex.glob("thread_history_*.sqlite")):
        key = f"history:{db}"
        saved = state.get(key)
        entry: dict = saved if isinstance(saved, dict) else {}
        pending = {t: until for t, until in entry.get("threads", {}).items() if until > now}
        pending.update(dict.fromkeys(fresh, now + PENDING_SECONDS))
        if not full and entry.get("signature") == history_signature(db) and not fresh:
            entry["threads"] = pending
            continue
        con = sqlite3.connect(str(db), timeout=30)
        try:
            con.execute("pragma secure_delete=on")
            tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
            present = [table for table in HISTORY_TABLES if table in tables]
            tops = {
                table: con.execute(f"select coalesce(max(rowid), 0) from {table}").fetchone()[0] for table in present
            }
            updates = []
            for table in present:
                for rowid, text in history_rows(con, table, entry, sorted(pending), full).items():
                    masked, count = mask_text(text)
                    if count:
                        updates.append((table, masked, rowid))
                        codes += count
            if updates:
                with con:
                    for table, masked, rowid in updates:
                        con.execute(f"update {table} set item_json = ? where rowid = ?", (masked, rowid))
                rows += len(updates)
            con.execute("pragma busy_timeout=200")
            for mode in ("PASSIVE", "TRUNCATE"):
                try:
                    con.execute(f"pragma wal_checkpoint({mode})")
                except sqlite3.OperationalError:
                    break
        except sqlite3.OperationalError as exc:
            print(f"skip {db.name}: {exc}", file=sys.stderr)
            continue
        finally:
            con.close()
        state[key] = {"signature": history_signature(db), "rowids": tops, "threads": pending}
    return rows, codes


def main() -> int:
    parser = argparse.ArgumentParser(description="Mask claim codes in Codex rollouts and thread history; counts only.")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--full", action="store_true", help="rescan every rollout and history row")
    parser.add_argument("--quiet", action="store_true", help="print only when something was masked")
    args = parser.parse_args()
    state_dir = args.home / STATE
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(state_dir / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if args.full else fcntl.LOCK_NB))
        except BlockingIOError:
            return 0
        state_path = state_dir / "state.json"
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        for codex in codex_homes(args.home):
            files, codes, threads = scrub_rollouts(codex, state, args.full)
            rows, row_codes = scrub_history(codex, state, args.full, threads)
            if not args.quiet or codes or row_codes:
                print(
                    f"{codex}: rollouts {files} files {codes} codes masked; history {rows} rows {row_codes} codes masked"
                )
        kept = {key: value for key, value in state.items() if key.startswith("history:") or Path(key).exists()}
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(kept))
        os.replace(temp, state_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
