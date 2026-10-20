from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Subscription:
    plan: str
    renews_on: str
    payment: str
    amount_usd: float | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, str) or not self.plan.strip():
            raise ValueError("plan is required")
        if not isinstance(self.renews_on, str) or date.fromisoformat(self.renews_on).isoformat() != self.renews_on:
            raise ValueError("renews-on must be YYYY-MM-DD")
        if self.payment not in ("card", "single-use", "none"):
            raise ValueError("payment must be card, single-use or none")
        if self.amount_usd is not None and (
            type(self.amount_usd) not in (int, float) or not math.isfinite(self.amount_usd) or self.amount_usd < 0
        ):
            raise ValueError("amount must be a finite nonnegative number")
        if self.note is not None and not isinstance(self.note, str):
            raise ValueError("note must be text")

    def as_row(self) -> dict:
        return {
            "plan": self.plan,
            "renewsOn": self.renews_on,
            "payment": self.payment,
            "amountUsd": self.amount_usd,
            "note": self.note,
        }


def registry_path() -> Path:
    return Path(os.getenv("AGENT_LB_STATE_DIR", str(Path.home() / ".agent-lb/state"))) / "subscriptions.json"


def load_subscriptions() -> dict[str, Subscription]:
    try:
        data = json.loads(registry_path().read_text())
        if type(data["version"]) is not int or data["version"] != 1 or not isinstance(data["subscriptions"], dict):
            return {}
        subscriptions = {}
        for email, row in data["subscriptions"].items():
            if not email or email != email.strip().lower():
                return {}
            subscriptions[email] = Subscription(
                row["plan"], row["renewsOn"], row["payment"], row["amountUsd"], row["note"]
            )
        return subscriptions
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        return {}


def save_subscriptions(subscriptions: dict[str, Subscription]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "subscriptions": {email.strip().lower(): sub.as_row() for email, sub in subscriptions.items()},
    }
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".subscriptions-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def subscription_for(email: str) -> Subscription | None:
    return load_subscriptions().get(email.strip().lower())
