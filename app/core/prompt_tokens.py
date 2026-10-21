from __future__ import annotations

SESSION_KEY_SQL = "coalesce(nullif(client_session_id,''),nullif(session_id,''))"


def prompt_split(
    provider: str,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    cache_read_tokens: int | None,
    cache_creation_tokens: int | None,
) -> tuple[int, int, int, int]:
    read = cache_read_tokens if cache_read_tokens is not None else (cached_input_tokens or 0)
    write = cache_creation_tokens or 0
    raw = input_tokens or 0
    uncached = raw if provider == "anthropic" else max(raw - read, 0)
    return uncached, read, write, uncached + read + write
