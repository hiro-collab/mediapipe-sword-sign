from __future__ import annotations

import json
import socket
from types import TracebackType
from typing import Mapping

from mediapipe_sword_sign.types import GestureState


class UdpGesturePublisher:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        encoding: str = "utf-8",
        sock: socket.socket | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.encoding = encoding
        self.auth_token = auth_token
        self._sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._owns_socket = sock is None

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def _payload_with_auth(self, payload: Mapping[str, object]) -> dict[str, object]:
        udp_payload = dict(payload)
        if self.auth_token:
            udp_payload["auth_token"] = self.auth_token
        return udp_payload

    def publish_payload(self, payload: Mapping[str, object]) -> None:
        self._sock.sendto(
            json.dumps(
                self._payload_with_auth(payload),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(self.encoding),
            self.address,
        )

    def publish(self, state: GestureState) -> None:
        if not self.auth_token:
            self._sock.sendto(state.to_json().encode(self.encoding), self.address)
            return

        self._sock.sendto(
            json.dumps(
                self._payload_with_auth(state.to_dict()),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode(self.encoding),
            self.address,
        )

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
