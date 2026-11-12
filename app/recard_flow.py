from __future__ import annotations

import calendar
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from app.core import rails_ask
from app.core.browser_profiles import profile_for
from app.core.cdp_page import CdpError, cdp_up
from app.core.claude_billing import BillingError, put_card, signed_in_email
from app.core.link_spend import (
    LinkError,
    approval_url,
    create_card_request,
    load_card,
    request_approval,
    request_state,
)
from app.core.recards import load_recards, save_recard
from app.core.subscriptions import Subscription, load_subscriptions, save_subscriptions
from app.recard_notice import ASKER, CHAT, approval_ask, ask_for_approval, card_on, chat_line, tell_factory
from app.recard_signin import signed_in_ahead
from app.recard_watch import held, spawn
from app.recard_window import open_billing_window

OPEN = ("created", "pending_approval", "approved")
CLOSED = ("denied", "expired", "canceled", "declined")
POLL_S = 4
WATCH_S = 36 * 3600
CARD_TRIES = 3


class RecardError(Exception):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _subscription(email: str) -> Subscription:
    subscription = load_subscriptions().get(email)
    if subscription is None:
        raise RecardError("no subscription in the ledger")
    return subscription


def _signed_in_port(email: str) -> int:
    profile = profile_for(email)
    if profile is None:
        raise RecardError(f"no browser profile; run: agent-lb profiles add {email} --provider anthropic")
    if not cdp_up(profile.cdp_port):
        open_billing_window(email)
    try:
        signed = signed_in_email(profile.cdp_port)
    except CdpError:
        raise RecardError(f"browser profile {profile.dir} did not open on CDP {profile.cdp_port}") from None
    if signed != email:
        raise RecardError(f"sign in to claude.ai as {email} in its browser profile (CDP {profile.cdp_port})")
    return profile.cdp_port


def _status(request_id: str, fallback: str) -> str:
    try:
        return request_state(request_id)["status"]
    except LinkError:
        return fallback


def _dry_start(email: str, subscription: Subscription, total: float) -> dict:
    window = open_billing_window(email)
    url = approval_url("<request id from Link>")
    signed = window.get("signedIn") == email
    question, options = approval_ask(email, subscription.plan, total, subscription.renews_on, url, signed)
    return {
        "dryRun": True,
        "linkRequest": "not created",
        "window": window,
        "ownerAsk": {
            "from": ASKER,
            "command": rails_ask.rails_command(ASKER, "ask"),
            "arguments": {"to": "owner", "question": question, "options": options},
            **rails_ask.reachable(ASKER),
        },
        "chatLine": {"to": CHAT, "mode": "queue", "text": chat_line(email, subscription.plan, total, url, signed)},
    }


def start(email: str, amount: float | None = None, *, dry_run: bool = False) -> dict:
    subscription = _subscription(email)
    total = amount if amount is not None else subscription.amount_usd
    if not total:
        raise RecardError("no amount; pass --amount or set it with agent-lb subscriptions set")
    with held(email, "start") as free:
        if not free:
            raise RecardError("another recard start is running for this account")
        row = load_recards().get(email, {})
        if row.get("renewsOn") == subscription.renews_on and row.get("requestId") and row.get("status") not in CLOSED:
            if not dry_run and row.get("status") in OPEN:
                spawn(email)
            return row
        if dry_run:
            return _dry_start(email, subscription, total)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            window_job = pool.submit(open_billing_window, email)
            request_id = create_card_request(email, subscription.plan, total, subscription.renews_on)
            save_recard(
                email,
                renewsOn=subscription.renews_on,
                requestId=request_id,
                approvalUrl=approval_url(request_id),
                status="created",
                createdAt=_now(),
                askId=None,
                notice=None,
                detail=None,
            )
            approval_detail = None
            try:
                request_approval(request_id)
            except LinkError as exc:
                approval_detail = f"request-approval: {exc}"
            window = window_job.result()
        row = save_recard(email, status=_status(request_id, "created"), window=window, detail=approval_detail)
        notice = ask_for_approval(email, subscription.plan, total, subscription.renews_on, row["approvalUrl"], window)
        notice["seconds"] = round(time.monotonic() - started, 1)
        row = save_recard(email, askId=(notice.get("ask") or {}).get("askId"), notice=notice)
        print(
            f"{email}: Link request created; owner ask landed={notice['ask'].get('landed')}, "
            f"chat line landed={notice['chat'].get('landed')}, window on screen={window.get('onScreen')}, "
            f"signed in={window.get('signedIn') == email}, {notice['seconds']} s",
            flush=True,
        )
        spawn(email)
        return row


