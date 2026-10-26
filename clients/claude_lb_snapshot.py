#!/usr/bin/env python3

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def bounded_process_snapshots():
    previous_path = os.environ.get("PATH")
    previous_cache = os.environ.get("CLAUDE_LB_PS_CACHE")
    directory = tempfile.mkdtemp(prefix="claude-lb-ps-")
    root = Path(directory)
    try:
        (root / "ps").symlink_to(Path(__file__).resolve())
        os.environ["PATH"] = directory + os.pathsep + (previous_path or os.defpath)
        os.environ["CLAUDE_LB_PS_CACHE"] = directory
        yield
    finally:
        for key, value in (("PATH", previous_path), ("CLAUDE_LB_PS_CACHE", previous_cache)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        deadline = time.monotonic() + 0.2
        for attempt in range(4):
            try:
                shutil.rmtree(root)
                break
            except FileNotFoundError:
                if not root.exists():
                    break
            except OSError:
                pass
            if attempt == 3 or time.monotonic() >= deadline:
                try:
                    print(f"cc: leaving process snapshot cache after cleanup race: {root}", file=sys.stderr)
                except OSError:
                    pass
                break
            time.sleep(0.02)


def cached_snapshot(path: Path) -> str | None:
    try:
        record = json.loads(path.read_text())
        age = time.monotonic() - record["time"]
        if 0 <= age < 2:
            return record["output"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def snapshot(root: Path, session: bool) -> str:
    cache = root / ("session.json" if session else "parents.json")
    deadline = time.monotonic() + 0.8
    output = ""
    try:
        with (root / "snapshot.lock").open("a") as lock:
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return cached_snapshot(cache) or ""
                    time.sleep(0.02)
            output = cached_snapshot(cache)
            if output is not None:
                return output
            args = ["/bin/ps", "-A", "-o", "pid=", "-o", "ppid="]
            if session:
                args.extend(["-o", "sess=" if sys.platform == "darwin" else "sid="])
            try:
                result = subprocess.run(args, capture_output=True, text=True, timeout=0.5, check=False)
                output = result.stdout if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                output = ""
            temporary = cache.with_suffix(".pending")
            temporary.write_text(json.dumps({"time": time.monotonic(), "output": output}))
            temporary.replace(cache)
            return output
    except OSError:
        return output or ""


def main() -> None:
    args = sys.argv[1:]
    base = ["-A", "-o", "pid=", "-o", "ppid="]
    session_args = ["-o", "sess=" if sys.platform == "darwin" else "sid="]
    root = os.environ.get("CLAUDE_LB_PS_CACHE")
    if root and args in (base, base + session_args):
        sys.stdout.write(snapshot(Path(root), args != base))
        return
    os.execv("/bin/ps", ["/bin/ps", *args])


if __name__ == "__main__":
    main()
