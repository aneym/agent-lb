import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MAX_AGE_S = float(os.environ.get("FACTORY_GIT_LOCK_MAX_AGE_S", "600"))
CLEANUP_WAIT_S = float(os.environ.get("FACTORY_GIT_LOCK_CLEANUP_WAIT_S", "30"))
LOCK_NAMES = {"packed-refs.lock", "index.lock", "HEAD.lock", "config.lock"}
LOCK_FILE = re.compile(r"\.lock': File exists")
PACKED_REFS = re.compile(r"Unable to create '[^']*packed-refs\.lock'")
CAS = re.compile(r"cannot lock ref 'refs/heads/[^']+': is at [0-9a-f]+ but expected [0-9a-f]+")
PID_RECORD = re.compile(r"\A([1-9][0-9]{0,9})(?:\s+\S+)?\s*\Z")


def _lock_paths(common):
    root = Path(common).resolve()
    candidates = [root / name for name in LOCK_NAMES]
    candidates += root.glob("refs/**/*.lock")
    for tree in root.glob("worktrees/*"):
        candidates += [tree / name for name in LOCK_NAMES]
        candidates += tree.glob("refs/**/*.lock")
    return sorted({path for path in candidates if path.is_file()})


class _CleanupLock:
    def __init__(self, common):
        self.stream = None
        self.error = None
        self.path = Path(common) / "factory-git-lock-recovery.lock"

    def __enter__(self):
        deadline = time.monotonic() + CLEANUP_WAIT_S
        try:
            self.stream = self.path.open("a")
            while True:
                try:
                    fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
        except (OSError, BlockingIOError) as error:
            self.error = f"advisory cleanup lock unavailable: {error}"
            if self.stream:
                self.stream.close()
                self.stream = None
        return self

    def __exit__(self, *_):
        if self.stream:
            fcntl.flock(self.stream, fcntl.LOCK_UN)
            self.stream.close()
        return False


