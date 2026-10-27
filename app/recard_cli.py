from __future__ import annotations

import calendar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.core.browser_profiles import profile_for
from app.core.cdp_page import CdpError
from app.core.claude_billing import BillingError, put_card, signed_in_email
from app.core.link_spend import LinkError, create_card_request, load_card, request_state
from app.core.recards import load_recards, save_recard
from app.core.subscriptions import Subscription, load_subscriptions, save_subscriptions
from app.profiles_cli import launch_profile

OPEN = ("created", "pending_approval", "approved")
CLOSED = ("denied", "expired", "canceled", "declined")


class RecardError(Exception):
    pass


def add_parser(subparsers) -> None:
    recard = subparsers.add_parser("recard", help="Put a fresh Link card on a single-use subscription before renewal.")
    commands = recard.add_subparsers(dest="recard_command", required=True)
    start = commands.add_parser("start", help="Create the Link request; Link asks the operator to approve it.")
    start.add_argument("email")
    start.add_argument("--amount", type=float)
    finish = commands.add_parser("finish", help="Once approved, put the card on the claude.ai billing page.")
    finish.add_argument("email")
    finish.add_argument("--no-save", action="store_true")
    tick = commands.add_parser("run", help="Start due recards and finish approved ones.")
    tick.add_argument("--lead-days", type=int, default=2)
    tick.add_argument("--dry-run", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")


def _notify(message: str) -> None:
    print(message)
    lane_post = shutil.which("lane-post")
    if lane_post and not os.getenv("AGENT_LB_RECARD_QUIET"):
        try:
            subprocess.run(
                [lane_post, "post", "--to", "factory", "--from", "agent-lb-recard", "--kind", "info", message[:799]],
                timeout=30,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _signed_in_port(email: str) -> int:
    profile = profile_for(email)
    if profile is None:
        raise RecardError(f"no browser profile; run: agent-lb profiles add {email} --provider anthropic")
    try:
        return _check_signed_in(email, profile.cdp_port)
    except CdpError:
        launch_profile(profile, "about:blank")
        time.sleep(8)
        try:
            return _check_signed_in(email, profile.cdp_port)
        except CdpError:
            raise RecardError(f"browser profile {profile.dir} did not open on CDP {profile.cdp_port}") from None


def _check_signed_in(email: str, port: int) -> int:
    signed = signed_in_email(port)
    if signed != email:
        raise RecardError(f"sign in to claude.ai as {email} in its browser profile (CDP {port}), then rerun")
    return port


def _subscription(email: str) -> Subscription:
    subscription = load_subscriptions().get(email)
    if subscription is None:
        raise RecardError("no subscription in the ledger")
    return subscription


def start(email: str, amount: float | None = None) -> dict:
    subscription = _subscription(email)
    row = load_recards().get(email, {})
    if row.get("renewsOn") == subscription.renews_on and row.get("status") in (*OPEN, "done"):
        return row
    total = amount if amount is not None else subscription.amount_usd
    if not total:
        raise RecardError("no amount; pass --amount or set it with agent-lb subscriptions set")
    _signed_in_port(email)
    request = create_card_request(email, subscription.plan, total, subscription.renews_on)
    row = save_recard(
        email,
        renewsOn=subscription.renews_on,
        requestId=request["id"],
        approvalUrl=request["approvalUrl"],
        status=request["status"],
        detail=None,
    )
    _notify(
        f"{email}: approve a ${total:.2f} Link card for the {subscription.plan} renewal on "
        f"{subscription.renews_on}: {request['approvalUrl']} (agent-lb puts it on claude.ai once approved)"
    )
    return row


def finish(email: str, *, save: bool = True) -> dict:
    row = load_recards().get(email)
    if not row or not row.get("requestId"):
        raise RecardError(f"no Link request; run: agent-lb recard start {email}")
    state = request_state(row["requestId"])
    if state["status"] != "approved":
        return save_recard(email, status=state["status"])
    port = _signed_in_port(email)
    with tempfile.TemporaryDirectory(prefix="agent-lb-recard-") as directory:
        os.chmod(directory, 0o700)
        card = load_card(row["requestId"], Path(directory))
        last4 = put_card(port, email, card, save=save)
        valid_until = str(card.get("valid_until") or "") or None
        del card
    if not save:
        return save_recard(email, detail="card form filled, not saved")
    subscriptions = load_subscriptions()
    subscription = subscriptions[email]
    note = f"Recarded {date.today().isoformat()} with Link card ending {last4} for the {subscription.renews_on} renewal"
    if valid_until:
        note += f"; card valid until {valid_until}"
    subscriptions[email] = replace(subscription, note=note)
    save_subscriptions(subscriptions)
    row = save_recard(email, status="done", last4=last4, validUntil=valid_until, detail=None)
    _notify(f"{email}: Link card ending {last4} is on claude.ai for the {subscription.renews_on} renewal")
    return row


def _next_month(day: str) -> str:
    current = date.fromisoformat(day)
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    return date(year, month, min(current.day, calendar.monthrange(year, month)[1])).isoformat()


def tick(lead_days: int, dry_run: bool) -> None:
    today = date.today()
    recards = load_recards()
    for email, subscription in sorted(load_subscriptions().items()):
        if subscription.payment != "single-use":
            continue
        row = recards.get(email, {})
        days = (date.fromisoformat(subscription.renews_on) - today).days
        same_cycle = row.get("renewsOn") == subscription.renews_on
        if same_cycle and row.get("status") == "done":
            if days < 0 and not dry_run:
                subscriptions = load_subscriptions()
                subscriptions[email] = replace(subscription, renews_on=_next_month(subscription.renews_on))
                save_subscriptions(subscriptions)
            continue
        if same_cycle and row.get("status") in CLOSED:
            continue
        action = "finish" if same_cycle and row.get("status") in OPEN else "start" if -7 <= days <= lead_days else None
        if action is None:
            continue
        if dry_run:
            print(f"{email}: would {action} (renews {subscription.renews_on})")
            continue
        try:
            if action == "start":
                start(email)
            else:
                finish(email)
        except (RecardError, LinkError, BillingError, CdpError) as exc:
            detail = str(exc)
            if load_recards().get(email, {}).get("detail") != detail:
                save_recard(
                    email,
                    renewsOn=subscription.renews_on,
                    detail=detail,
                    **({} if same_cycle else {"status": "blocked"}),
                )
                _notify(f"{email}: recard for the {subscription.renews_on} renewal is stuck: {detail}")


def run(args) -> None:
    try:
        if args.recard_command == "status":
            subscriptions = load_subscriptions()
            rows = [
                {"email": email, "renewsOn": sub.renews_on, **load_recards().get(email, {})}
                for email, sub in sorted(subscriptions.items())
                if sub.payment == "single-use"
            ]
            if args.json:
                print(json.dumps(rows))
            for row in [] if args.json else rows:
                print(f"{row['email']}\t{row['renewsOn']}\t{row.get('status', '-')}\t{row.get('detail') or ''}")
            return
        if args.recard_command == "run":
            tick(args.lead_days, args.dry_run)
            return
        email = args.email.strip().lower()
        row = start(email, args.amount) if args.recard_command == "start" else finish(email, save=not args.no_save)
        print(json.dumps({key: row.get(key) for key in ("status", "approvalUrl", "last4", "validUntil", "detail")}))
    except (RecardError, LinkError, BillingError, CdpError) as exc:
        print(f"recard: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
