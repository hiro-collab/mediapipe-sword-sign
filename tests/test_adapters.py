import asyncio
import json
import unittest

from mediapipe_sword_sign.adapters import UdpGesturePublisher, WebSocketGestureBroadcaster
from mediapipe_sword_sign.types import GesturePrediction, GestureState


def make_state() -> GestureState:
    return GestureState(
        timestamp=123.0,
        source="test",
        hand_detected=True,
        primary="sword_sign",
        gestures={
            "sword_sign": GesturePrediction(
                name="sword_sign",
                active=True,
                confidence=0.95,
                label=0,
            )
        },
    )


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.closed = False

    def sendto(self, payload, address):
        self.sent.append((payload, address))

    def close(self):
        self.closed = True


class FakeWebSocketClient:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


class FailingWebSocketClient:
    async def send(self, message):
        raise ConnectionError("client disconnected")


class AdapterTests(unittest.TestCase):
    def test_udp_publisher_sends_gesture_state_json(self):
        fake_socket = FakeSocket()
        publisher = UdpGesturePublisher("127.0.0.1", 9999, sock=fake_socket)

        publisher.publish(make_state())

        payload, address = fake_socket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 9999))
        self.assertEqual(json.loads(payload.decode("utf-8"))["primary"], "sword_sign")

    def test_websocket_broadcaster_sends_to_connected_clients(self):
        async def run():
            client = FakeWebSocketClient()
            broadcaster = WebSocketGestureBroadcaster()
            broadcaster.clients.add(client)

            await broadcaster.publish(make_state())

            self.assertEqual(len(client.messages), 1)
            self.assertEqual(json.loads(client.messages[0])["primary"], "sword_sign")

        asyncio.run(run())

    def test_websocket_broadcaster_removes_failed_clients(self):
        async def run():
            client = FailingWebSocketClient()
            broadcaster = WebSocketGestureBroadcaster()
            broadcaster.clients.add(client)

            await broadcaster.publish(make_state())

            self.assertNotIn(client, broadcaster.clients)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
