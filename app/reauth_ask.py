from __future__ import annotations

import os
import subprocess
from datetime import date

from app.core import rails_ask
from app.core.browser_profiles import BrowserProfile, user_data_dir
from app.core.profile_theme import profile_name
from app.core.thread_delivery import send

ASKER = "factory"
DASHBOARD = os.getenv("AGENT_LB_DASHBOARD_URL", "https://studio.tailf266ac.ts.net:2455").rstrip("/")

WHY = {
    "stuck:password": "it stopped at a password prompt",
    "stuck:code": "it stopped at a verification code prompt",
    "stuck:passkey": "it stopped at a passkey prompt",
    "stuck:captcha": "it stopped at a captcha",
    "stuck:verify": "Google asked to verify it's you",
    "stuck:email_login": "claude.ai wants an emailed sign-in code",
    "stuck:signed_out": "the window is signed out of claude.ai",
    "stuck:google_signed_out": "the window is signed out of Google",
    "stuck:wrong_account": "the window is signed in as a different account",
    "stuck:identity_unknown": "it could not confirm which account the consent page was for",
    "stuck:repeat": "the same page kept coming back",
    "stuck:unknown": "it reached a page it does not know",
    "stuck:timeout": "the sign-in did not finish in three minutes",
    "bind_not_live": "automatic renewal waits for the next agent-lb restart",
    "lapsed:google": "the weekly check found its Google session signed out",
    "lapsed:claude": "the weekly check found its claude.ai session signed out",
}


def question(email: str, account_id: str, kind: str, profile: BrowserProfile) -> tuple[str, list[str]]:
    window = profile_name(user_data_dir(profile)) or profile.label or email
    lines = [
        f"Sign in to Claude as {email} in its Chrome window on Studio",
        f"agent-lb could not renew this sign-in by itself: {WHY.get(kind, 'it stopped')}.",
        f'The window is the Chrome profile "{window}". Once Claude shows you signed in there, agent-lb finishes on its own.',
        f"Or sign in from any browser: {DASHBOARD}/providers/{account_id}?signin=1 opens a fresh sign-in with a box for the code.",
    ]
    return "\n".join(lines), ["Signed in on Studio", "Pasted the code in the dashboard"]


def file_ask(entry: dict, email: str, account_id: str, kind: str, profile: BrowserProfile, *, dry_run: bool) -> str:
    if entry.get("askId"):
        return f"ask already open ({entry['askId']})"
    text, options = question(email, account_id, kind, profile)
    if dry_run:
        entry["askId"] = "dry-run"
        entry["askKind"] = kind
        return "would file ask: " + text.splitlines()[0] + f" ({WHY.get(kind, kind)})"
    landed = rails_ask.ask_owner(ASKER, text, options, floor="login")
    if not landed.get("landed"):
        return f"ask not filed: {landed.get('detail')}"
    entry["askId"] = landed["askId"]
    entry["askKind"] = kind
    return f"filed ask {landed['askId']}"


def answered(entry: dict) -> bool:
    ask_id = entry.get("askId")
    if not ask_id or ask_id == "dry-run" or rails_ask.ask_open(ASKER, ask_id):
        return False
    entry.pop("askId", None)
    entry.pop("askKind", None)
    return True


def withdraw_ask(entry: dict, *, dry_run: bool) -> str | None:
    ask_id = entry.pop("askId", None)
    entry.pop("askKind", None)
    if not ask_id:
        return None
    if dry_run or ask_id == "dry-run":
        return f"would withdraw ask {ask_id}"
    withdrawn = rails_ask.withdraw(ASKER, ask_id)
    return f"withdrew ask {ask_id}" if withdrawn else f"could not withdraw ask {ask_id}"


def note_error(entry: dict, message: str, *, dry_run: bool) -> str:
    today = date.today().isoformat()
    if entry.get("errorNotedOn") == today:
        return "error already noted today"
    if dry_run:
        return "would note the error to Factory"
    try:
        result = send(ASKER, f"agent-lb auto-reauth error: {message}", mode="queue")
        landed = result.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        landed = False
    if landed:
        entry["errorNotedOn"] = today
    return "noted the error to Factory" if landed else "could not reach Factory's thread"
