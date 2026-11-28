from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.config.settings import get_settings
from app.core.providers.gemini_models import GEMINI_PRIMARY_MODELS, GeminiModel
from app.core.usage.pricing import UsageTokens, calculate_cost_from_usage, get_pricing_for_model
from app.modules.proxy.gemini_errors import GeminiGatewayError
from app.modules.proxy.gemini_request import (
    apply_thinking_level,
    gemini_session_id,
    is_antigravity_subagent_request,
)
from app.modules.proxy.gemini_stream import GeminiUsage

logger = logging.getLogger(__name__)

_MAX_TRACKED_SESSIONS = 2048


@dataclass(slots=True)
class _SessionRunState:
    last_activity_at: float
    requests: int = 0
    usd: float = 0.0
    consecutive_errors: int = 0
    blocked_until: float = 0.0


def _estimate_usd(model: str, usage: GeminiUsage | None) -> float:
    if usage is None:
        return 0.0
    resolved = get_pricing_for_model(model)
    if resolved is None:
        return 0.0
    _, price = resolved
    tokens = UsageTokens(
        input_tokens=float(usage.input_tokens),
        output_tokens=float(usage.output_tokens),
        cached_input_tokens=float(usage.cached_input_tokens),
    )
    cost = calculate_cost_from_usage(tokens, price)
    return float(cost) if cost is not None else 0.0


class GeminiRunawayGuard:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRunState] = {}
        self._subagent_sessions: dict[str, float] = {}

    def remember_subagent(self, session_id: str | None, detected: bool) -> bool:
        if not session_id:
            return detected
        now = time.time()
        if detected:
            if len(self._subagent_sessions) >= _MAX_TRACKED_SESSIONS:
                cutoff = now - 3600.0
                self._subagent_sessions = {k: v for k, v in self._subagent_sessions.items() if v >= cutoff}
            self._subagent_sessions[session_id] = now
            return True
        last = self._subagent_sessions.get(session_id)
        if last is not None and (now - last) < 7200.0:
            self._subagent_sessions[session_id] = now
            return True
        return False

    def prepare(self, body: dict[str, Any], choice: GeminiModel) -> str:
        session_id = gemini_session_id(body)
        subagent = self.remember_subagent(session_id, is_antigravity_subagent_request(body))
        effort = apply_thinking_level(
            body,
            choice,
            subagent=subagent,
            subagent_default_effort=get_settings().gemini_subagent_default_effort,
        )
        if not choice.auxiliary:
            self.check(session_id=session_id, model=choice.canonical_model)
        return effort

    def _prune(self, now: float, idle_reset: float) -> None:
        if len(self._sessions) < _MAX_TRACKED_SESSIONS:
            return
        stale_before = now - max(idle_reset * 4.0, 900.0)
        self._sessions = {
            sid: state
            for sid, state in self._sessions.items()
            if state.last_activity_at >= stale_before or state.blocked_until > now
        }

    def _state(self, session_id: str, now: float, idle_reset: float) -> _SessionRunState:
        state = self._sessions.get(session_id)
        if state is None:
            self._prune(now, idle_reset)
            state = _SessionRunState(last_activity_at=now)
            self._sessions[session_id] = state
            return state
        if state.blocked_until > 0.0 and now >= state.blocked_until:
            state.requests = 0
            state.usd = 0.0
            state.consecutive_errors = 0
            state.blocked_until = 0.0
        elif state.blocked_until <= now and (now - state.last_activity_at) >= idle_reset:
            state.requests = 0
            state.usd = 0.0
            state.consecutive_errors = 0
        state.last_activity_at = now
        return state

    def check(self, *, session_id: str | None, model: str) -> None:
        if not session_id or model not in GEMINI_PRIMARY_MODELS:
            return
        settings = get_settings()
        if not settings.gemini_runaway_guard_enabled:
            return
        now = time.time()
        state = self._state(session_id, now, settings.gemini_runaway_idle_reset_seconds)
        if state.blocked_until > now:
            raise GeminiGatewayError(
                429,
                "Runaway loop breaker paused this Gemini session after excessive continuous requests; retry after cooldown.",
                code="runaway_loop_breaker",
                retry_at=state.blocked_until,
            )
        reason: str | None = None
        if state.requests >= settings.gemini_runaway_max_requests:
            reason = f"requests={state.requests}>={settings.gemini_runaway_max_requests}"
        elif state.usd >= settings.gemini_runaway_max_usd:
            reason = f"usd={state.usd:.2f}>={settings.gemini_runaway_max_usd:.2f}"
        elif state.consecutive_errors >= settings.gemini_runaway_max_consecutive_errors:
            reason = f"consecutive_errors={state.consecutive_errors}>={settings.gemini_runaway_max_consecutive_errors}"
        if reason is not None:
            state.blocked_until = now + settings.gemini_runaway_cooldown_seconds
            logger.warning(
                "gemini_runaway_loop_breaker_tripped session_id=%s model=%s reason=%s cooldown_seconds=%s",
                session_id,
                model,
                reason,
                settings.gemini_runaway_cooldown_seconds,
            )
            raise GeminiGatewayError(
                429,
                f"Runaway loop breaker paused this Gemini session ({reason}); retry after cooldown.",
                code="runaway_loop_breaker",
                retry_at=state.blocked_until,
            )
        state.requests += 1

    def record(
        self,
        *,
        session_id: str | None,
        model: str,
        usage: GeminiUsage | None = None,
        failed: bool = False,
    ) -> None:
        if not session_id or model not in GEMINI_PRIMARY_MODELS:
            return
        settings = get_settings()
        if not settings.gemini_runaway_guard_enabled:
            return
        now = time.time()
        state = self._state(session_id, now, settings.gemini_runaway_idle_reset_seconds)
        if usage is not None:
            state.usd += _estimate_usd(model, usage)
        if failed:
            state.consecutive_errors += 1
        else:
            state.consecutive_errors = 0
