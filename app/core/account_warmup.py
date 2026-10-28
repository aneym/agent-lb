from datetime import UTC, datetime

from app.db.models import Account


def warmup_active(account: Account) -> bool:
    until = account.warmup_until
    return until is not None and until.replace(tzinfo=UTC) > datetime.now(UTC)
