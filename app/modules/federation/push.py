"""Send selected locally owned access tokens to trusted federation receivers."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.federation.peer_client import AiohttpFederationPeerClient, FederationPeerClient
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationMirrorAccount, FederationPushRequest, FederationPushResponse
from app.modules.federation.service import _account_expiry_ms

logger = logging.getLogger(__name__)
_RepoFactory = Callable[[], AbstractAsyncContextManager[FederationRepository]]


@dataclass(frozen=True)
class PushTarget:
    name: str
    url: str
    accounts: tuple[str, ...]


def load_push_targets(path: Path) -> list[PushTarget]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict) or not isinstance(raw.get("targets"), list):
            raise ValueError("targets must be a list")
        targets = []
        for entry in raw["targets"]:
            if not isinstance(entry, dict):
                raise ValueError("target must be an object")
            name, url, accounts = entry["name"], entry["url"], entry["accounts"]
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(url, str)
                or not url.startswith(("http://", "https://"))
                or not isinstance(accounts, list)
                or any(not isinstance(item, str) or not item for item in accounts)
            ):
                raise ValueError("invalid target")
            targets.append(PushTarget(name=name, url=url.rstrip("/"), accounts=tuple(accounts)))
        if len({target.name for target in targets}) != len(targets):
            raise ValueError("duplicate target name")
        return targets
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("Invalid federation push targets configuration")
        return []


async def build_push_request(
    repository: FederationRepository,
    target: PushTarget,
    *,
    local_instance_id: str,
    encryptor: TokenEncryptor,
) -> tuple[FederationPushRequest, list[str]]:
    owned = await repository.list_locally_owned_accounts(local_instance_id)
    by_id = {account.id: account for account in owned}
    by_email = {account.email.casefold(): account for account in owned}
    selected = []
    unmatched = []
    seen: set[str] = set()
    for entry in target.accounts:
        account = by_id.get(entry) or by_email.get(entry.casefold())
        if account is None:
            unmatched.append(entry)
        elif account.id not in seen:
            selected.append(account)
            seen.add(account.id)
    accounts = []
    for account in selected:
        token = encryptor.decrypt(account.access_token_encrypted)
        accounts.append(
            FederationMirrorAccount(
                account_id=account.id,
                provider=account.provider,
                alias=account.alias,
                email=account.email,
                status=account.status.value,
                plan_type=account.plan_type,
                chatgpt_account_id=account.chatgpt_account_id,
                access_token=token,
                expires_at_ms=_account_expiry_ms(account, token),
            )
        )
    return FederationPushRequest(instance_id=local_instance_id, accounts=accounts), unmatched


@dataclass
class FederationPushScheduler:
    interval_seconds: int
    path: Path
    local_instance_id: str
    repo_factory: _RepoFactory
    peer_client: FederationPeerClient = field(default_factory=AiohttpFederationPeerClient)
    encryptor: TokenEncryptor = field(default_factory=TokenEncryptor)
    sleep: Callable[[float], Awaitable[None]] = field(default_factory=lambda: asyncio.sleep)
    last_results: dict[str, dict] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _failures: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _retry_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            await self.push_once(respect_backoff=True)
            await self.sleep(self.interval_seconds)

    async def push_once(self, *, respect_backoff: bool = False) -> dict[str, FederationPushResponse | Exception]:
        results: dict[str, FederationPushResponse | Exception] = {}
        for target in load_push_targets(self.path):
            if respect_backoff and time.monotonic() < self._retry_at.get(target.name, 0):
                continue
            try:
                async with self.repo_factory() as repo:
                    request, unmatched = await build_push_request(
                        repo, target, local_instance_id=self.local_instance_id, encryptor=self.encryptor
                    )
                response = await self.peer_client.push_accounts(url=target.url, request=request)
                async with self.repo_factory() as repo:
                    await repo.upsert_usage_report(target.name, response.usage, reported_at=utcnow())
            except Exception as exc:
                # Exceptions from HTTP libraries can include the serialized request.
                # Never expose their contents (or credentials) in logs or CLI output.
                self._failures[target.name] = self._failures.get(target.name, 0) + 1
                backoff = min(1800, 30 * 2 ** min(self._failures[target.name] - 1, 6))
                self._retry_at[target.name] = time.monotonic() + backoff
                self.last_results[target.name] = {"error": type(exc).__name__, "retry_in_seconds": backoff}
                logger.warning("Federation push failed target=%s error_type=%s", target.name, type(exc).__name__)
                results[target.name] = exc
                continue
            self._failures.pop(target.name, None)
            self._retry_at.pop(target.name, None)
            self.last_results[target.name] = {
                "accepted": response.accepted,
                "skipped": [skip.model_dump() for skip in response.skipped],
                "removed": response.removed,
                "unmatched": unmatched,
                "success_at": utcnow(),
            }
            results[target.name] = response
        return results


@asynccontextmanager
async def _default_repo_factory() -> AsyncIterator[FederationRepository]:
    async with get_background_session() as session:
        yield FederationRepository(session)


def build_federation_push_scheduler() -> FederationPushScheduler:
    settings = get_settings()
    return FederationPushScheduler(
        interval_seconds=settings.federation_push_interval_seconds,
        path=settings.federation_push_path,
        local_instance_id=settings.local_instance_id,
        repo_factory=_default_repo_factory,
    )


async def _run_cli(command: str) -> int:
    scheduler = build_federation_push_scheduler()
    targets = load_push_targets(scheduler.path)
    if command == "status":
        for target in targets:
            async with scheduler.repo_factory() as repo:
                request, unmatched = await build_push_request(
                    repo, target, local_instance_id=scheduler.local_instance_id, encryptor=scheduler.encryptor
                )
            print(f"{target.name} {target.url}")
            for account in request.accounts:
                print(f"  {account.email} {account.provider} {account.account_id}")
            for entry in unmatched:
                print(f"  unmatched: {entry}")
        return 0
    results = await scheduler.push_once()
    failed = False
    for target in targets:
        result = results[target.name]
        print(f"{target.name} {target.url}")
        if isinstance(result, Exception):
            failed = True
            # Only the structured HTTP status is safe to print: exception messages
            # from HTTP clients can contain request payloads and access tokens.
            status_code = getattr(result, "status_code", None)
            status = f" status={status_code}" if isinstance(status_code, int) else ""
            print(f"  failed: {type(result).__name__}{status}")
        else:
            print(f"  accepted: {', '.join(result.accepted)}")
            for skip in result.skipped:
                print(f"  skipped: {skip.account_id} ({skip.reason})")
            print(f"  removed: {', '.join(result.removed)}")
    return int(failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Federation account push")
    parser.add_argument("command", choices=("status", "once"))
    args = parser.parse_args(argv)
    return asyncio.run(_run_cli(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
