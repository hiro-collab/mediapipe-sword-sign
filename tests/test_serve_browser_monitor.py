import argparse
import importlib.util
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def load_server_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "apps" / "serve_browser_monitor.py"
    spec = importlib.util.spec_from_file_location("serve_browser_monitor", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


server = load_server_module()


class BrowserMonitorServerTests(unittest.TestCase):
    def test_parse_port_rejects_invalid_values(self):
        self.assertEqual(server.parse_port("8770"), 8770)
        with self.assertRaises(argparse.ArgumentTypeError):
            server.parse_port("0")
        with self.assertRaises(argparse.ArgumentTypeError):
            server.parse_port("not-a-port")

    def test_validate_bind_host_requires_explicit_remote_opt_in(self):
        self.assertEqual(
            server.validate_bind_host("127.0.0.1", allow_remote=False),
            "127.0.0.1",
        )
        with self.assertRaises(ValueError):
            server.validate_bind_host("0.0.0.0", allow_remote=False)
        self.assertEqual(
            server.validate_bind_host("0.0.0.0", allow_remote=True),
            "0.0.0.0",
        )

    def test_viewer_http_url_preserves_query_parameters(self):
        url = server.viewer_http_url(
            host="127.0.0.1",
            port=8770,
            media_url="http://127.0.0.1:8889/cam0?controls=false&muted=true",
            ws_url="ws://127.0.0.1:8765",
            target="sword_sign",
        )

        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.netloc, "127.0.0.1:8770")
        self.assertEqual(parsed.path, "/browser_camera_hub_viewer.html")
        self.assertEqual(
            query["mediaUrl"],
            ["http://127.0.0.1:8889/cam0?controls=false&muted=true"],
        )
        self.assertEqual(query["wsUrl"], ["ws://127.0.0.1:8765"])
        self.assertEqual(query["target"], ["sword_sign"])

    def test_request_path_decodes_path_without_query(self):
        self.assertEqual(
            server.request_path("/browser_camera_hub_viewer.html?mediaUrl=x"),
            "/browser_camera_hub_viewer.html",
        )
        self.assertEqual(server.request_path("/foo%20bar"), "/foo bar")


if __name__ == "__main__":
    unittest.main()
