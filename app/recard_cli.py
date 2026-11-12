from __future__ import annotations

import json
import sys

from app.core.cdp_page import CdpError
from app.core.claude_billing import BillingError
from app.core.link_spend import LinkError
from app.core.recards import load_recards
from app.core.subscriptions import load_subscriptions
from app.recard_flow import RecardError, finish, start, tick, watch
from app.recard_signin import signed_in_ahead
from app.recard_window import open_billing_window


def add_parser(subparsers) -> None:
    recard = subparsers.add_parser(
        "recard", help="Put a fresh Link card on a single-use subscription once it runs out."
    )
    commands = recard.add_subparsers(dest="recard_command", required=True)
    start_parser = commands.add_parser(
        "start", help="Create the Link request at once, open the billing window and ask the operator in Rails."
    )
    start_parser.add_argument("email")
    start_parser.add_argument("--amount", type=float)
    start_parser.add_argument(
        "--dry-run", action="store_true", help="Open the window and show the ask; create no Link request."
    )
    finish_parser = commands.add_parser("finish", help="Once approved, put the card on the claude.ai billing page.")
    finish_parser.add_argument("email")
    finish_parser.add_argument("--no-save", action="store_true")
    watch_parser = commands.add_parser("watch", help="Poll the open Link request every few seconds and finish it.")
    watch_parser.add_argument("email")
    window_parser = commands.add_parser("window", help="Open the billing window on the main display and report.")
    window_parser.add_argument("email")
    signin_parser = commands.add_parser("signin", help="Read-only: is the account's profile signed in to claude.ai?")
    signin_parser.add_argument("email")
    tick_parser = commands.add_parser("run", help="Start recards for plans that ran out and watch open ones.")
    tick_parser.add_argument("--dry-run", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")


def _status(as_json: bool) -> None:
    rows = [
        {"email": email, "renewsOn": sub.renews_on, **load_recards().get(email, {})}
        for email, sub in sorted(load_subscriptions().items())
        if sub.payment == "single-use"
    ]
    if as_json:
        print(json.dumps(rows))
        return
    for row in rows:
        print(f"{row['email']}\t{row['renewsOn']}\t{row.get('status', '-')}\t{row.get('detail') or ''}")


def run(args) -> None:
    try:
        command = args.recard_command
        if command == "status":
            _status(args.json)
            return
        if command == "run":
            tick(args.dry_run)
            return
        email = args.email.strip().lower()
        if command == "watch":
            watch(email)
            return
        if command == "window":
            print(json.dumps(open_billing_window(email)))
            return
        if command == "signin":
            print(json.dumps(signed_in_ahead(email)))
            return
        if command == "start":
            row = start(email, args.amount, dry_run=args.dry_run)
            if row.get("dryRun"):
                print(json.dumps(row, indent=2))
                return
        else:
            row = finish(email, save=not args.no_save)
        keys = ("status", "approvalUrl", "last4", "validUntil", "detail", "notice", "window")
        print(json.dumps({key: row.get(key) for key in keys}))
    except (RecardError, LinkError, BillingError, CdpError) as exc:
        print(f"recard: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
