from __future__ import annotations

import asyncio
from contextlib import suppress
from types import TracebackType
from typing import Any

from mediapipe_sword_sign.types import GestureState


class WebSocketGestureBroadcaster:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = int(port)
        self.clients: set[Any] = set()
        self._server: Any | None = None

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    async def start(self) -> None:
        if self._server is not None:
            return
        serve = _load_serve()
        self._server = await serve(self._handler, self.host, self.port)

    async def stop(self) -> None:
        server = self._server
        if server is None:
            return

        server.close()
        await server.wait_closed()
        self._server = None

        for client in list(self.clients):
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    with suppress(Exception):
                        await result
        self.clients.clear()

    async def publish(self, state: GestureState) -> None:
        if not self.clients:
            return

        message = state.to_json()
        clients = list(self.clients)
        results = await asyncio.gather(
            *(client.send(message) for client in clients),
            return_exceptions=True,
        )

        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                self.clients.discard(client)

    async def _handler(self, websocket: Any, path: str | None = None) -> None:
        self.clients.add(websocket)
        try:
            async for _message in websocket:
                pass
        finally:
            self.clients.discard(websocket)

    async def __aenter__(self) -> "WebSocketGestureBroadcaster":
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()


def _load_serve():
    try:
        from websockets.asyncio.server import serve
    except ImportError:
        try:
            from websockets.legacy.server import serve
        except ImportError as exc:
            raise RuntimeError(
                "WebSocket support requires the 'websockets' package. "
                "Install project dependencies before using this adapter."
            ) from exc
    return serve
