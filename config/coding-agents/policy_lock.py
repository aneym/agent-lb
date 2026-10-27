from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from policy_files import sync_policy_boxes  # type: ignore[missing-import]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--print", action="store_true", dest="preview")
    parser.add_argument(
        "--preserve-claude-md",
        action="store_true",
        help="Preserve personal instructions",
    )
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--autocompact-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-box-sync", action="store_true", help="Skip box sync")
    parser.add_argument("--reason", help="Logged reason for forcing installation from a stale revision")
    parser.add_argument(
        "--hook-dispatcher",
        choices=("on", "off"),
        help="on: fold after parity; off: unfold; absent: keep mode",
    )
    parser.add_argument(
        "--check-hook-paths",
        action="store_true",
        help="Check folded and direct hook paths",
    )
    parser.add_argument(
        "--register-hook",
        action="append",
        default=[],
        metavar="JSON",
        help="Register a persistent external hook through the dispatcher owner",
    )
    args = parser.parse_args()
    if args.uninstall and args.register_hook:
        parser.error("--register-hook cannot be combined with --uninstall")
    return args


def same_policy_tree(bundle: Path, checkout: Path) -> bool:

    def files(root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }

    try:
        return checkout.is_dir() and files(bundle) == files(checkout)
    except OSError:
        return False


def verify_install_revision(
    source: Path, force: bool = False, reason: str | None = None, home: Path | None = None
) -> None:

    if home is not None and source.resolve() == (home.expanduser() / ".agents/policy/coding-agents").resolve():
        return
    repo = source.parent.parent
    if not (repo / ".git").exists():
        origin_repo = os.environ.get("AGENT_LB_POLICY_SOURCE_REPO")
        if not origin_repo or not same_policy_tree(source, Path(origin_repo) / "config/coding-agents"):
            raise SystemExit("error: installer needs a git worktree; rebase onto origin/main")
        repo = Path(origin_repo)
        if not (repo / ".git").exists():
            raise SystemExit("error: installer needs a git worktree; rebase onto origin/main")

    def git(*words):
        return subprocess.run(["git", "-C", str(repo), *words], capture_output=True, text=True, timeout=60)

    pinned = os.environ.get("AGENT_LB_POLICY_TEST_PIN", "").strip()
    if pinned and git("cat-file", "-e", pinned + "^{commit}").returncode == 0:
        known = git("merge-base", "--is-ancestor", pinned, "origin/main").returncode == 0
        if known and git("merge-base", "--is-ancestor", pinned, "HEAD").returncode == 0:
            return
        raise SystemExit(f"error: stale installer: HEAD does not contain the test pin {pinned} from origin/main")
    fetched = git("fetch", "origin", "main")
    if fetched.returncode:
        raise SystemExit("error: could not fetch origin/main; refusing policy installation")
    head = git("rev-parse", "HEAD").stdout.strip()
    main_sha = git("rev-parse", "origin/main").stdout.strip()
    if git("merge-base", "--is-ancestor", "origin/main", "HEAD").returncode == 0:
        return
    message = f"HEAD={head} origin/main={main_sha}; rebase onto origin/main"
    if not force or not reason or not reason.strip():
        raise SystemExit("error: stale installer: " + message)
    print("WARNING: forced stale installation: " + message + "; reason=" + reason, flush=True)


def locked_main(main) -> int:

    import fcntl
    import signal

    args = parse_args()
    if not (args.check_hook_paths or args.preview or args.uninstall or args.check):
        verify_install_revision(Path(__file__).resolve().parent, args.force, args.reason, args.home)
    home = args.home.expanduser().resolve()
    digest = hashlib.sha256(str(home).encode()).hexdigest()[:16]
    lock_dir = Path(tempfile.gettempdir())
    lock_path = lock_dir / f"agent-lb-install-policy-{os.getuid()}-{digest}.lock"
    with open(lock_path, "a+") as handle:
        deadline = time.monotonic() + 600
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                handle.seek(0)
                try:
                    holder = json.loads(handle.read())
                    pid = int(holder["pid"])
                    stale = time.time() - float(holder["progress_at"]) > 900
                    started = time.time() - float(holder["started_at"]) > 900
                except (ValueError, KeyError, TypeError):
                    stale = started = False
                if stale and started:
                    process = subprocess.run(
                        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10
                    ).stdout.strip()
                    if "install-policy.py" in process and "python" in process and "claude" not in process.lower():
                        print(
                            f"WARNING: retiring stalled policy installer pid={pid}; "
                            "no recorded progress for over 15 minutes",
                            flush=True,
                        )
                        os.kill(pid, signal.SIGTERM)
                        until = time.monotonic() + 30
                        while time.monotonic() < until:
                            try:
                                os.kill(pid, 0)
                            except ProcessLookupError:
                                break
                            time.sleep(1)
                        else:
                            os.kill(pid, signal.SIGKILL)
                        continue
                if time.monotonic() > deadline:
                    raise SystemExit(f"error: another install-policy run holds {lock_path}")
                time.sleep(1)
        started_at = time.time()

        def progress():
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": os.getpid(), "started_at": started_at, "progress_at": time.time()}, handle)
            handle.flush()

        progress()

        class ProgressStream:
            def __init__(self, stream):
                self.stream = stream

            def write(self, text):
                if text.strip():
                    progress()
                return self.stream.write(text)

            def flush(self):
                return self.stream.flush()

        stdout = sys.stdout
        sys.stdout = ProgressStream(stdout)
        try:
            result = main()
            if result == 0 and not (
                args.check_hook_paths or args.preview or args.uninstall or args.check or args.no_box_sync
            ):
                sync_policy_boxes(home)
            return result
        finally:
            sys.stdout = stdout
            handle.seek(0)
            handle.truncate()
