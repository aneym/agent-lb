from __future__ import annotations

import json
import os
import sys

from app.core.browser_profiles import profile_for
from app.lb_service import ServiceError, request, retrying
from app.reauth_auto import reauth_account
from app.reauth_auto_job import DEFAULT_BASE_URL, Job
from app.reauth_enroll import drop, enroll, load_dropped, load_enrolled, refusal, unenroll


def add_parser(subparsers) -> None:
    auto = subparsers.add_parser("reauth-auto", help="Reauth enrolled Anthropic accounts in their own Chrome profiles.")
    commands = auto.add_subparsers(dest="reauth_auto_command", required=True)
    run = commands.add_parser("run", help="One tick: reauth enrolled accounts that need it, then keep-alive.")
    run.add_argument("--dry-run", action="store_true", help="Click nothing, file nothing; log what it would ask.")
    run.add_argument("--now", action="append", default=[], metavar="ACCOUNT_ID", help="Try this enrolled account now.")
    run.add_argument("--no-keepalive", action="store_true")
    adding = commands.add_parser("enroll", help="Enroll an account by id or unique id prefix.")
    adding.add_argument("account")
    removing = commands.add_parser("unenroll")
    removing.add_argument("account")
    dropping = commands.add_parser(
        "drop", help="Stop renewing an account the owner does not need; nothing pages about its sign-in."
    )
    dropping.add_argument("account")
    dropping.add_argument("--why", required=True)
    commands.add_parser("list")


def _base() -> str:
    return os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _accounts() -> list[dict]:
    payload = retrying(lambda: request(_base(), "/api/accounts"), "accounts")
    rows = payload.get("accounts") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ServiceError("agent-lb returned a malformed accounts list")
    return [row for row in rows if isinstance(row, dict)]


def _resolve(prefix: str) -> dict:
    hits = [row for row in _accounts() if str(row.get("accountId", "")).startswith(prefix)]
    if len(hits) != 1:
        raise ServiceError(f"{prefix}: matches {len(hits)} accounts; give more of the id")
    return hits[0]


def _resolve_ids(prefixes: list[str]) -> tuple[str, ...]:
    return tuple(str(_resolve(prefix)["accountId"]) for prefix in prefixes)


def run(args) -> None:
    try:
        command = args.reauth_auto_command
        if command == "run":
            job = Job(dry_run=args.dry_run, now_ids=_resolve_ids(args.now), keepalive=not args.no_keepalive)
            raise SystemExit(job.run())
        if command == "enroll":
            account = _resolve(args.account)
            if profile_for(str(account.get("email", ""))) is None:
                raise ServiceError(
                    f"{account.get('email')}: no browser profile on this host; run agent-lb profiles add"
                )
            row = enroll(account)
            print(f"enrolled {account['accountId']} {row['email']}")
        elif command == "unenroll":
            ids = [key for key in load_enrolled() if key.startswith(args.account)]
            for key in ids:
                unenroll(key)
            print(f"unenrolled {len(ids)}")
        elif command == "drop":
            ids = [key for key in load_enrolled() if key.startswith(args.account)]
            if len(ids) != 1:
                raise ServiceError(f"{args.account}: matches {len(ids)} enrolled accounts")
            row = drop(ids[0], args.why)
            print(f"dropped {ids[0]} {row and row.get('email')}")
        else:
            for key, row in sorted(load_enrolled().items()):
                print(f"{key}\t{row.get('email')}\t{row.get('enrolledAt')}")
            for key, row in sorted(load_dropped().items()):
                print(f"{key}\t{row.get('email')}\tdropped {row.get('droppedAt')}: {row.get('why')}")
    except (ServiceError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


def run_reauth_auto(args) -> None:
    email = args.email.strip().lower()
    try:
        account = next(
            (
                row
                for row in _accounts()
                if str(row.get("email", "")).lower() == email and row.get("provider") == "anthropic"
            ),
            None,
        )
    except ServiceError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    profile = profile_for(email)
    if account is None or profile is None:
        print(json.dumps({"email": email, "result": "skipped", "detail": "no anthropic account or no profile"}))
        raise SystemExit(2)
    reason = refusal(account) or (None if account["accountId"] in load_enrolled() else "not enrolled")
    if reason:
        print(json.dumps({"email": email, "result": "skipped", "detail": reason}))
        raise SystemExit(2)
    outcome = reauth_account(_base(), account, profile, observe_only=args.dry_run)
    print(json.dumps({"email": email, **outcome.__dict__}))
    raise SystemExit(0 if outcome.result in ("active", "observed") else 1)
