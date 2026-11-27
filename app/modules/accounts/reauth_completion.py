from __future__ import annotations

import asyncio
import logging

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import is_locally_owned
from app.modules.accounts.probes import ProbeVerdict
from app.modules.accounts.pulse_backend import default_openai_auth_check_sender, default_probe_sender
from app.modules.accounts.pulse_verdicts import apply_verdict
from app.modules.accounts.repository import AccountsRepository
from app.modules.federation.nudge import nudge_federation_mirror
from app.reauth_needs_you import enqueue_verified

logger = logging.getLogger(__name__)


async def verify_bound_reauth(account_id: str) -> int:
    try:
        return await _probe_saved_account(account_id)
    except Exception:
        logger.warning("Could not verify saved OAuth credentials account_id=%s", account_id, exc_info=True)
        return 0


async def _probe_saved_account(account_id: str) -> int:
    settings = get_settings()
    async with get_background_session() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        if account is None or not is_locally_owned(account, settings):
            return 0
        provider = normalize_provider_name(account.provider)
        if provider not in {ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME}:
            return 0
        encrypted = account.access_token_encrypted
        access_token = TokenEncryptor().decrypt(encrypted)
        await repo.release()
        try:
            async with asyncio.timeout(15):
                if provider == OPENAI_PROVIDER_NAME:
                    status = await default_openai_auth_check_sender(account, access_token)
                else:
                    status, _ = await default_probe_sender(account, access_token)
        except TimeoutError:
            return 0
        if status != 200:
            return status
        current = await repo.reload_by_id(account_id)
        if current is None or current.access_token_encrypted != encrypted or not is_locally_owned(current, settings):
            return 0
        await apply_verdict(
            repo,
            current,
            ProbeVerdict.HEALTHY,
            status,
            None,
            now=utcnow,
            failures={},
            record_failure=_ignore_failure,
            verified_reauth=True,
        )
    results = await asyncio.gather(
        nudge_federation_mirror(),
        asyncio.to_thread(enqueue_verified, account_id),
        return_exceptions=True,
    )
    for operation, result in zip(("edge mirror nudge", "Needs You withdrawal"), results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Failed to queue %s after reauth account_id=%s", operation, account_id, exc_info=result)
    return status


def _ignore_failure(account_id: str) -> None:
    del account_id
