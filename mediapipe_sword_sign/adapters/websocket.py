from __future__ import annotations

import asyncio
import hmac
from contextlib import suppress
from types import TracebackType
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit

from mediapipe_sword_sign.payloads import gesture_state_json
from mediapipe_sword_sign.temporal import GestureHoldState
from mediapipe_sword_sign.types import GestureState


LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_MAX_CLIENTS = 8
DEFAULT_MAX_MESSAGE_BYTES = 4096
DEFAULT_MAX_QUEUE = 4


class WebSocketGestureBroadcaster:
    """Compatibility broadcaster for raw GestureState JSON.

    Camera Hub integrations should publish topic envelopes through
    WebSocketTopicBroadcaster instead of this gesture-specific shape.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        auth_token: str | None = None,
        allowed_origins: Iterable[str] | None = None,
        max_clients: int | None = DEFAULT_MAX_CLIENTS,
        max_message_bytes: int | None = DEFAULT_MAX_MESSAGE_BYTES,
        max_queue: int | None = DEFAULT_MAX_QUEUE,
        allow_remote_unauthenticated: bool = False,
    ) -> None:
        auth_token = _normalize_token(auth_token, name="auth_token")
        if (
            not _is_local_bind_host(host)
            and not auth_token
            and not allow_remote_unauthenticated
        ):
            raise ValueError(
                "refusing to bind WebSocket broadcaster to a non-local host without auth_token"
            )
        self.host = host
        self.port = _validate_port(port)
        self.auth_token = auth_token
        self.allowed_origins = _normalize_allowed_origins(allowed_origins)
        self.max_clients = _validate_optional_positive_int(max_clients, name="max_clients")
        self.max_message_bytes = _validate_optional_positive_int(
            max_message_bytes,
            name="max_message_bytes",
        )
        self.max_queue = _validate_optional_positive_int(max_queue, name="max_queue")
        self.clients: set[Any] = set()
        self._server: Any | None = None
        self._publish_lock = asyncio.Lock()

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
        if self.max_message_bytes is not None:
            options["max_size"] = self.max_message_bytes
        if self.max_queue is not None:
            options["max_queue"] = self.max_queue
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

    async def publish(
        self,
        state: GestureState,
        *,
        sequence: int | None = None,
        stable: GestureHoldState | None = None,
    ) -> None:
        await self.publish_message(
            gesture_state_json(state, sequence=sequence, stable=stable),
        )

    async def publish_message(self, message: str | bytes) -> None:
        if not self.clients:
            return

        async with self._publish_lock:
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
            try:
                async for _message in websocket:
                    pass
            except Exception as exc:
                if not _is_websocket_connection_closed(exc):
                    raise
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


class WebSocketTopicBroadcaster(WebSocketGestureBroadcaster):
    """Camera Hub topic-envelope broadcaster used by the normal integration path."""


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


def _is_websocket_connection_closed(exc: BaseException) -> bool:
    try:
        from websockets.exceptions import ConnectionClosed
    except ImportError:
        return False
    return isinstance(exc, ConnectionClosed)


def _is_local_bind_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in LOCAL_BIND_HOSTS or normalized.startswith("127.")


def _validate_port(port: int) -> int:
    parsed = int(port)
    if not 1 <= parsed <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return parsed


def _validate_optional_positive_int(value: int | None, *, name: str) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return parsed


def _normalize_allowed_origins(origins: Iterable[str] | None) -> list[str] | None:
    if origins is None:
        return None
    if isinstance(origins, str):
        origins = [origins]

    normalized: list[str] = []
    for origin in origins:
        origin_text = str(origin).strip()
        if not origin_text:
            raise ValueError("allowed origin must not be empty")
        if origin_text == "*":
            raise ValueError("wildcard WebSocket origins are not allowed")
        normalized.append(origin_text)
    return normalized


def _normalize_token(token: str | None, *, name: str) -> str | None:
    if token is None:
        return None
    raw_token = str(token)
    if any(ord(char) < 32 or ord(char) == 127 for char in raw_token):
        raise ValueError(f"{name} must not contain control characters")
    token_text = raw_token.strip()
    if not token_text:
        return None
    return token_text


def _extract_auth_token(websocket: Any, path: str | None) -> str | None:
    headers = _request_headers(websocket)
    authorization = _header(headers, "Authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return _request_token(authorization[7:])

    header_token = _header(headers, "X-Gesture-Token")
    if header_token:
        return _request_token(header_token)

    request_path = path or _request_path(websocket)
    try:
        query = parse_qs(urlsplit(request_path).query, max_num_fields=8)
    except ValueError:
        return None
    tokens = query.get("token")
    return _request_token(tokens[0]) if tokens else None


def _request_token(token: str) -> str | None:
    try:
        return _normalize_token(token, name="token")
    except ValueError:
        return None


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
