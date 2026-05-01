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


class ClosingWebSocketClient:
    def __init__(self):
        self.closed = []

    async def close(self, *, code, reason):
        self.closed.append((code, reason))

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class AdapterTests(unittest.TestCase):
    def test_udp_publisher_sends_gesture_state_json(self):
        fake_socket = FakeSocket()
        publisher = UdpGesturePublisher("127.0.0.1", 9999, sock=fake_socket)

        publisher.publish(make_state())

        payload, address = fake_socket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 9999))
        self.assertEqual(json.loads(payload.decode("utf-8"))["primary"], "sword_sign")

    def test_udp_publisher_sends_generic_json_payload(self):
        fake_socket = FakeSocket()
        publisher = UdpGesturePublisher("127.0.0.1", 9999, sock=fake_socket)

        publisher.publish_payload({"type": "gesture_heartbeat", "status": "sending"})

        payload, address = fake_socket.sent[0]
        self.assertEqual(address, ("127.0.0.1", 9999))
        self.assertEqual(json.loads(payload.decode("utf-8"))["status"], "sending")

    def test_udp_publisher_attaches_auth_token_to_state_payload(self):
        fake_socket = FakeSocket()
        publisher = UdpGesturePublisher(
            "127.0.0.1",
            9999,
            sock=fake_socket,
            auth_token="secret-token",
        )

        publisher.publish(make_state())

        payload, address = fake_socket.sent[0]
        decoded = json.loads(payload.decode("utf-8"))
        self.assertEqual(address, ("127.0.0.1", 9999))
        self.assertEqual(decoded["primary"], "sword_sign")
        self.assertEqual(decoded["auth_token"], "secret-token")

    def test_udp_publisher_attaches_auth_token_to_generic_payload(self):
        fake_socket = FakeSocket()
        publisher = UdpGesturePublisher(
            "127.0.0.1",
            9999,
            sock=fake_socket,
            auth_token="secret-token",
        )

        publisher.publish_payload({"type": "gesture_heartbeat", "status": "sending"})

        payload, address = fake_socket.sent[0]
        decoded = json.loads(payload.decode("utf-8"))
        self.assertEqual(address, ("127.0.0.1", 9999))
        self.assertEqual(decoded["auth_token"], "secret-token")

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

    def test_websocket_broadcaster_requires_auth_for_remote_bind(self):
        with self.assertRaises(ValueError):
            WebSocketGestureBroadcaster(host="0.0.0.0")

    def test_websocket_broadcaster_normalizes_empty_auth_token(self):
        broadcaster = WebSocketGestureBroadcaster(auth_token=" ")

        self.assertIsNone(broadcaster.auth_token)

    def test_websocket_broadcaster_rejects_invalid_runtime_limits(self):
        with self.assertRaises(ValueError):
            WebSocketGestureBroadcaster(port=0)
        with self.assertRaises(ValueError):
            WebSocketGestureBroadcaster(max_clients=0)
        with self.assertRaises(ValueError):
            WebSocketGestureBroadcaster(max_message_bytes=0)

    def test_websocket_broadcaster_rejects_wildcard_origin(self):
        with self.assertRaises(ValueError):
            WebSocketGestureBroadcaster(allowed_origins=["*"])

    def test_websocket_broadcaster_accepts_single_origin_string(self):
        broadcaster = WebSocketGestureBroadcaster(allowed_origins="http://localhost:3000")

        self.assertEqual(broadcaster.allowed_origins, ["http://localhost:3000"])

    def test_websocket_broadcaster_rejects_wrong_token(self):
        async def run():
            client = ClosingWebSocketClient()
            broadcaster = WebSocketGestureBroadcaster(auth_token="secret")

            await broadcaster._handler(client, "/?token=wrong")

            self.assertEqual(client.closed, [(1008, "unauthorized")])
            self.assertNotIn(client, broadcaster.clients)

        asyncio.run(run())

    def test_websocket_broadcaster_rejects_query_parameter_flood(self):
        async def run():
            client = ClosingWebSocketClient()
            broadcaster = WebSocketGestureBroadcaster(auth_token="secret")
            query = "&".join(f"unused{i}=1" for i in range(9))

            await broadcaster._handler(client, f"/?{query}&token=secret")

            self.assertEqual(client.closed, [(1008, "unauthorized")])
            self.assertNotIn(client, broadcaster.clients)

        asyncio.run(run())

    def test_websocket_broadcaster_rejects_clients_over_limit(self):
        async def run():
            existing_client = FakeWebSocketClient()
            client = ClosingWebSocketClient()
            broadcaster = WebSocketGestureBroadcaster(max_clients=1)
            broadcaster.clients.add(existing_client)

            await broadcaster._handler(client, "/")

            self.assertEqual(client.closed, [(1013, "too many clients")])
            self.assertNotIn(client, broadcaster.clients)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
