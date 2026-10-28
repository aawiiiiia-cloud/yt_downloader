"""Loopback-only regression: real yt-dlp transport, slow and stalled responses."""
from __future__ import annotations
import io
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import yt_dlp
from PIL import Image
from xhs_image_downloader import XhsImageDownloader, XhsImage, XhsPost, XhsDownloadError, XhsCancelled

class NetworkProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        out = io.BytesIO(); Image.new('RGB', (256,256), (35,70,105)).save(out, format='BMP')
        self.data = out.getvalue()
        self.received = threading.Event(); self.entered = threading.Event(); self.release = threading.Event()
        self.logs, self.events = [], []
        self.calls = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                owner.calls += 1; owner.entered.set()
                if self.path == '/timeout':
                    owner.release.wait(0.6)
                    return
                if self.path == '/headers': owner.release.wait(3)
                self.send_response(200);self.send_header('Content-Type','image/bmp')
                if self.path != '/unknown':self.send_header('Content-Length',str(len(owner.data)))
                self.end_headers()
                try:
                    self.wfile.write(owner.data[:65536]);self.wfile.flush()
                    if self.path in {'/stream','/unknown'}:owner.release.wait(3)
                    self.wfile.write(owner.data[65536:]);self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError): pass
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        def cleanup():
            self.release.set();self.server.shutdown();self.server.server_close();thread.join(2)
        self.addCleanup(cleanup)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.post=XhsPost('fixture','network-fixture','','','https://www.xiaohongshu.com/explore/abc123',
            (XhsImage(1,self.base+'/stream'),))
        def report(event):
            self.events.append(event)
            if event['status']=='image-receiving' and event['downloadedBytes']:self.received.set()
        self.writer=XhsImageDownloader(progress=self.logs.append,transfer_progress=report)
    def download(self, endpoint='/stream', timeout=1, post=None):
        post=post or replace(self.post,images=(XhsImage(1,self.base+endpoint),))
        options=self.writer._ydl_options();options.update(proxy='',socket_timeout=timeout)
        with yt_dlp.YoutubeDL(options) as ydl:
            return self.writer._download_one(ydl,post,post.images[0],self.folder)
    def test_slow_real_response_updates_before_file_is_saved(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.download)
            try:
                self.assertTrue(self.received.wait(3),'byte progress missing during live streaming')
                self.assertFalse(future.done());self.assertFalse((self.folder/'01.bmp').exists())
                progress=[e for e in self.events if e['status']=='image-receiving' and e['downloadedBytes']]
                self.assertGreater(progress[-1]['percent'],0);self.assertLess(progress[-1]['percent'],99)
                self.assertGreater(progress[-1]['speed'],0)
                self.assertTrue(all(e['speed'] is None for e in self.events if e['status']=='image-connecting'))
            finally:self.release.set()
            target=future.result(timeout=4)
        self.assertEqual(target.read_bytes(),self.data)
    def test_waiting_for_headers_is_logged_before_response_arrives(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.download,'/headers')
            try:
                self.assertTrue(self.entered.wait(3));self.assertTrue(any('[连接]' in line for line in self.logs))
                self.assertEqual(self.events[-1]['status'],'image-connecting');self.assertFalse(future.done())
            finally:self.release.set()
            self.assertEqual(future.result(timeout=4).read_bytes(),self.data)
    def test_unknown_content_length_reports_real_bytes(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.download,'/unknown')
            try:
                self.assertTrue(self.received.wait(3))
                progress=[e for e in self.events if e['status']=='image-receiving' and e['downloadedBytes']]
                self.assertIsNone(progress[-1]['percent']);self.assertGreater(progress[-1]['downloadedBytes'],0)
                self.assertGreater(progress[-1]['speed'],0)
            finally:self.release.set()
            self.assertEqual(future.result(timeout=4).read_bytes(),self.data)
    def test_real_timeout_retries_then_uses_backup(self):
        post=replace(self.post,images=(XhsImage(1,self.base+'/timeout',(self.base+'/ok',)),))
        self.download(post=post,timeout=0.15)
        self.assertEqual(self.calls,3)
        self.assertTrue(any('[重试]' in line for line in self.logs));self.assertTrue(any('[回退]' in line for line in self.logs))
        self.assertFalse((self.folder/'.01.part').exists())
    def test_real_timeout_finishes_as_error_with_cleanup(self):
        start=time.monotonic()
        with self.assertRaises(XhsDownloadError):self.download('/timeout',timeout=0.15)
        self.assertLess(time.monotonic()-start,3)
        self.assertEqual(self.calls,2);self.assertEqual(list(self.folder.iterdir()),[])
    def test_real_stream_cancellation_cleans_partial_file(self):
        flag=threading.Event();self.writer.cancel_event=flag
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.download)
            try:
                self.assertTrue(self.received.wait(3));flag.set()
            finally:self.release.set()
            with self.assertRaises(XhsCancelled):future.result(timeout=4)
        self.assertEqual(list(self.folder.iterdir()),[])


