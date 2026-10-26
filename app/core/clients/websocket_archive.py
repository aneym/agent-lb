from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.clients.failure_observation import observe_status, observe_text
from app.core.conversation_archive import archive_bytes, archive_text
from app.core.upstream_proxy import ResolvedUpstreamRoute

if TYPE_CHECKING:
    from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket, UpstreamWebSocketMessage


class ArchivingResponsesWebSocket:
    def __init__(
        self,
        wrapped: UpstreamResponsesWebSocket,
        *,
        url: str,
        headers: dict[str, str],
        account_id: str | None,
        route: ResolvedUpstreamRoute | None = None,
        fallback_used: bool | None = None,
        direct_egress: bool = False,
    ) -> None:
        self._wrapped = wrapped
        self._url = url
        self._headers = headers
        self._account_id = account_id
        self.upstream_proxy_route_mode = route.mode if route is not None else ("direct" if direct_egress else None)
        self.upstream_proxy_pool_id = route.pool_id if route is not None else None
        self.upstream_proxy_endpoint_id = route.endpoint_id if route is not None else None
        self.upstream_proxy_fallback_used = fallback_used if route is not None else None

    async def send_text(self, text: str) -> None:
        archive_text(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            text=text,
            account_id=self._account_id,
            method="GET",
            url=self._url,
            headers=self._headers,
            extra={"frame_type": "text"},
        )
        observe_status(101)
        await self._wrapped.send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        archive_bytes(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            data=data,
            account_id=self._account_id,
            method="GET",
            url=self._url,
            headers=self._headers,
            extra={"frame_type": "binary"},
        )
        await self._wrapped.send_bytes(data)

    async def receive(self) -> UpstreamWebSocketMessage:
        message = await self._wrapped.receive()
        if message.kind == "text" and message.text is not None:
            observe_text(message.text)
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.text,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "text"},
            )
        elif message.kind == "binary" and message.data is not None:
            archive_bytes(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                data=message.data,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "binary"},
            )
        else:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.error or "",
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": message.kind, "close_code": message.close_code},
            )
        return message

    async def close(self) -> None:
        await self._wrapped.close()

    def response_header(self, name: str) -> str | None:
        return self._wrapped.response_header(name)
