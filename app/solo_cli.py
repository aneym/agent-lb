from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app import solo_env


def add_parser(subparsers) -> None:
    solo = subparsers.add_parser("solo", help="One-person mode: one person's own plans, on loopback.")
    commands = solo.add_subparsers(dest="solo_command", required=True)
    serve = commands.add_parser("serve", help="Serve this person's plans on 127.0.0.1, no dashboard, no federation.")
    serve.add_argument("--data-dir", help=f"Data dir (default: AGENT_LB_SOLO_DATA_DIR or {solo_env.DEFAULT_DATA_DIR}).")
    serve.add_argument("--port", type=int, default=int(os.environ.get("AGENT_LB_SOLO_PORT") or solo_env.DEFAULT_PORT))
    signin = commands.add_parser("import-claude", help="Import this person's Claude Code sign-in.")
    signin.add_argument("--data-dir")
    source = signin.add_mutually_exclusive_group()
    source.add_argument("--file", type=Path, help="Credential file (default: ~/.claude/.credentials.json).")
    source.add_argument("--stdin", action="store_true", help="Read a pasted or exported credential from stdin.")
    signin.add_argument("--profile", type=Path, help="Claude Code profile with oauthAccount (default: ~/.claude.json).")
    headroom = commands.add_parser("headroom", help="Print the headroom summary this instance posts.")
    headroom.add_argument("--data-dir")
    headroom.add_argument("--post", action="store_true", help="Also post it once to AGENT_LB_SOLO_HEADROOM_URL.")


def run(args: argparse.Namespace) -> None:
    data_dir = solo_env.resolve_data_dir(args.data_dir)
    solo_env.apply(data_dir)
    if args.solo_command == "serve":
        _serve(args.port)
    elif args.solo_command == "import-claude":
        _import_claude(args)
    else:
        _headroom(args.post)


def _serve(port: int) -> None:
    import uvicorn

    from app.core.runtime_logging import configure_runtime_logging

    os.environ["PORT"] = str(port)
    configure_runtime_logging()
    uvicorn.run(
        "app.solo_app:build",
        factory=True,
        host=solo_env.LOOPBACK_HOST,
        port=port,
        ws_ping_interval=20.0,
        ws_ping_timeout=None,
        ws_per_message_deflate=False,
        log_config=None,
    )


def _import_claude(args: argparse.Namespace) -> None:
    from app import claude_signin
    from app.claude_signin_import import store

    if args.stdin:
        raw, origin = sys.stdin.read(), "stdin"
    else:
        path = (args.file or claude_signin.DEFAULT_CREDENTIALS_PATH).expanduser()
        if not path.is_file():
            raise SystemExit(f"no Claude sign-in at {path}; on macOS export it and pass --stdin")
        raw, origin = path.read_text(encoding="utf-8"), str(path)
    profile_path = args.profile or (None if args.stdin or args.file else claude_signin.DEFAULT_PROFILE_PATH)
    try:
        sign_in = claude_signin.parse(raw, claude_signin.read_profile(profile_path))
    except claude_signin.ClaudeSignInError as exc:
        raise SystemExit(f"import refused: {exc}") from None
    plan = asyncio.run(store(sign_in))
    verb = "added" if plan.created else "updated"
    expiry = plan.access_expires_at.isoformat() + "Z" if plan.access_expires_at else "unknown"
    print(f"{verb} Claude plan {plan.account} ({plan.plan_type}, {plan.status}) from {origin}; access expires {expiry}")


def _headroom(post: bool) -> None:
    from app import solo_headroom_sender

    body = asyncio.run(_summary_with_db())
    print(json.dumps(body, indent=2))
    if post:
        target = solo_headroom_sender.target_from_env()
        if target is None:
            raise SystemExit("AGENT_LB_SOLO_HEADROOM_URL is not set")
        status = asyncio.run(solo_headroom_sender.post_once(target, body))
        print(f"posted to {target.url}: HTTP {status}", file=sys.stderr)


async def _summary_with_db() -> dict:
    from app import solo_headroom_sender
    from app.db.session import close_db, init_db

    await init_db()
    try:
        return await solo_headroom_sender.summary()
    finally:
        await close_db()
