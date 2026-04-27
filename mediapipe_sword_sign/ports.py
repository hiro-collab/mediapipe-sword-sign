from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import GestureState


@runtime_checkable
class GesturePublisher(Protocol):
    def publish(self, state: GestureState) -> None:
        """Publish a gesture state to an external system."""


@runtime_checkable
class AsyncGesturePublisher(Protocol):
    async def publish(self, state: GestureState) -> None:
        """Publish a gesture state to an external system asynchronously."""
