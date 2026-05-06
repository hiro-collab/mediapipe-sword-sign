from __future__ import annotations

import argparse
import asyncio
import queue
import socket
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from apps.serve_camera_hub import (  # noqa: E402
    camera_status_payload,
    normalized_landmarks_payload,
)
from mediapipe_sword_sign import SwordSignDetector, gesture_state_payload  # noqa: E402
from mediapipe_sword_sign.adapters import WebSocketTopicBroadcaster  # noqa: E402
from mediapipe_sword_sign.topics import (  # noqa: E402
    CAMERA_STATUS_TOPIC,
    MSG_TYPE_CAMERA_STATUS,
    MSG_TYPE_GESTURE_STATE,
    SWORD_SIGN_STATE_TOPIC,
    topic_json,
)
from mediapipe_sword_sign.types import GestureState  # noqa: E402


DEFAULT_IMAGE_DIR = PROJECT_ROOT / "tests" / "pict_for_debug"
DEFAULT_HTTP_PORT = 8771
DEFAULT_WS_PORT = 8772
DEFAULT_PERIOD_MS = 1200
DEFAULT_MAX_CLIENTS = 8


@dataclass(frozen=True)
class ProbeEvent:
    seq: int
    state: str
    shown_epoch_ms: float


@dataclass(frozen=True)
class LandmarkTemplate:
    hand_state: Any
    points: list[dict[str, float]]


def parse_port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed


def parse_non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be 0 or greater")
    return parsed


def image_paths(image_dir: Path) -> dict[str, Path]:
    paths = {
        "hand_in": image_dir / "hand_in.png",
        "hand_out": image_dir / "hand_out.png",
    }
    missing = [path for path in paths.values() if not path.is_file()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"missing debug image(s): {names}")
    return paths


def port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def find_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def select_port(host: str, requested: int, *, auto_port: bool) -> tuple[int, str | None]:
    if port_is_available(host, requested):
        return requested, None
    if not auto_port:
        raise OSError(f"{host}:{requested} is already in use")
    selected = find_free_port(host)
    return selected, f"{host}:{requested} is in use; using {selected} instead"


def build_viewer_url(
    *,
    viewer_path: Path,
    media_url: str,
    ws_url: str,
    target: str,
) -> str:
    params = urlencode(
        {
            "mediaUrl": media_url,
            "wsUrl": ws_url,
            "target": target,
            "measure": "1",
        }
    )
    return f"{viewer_path.resolve().as_uri()}?{params}"


def probe_media_html(*, period_ms: int) -> bytes:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Camera Hub Latency Probe Media</title>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      background: #000;
    }}
    #probeFrame {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: fill;
    }}
    #label {{
      position: absolute;
      left: 10px;
      top: 10px;
      padding: 4px 7px;
      border-radius: 4px;
      background: rgba(0, 0, 0, 0.65);
      color: #fff;
      font: 12px Consolas, monospace;
    }}
  </style>
</head>
<body>
  <img id="probeFrame" alt="latency probe frame">
  <div id="label">loading</div>
  <script>
    const periodMs = {int(period_ms)};
    const states = ["hand_out", "hand_in"];
    const imageCache = new Map();
    let seq = 0;
    let stateIndex = 0;

    function imageUrl(state) {{
      return `/${{state}}.png`;
    }}

    function loadImage(state) {{
      return new Promise((resolve, reject) => {{
        const image = new Image();
        image.onload = () => {{
          imageCache.set(state, image);
          resolve();
        }};
        image.onerror = reject;
        image.src = imageUrl(state);
      }});
    }}

    async function showState(state) {{
      seq += 1;
      const frame = document.getElementById("probeFrame");
      const label = document.getElementById("label");
      frame.src = imageUrl(state);
      label.textContent = `${{state}} seq=${{seq}}`;
      if (frame.decode) {{
        await frame.decode().catch(() => {{}});
      }}
      requestAnimationFrame(() => {{
        requestAnimationFrame(() => {{
          const shownEpochMs = Date.now();
          const payload = {{
            type: "mpss_latency_video_state",
            seq,
            state,
            shown_epoch_ms: shownEpochMs
          }};
          window.parent.postMessage(payload, "*");
          fetch(
            `/probe_state?seq=${{seq}}&state=${{encodeURIComponent(state)}}&shown_epoch_ms=${{shownEpochMs}}`,
            {{ cache: "no-store" }}
          ).catch(() => {{}});
        }});
      }});
    }}

    Promise.all(states.map(loadImage)).then(() => {{
      showState(states[0]);
      window.setInterval(() => {{
        stateIndex = (stateIndex + 1) % states.length;
        showState(states[stateIndex]);
      }}, periodMs);
    }});
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


