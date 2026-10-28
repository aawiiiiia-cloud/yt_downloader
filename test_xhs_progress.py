from __future__ import annotations
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from collector_core import Collector
from test_xhs_image_downloader import _Response, _Ydl, _image_data
from xhs_image_downloader import XhsImageDownloader, XhsImage, XhsPost, XhsCancelled, XhsDownloadError

class _ContextYdl(_Ydl):
    def __enter__(self): return self
    def __exit__(self, *_args): pass

class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.data = _image_data()
        self.post = XhsPost('abc123', 'fixture', '', '',
            'https://www.xiaohongshu.com/explore/abc123?xsec_token=secret',
            (XhsImage(7, 'https://img.example/image?token=private'),))
        self.events, self.logs = [], []
        self.writer = XhsImageDownloader(progress=self.logs.append, transfer_progress=self.events.append)
    def download(self, factory=None):
        return self.writer._download_one(_Ydl(factory or (lambda: _Response(self.data, len(self.data)))),
            self.post, self.post.images[0], self.folder)
    def test_connection_log_precedes_even_first_response(self):
        def waiting():
            self.assertTrue(any('[连接]' in row for row in self.logs))
            self.assertEqual(self.events[-1]['status'], 'image-connecting')
            self.assertIsNone(self.events[-1]['percent'])
            return _Response(self.data, len(self.data))
        self.download(waiting)
        self.assertNotIn('private', '\n'.join(self.logs))
        self.assertNotIn('secret', '\n'.join(self.logs))
    def test_real_byte_progress_happens_before_atomic_save(self):
        class Chunks(_Response):
            def read(inner, count=-1):
                return super().read(min(count, 100))
        snapshots = []
        def report(event):
            self.events.append(event)
            if event['status'] == 'image-receiving' and event['downloadedBytes']:
                snapshots.append((event['percent'], (self.folder / '07.jpg').exists()))
        self.writer.transfer_progress = report
        self.download(lambda: Chunks(self.data, len(self.data)))
        self.assertTrue(snapshots)
        self.assertGreater(snapshots[0][0], 0)
        self.assertLess(snapshots[0][0], 99)
        self.assertFalse(snapshots[0][1])
        self.assertTrue((self.folder / '07.jpg').is_file())
        self.assertTrue(all(event['percent'] is None or event['percent'] < 100 for event in self.events))
    def test_unknown_length_shows_bytes_not_fake_percentage(self):
        self.download(lambda: _Response(self.data))
        receiving = [event for event in self.events if event['status'] == 'image-receiving']
        self.assertTrue(all(event['percent'] is None for event in receiving))
        self.assertEqual(receiving[-1]['downloadedBytes'], len(self.data))
        self.assertEqual(self.events[-1]['status'], 'image-validating')
    def test_single_selected_image_seven_completes_at_100_not_700(self):
        events = []
        writer = Collector({'settings': {}}, lambda kind, data: events.append((kind, data))).xhs_downloader()
        with mock.patch('yt_dlp.YoutubeDL', return_value=_ContextYdl(lambda: _Response(self.data, len(self.data)))):
            writer.download_post(self.post, self.folder)
        percents = [data['percent'] for kind, data in events if kind == 'progress']
        self.assertEqual(percents[-1], 100)
        self.assertTrue(all(value is None or 0 <= value <= 100 for value in percents))
    def test_sparse_selection_uses_selected_count_keeps_original_names(self):
        events = []
        writer = Collector({'settings': {}}, lambda kind, data: events.append((kind, data))).xhs_downloader()
        post = replace(self.post, images=(XhsImage(2, 'https://img.example/2'), XhsImage(7, 'https://img.example/7')))
        with mock.patch('yt_dlp.YoutubeDL', return_value=_ContextYdl(lambda: _Response(self.data, len(self.data)))):
            _, paths = writer.download_post(post, self.folder)
        self.assertEqual([path.name for path in paths], ['02.jpg', '07.jpg'])
        saved = [data['percent'] for kind, data in events if kind == 'progress' and data['status'] == 'downloading']
        self.assertEqual(saved, [50, 100])
        second = [data for kind, data in events if kind == 'progress' and data.get('imageIndex') == 7 and data['status'] == 'image-validating']
        self.assertEqual(second[-1]['percent'], 99.5)
    def test_retry_resets_transfer_bytes_and_announces_new_attempt(self):
        calls = 0
        class Broken(_Response):
            def read(inner, count=-1):
                if inner.tell(): raise TimeoutError('socket timed out')
                return super().read(100)
        def response():
            nonlocal calls
            calls += 1
            return Broken(self.data, len(self.data)) if calls == 1 else _Response(self.data, len(self.data))
        self.download(response)
        connections = [event for event in self.events if event['status'] == 'image-connecting']
        self.assertEqual(len(connections), 2)
        self.assertTrue(all(event['downloadedBytes'] == 0 for event in connections))
        self.assertTrue(any('[重试]' in line for line in self.logs))
        self.assertFalse((self.folder / '.07.part').exists())
    def test_failed_response_does_not_report_completion_or_overwrite(self):
        old = self.folder / '07.jpg'; old.write_bytes(self.data)
        with self.assertRaises(XhsDownloadError):
            self.download(lambda: _Response(b'incomplete', 100))
        self.assertEqual(old.read_bytes(), self.data)
        self.assertFalse(any(event['status'] == 'image-validating' for event in self.events))
        self.assertFalse((self.folder / '.07.part').exists())
    def test_cancel_while_receiving_removes_partial_file(self):
        flag = threading.Event(); self.writer.cancel_event = flag
        def receive(event):
            self.events.append(event)
            if event['downloadedBytes']: flag.set()
        self.writer.transfer_progress = receive
        with self.assertRaises(XhsCancelled): self.download()
        self.assertFalse((self.folder / '.07.part').exists())
        self.assertFalse((self.folder / '07.jpg').exists())
    def test_conversion_stage_is_explicit(self):
        self.writer.image_format = 'png'
        target = self.download()
        self.assertEqual(target.suffix, '.png')
        self.assertEqual(self.events[-1]['status'], 'image-converting')
    def test_legacy_callback_keeps_original_index_argument(self):
        saved = []
        self.writer.item_progress = lambda *args: saved.append(args)
        self.download()
        self.assertEqual(saved, [(7, 1, '07.jpg')])

if __name__ == '__main__': unittest.main()