class AdaptiveNetworkTests(unittest.TestCase):
    """Exercise low-rate decisions with real sockets, not a mocked clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        out = io.BytesIO()
        Image.new('RGB', (256, 256), (35, 70, 105)).save(out, format='BMP')
        self.data = out.getvalue()
        self.calls = []
        self.logs, self.events = [], []
        self.release = threading.Event()
        self.stop = threading.Event()
        self.received = threading.Event()
        self.backup_entered = threading.Event()
        self.cancel = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                owner.calls.append(self.path)
                if self.path == '/timeout':
                    owner.stop.wait(0.6)
                    return
                if self.path == '/backup-hold':
                    owner.backup_entered.set()
                    owner.release.wait(3)
                self.send_response(200)
                self.send_header('Content-Type', 'image/bmp')
                if self.path != '/unknown-trickle':
                    self.send_header('Content-Length', str(len(owner.data)))
                self.end_headers()
                delays = {'/trickle': 0.06, '/unknown-trickle': 0.06,
                          '/slow-a': 0.02, '/slow-b': 0.01, '/slow-c': 0.03}
                delay = delays.get(self.path, 0)
                try:
                    if self.path == '/body-stall':
                        self.wfile.write(owner.data[:4096])
                        self.wfile.flush()
                        owner.stop.wait(0.6)
                        return
                    if not delay:
                        self.wfile.write(owner.data)
                        self.wfile.flush()
                    else:
                        for start in range(0, len(owner.data), 1024):
                            if owner.stop.is_set():
                                return
                            self.wfile.write(owner.data[start:start + 1024])
                            self.wfile.flush()
                            if owner.stop.wait(delay):
                                return
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

        def report(event):
            self.events.append(event)
            if event['status'] == 'image-receiving' and event['downloadedBytes']:
                self.received.set()

        self.writer = XhsImageDownloader(progress=self.logs.append, transfer_progress=report,
                                         cancel_event=self.cancel)
        self.writer.transfer_policy = replace(
            self.writer.transfer_policy, read_size=1024, startup_grace=0.1,
            slow_window=0.15, rate_window=0.2, log_interval=0.15,
            original_idle_timeout=0.2,
        )

    def close_server(self):
        self.stop.set()
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def download(self, endpoints, original_count=None):
        from unittest import mock
        urls = tuple(self.base + endpoint for endpoint in endpoints)
        image = XhsImage(1, urls[0], urls[1:])
        post = XhsPost('fixture', 'network-fixture', '', '',
                       'https://www.xiaohongshu.com/explore/abc123', (image,))
        options = self.writer._ydl_options()
        options.update(proxy='', socket_timeout=1)
        # Local URLs represent identical original bodies in this fixture only.
        indices = tuple(range(original_count if original_count is not None else len(urls)))
        with mock.patch.object(self.writer, '_original_candidate_indices', return_value=indices):
            with yt_dlp.YoutubeDL(options) as ydl:
                return self.writer._download_one(ydl, post, image, self.folder)

    def test_continuous_low_rate_switches_to_equal_quality_backup(self):
        start = time.monotonic()
        target = self.download(('/trickle', '/ok'))
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(self.calls, ['/trickle', '/ok'])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertTrue(any('[\u4f4e\u901f]' in line for line in self.logs))
        self.assertTrue(any('[\u63a5\u6536\u8fdb\u5ea6]' in line for line in self.logs))
        self.assertFalse((self.folder / '.01.part').exists())

    def test_unknown_length_slow_stream_can_switch(self):
        target = self.download(('/unknown-trickle', '/ok'))
        self.assertEqual(self.calls, ['/unknown-trickle', '/ok'])
        self.assertEqual(target.read_bytes(), self.data)
        unknown = [event for event in self.events if event['status'] == 'image-receiving'
                   and event['totalBytes'] is None and event['downloadedBytes']]
        self.assertTrue(unknown)
        self.assertTrue(all(event['percent'] is None for event in unknown))

    def test_original_header_timeout_tries_other_original_before_same_url(self):
        target = self.download(('/timeout', '/ok'))
        self.assertEqual(self.calls, ['/timeout', '/ok'])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertTrue(any('[\u5931\u8d25]' in line for line in self.logs))

    def test_original_body_timeout_switches_after_received_bytes(self):
        target = self.download(('/body-stall', '/ok'))
        self.assertEqual(self.calls, ['/body-stall', '/ok'])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertTrue(any('[失败]' in line and '4 KB' in line for line in self.logs))
        self.assertFalse((self.folder / '.01.part').exists())

    def test_cancelled_trickle_does_not_start_another_source(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.download, ('/trickle', '/ok'))
            self.assertTrue(self.received.wait(3))
            self.cancel.set()
            with self.assertRaises(XhsCancelled):
                future.result(timeout=3)
        self.assertEqual(self.calls, ['/trickle'])
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_partial_and_switched_streams_never_publish_over_existing_image(self):
        existing = self.folder / '01.bmp'
        existing.write_bytes(b'previous-complete-image')
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.download, ('/trickle', '/backup-hold'))
            try:
                self.assertTrue(self.backup_entered.wait(3))
                self.assertFalse(future.done())
                self.assertEqual(existing.read_bytes(), b'previous-complete-image')
            finally:
                self.release.set()
            target = future.result(timeout=4)
        self.assertEqual(target.read_bytes(), self.data)
        self.assertFalse((self.folder / '.01.part').exists())

    def test_three_slow_originals_resume_fastest_without_using_preview(self):
        self.writer.transfer_policy = replace(
            self.writer.transfer_policy, startup_grace=0.03, slow_window=0.04,
            minimum_rate=256 * 1024, original_idle_timeout=1,
        )
        target = self.download(('/slow-a', '/slow-b', '/slow-c', '/preview'), original_count=3)
        self.assertEqual(self.calls, ['/slow-a', '/slow-b', '/slow-c', '/slow-b'])
        self.assertEqual(target.read_bytes(), self.data)
        self.assertEqual(sum('[\u4f4e\u901f]' in line for line in self.logs), 3)
        self.assertTrue(any('[\u7ee7\u7eed]' in line for line in self.logs))

if __name__=='__main__':unittest.main()
