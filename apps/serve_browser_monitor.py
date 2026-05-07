from __future__ import annotations

import argparse
import html
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_PATH = PROJECT_ROOT / "apps" / "browser_camera_hub_viewer.html"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the Camera Hub Browser Monitor as a static HTTP page.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=parse_port, default=8770)
    parser.add_argument(
        "--viewer-path",
        default=str(VIEWER_PATH),
        help="Path to browser_camera_hub_viewer.html.",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding to non-localhost addresses.",
    )
    return parser


def parse_port(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer") from exc
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("--port must be between 1 and 65535")
    return parsed


def validate_bind_host(host: str, *, allow_remote: bool) -> str:
    normalized = host.strip() or "127.0.0.1"
    if allow_remote or normalized in LOCAL_HOSTS:
        return normalized
    raise ValueError(
        "Browser Monitor server binds to localhost by default. "
        "Use --allow-remote to bind to a non-localhost address."
    )


def viewer_http_url(
    *,
    host: str,
    port: int,
    media_url: str,
    ws_url: str,
    target: str = "sword_sign",
) -> str:
    from urllib.parse import urlencode

    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    query = urlencode(
        {
            "mediaUrl": media_url,
            "wsUrl": ws_url,
            "target": target,
        }
    )
    return f"http://{connect_host}:{port}/browser_camera_hub_viewer.html?{query}"


def request_path(raw_path: str) -> str:
    return unquote(urlsplit(raw_path).path)


class BrowserMonitorHandler(BaseHTTPRequestHandler):
    viewer_path: Path = VIEWER_PATH

    server_version = "CameraHubBrowserMonitor/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = request_path(self.path)
        if path == "/healthz":
            self._send_bytes(
                HTTPStatus.OK,
                b"ok\n",
                content_type="text/plain; charset=utf-8",
            )
            return
        if path not in {"/", "/browser_camera_hub_viewer.html"}:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return

        try:
            body = self.viewer_path.read_bytes()
        except OSError:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "viewer not available")
            return

        self._send_bytes(
            HTTPStatus.OK,
            body,
            content_type="text/html; charset=utf-8",
        )

    def log_message(self, fmt: str, *args: object) -> None:
        client = self.client_address[0] if self.client_address else "-"
        print(f"[browser-monitor] {client} - {fmt % args}", flush=True)

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{status.value}</title><p>{html.escape(message)}</p>"
        ).encode("utf-8")
        self._send_bytes(status, body, content_type="text/html; charset=utf-8")


def make_handler(viewer_path: Path) -> type[BrowserMonitorHandler]:
    class Handler(BrowserMonitorHandler):
        pass

    Handler.viewer_path = viewer_path
    return Handler


def run(args: argparse.Namespace) -> None:
    host = validate_bind_host(args.host, allow_remote=args.allow_remote)
    viewer_path = Path(args.viewer_path).resolve()
    if not viewer_path.exists():
        raise SystemExit(f"Browser Monitor HTML not found: {viewer_path}")

    server = ThreadingHTTPServer((host, args.port), make_handler(viewer_path))
    print(
        "Browser Monitor listening on "
        f"http://{host}:{args.port}/browser_camera_hub_viewer.html",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    args = build_parser().parse_args()
    try:
        run(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
