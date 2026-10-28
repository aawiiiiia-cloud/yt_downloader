from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from image_summary import summary_lock, write_image_summary


def write_worker(path, index, ready):
    if not ready.wait(10):
        raise RuntimeError('worker start timed out')
    write_image_summary(Path(path), {'site': 'fixture', 'work_id': 'same'},
                        [{'index': index, 'width': index * 100, 'height': 200}], ('site', 'work_id'))


def hold_worker(path, entered, release):
    with summary_lock(Path(path)):
        entered.set()
        release.wait(15)


class ImageSummaryTests(unittest.TestCase):
    def test_separate_processes_merge_all_records_without_lost_updates(self):
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'work.json'; ready = context.Event()
            children = [context.Process(target=write_worker, args=(str(target), index, ready)) for index in range(1, 9)]
            try:
                for child in children: child.start()
                ready.set()
                for child in children: child.join(15)
                self.assertTrue(all(child.exitcode == 0 for child in children))
                data = json.loads(target.read_text(encoding='utf-8'))
                self.assertEqual(data['image_count'], 8)
                self.assertEqual([row['index'] for row in data['images']], list(range(1, 9)))
                self.assertTrue(all(row['width'] == row['index'] * 100 for row in data['images']))
                self.assertFalse(list(Path(folder).glob('.*.tmp')))
            finally:
                for child in children:
                    if child.is_alive(): child.terminate(); child.join(3)

    def test_lock_recovers_when_another_worker_is_terminated(self):
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'work.json'; entered, release = context.Event(), context.Event()
            child = context.Process(target=hold_worker, args=(str(target), entered, release)); child.start()
            try:
                self.assertTrue(entered.wait(8))
                with self.assertRaises(TimeoutError):
                    with summary_lock(target, timeout=.1): self.fail('another process still owns the lock')
                child.terminate(); child.join(3)
                write_image_summary(target, {'id': 'same'}, [{'index': 1}], ('id',))
                self.assertEqual(json.loads(target.read_text())['image_count'], 1)
            finally:
                # A killed multiprocessing.Event waiter can leave its condition
                # counters stale. Never notify that event after termination.
                if child.is_alive(): child.terminate()
                child.join(3)

    def test_waiting_writer_honours_cancellation_without_touching_existing_data(self):
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'work.json'; original = b'{"id":"old","images":[]}'
            target.write_bytes(original); entered, release = context.Event(), context.Event()
            child = context.Process(target=hold_worker, args=(str(target), entered, release)); child.start()
            count = 0
            def cancel():
                nonlocal count
                count += 1
                if count >= 2: raise RuntimeError('fixture cancelled')
            try:
                self.assertTrue(entered.wait(8))
                with self.assertRaisesRegex(RuntimeError, 'fixture cancelled'):
                    write_image_summary(target, {'id': 'old'}, [{'index': 1}], ('id',), cancel)
                self.assertEqual(target.read_bytes(), original); self.assertFalse(list(Path(folder).glob('.*.tmp')))
            finally:
                release.set(); child.join(3)
                if child.is_alive(): child.terminate(); child.join(3)

    def test_failed_publication_keeps_old_summary_and_removes_only_its_own_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'work.json'
            write_image_summary(target, {'id': 'same'}, [{'index': 1}], ('id',)); original = target.read_bytes()
            with mock.patch('image_summary.os.replace', side_effect=OSError('fixture disk failure')):
                with self.assertRaises(OSError): write_image_summary(target, {'id': 'same'}, [{'index': 2}], ('id',))
            self.assertEqual(target.read_bytes(), original); self.assertFalse(list(Path(folder).glob('.*.tmp')))
            write_image_summary(target, {'id': 'same'}, [{'index': 2}], ('id',))
            self.assertEqual(json.loads(target.read_text())['image_count'], 2)

    def test_foreign_or_corrupt_existing_summaries_are_preserved(self):
        for original in (b'{"id":"someone-else","images":[]}', b'{broken'):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'work.json'; target.write_bytes(original)
                with self.assertRaises(ValueError): write_image_summary(target, {'id': 'same'}, [{'index': 1}], ('id',))
                self.assertEqual(target.read_bytes(), original)

    def test_legacy_counts_recover_saved_indices_and_new_aliases_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'post.json'; target.write_text('{"note_id":"same","image_count":1}')
            (target.parent / '01.png').write_bytes(b'previously-saved-image')
            write_image_summary(target, {'note_id': 'same'}, [{'index': 2, 'names': ['red']}], ('note_id',))
            write_image_summary(target, {'note_id': 'same'}, [{'index': 2, 'names': ['red', 'red set']}], ('note_id',))
            data = json.loads(target.read_text()); self.assertEqual(data['image_count'], 2)
            self.assertEqual(data['images'][1]['names'], ['red', 'red set'])

    def test_every_site_writer_merges_independent_selections(self):
        from xhs_image_downloader import XhsImage, XhsPost, XhsImageDownloader
        from behance_adapter import BehanceImages
        from taobao_adapter import TaobaoImages
        from zcool_adapter import ZcoolImages
        post = XhsPost('fixture', 'fixture', 'description', 'author', 'https://www.behance.net/gallery/123/Fixture',
                       (XhsImage(1, 'https://img.example/1'), XhsImage(2, 'https://img.example/2')))
        for kind, filename in [('xhs', 'post.json'), ('behance', 'project.json'), ('taobao', 'product.json'), ('zcool', 'work.json')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                writers = [XhsImageDownloader(), XhsImageDownloader()]
                if kind == 'behance': writers = [BehanceImages(), BehanceImages()]
                if kind == 'taobao': writers = [TaobaoImages(category='main', rows=[{'index': i, 'names': [str(i)]} for i in [1, 2]]) for _ in [1, 2]]
                if kind == 'zcool':
                    writers = [ZcoolImages(rows=[]) for _ in [1, 2]]
                    for i, writer in enumerate(writers, 1): writer.verified[i] = {'index': i, 'width': 100 * i, 'height': 200, 'dimensionsVerified': True}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(writer._write_summary, replace(post, images=(post.images[i],)), Path(folder)) for i, writer in enumerate(writers)]
                    for future in futures: future.result(5)
                data = json.loads((Path(folder) / filename).read_text(encoding='utf-8'))
                self.assertEqual([row['index'] for row in data['images']], [1, 2]); self.assertEqual(data['image_count'], 2)
                self.assertNotIn('img.example', json.dumps(data))

    def test_actual_batch_and_distinct_single_download_preserve_all_files_and_manifest(self):
        from collector_core import Collector
        from test_taobao_adapter import URL, context, response, image_bytes
        from PIL import Image
        data = context(); data['groups']['main'].append({'url': 'https://img.alicdn.com/c.jpg'})
        entered = threading.Barrier(2); content = image_bytes()
        def get(url, **kwargs):
            if url.endswith('/a.jpg') or url.endswith('/c.jpg'): entered.wait(5)
            return response(content)
        with tempfile.TemporaryDirectory() as folder, mock.patch('requests.Session.get', side_effect=get):
            workers = [Collector({'url': URL, 'context': data, 'kind': 'images', 'mediaId': 'main', 'indices': indices,
                                  'settings': {'saveDir': folder}}, lambda *_: None) for indices in ([1, 2], [3])]
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker.download) for worker in workers]
                for future in futures: future.result(10)
            output = next(Path(folder).rglob('product.json')).parent
            self.assertEqual([p.name for p in sorted(output.glob('*.png'))], ['01.png', '02.png', '03.png'])
            for file in output.glob('*.png'):
                self.assertEqual(file.read_bytes(), content)
                with Image.open(file) as image: image.load(); self.assertEqual(image.size, (9, 7))
            self.assertEqual([row['index'] for row in json.loads((output / 'product.json').read_text(encoding='utf-8'))['images']], [1, 2, 3])
            self.assertFalse(list(output.glob('.*.tmp'))); self.assertFalse(list(output.glob('*.part')))


if __name__ == '__main__':
    unittest.main()
