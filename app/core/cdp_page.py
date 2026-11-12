from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import ProxyHandler, build_opener

from websockets.sync.client import connect


class CdpError(Exception):
    pass


def _devtools(port: int, path: str) -> Any:
    try:
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise CdpError(f"no Chrome DevTools endpoint on port {port}") from None


def cdp_up(port: int) -> bool:
    try:
        return isinstance(_devtools(port, "/json/version"), dict)
    except CdpError:
        return False


def page_targets(port: int) -> list[dict]:
    targets = _devtools(port, "/json/list")
    return [target for target in targets if isinstance(target, dict) and target.get("type") == "page"]


class CdpPage:
    def __init__(
        self,
        port: int,
        url: str | None = None,
        *,
        timeout: float = 20,
        target_id: str | None = None,
        background: bool = False,
        new_window: bool = False,
    ) -> None:
        try:
            endpoint = _devtools(port, "/json/version")["webSocketDebuggerUrl"]
        except (KeyError, TypeError):
            raise CdpError(f"no Chrome DevTools endpoint on port {port}") from None
        self._socket = connect(endpoint, max_size=None, open_timeout=timeout)
        self._timeout = timeout
        self._next_id = 0
        self.frame_sessions: list[str] = []
        self.owned = target_id is None
        if target_id is None:
            params: dict[str, Any] = {"url": url or "about:blank"}
            if background:
                params["background"] = True
            if new_window:
                params["newWindow"] = True
            target_id = self.call("Target.createTarget", params)["targetId"]
        self.target_id = target_id
        self.session = self.call("Target.attachToTarget", {"targetId": self.target_id, "flatten": True})["sessionId"]
        self._auto_attach(self.session)

    def navigate(self, url: str) -> None:
        self.frame_sessions.clear()
        self.call("Page.navigate", {"url": url}, session=self.session)

    def _auto_attach(self, session: str) -> None:
        self.call(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
            session=session,
        )

    def call(self, method: str, params: dict | None = None, *, session: str | None = None) -> dict:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        self._socket.send(json.dumps(message))
        deadline = time.monotonic() + self._timeout
        attached: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError(f"{method} timed out")
            reply = json.loads(self._socket.recv(timeout=remaining))
            if reply.get("method") == "Target.attachedToTarget":
                info = reply["params"]["targetInfo"]
                if info.get("type") == "iframe":
                    attached.append(reply["params"]["sessionId"])
                continue
            if reply.get("id") != self._next_id:
                continue
            if "error" in reply:
                raise CdpError(f"{method}: {reply['error'].get('message', 'failed')}")
            for child in attached:
                self.frame_sessions.append(child)
                self._auto_attach(child)
            return reply.get("result", {})

    def evaluate(self, expression: str, *, session: str | None = None) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
            session=session or self.session,
        )
        if "exceptionDetails" in result:
            raise CdpError("page script failed")
        return result.get("result", {}).get("value")

    def insert_text(self, text: str, *, session: str) -> None:
        self.call("Input.insertText", {"text": text}, session=session)

    def wait_for(self, expression: str, *, seconds: float, session: str | None = None) -> Any:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                value = self.evaluate(expression, session=session)
            except CdpError:
                value = None
            if value:
                return value
            time.sleep(0.5)
        return None

    def detach(self) -> None:
        self._socket.close()

    def close(self) -> None:
        try:
            if self.owned:
                self.call("Target.closeTarget", {"targetId": self.target_id})
        except CdpError:
            pass
        finally:
            self._socket.close()
