from __future__ import annotations

import json
import sys

from app.core.subscriptions import Subscription, load_subscriptions, save_subscriptions


def add_parser(subparsers) -> None:
    subscriptions = subparsers.add_parser("subscriptions", help="Track paid account subscriptions and renewals.")
    commands = subscriptions.add_subparsers(dest="subscriptions_command", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    setting = commands.add_parser("set")
    setting.add_argument("email")
    setting.add_argument("--plan")
    setting.add_argument("--renews-on")
    setting.add_argument("--payment", choices=("card", "single-use", "none"))
    setting.add_argument("--amount", type=float)
    setting.add_argument("--note")
    clearing = commands.add_parser("clear")
    clearing.add_argument("email")


def run(args) -> None:
    subscriptions = load_subscriptions()
    if args.subscriptions_command == "list":
        rows = [{"email": email, **sub.as_row()} for email, sub in sorted(subscriptions.items())]
        if args.json:
            print(json.dumps(rows))
        else:
            for row in rows:
                print(
                    f"{row['email']}\t{row['plan']}\t{row['renewsOn']}\t{row['payment']}\t{row['amountUsd']}\t{row['note']}"
                )
        return
    try:
        email = args.email.strip().lower()
        if not email:
            raise ValueError("email is required")
        if args.subscriptions_command == "clear":
            subscriptions.pop(email, None)
        else:
            previous = subscriptions.get(email)
            if previous is None and any(value is None for value in (args.plan, args.renews_on, args.payment)):
                raise ValueError("new subscriptions require --plan, --renews-on and --payment")
            if previous is None:
                previous = Subscription(args.plan, args.renews_on, args.payment)
            subscriptions[email] = Subscription(
                plan=args.plan if args.plan is not None else previous.plan,
                renews_on=args.renews_on if args.renews_on is not None else previous.renews_on,
                payment=args.payment if args.payment is not None else previous.payment,
                amount_usd=args.amount if args.amount is not None else previous.amount_usd if previous else None,
                note=args.note if args.note is not None else previous.note if previous else None,
            )
        save_subscriptions(subscriptions)
    except ValueError as exc:
        print(f"subscriptions: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    except OSError as exc:
        print(f"subscriptions: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"{'cleared' if args.subscriptions_command == 'clear' else 'recorded'} {email}")
