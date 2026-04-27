from __future__ import annotations

import socket
from types import TracebackType

from mediapipe_sword_sign.types import GestureState


class UdpGesturePublisher:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        encoding: str = "utf-8",
        sock: socket.socket | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.encoding = encoding
        self._sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns_socket = sock is None

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def publish(self, state: GestureState) -> None:
        self._sock.sendto(state.to_json().encode(self.encoding), self.address)

    def close(self) -> None:
        if self._owns_socket:
            self._sock.close()

    def __enter__(self) -> "UdpGesturePublisher":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