def finish(email: str, *, save: bool = True) -> dict:
    with held(email, "card") as free:
        row = load_recards().get(email)
        if not row or not row.get("requestId"):
            raise RecardError(f"no Link request; run: agent-lb recard start {email}")
        if not free or row.get("status") == "done":
            return row
        state = request_state(row["requestId"])
        if state["status"] != "approved":
            return save_recard(email, status=state["status"])
        save_recard(email, status="approved", approvedSeenAt=row.get("approvedSeenAt") or _now())
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
        row = save_recard(email, status="done", last4=last4, validUntil=valid_until, detail=None, cardOnAt=_now())
        card_on(
            email, f"{email}: Link card ending {last4} is on claude.ai for the {subscription.renews_on} renewal", row
        )
        return row


def watch(email: str) -> None:
    with held(email, "watch") as free:
        if not free:
            print(f"{email}: a watcher is already running", flush=True)
            return
        print(f"{_now()} {email}: watching the Link request every {POLL_S} s", flush=True)
        deadline = time.monotonic() + WATCH_S
        failures: dict[str, int] = {}
        asked_signin = False
        while time.monotonic() < deadline:
            row = load_recards().get(email) or {}
            if not row.get("requestId") or row.get("status") in ("done", *CLOSED):
                print(f"{_now()} {email}: watcher done ({row.get('status')})", flush=True)
                return
            try:
                status = request_state(row["requestId"])["status"]
            except LinkError as exc:
                failures["link"] = failures.get("link", 0) + 1
                if failures["link"] == 15:
                    save_recard(email, detail=f"watcher: {exc}")
                time.sleep(POLL_S * 2)
                continue
            failures.pop("link", None)
            if status in CLOSED:
                save_recard(email, status=status)
                card_on(email, f"{email}: the Link request was {status}; no card went on", {**row, "notice": None})
                return
            if status != "approved":
                if status != row.get("status"):
                    save_recard(email, status=status)
                time.sleep(POLL_S)
                continue
            try:
                finish(email)
                time.sleep(POLL_S)
            except RecardError as exc:
                if not asked_signin:
                    asked_signin = True
                    open_billing_window(email)
                    tell_factory(f"{email}: Link approved; waiting for claude.ai sign-in in the billing window ({exc})")
                time.sleep(POLL_S)
            except (BillingError, LinkError, CdpError) as exc:
                failures["card"] = failures.get("card", 0) + 1
                save_recard(email, detail=str(exc))
                if failures["card"] >= CARD_TRIES:
                    tell_factory(f"{email}: Link approved but the card did not go on after {CARD_TRIES} tries: {exc}")
                    return
                time.sleep(15)
        print(f"{_now()} {email}: watcher timed out; the 15-minute job starts a new one", flush=True)


def _next_month(day: str) -> str:
    current = date.fromisoformat(day)
    year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
    return date(year, month, min(current.day, calendar.monthrange(year, month)[1])).isoformat()


def _check_ahead(email: str, today: date, dry_run: bool) -> None:
    row = load_recards().get(email, {})
    if row.get("signinCheckedOn") == today.isoformat():
        return
    if dry_run:
        print(f"{email}: would check claude.ai sign-in a day ahead")
        return
    result = signed_in_ahead(email)
    save_recard(email, signinCheckedOn=today.isoformat(), signinAhead=result)
    if not result.get("signedIn"):
        tell_factory(
            f"{email}: its plan renews tomorrow and the Studio profile is not signed in to claude.ai "
            f"({result.get('how')}: {result.get('detail') or 'no session'}); no ask to Alex ahead of time",
            mode="note",
        )


def tick(dry_run: bool) -> None:
    today = date.today()
    recards = load_recards()
    for email, subscription in sorted(load_subscriptions().items()):
        if subscription.payment != "single-use":
            continue
        row = recards.get(email, {})
        days = (date.fromisoformat(subscription.renews_on) - today).days
        if days == 1:
            _check_ahead(email, today, dry_run)
            continue
        same_cycle = row.get("renewsOn") == subscription.renews_on
        if same_cycle and row.get("status") == "done":
            if days < 0 and not dry_run:
                subscriptions = load_subscriptions()
                subscriptions[email] = replace(subscription, renews_on=_next_month(subscription.renews_on))
                save_subscriptions(subscriptions)
            continue
        if same_cycle and row.get("status") in CLOSED:
            continue
        action = "watch" if same_cycle and row.get("status") in OPEN else "start" if -7 <= days <= 0 else None
        if action is None:
            continue
        if dry_run:
            print(f"{email}: would {action} (renews {subscription.renews_on})")
            continue
        try:
            if action == "start":
                start(email)
            elif row.get("notice"):
                spawn(email)
            else:
                finish(email)
        except (RecardError, LinkError, BillingError, CdpError) as exc:
            detail = str(exc)
            if load_recards().get(email, {}).get("detail") != detail:
                has_request = load_recards().get(email, {}).get("renewsOn") == subscription.renews_on
                save_recard(
                    email,
                    renewsOn=subscription.renews_on,
                    detail=detail,
                    **({} if has_request else {"status": "blocked", "requestId": None, "approvalUrl": None}),
                )
                tell_factory(f"{email}: recard for the {subscription.renews_on} renewal is stuck: {detail}")