def make_http_handler(
    *,
    images: dict[str, Path],
    events: "queue.Queue[ProbeEvent]",
    period_ms: int,
):
    class LatencyProbeHandler(BaseHTTPRequestHandler):
        server_version = "CameraHubLatencyProbe/1.0"

        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/media.html"}:
                self._send_bytes(
                    probe_media_html(period_ms=period_ms),
                    content_type="text/html; charset=utf-8",
                    cache_control="no-store",
                )
                return

            if parsed.path in {"/hand_in.png", "/hand_out.png"}:
                state = parsed.path.removeprefix("/").removesuffix(".png")
                self._send_file(images[state], content_type="image/png")
                return

            if parsed.path == "/probe_state":
                self._handle_probe_state(parsed.query)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def _handle_probe_state(self, query: str) -> None:
            values = parse_qs(query)
            try:
                seq = int(values.get("seq", [""])[0])
                state = values.get("state", [""])[0]
                shown_epoch_ms = float(values.get("shown_epoch_ms", [""])[0])
            except (TypeError, ValueError):
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid probe state")
                return
            if state not in {"hand_in", "hand_out"} or seq < 0:
                self.send_error(HTTPStatus.BAD_REQUEST, "invalid probe state")
                return
            events.put(ProbeEvent(seq=seq, state=state, shown_epoch_ms=shown_epoch_ms))
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

        def _send_file(self, path: Path, *, content_type: str) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_bytes(
                data,
                content_type=content_type,
                cache_control="public, max-age=3600",
            )

        def _send_bytes(
            self,
            data: bytes,
            *,
            content_type: str,
            cache_control: str,
        ) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

    return LatencyProbeHandler


def extract_landmark_template(
    hand_image_path: Path,
    *,
    model_complexity: int,
    flip: bool,
) -> LandmarkTemplate:
    import cv2

    frame = cv2.imread(str(hand_image_path))
    if frame is None:
        raise RuntimeError(f"failed to read debug image: {hand_image_path}")

    detector = SwordSignDetector(
        model_complexity=model_complexity,
        source="latency_probe",
    )
    try:
        result = detector.detect_frame(frame, flip=flip)
    finally:
        detector.close()

    points = normalized_landmarks_payload(result.hand_landmarks)
    if not points:
        raise RuntimeError(
            f"no hand landmarks detected in {hand_image_path}; replace hand_in.png "
            "with a clear hand frame"
        )
    return LandmarkTemplate(hand_state=result.state, points=points)


def gesture_probe_payload(
    *,
    event: ProbeEvent,
    template: LandmarkTemplate,
) -> dict[str, object]:
    timestamp = time.time()
    if event.state == "hand_in":
        gesture_state = replace(
            template.hand_state,
            timestamp=timestamp,
            source="latency_probe",
        )
        points = template.points
    else:
        gesture_state = GestureState.no_hand(
            source="latency_probe",
            timestamp=timestamp,
        )
        points = []

    payload = gesture_state_payload(gesture_state, sequence=event.seq)
    payload["landmarks"] = {
        "type": "mediapipe_hand_landmarks",
        "coordinate_space": "normalized_input_frame_mirrored",
        "points": points,
    }
    payload["debug_probe"] = {
        "seq": event.seq,
        "state": event.state,
        "shown_epoch_ms": round(event.shown_epoch_ms, 3),
        "sent_epoch_ms": round(time.time() * 1000, 3),
    }
    return payload


async def publish_probe_events(
    *,
    events: "queue.Queue[ProbeEvent]",
    broadcaster: WebSocketTopicBroadcaster,
    template: LandmarkTemplate,
    delay_ms: float,
    frame_id: str,
) -> None:
    while True:
        event = await asyncio.to_thread(events.get)
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        stamp = time.time()
        payload = gesture_probe_payload(event=event, template=template)
        await broadcaster.publish_message(
            topic_json(
                SWORD_SIGN_STATE_TOPIC,
                MSG_TYPE_GESTURE_STATE,
                payload,
                sequence=event.seq,
                stamp=stamp,
                frame_id=frame_id,
            )
        )


