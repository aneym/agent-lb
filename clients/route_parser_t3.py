from __future__ import annotations

from pathlib import Path
from typing import Any

from route_run import command_run  # pyrefly: ignore [missing-import]
from route_run_jobs import command_wait  # pyrefly: ignore [missing-import]
from route_seat_run_import import command_import_seat_run  # pyrefly: ignore [missing-import]
from route_target import command_target  # pyrefly: ignore [missing-import]


def add_t3_parsers(sub: Any) -> None:
    run = sub.add_parser("run", help="run a routed headless model and return its final message")
    run.add_argument("task_class", metavar="class")
    brief = run.add_mutually_exclusive_group(required=True)
    brief.add_argument("--brief", type=Path)
    brief.add_argument("--brief-stdin", action="store_true")
    run.add_argument("--cwd", type=Path)
    run.add_argument("--on", metavar="ENV_NAME", help="pin execution to this environment")
    run.add_argument("--bg", action="store_true", help="detach and print the decision id")
    run.add_argument("--write", action="store_true")
    run.add_argument("--author-vendor")
    run.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    run.add_argument("--caller")
    run.add_argument("--job")
    run.add_argument("--timeout", type=float, default=600, help="run limit in seconds; foreground waits at most 240")
    run.add_argument("--json", action="store_true")
    run.set_defaults(handler=command_run)

    wait = sub.add_parser("wait", help="collect a detached run; exit 3 while queued or running")
    wait.add_argument("decision_id")
    wait.add_argument("--max", type=float, default=240, help="wait seconds, capped at 240")
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(handler=command_wait)

    target = sub.add_parser(
        "target",
        help="T3 delegate target and admission lease for a task class",
        description="Route through dispatch policy, then lease capacity for at most 10 minutes. "
        "Release the lease with route release ID --outcome ok when the child starts.",
    )
    target.add_argument("task_class", metavar="class")
    target.add_argument(
        "--write",
        action="store_true",
        help="write child: placed by headroom; pass the target to delegate_task with isolation worktree",
    )
    target.add_argument("--mac", action="store_true", help="require Studio")
    target.add_argument(
        "--here", metavar="ENV_NAME", help="caller's environment; the fallback when no box has measured room"
    )
    target.add_argument("--on", metavar="ENV_NAME", help="pin execution to this environment")
    target.add_argument("--caller")
    target.add_argument("--job")
    target.add_argument("--author-vendor")
    target.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    target.add_argument("--json", action="store_true")
    target.add_argument("--dry-run", action="store_true", help="no decision log or lease")
    target.set_defaults(handler=command_target)

    seat_import = sub.add_parser("import-seat-run", help="append historical seat-run routing decisions")
    seat_import.add_argument("--from", dest="source_dir", default="~/.agent-rails/jobs/seat-run")
    seat_import.add_argument("--host")
    seat_import.add_argument("--out")
    seat_import.add_argument("--dry-run", action="store_true")
    seat_import.add_argument("--json", action="store_true")
    seat_import.set_defaults(handler=command_import_seat_run)
