from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from app.core.browser_profiles import profile_for, registry_path
from app.lb_service import ServiceError, request, retrying
from app.reauth_ask import answered, file_ask, note_error, withdraw_ask
from app.reauth_auto import reauth_account
from app.reauth_enroll import load_enrolled, refusal
from app.reauth_keepalive import due, visit

DEFAULT_BASE_URL = "http://127.0.0.1:2455"
TRY_GAP_S = 6 * 3600
STUCK_HOLD_S = 24 * 3600
TRIES_PER_DAY = 3


class Job:
    def __init__(self, *, dry_run: bool, now_ids: tuple[str, ...] = (), keepalive: bool = True) -> None:
        self.dry_run = dry_run
        self.now_ids = now_ids
        self.keepalive = keepalive
        self.base = os.getenv("AGENT_LB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        root = registry_path().parent
        self.state_path = root / ("auto-reauth-state.dry.json" if dry_run else "auto-reauth-state.json")
        self.log_path = root / "auto-reauth.log"
        self.state: dict[str, dict] = {}

    def log(self, account_id: str, email: str, result: str, detail: str = "", *, quiet_repeat: bool = False) -> None:
        entry = self.state.setdefault(account_id, {})
        key = f"{result}|{detail}"
        line = f"{account_id[:8]} {email} {result}" + (f" ({detail})" if detail else "")
        print(line, flush=True)
        if quiet_repeat and entry.get("lastLogged") == key:
            return
        entry["lastLogged"] = key
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with self.log_path.open("a") as stream:
            stream.write(f"{stamp} {'dry ' if self.dry_run else ''}{line}\n")

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text())
            self.state = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.state = {}

    def _save(self) -> None:
        fd, temporary = tempfile.mkstemp(dir=self.state_path.parent, prefix=".auto-reauth-state-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.state, stream, indent=1, sort_keys=True)
                stream.write("\n")
            os.replace(temporary, self.state_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _may_try(self, entry: dict, now: float) -> str | None:
        tries = [moment for moment in entry.get("tries", []) if now - moment < 86400]
        entry["tries"] = tries
        if len(tries) >= TRIES_PER_DAY:
            return f"{TRIES_PER_DAY} tries in the last day"
        if tries and now - tries[-1] < TRY_GAP_S:
            return "tried in the last 6 h"
        if entry.get("stuckAt") and now - entry["stuckAt"] < STUCK_HOLD_S:
            return "stuck in the last 24 h; waiting for the ask"
        return None

    def _reauth(self, account: dict, profile) -> None:
        account_id, email = account["accountId"], profile.email
        entry = self.state.setdefault(account_id, {})
        now = time.time()
        forced = account_id in self.now_ids
        if not self.dry_run and answered(entry):
            entry.pop("stuckAt", None)
            entry["tries"] = []
            self.log(account_id, email, "ask_answered", "retrying now")
        hold = None if forced else self._may_try(entry, now)
        if hold:
            self.log(account_id, email, "backoff", hold, quiet_repeat=True)
            return
        outcome = reauth_account(self.base, account, profile, observe_only=self.dry_run)
        entry.setdefault("tries", []).append(now)
        entry["lastResult"] = outcome.result
        clicks = f"; clicks {','.join(outcome.clicks)}" if outcome.clicks else "; no clicks"
        bound = "bound" if outcome.bound else "unbound"
        self.log(account_id, email, outcome.result, f"{outcome.detail}; {bound}{clicks}".strip("; "))
        if outcome.result == "active":
            entry.pop("stuckAt", None)
            if note := withdraw_ask(entry, dry_run=self.dry_run):
                self.log(account_id, email, "ask", note)
        elif outcome.result.startswith("stuck:") or outcome.result == "bind_not_live":
            entry["stuckAt"] = now
            if account.get("status") == "reauth_required" or (forced and self.dry_run):
                self.log(
                    account_id,
                    email,
                    "ask",
                    file_ask(entry, email, account_id, outcome.result, profile, dry_run=self.dry_run),
                )
        elif outcome.result == "error":
            self.log(
                account_id, email, "error_note", note_error(entry, f"{email}: {outcome.detail}", dry_run=self.dry_run)
            )

    def _keepalive(self, account: dict, profile) -> None:
        account_id = account["accountId"]
        entry = self.state.setdefault(account_id, {})
        if not due(profile.email, entry.get("keepaliveAt")):
            return
        if self.dry_run:
            self.log(account_id, profile.email, "keepalive", "would visit Google and claude.ai with no clicks")
            return
        result, detail = visit(profile)
        if result != "skipped":
            entry["keepaliveAt"] = time.time()
        self.log(account_id, profile.email, f"keepalive:{result}", detail)
        if result == "ok" and str(entry.get("askKind", "")).startswith("lapsed:"):
            self.log(account_id, profile.email, "ask", withdraw_ask(entry, dry_run=False) or "")
        elif result.startswith("lapsed:"):
            self.log(
                account_id,
                profile.email,
                "ask",
                file_ask(entry, profile.email, account_id, result, profile, dry_run=False),
            )

    def _account(self, account: dict, enrolled: dict) -> None:
        account_id, email = str(account.get("accountId")), str(account.get("email", "")).lower()
        needs = account.get("status") == "reauth_required" or account_id in self.now_ids
        reason = refusal(account)
        if account_id not in enrolled or reason:
            if needs:
                self.log(account_id, email, "skipped", reason or "not enrolled", quiet_repeat=True)
            return
        profile = profile_for(email)
        if profile is None:
            self.log(account_id, email, "no_profile", "enrolled but no browser profile on this host", quiet_repeat=True)
            return
        entry = self.state.setdefault(account_id, {})
        if needs:
            self._reauth(account, profile)
        elif entry.get("askId") and not str(entry.get("askKind", "")).startswith("lapsed:"):
            entry.pop("stuckAt", None)
            self.log(account_id, email, "active", withdraw_ask(entry, dry_run=self.dry_run) or "")
        if self.keepalive:
            self._keepalive(account, profile)

    def run(self) -> int:
        lock_path = registry_path().parent / "auto-reauth.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("another reauth-auto run is going", flush=True)
                return 0
            self._load()
            try:
                payload = retrying(lambda: request(self.base, "/api/accounts"), "accounts")
            except ServiceError as exc:
                print(f"agent-lb unreachable: {exc}", flush=True)
                return 1
            accounts = payload.get("accounts") if isinstance(payload, dict) else None
            if not isinstance(accounts, list):
                print("agent-lb returned a malformed accounts list", flush=True)
                return 1
            enrolled = load_enrolled()
            try:
                for account in sorted(accounts, key=lambda row: str(row.get("accountId"))):
                    if isinstance(account, dict) and account.get("provider") != "openai":
                        self._account(account, enrolled)
            finally:
                self._save()
            return 0
