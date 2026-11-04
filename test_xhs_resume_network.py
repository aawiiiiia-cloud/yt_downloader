"""Loopback regressions using real yt-dlp responses and atomic image output."""
from __future__ import annotations

import io
import tempfile
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import yt_dlp
from PIL import Image

from xhs_image_downloader import XhsImage, XhsImageDownloader, XhsPost


class ResumeNetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.bodies = []
        for color in ((35, 70, 105), (105, 70, 35)):
            data = io.BytesIO()
            Image.new("RGB", (256, 256), color).save(data, format="BMP")
            self.bodies.append(data.getvalue())
        self.calls = []
        self.logs = []
        self.mode = "resume"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.calls.append((self.headers.get("Range"), self.headers.get("If-Range")))
                number = len(owner.calls)
                body = owner.bodies[1 if owner.mode == "ignore" and number > 1 else 0]
                offset = 0
                partial = number == 2 and owner.mode != "ignore"
                if partial:
                    offset = int(self.headers["Range"].split("=")[1].split("-")[0])
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", "image/bmp")
                self.send_header("Content-Length", str(len(body) - offset))
                self.send_header("ETag", '"changed"' if owner.mode == "validator" and partial else '"Exact"')
                if partial:
                    self.send_header("Content-Range", f"bytes {offset}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                try:
                    # First response closes after one complete application block.
                    self.wfile.write(body[:65536] if number == 1 else body[offset:])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def close_server():
            self.server.shutdown()
            self.server.server_close()
            thread.join(2)

        self.addCleanup(close_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}/original"

    def download(self):
        image = XhsImage(1, self.url)
        post = XhsPost("fixture", "fixture", "", "",
                       "https://www.xiaohongshu.com/explore/abc123", (image,))
        writer = XhsImageDownloader(progress=self.logs.append)
        writer.transfer_policy = replace(writer.transfer_policy, read_size=65536)
        options = writer._ydl_options()
        options.update(proxy="", socket_timeout=1)
        # Only this loopback fixture is treated as an original CDN.
        with mock.patch.object(writer, "_original_candidate_indices", return_value=(0,)):
            with yt_dlp.YoutubeDL(options) as ydl:
                return writer._download_one(ydl, post, image, self.folder)

    def test_real_206_resumes_once_and_keeps_exact_original_bytes(self):
        target = self.download()
        self.assertEqual(target.read_bytes(), self.bodies[0])
        self.assertEqual(self.calls, [(None, None), ("bytes=65536-", '"Exact"')])
        self.assertFalse((self.folder / ".01.part").exists())
        self.assertTrue(any("resume" in line for line in self.logs))

    def test_real_200_ignoring_range_replaces_instead_of_appending(self):
        self.mode = "ignore"
        target = self.download()
        self.assertEqual(target.read_bytes(), self.bodies[1])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.folder / ".01.part").exists())

    def test_real_changed_etag_closes_suffix_and_restarts_full_get(self):
        self.mode = "validator"
        target = self.download()
        self.assertEqual(target.read_bytes(), self.bodies[0])
        self.assertEqual(self.calls, [(None, None), ("bytes=65536-", '"Exact"'), (None, None)])
        self.assertFalse((self.folder / ".01.part").exists())


if __name__ == "__main__":
    unittest.main()