def _dead(pid):
    if type(pid) is not int or pid <= 0:
        return True
    proc = Path(f"/proc/{pid}")
    if proc.exists():
        try:
            state = (proc / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return False
        return state == "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _recorded_pid(path):
    info = path.stat()
    if info.st_size > 64:
        return None
    match = PID_RECORD.fullmatch(path.read_text(errors="replace").strip())
    return int(match.group(1)) if match else None


def _same_lock(first, second):
    return (
        first.st_dev,
        first.st_ino,
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def _owners(path):
    probe = subprocess.run(
        ["lsof", "-t", "--", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if probe.stderr.strip() or probe.returncode not in (0, 1):
        return None, probe.stderr.strip() or f"lsof exited {probe.returncode}"
    pids = [int(token) for token in probe.stdout.split() if token.isdigit()]
    recorded = _recorded_pid(path)
    if recorded is not None:
        pids.append(recorded)
    return sorted({pid for pid in pids if not _dead(pid)}), None


def _git_holders(common, path):
    relative = path.relative_to(common)
    if len(relative.parts) >= 3 and relative.parts[0] == "worktrees":
        admin = common / "worktrees" / relative.parts[1]
        worktree = Path((admin / "gitdir").read_text().strip()).parent
        locations = [str(admin), str(worktree)]
    else:
        locations = _repository_locations(common)
    probe = subprocess.run(
        ["lsof", "-w", "-a", "-c", "git", "-d", "cwd", "-Fpn"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if probe.stderr.strip() or probe.returncode not in (0, 1):
        raise OSError("git holder inspection failed")
    holders = []
    pid = None
    for line in probe.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif (
            line.startswith("n")
            and pid
            and any(line[1:] == location or line[1:].startswith(location + os.sep) for location in locations)
        ):
            holders.append(pid)
    return holders


def _repository_locations(common):
    listed = subprocess.run(
        ["git", "--git-dir", str(common), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    locations = [
        str(common),
        *[line[9:] for line in listed.stdout.splitlines() if line.startswith("worktree ")],
    ]
    return locations


def recover(common, max_age_s=DEFAULT_MAX_AGE_S, now=None, actor=None, apply=True, paths=None):
    common = Path(common).resolve()
    now = time.time() if now is None else now
    actor = actor or f"factory-git-lock-recovery:{os.getpid()}"
    records: list[dict[str, object]] = []
    with _CleanupLock(common) as cleanup:
        locks = _lock_paths(common)
        if paths is not None:
            selected = {str(Path(path).absolute()) for path in paths}
            locks = [path for path in locks if str(path) in selected]
        if cleanup.error:
            reason = cleanup.error
            return [
                {
                    "path": str(path) if path else None,
                    "action": "retained" if path else "none",
                    "reason": reason,
                    "holder_check": "clear",
                    "cleanup_lock": "failed" if cleanup.error else "acquired",
                    "actor": actor,
                }
                for path in locks or [None]
            ]
        for path in locks:
            try:
                stat = path.stat()
                age_s = max(0.0, now - stat.st_mtime)
            except OSError as error:
                records.append(
                    {
                        "path": str(path),
                        "action": "retained",
                        "reason": f"stat failed: {error}",
                    }
                )
                continue
            record = {
                "path": str(path),
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                "age_s": round(age_s, 3),
                "actor": actor,
                "at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
                "holder_check": "clear",
                "cleanup_lock": "acquired",
            }
            if age_s < max_age_s:
                record.update(
                    action="retained",
                    reason="lock younger than bound",
                    max_age_s=max_age_s,
                )
            else:
                try:
                    live, error = _owners(path)
                    live = (live or []) + _git_holders(common, path)
                    fresh = path.stat()
                    if error:
                        record.update(
                            action="retained",
                            reason=error,
                            holder_check="failed",
                        )
                    elif live or not _same_lock(stat, fresh):
                        record.update(
                            action="retained",
                            reason="live lock holder" if live else "lock changed during inspection",
                            **({"holders": live} if live else {}),
                        )
                    elif max(0.0, now - fresh.st_mtime) < max_age_s:
                        record.update(
                            action="retained",
                            reason="lock younger than bound",
                            max_age_s=max_age_s,
                        )
                    else:
                        live, error = _owners(path)
                        live = (live or []) + _git_holders(common, path)
                        fresh = path.stat()
                        young = max(0.0, now - fresh.st_mtime) < max_age_s
                        if error or live or not _same_lock(stat, fresh) or young:
                            if error:
                                reason = error
                            elif live:
                                reason = "live lock holder"
                            elif young and _same_lock(stat, fresh):
                                reason = "lock younger than bound"
                            else:
                                reason = "lock changed during inspection"
                            record.update(
                                action="retained",
                                reason=reason,
                                holder_check="failed" if error else "clear",
                                **({"holders": live} if live else {}),
                            )
                        elif not apply:
                            record.update(action="would_remove", reason="stale holderless lock")
                        else:
                            path.unlink()
                            record.update(
                                action="removed",
                                reason="stale holderless lock",
                                max_age_s=max_age_s,
                            )
                except FileNotFoundError:
                    record.update(action="none", reason="lock or its worktree vanished during inspection")
                except (OSError, subprocess.SubprocessError) as error:
                    record.update(
                        action="retained",
                        reason=f"inspection or removal failed: {error}",
                        holder_check="failed",
                    )
            records.append(record)
    return records


def _retryable(text):
    text = str(text)
    lost = bool(LOCK_FILE.search(text) or "cannot lock ref" in text or PACKED_REFS.search(text))
    return lost and not CAS.search(text)


def _common(repo):
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or "git-common-dir failed")
    return result.stdout.strip()


def recover_failure(repo, text, max_age_s=DEFAULT_MAX_AGE_S):
    if not _retryable(text):
        return False
    try:
        common = _common(repo)
        paths = re.findall(r"(?:Unable to create|unable to create) '([^']+\.lock)'", str(text))
        paths = [str(Path(repo) / path) if not Path(path).is_absolute() else path for path in paths]
        if not paths:
            refs = re.findall(r"cannot lock ref '(refs/[^']+)'", str(text))
            paths = [str(Path(common) / (ref + ".lock")) for ref in refs]
        if not paths:
            return False
        records = recover(common, max_age_s=max_age_s, paths=paths)
        for record in records:
            print(
                "git lock recovery " + json.dumps(record, sort_keys=True),
                file=sys.stderr,
            )
        return any(record.get("action") == "removed" for record in records)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"git lock recovery failed: {type(error).__name__}", file=sys.stderr)
        return False


def retry_locked(repo, operation, max_age_s=DEFAULT_MAX_AGE_S):
    result = operation()
    text = f"{getattr(result, 'stdout', '') or ''}{getattr(result, 'stderr', '') or ''}"
    if getattr(result, "returncode", 1) != 0 and recover_failure(repo, text, max_age_s):
        return operation()
    return result


def inspection_failed(records):
    return any(r.get("holder_check") == "failed" or r.get("cleanup_lock") == "failed" for r in records)


def run_guarded(common, operation):
    records = recover(common)
    if inspection_failed(records):
        return records
    if operation() in (0, None):
        return []
    records = recover(common)
    if inspection_failed(records):
        return records
    operation()
    return []


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="git-lock-recover")
    parser.add_argument("--common-dir", required=True)
    parser.add_argument("--max-age-s", type=float, default=DEFAULT_MAX_AGE_S)
    args = parser.parse_args(argv)
    records = recover(args.common_dir, args.max_age_s)
    print(json.dumps({"common_dir": str(Path(args.common_dir).resolve()), "records": records}))
    return 1 if inspection_failed(records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
