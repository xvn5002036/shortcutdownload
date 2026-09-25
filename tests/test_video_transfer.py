import ast
import os
import re
import tempfile
import threading
import unittest
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


source = Path(__file__).resolve().parents[1] / "app/compat2.py"
tree = ast.parse(source.read_text())
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_download_video_to_file")
scope = {
    "os": os, "re": re, "Path": Path, "URLRequest": Request, "urlopen": urlopen,
    "HTTPError": HTTPError, "URLError": URLError, "IncompleteRead": IncompleteRead,
    "HTTPException": HTTPException, "TimeoutError": TimeoutError,
    "UA": "test", "VIDEO_DOWNLOAD_ATTEMPTS": 3, "MAX_VIDEO_PROXY_BYTES": 8 * 1024 * 1024,
}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
download = scope["_download_video_to_file"]
body = bytes(range(256)) * 12288  # 3 MiB


class VideoHandler(BaseHTTPRequestHandler):
    first = True
    accept_ranges = True
    calls = []

    def do_GET(self):
        offset = int(self.headers["Range"].split("=")[1].split("-")[0]) if "Range" in self.headers else 0
        type(self).calls.append(offset)
        if offset and self.accept_ranges:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {offset}-{len(body)-1}/{len(body)}")
            part = body[offset:]
        else:
            self.send_response(200)
            part = body
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(part) if offset and self.accept_ranges else len(body)))
        self.end_headers()
        if self.first:
            type(self).first = False
            self.wfile.write(body[:1536 * 1024])
            self.wfile.flush()
            self.connection.shutdown(2)
            return
        try:
            self.wfile.write(part)
        except ConnectionResetError:
            pass

    def log_message(self, *args):
        pass


class VideoDownloadTests(unittest.TestCase):
    def setUp(self):
        VideoHandler.first = True
        VideoHandler.accept_ranges = True
        VideoHandler.calls = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), VideoHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "video.bin"
        self.url = f"http://127.0.0.1:{self.server.server_port}/video"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.folder.cleanup()

    def test_interrupted_transfer_resumes_without_corruption(self):
        download(self.url, self.path)
        self.assertEqual(self.path.read_bytes(), body)
        self.assertEqual(VideoHandler.calls, [0, 1536 * 1024])

    def test_server_ignoring_range_is_rejected(self):
        VideoHandler.accept_ranges = False
        with self.assertRaises(HTTPException) as ctx:
            download(self.url, self.path)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(VideoHandler.calls, [0, 1536 * 1024])


if __name__ == "__main__":
    unittest.main()
