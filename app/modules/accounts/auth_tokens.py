from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from app.core.auth import token_expiry_epoch_ms
from app.core.auth.refresh import (
    should_refresh,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import (
    OPENAI_PROVIDER_NAME,
    ProviderLookupError,
    get_provider,
)
from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account

_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS = 300

_ACCESS_TOKEN_SERVING_MARGIN_SECONDS = 300


def _exchange_timeout_seconds() -> float:
    settings = get_settings()
    return max(settings.oauth_timeout_seconds, settings.token_refresh_timeout_seconds, 30.0)


def _chatgpt_account_id_from_id_token(id_token: str) -> str | None:
    return get_provider(OPENAI_PROVIDER_NAME).account_metadata_from_id_token(id_token).account_id


def _expires_within(expires_ms: int, *, margin_seconds: float, now: datetime | None = None) -> bool:

    current = to_utc_naive(now) if now is not None else utcnow()
    threshold_ms = naive_utc_to_epoch(current) * 1000 + (margin_seconds * 1000)
    return expires_ms <= threshold_ms


def _access_token_needs_refresh(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return False
    expires_ms = _stored_expiry_ms(account, access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_REFRESH_MARGIN_SECONDS, now=now)


def access_token_hard_expired(
    encryptor: TokenEncryptor,
    account: Account,
    *,
    now: datetime | None = None,
) -> bool:

    if not account.access_token_encrypted:
        return True
    try:
        access_token = encryptor.decrypt(account.access_token_encrypted)
    except Exception:
        return True
    expires_ms = _stored_expiry_ms(account, access_token)
    if expires_ms is None:
        return False
    return _expires_within(expires_ms, margin_seconds=_ACCESS_TOKEN_SERVING_MARGIN_SECONDS, now=now)


def _account_needs_refresh(encryptor: TokenEncryptor, account: Account, *, now: datetime | None = None) -> bool:
    current = to_utc_naive(now) if now is not None else utcnow()
    if _access_token_needs_refresh(encryptor, account, now=current):
        return True
    try:
        provider = get_provider(account.provider)
    except ProviderLookupError:
        return should_refresh(account.last_refresh, now=current)
    interval_seconds = provider.access_token_refresh_interval_seconds
    if interval_seconds is not None:
        last = to_utc_naive(account.last_refresh)
        return current - last > timedelta(seconds=interval_seconds)
    return should_refresh(account.last_refresh, now=current)


def _snapshot(repo: object, account: Account) -> Account:

    if not hasattr(repo, "session"):
        return account
    return Account(**{column.key: getattr(account, column.key) for column in Account.__table__.columns})


def _refresh_token_material_changed(
    encryptor: TokenEncryptor,
    latest_refresh_token_encrypted: bytes,
    current_refresh_token_encrypted: bytes,
) -> bool:
    return _refresh_token_material_fingerprint(
        encryptor,
        latest_refresh_token_encrypted,
    ) != _refresh_token_material_fingerprint(
        encryptor,
        current_refresh_token_encrypted,
    )


def _refresh_token_material_fingerprint(encryptor: TokenEncryptor, refresh_token_encrypted: bytes) -> str:
    try:
        material = encryptor.decrypt(refresh_token_encrypted).encode("utf-8")
    except Exception:
        material = refresh_token_encrypted
    return sha256(material).hexdigest()


def _clean_optional(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


async def _call_with_supported_optional_kwargs(
    func: Callable[..., Awaitable[Any]],
    /,
    *args: Any,
    optional_kwargs: Mapping[str, Any],
    **required_kwargs: Any,
) -> Any:
    kwargs = dict(required_kwargs)
    kwargs.update(optional_kwargs)
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    accepts_var_keyword = signature is not None and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if signature is not None and not accepts_var_keyword:
        for name in optional_kwargs:
            if name not in signature.parameters:
                kwargs.pop(name, None)
    return await func(*args, **kwargs)


def expiry_datetime(
    access_token: str, expires_in: int | None = None, *, now: datetime | None = None
) -> datetime | None:
    expires_ms = token_expiry_epoch_ms(access_token)
    if expires_ms is not None:
        return datetime.utcfromtimestamp(expires_ms / 1000)
    if expires_in is not None and expires_in > 0:
        return (now or utcnow()) + timedelta(seconds=expires_in)
    return None


def _stored_expiry_ms(account: Account, access_token: str) -> int | None:
    if account.access_expires_at is not None:
        return naive_utc_to_epoch(to_utc_naive(account.access_expires_at)) * 1000
    return token_expiry_epoch_ms(access_token)
