from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Iterable
from enum import Enum
from typing import TypeVar

import aiohttp

from app.core.config.settings import Settings

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 60,
        success_threshold: int = 2,
    ) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_probe_in_flight = False
        self._lock = asyncio.Lock()
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.success_threshold = success_threshold

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time >= self.recovery_timeout_seconds
        ):
            return CircuitState.HALF_OPEN
        return self._state

    async def pre_call_check(self) -> bool:
        async with self._lock:
            current_state = self.state
            if current_state == CircuitState.OPEN:
                raise CircuitBreakerOpenError("Circuit breaker is OPEN")
            if current_state == CircuitState.HALF_OPEN:
                if self._half_open_probe_in_flight:
                    raise CircuitBreakerOpenError("Circuit breaker is HALF_OPEN — probe in flight")
                self._half_open_probe_in_flight = True
                return True
            return False

    async def release_half_open_probe(self) -> None:
        async with self._lock:
            self._half_open_probe_in_flight = False

    async def call(self, coro: Awaitable[T]) -> T:
        try:
            is_probe = await self.pre_call_check()
        except CircuitBreakerOpenError:
            if asyncio.iscoroutine(coro):
                coro.close()
            raise
        try:
            result = await coro
            await self._record_success()
            return result
        except Exception as exc:
            await self._record_failure(exc)
            raise
        finally:
            if is_probe:
                await self.release_half_open_probe()

    async def record_success(self) -> None:
        await self._record_success()

    async def record_failure(self, exc: Exception) -> None:
        await self._record_failure(exc)

    async def _record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self.state == CircuitState.CLOSED:
                if self._failure_count > 0:
                    self._failure_count = 0

    async def _record_failure(self, exc: Exception) -> None:
        if not _is_server_error(exc):
            return
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._success_count = 0
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN


def _is_server_error(exc: Exception) -> bool:
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status >= 500
    if isinstance(exc, (asyncio.TimeoutError, aiohttp.ServerTimeoutError, aiohttp.ClientConnectionError, OSError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code >= 500

    status = getattr(exc, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status >= 500

    return True


def _normalize_provider(provider: str | None) -> str:
    return (provider or "openai").strip().lower() or "openai"


_circuit_breaker: CircuitBreaker | None = None
_account_circuit_breakers: dict[str, dict[str, CircuitBreaker]] = {}


def get_circuit_breaker(settings: Settings | None = None) -> CircuitBreaker | None:
    global _circuit_breaker

    if settings is None:
        return _circuit_breaker

    enabled = getattr(settings, "circuit_breaker_enabled", False)
    if enabled and _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker(
            failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
            recovery_timeout_seconds=getattr(settings, "circuit_breaker_recovery_timeout_seconds", 60),
        )

    return _circuit_breaker if enabled else None


def get_circuit_breaker_for_account(
    account_id: str,
    settings: Settings,
    *,
    provider: str = "openai",
) -> CircuitBreaker | None:
    enabled = getattr(settings, "circuit_breaker_enabled", False)
    if not enabled:
        return None

    provider_breakers = _account_circuit_breakers.setdefault(_normalize_provider(provider), {})
    breaker = provider_breakers.get(account_id)
    if breaker is None:
        breaker = CircuitBreaker(
            failure_threshold=getattr(settings, "circuit_breaker_failure_threshold", 5),
            recovery_timeout_seconds=getattr(settings, "circuit_breaker_recovery_timeout_seconds", 60),
        )
        provider_breakers[account_id] = breaker
    return breaker


async def record_account_circuit_success(
    account_id: str,
    settings: Settings,
    *,
    provider: str = "openai",
) -> None:
    breaker = get_circuit_breaker_for_account(account_id, settings, provider=provider)
    if breaker is not None:
        await breaker.record_success()


async def record_account_circuit_failure(
    account_id: str,
    exc: Exception,
    settings: Settings,
    *,
    provider: str = "openai",
) -> None:
    breaker = get_circuit_breaker_for_account(account_id, settings, provider=provider)
    if breaker is not None:
        await breaker.record_failure(exc)


def is_account_circuit_open(
    account_id: str,
    settings: Settings,
    *,
    provider: str = "openai",
) -> bool:
    if not getattr(settings, "circuit_breaker_enabled", False):
        return False
    breaker = _account_circuit_breakers.get(_normalize_provider(provider), {}).get(account_id)
    return breaker is not None and breaker.state == CircuitState.OPEN


def clear_account_circuit_breakers(provider: str | None = None) -> None:
    if provider is None:
        _account_circuit_breakers.clear()
        return
    _account_circuit_breakers.pop(_normalize_provider(provider), None)


def are_all_account_circuit_breakers_open(
    *,
    provider: str | None = None,
    account_ids: Iterable[str] | None = None,
) -> bool:
    if provider is not None:
        scoped = _account_circuit_breakers.get(_normalize_provider(provider), {})
        if account_ids is not None:
            ids = set(account_ids)
            if not ids:
                return False
            return all(
                (cb := scoped.get(account_id)) is not None and cb.state == CircuitState.OPEN for account_id in ids
            )
        return bool(scoped) and all(cb.state == CircuitState.OPEN for cb in scoped.values())

    if account_ids is not None:
        ids = set(account_ids)
        if not ids:
            return False
        merged: dict[str, list[CircuitBreaker]] = {}
        for provider_breakers in _account_circuit_breakers.values():
            for account_id, cb in provider_breakers.items():
                merged.setdefault(account_id, []).append(cb)
        return all(
            bool(cbs := merged.get(account_id)) and all(cb.state == CircuitState.OPEN for cb in cbs)
            for account_id in ids
        )

    all_breakers = [cb for provider_breakers in _account_circuit_breakers.values() for cb in provider_breakers.values()]
    return bool(all_breakers) and all(cb.state == CircuitState.OPEN for cb in all_breakers)
