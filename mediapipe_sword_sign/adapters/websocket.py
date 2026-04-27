from __future__ import annotations

import asyncio
import hmac
from contextlib import suppress
from types import TracebackType
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mediapipe_sword_sign.types import GestureState


LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}


class WebSocketGestureBroadcaster:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        auth_token: str | None = None,
        allowed_origins: list[str] | None = None,
        max_clients: int | None = 8,
        allow_remote_unauthenticated: bool = False,
    ) -> None:
        if (
            not _is_local_bind_host(host)
            and not auth_token
            and not allow_remote_unauthenticated
        ):
            raise ValueError(
                "refusing to bind WebSocket broadcaster to a non-local host without auth_token"
            )
        self.host = host
        self.port = int(port)
        self.auth_token = auth_token
        self.allowed_origins = allowed_origins
        self.max_clients = max_clients
        self.clients: set[Any] = set()
        self._server: Any | None = None

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    async def start(self) -> None:
        if self._server is not None:
            return
        serve = _load_serve()
        options: dict[str, object] = {}
        if self.allowed_origins is not None:
            options["origins"] = self.allowed_origins
        self._server = await serve(self._handler, self.host, self.port, **options)

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
        if not self._is_authorized(websocket, path):
            await _close_websocket(websocket, 1008, "unauthorized")
            return

        if self.max_clients is not None and len(self.clients) >= self.max_clients:
            await _close_websocket(websocket, 1013, "too many clients")
            return

        self.clients.add(websocket)
        try:
            async for _message in websocket:
                pass
        finally:
            self.clients.discard(websocket)

    def _is_authorized(self, websocket: Any, path: str | None) -> bool:
        if self.auth_token is None:
            return True
        token = _extract_auth_token(websocket, path)
        return token is not None and hmac.compare_digest(token, self.auth_token)

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


def _is_local_bind_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in LOCAL_BIND_HOSTS or normalized.startswith("127.")


def _extract_auth_token(websocket: Any, path: str | None) -> str | None:
    headers = _request_headers(websocket)
    authorization = _header(headers, "Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()

    header_token = _header(headers, "X-Gesture-Token")
    if header_token:
        return header_token.strip()

    request_path = path or _request_path(websocket)
    query = parse_qs(urlsplit(request_path).query)
    tokens = query.get("token")
    return tokens[0] if tokens else None


def _request_headers(websocket: Any) -> Any:
    request = getattr(websocket, "request", None)
    if request is not None:
        return getattr(request, "headers", None)
    return getattr(websocket, "request_headers", None)


def _request_path(websocket: Any) -> str:
    request = getattr(websocket, "request", None)
    if request is not None:
        path = getattr(request, "path", None)
        if path is not None:
            return str(path)
    return str(getattr(websocket, "path", ""))


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    if get is None:
        return None
    value = get(name)
    return str(value) if value is not None else None


async def _close_websocket(websocket: Any, code: int, reason: str) -> None:
    close = getattr(websocket, "close", None)
    if close is None:
        return
    result = close(code=code, reason=reason)
    if asyncio.iscoroutine(result):
        with suppress(Exception):
            await result
