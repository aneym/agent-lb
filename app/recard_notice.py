from __future__ import annotations

import os
import subprocess
from datetime import UTC, date, datetime

from app.core import rails_ask
from app.core.thread_delivery import send

ASKER = os.getenv("AGENT_LB_RECARD_ASKER", "factory")
CHAT = os.getenv("AGENT_LB_RECARD_CHAT", "rails")


def quiet() -> bool:
    return bool(os.getenv("AGENT_LB_RECARD_QUIET"))


def _deliver(target: str, message: str, mode: str) -> dict:
    try:
        result = send(target, message, mode=mode)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"landed": False, "detail": type(exc).__name__}
    if result.returncode != 0:
        return {
            "landed": False,
            "detail": (result.stderr or result.stdout).strip()[:200] or f"exit {result.returncode}",
        }
    return {"landed": True}


def tell_factory(message: str, *, mode: str = "queue") -> dict:
    print(message, flush=True)
    return {"landed": False, "detail": "quiet"} if quiet() else _deliver(ASKER, message, mode)


def approval_ask(
    email: str, plan: str, total: float, renews_on: str, url: str, signed_in: bool
) -> tuple[str, list[str]]:
    day = date.fromisoformat(renews_on).strftime("%b %-d")
    lines = [
        f"Approve the ${total:.2f} Link card for the {plan} renewal of {email} ({day})",
        f"Approve it here: {url}",
        "The card goes on the claude.ai billing page by itself within seconds of your approval.",
    ]
    if not signed_in:
        lines.append(f"The billing window on Studio is not signed in as {email}; sign in there too.")
    return "\n".join(lines), ["Approved in Link", "Skip this renewal"]


def chat_line(email: str, plan: str, total: float, url: str, signed_in: bool) -> str:
    line = (
        f"Pass to Alex now, one line: approve the ${total:.2f} Link card for the {plan} renewal of {email}: {url} "
        "(also in his Needs you; the card goes on by itself within seconds of approval)"
    )
    return line + ("; the Studio billing window also needs his claude.ai sign-in." if not signed_in else ".")


def ask_for_approval(email: str, plan: str, total: float, renews_on: str, url: str, window: dict) -> dict:
    signed_in = window.get("signedIn") == email
    notice: dict = {"at": datetime.now(UTC).isoformat(timespec="seconds")}
    if quiet():
        notice["ask"] = notice["chat"] = {"landed": False, "detail": "quiet"}
        return notice
    question, options = approval_ask(email, plan, total, renews_on, url, signed_in)
    notice["ask"] = rails_ask.ask_owner(ASKER, question, options)
    notice["chat"] = _deliver(CHAT, chat_line(email, plan, total, url, signed_in), "queue")
    return notice


def card_on(email: str, message: str, row: dict) -> None:
    tell_factory(message)
    if row.get("askId") and not quiet():
        rails_ask.withdraw(ASKER, row["askId"])
    if row.get("notice") and not quiet():
        _deliver(CHAT, f"Pass to Alex: {message}.", "queue")