async def publish_status(
    *,
    broadcaster: WebSocketTopicBroadcaster,
    period_ms: int,
    frame_id: str,
    media_url: str,
) -> None:
    frame_number = 0
    fps = 1000.0 / period_ms if period_ms > 0 else 0.0
    while True:
        stamp = time.time()
        await broadcaster.publish_message(
            topic_json(
                CAMERA_STATUS_TOPIC,
                MSG_TYPE_CAMERA_STATUS,
                camera_status_payload(
                    camera_index=0,
                    frame_number=frame_number,
                    fps=fps,
                    frame_read_ok=True,
                    capture={
                        "source": media_url,
                        "backend": "latency_probe",
                        "width": 640,
                        "height": 480,
                        "fps": fps,
                    },
                    camera_source=media_url,
                ),
                sequence=frame_number,
                stamp=stamp,
                frame_id=frame_id,
            )
        )
        frame_number += 1
        await asyncio.sleep(0.5)


async def run_probe(args: argparse.Namespace) -> None:
    images = image_paths(args.image_dir)
    http_port, http_warning = select_port(args.host, args.http_port, auto_port=args.auto_port)
    ws_port, ws_warning = select_port(args.host, args.ws_port, auto_port=args.auto_port)
    for warning in (http_warning, ws_warning):
        if warning:
            print(f"warning: {warning}")

    template = extract_landmark_template(
        images["hand_in"],
        model_complexity=args.gesture_model_complexity,
        flip=not args.no_flip,
    )
    print(f"loaded hand_in landmarks: {len(template.points)} points")

    events: queue.Queue[ProbeEvent] = queue.Queue()
    handler = make_http_handler(
        images=images,
        events=events,
        period_ms=args.period_ms,
    )
    httpd = ThreadingHTTPServer((args.host, http_port), handler)
    http_thread = threading.Thread(
        target=httpd.serve_forever,
        name="latency-probe-http",
        daemon=True,
    )
    http_thread.start()

    media_url = f"http://{args.host}:{http_port}/media.html"
    ws_url = f"ws://{args.host}:{ws_port}"
    viewer_url = build_viewer_url(
        viewer_path=PROJECT_ROOT / "apps" / "browser_camera_hub_viewer.html",
        media_url=media_url,
        ws_url=ws_url,
        target=args.target_gesture,
    )

    broadcaster = WebSocketTopicBroadcaster(
        args.host,
        ws_port,
        max_clients=args.max_clients,
        allow_remote_unauthenticated=True,
    )
    try:
        async with broadcaster:
            print(f"latency probe media: {media_url}")
            print(f"latency probe websocket: {ws_url}")
            print(f"open viewer: {viewer_url}")
            print("stop: press Ctrl+C")
            await asyncio.gather(
                publish_probe_events(
                    events=events,
                    broadcaster=broadcaster,
                    template=template,
                    delay_ms=args.landmark_delay_ms,
                    frame_id=args.frame_id,
                ),
                publish_status(
                    broadcaster=broadcaster,
                    period_ms=args.period_ms,
                    frame_id=args.frame_id,
                    media_url=media_url,
                ),
            )
    finally:
        httpd.shutdown()
        httpd.server_close()
        http_thread.join(timeout=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a camera-free browser overlay latency probe using hand_in.png "
            "and hand_out.png."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=parse_port, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--ws-port", type=parse_port, default=DEFAULT_WS_PORT)
    parser.add_argument(
        "--no-auto-port",
        action="store_false",
        dest="auto_port",
        help="Fail instead of selecting a free port when the requested port is busy.",
    )
    parser.set_defaults(auto_port=True)
    parser.add_argument("--image-dir", type=Path, default=DEFAULT_IMAGE_DIR)
    parser.add_argument("--period-ms", type=parse_positive_int, default=DEFAULT_PERIOD_MS)
    parser.add_argument(
        "--landmark-delay-ms",
        type=parse_non_negative_float,
        default=0.0,
        help="Optional artificial landmark delay for sanity-checking the meter.",
    )
    parser.add_argument("--gesture-model-complexity", type=int, choices=[0, 1], default=0)
    parser.add_argument(
        "--no-flip",
        action="store_true",
        help="Disable the mirror flip used by the normal camera hub detector path.",
    )
    parser.add_argument("--frame-id", default="latency_probe")
    parser.add_argument("--target-gesture", default="sword_sign")
    parser.add_argument("--max-clients", type=parse_positive_int, default=DEFAULT_MAX_CLIENTS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run_probe(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
