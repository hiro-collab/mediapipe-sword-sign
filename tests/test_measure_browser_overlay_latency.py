import argparse
import socket
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from apps.measure_browser_overlay_latency import (
    LandmarkTemplate,
    ProbeEvent,
    build_parser,
    build_viewer_url,
    gesture_probe_payload,
    image_paths,
    is_local_host,
    parse_non_negative_float,
    select_port,
    validate_probe_exposure,
)
from mediapipe_sword_sign.types import GesturePrediction, GestureState


class MeasureBrowserOverlayLatencyTests(unittest.TestCase):
    def test_build_parser_defaults_to_camera_free_probe_ports(self):
        args = build_parser().parse_args([])

        self.assertEqual(args.http_port, 8771)
        self.assertEqual(args.ws_port, 8772)
        self.assertTrue(args.auto_port)
        self.assertEqual(args.period_ms, 1200)
        self.assertFalse(args.allow_remote_probe)

    def test_parse_non_negative_float_rejects_non_finite_values(self):
        self.assertEqual(parse_non_negative_float("0"), 0.0)
        for value in ("nan", "inf", "-1"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    parse_non_negative_float(value)

    def test_validate_probe_exposure_requires_explicit_remote_opt_in(self):
        self.assertTrue(is_local_host("127.0.0.1"))
        validate_probe_exposure("127.0.0.1", allow_remote_probe=False)
        validate_probe_exposure("0.0.0.0", allow_remote_probe=True)
        with self.assertRaises(ValueError):
            validate_probe_exposure("0.0.0.0", allow_remote_probe=False)

    def test_image_paths_requires_hand_in_and_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hand_in.png").write_bytes(b"in")
            (root / "hand_out.png").write_bytes(b"out")

            paths = image_paths(root)

        self.assertEqual(paths["hand_in"].name, "hand_in.png")
        self.assertEqual(paths["hand_out"].name, "hand_out.png")

    def test_build_viewer_url_sets_debug_query_values(self):
        url = build_viewer_url(
            viewer_path=Path("apps/browser_camera_hub_viewer.html"),
            media_url="http://127.0.0.1:8771/media.html",
            ws_url="ws://127.0.0.1:8772",
            target="sword_sign",
        )

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(query["mediaUrl"], ["http://127.0.0.1:8771/media.html"])
        self.assertEqual(query["wsUrl"], ["ws://127.0.0.1:8772"])
        self.assertEqual(query["target"], ["sword_sign"])
        self.assertEqual(query["measure"], ["1"])

    def test_select_port_can_fall_back_when_requested_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            busy_port = int(sock.getsockname()[1])

            selected, warning = select_port(
                "127.0.0.1",
                busy_port,
                auto_port=True,
            )

        self.assertNotEqual(selected, busy_port)
        self.assertIn(str(busy_port), warning)

    def test_gesture_probe_payload_marks_hand_in_and_out_states(self):
        hand_state = GestureState(
            timestamp=1.0,
            source="test",
            hand_detected=True,
            primary=None,
            gestures={
                "sword_sign": GesturePrediction(
                    name="sword_sign",
                    active=False,
                    confidence=0.25,
                    label=0,
                )
            },
        )
        template = LandmarkTemplate(
            hand_state=hand_state,
            points=[{"x": 0.1, "y": 0.2, "z": 0.0}],
        )

        hand_in = gesture_probe_payload(
            event=ProbeEvent(seq=7, state="hand_in", shown_epoch_ms=1000.0),
            template=template,
        )
        hand_out = gesture_probe_payload(
            event=ProbeEvent(seq=8, state="hand_out", shown_epoch_ms=1200.0),
            template=template,
        )

        self.assertTrue(hand_in["hand_detected"])
        self.assertEqual(hand_in["landmarks"]["points"], template.points)
        self.assertEqual(hand_in["debug_probe"]["seq"], 7)
        self.assertEqual(hand_in["debug_probe"]["state"], "hand_in")
        self.assertFalse(hand_out["hand_detected"])
        self.assertEqual(hand_out["landmarks"]["points"], [])
        self.assertEqual(hand_out["debug_probe"]["state"], "hand_out")


if __name__ == "__main__":
    unittest.main()
