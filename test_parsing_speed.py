from __future__ import annotations

import copy
import http.cookiejar
import io
import json
import threading
import unittest
from unittest import mock


from collector_core import Collector, Cancelled
from pot_provider import PotProviderManager, PROVIDER_VERSION


class SharedProviderTests(unittest.TestCase):
    def test_connect_validates_loopback_and_version_without_starting_a_process(self):
        manager = PotProviderManager()
        with mock.patch.object(manager, '_ping', return_value={'version': PROVIDER_VERSION}) as ping:
            for url in ('https://127.0.0.1:45001', 'http://localhost:45001', 'http://user@127.0.0.1:45001',
                        'http://127.0.0.1:45001/path', 'http://127.0.0.1:0', 'http://127.0.0.1:65536'):
                self.assertFalse(manager.connect(url))
            ping.assert_not_called()
            self.assertTrue(manager.connect('http://127.0.0.1:45001'))
        with mock.patch('pot_provider.subprocess.Popen') as popen:
            opts = {}; manager.apply_to_opts(opts); manager.stop()
            popen.assert_not_called()
        self.assertEqual(opts['extractor_args']['youtubepot-bgutilhttp']['base_url'], ['http://127.0.0.1:45001'])
        self.assertEqual(opts['extractor_args']['youtube']['fetch_pot'], ['always'])
        self.assertIsNone(manager.url)
        with mock.patch.object(manager, '_ping', return_value={'version': 'wrong'}):
            self.assertFalse(manager.connect('http://127.0.0.1:45001'))

    def test_electron_task_borrows_provider_and_keeps_legacy_startup_for_other_callers(self):
        url = 'https://www.youtube.com/watch?v=fixture'
        with mock.patch('pot_provider.PotProviderManager') as factory:
            service = Collector({'url': url, 'providerUrl': 'http://127.0.0.1:45001'}, lambda *_: None)
            service.options(url); factory.return_value.connect.assert_called_once_with('http://127.0.0.1:45001')
            factory.return_value.ensure_started.assert_not_called()
            service.close(); factory.return_value.stop.assert_called_once()
        with mock.patch('pot_provider.PotProviderManager') as factory:
            service = Collector({'url': url}, lambda *_: None); service.options(url)
            factory.return_value.ensure_started.assert_called_once()
            factory.return_value.connect.assert_not_called()

    def test_failed_application_startup_does_not_repeat_the_full_wait_inside_each_task(self):
        service = Collector({'url': 'https://www.youtube.com/watch?v=fixture', 'providerUrl': None}, lambda *_: None)
        with mock.patch('pot_provider.PotProviderManager') as factory:
            service.options(service.request['url'])
            factory.return_value.ensure_started.assert_not_called()
            factory.return_value.apply_to_opts.assert_not_called()

    def test_provider_control_pipe_eof_releases_the_owned_server(self):
        import collector_worker
        output = io.StringIO()
        with (mock.patch.object(collector_worker.sys, 'stdin', io.StringIO('{"command":"provider"}\n')),
              mock.patch.object(collector_worker.sys, 'stdout', output),
              mock.patch.object(collector_worker, 'prepare_runtime'),
              mock.patch('pot_provider.PotProviderManager') as factory):
            manager = factory.return_value
            manager.ensure_started.return_value = True; manager.url = 'http://127.0.0.1:45001'
            result = collector_worker.main()
        manager.stop.assert_called_once()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['type'], 'cancelled')


def config_fixture():
    return {'video': {'id': 123, 'title': 'Vimeo fixture', 'duration': 62, 'thumbs': {'640': 'https://i.vimeocdn.com/a.jpg'}},
            'request': {'files': {'progressive': [{'quality': '360p', 'url': 'https://fixture.vimeocdn.com/v.mp4'}],
                                  'hls': {'cdns': {'one': {'url': 'https://one.vimeocdn.com/master.m3u8'},
                                                   'two': {'url': 'https://two.vimeocdn.com/master.m3u8'}}},
                                  'dash': {'cdns': {'one': {'url': 'https://one.vimeocdn.com/master.mpd'},
                                                    'two': {'url': 'https://two.vimeocdn.com/master.mpd'}}}}}}


def fake_parse(ie, config, _video_id):
    files = config['video'].get('files') or config.get('request', {}).get('files') or {}
    formats = [{'format_id': 'http-' + row['quality']} for row in files.get('progressive', [])]
    for kind in ('hls', 'dash'):
        for name in files.get(kind, {}).get('cdns', {}):
            formats.extend([{'format_id': f'{kind}-{name}-1080p'}, {'format_id': f'{kind}-{name}-audio-en'}])
    return {'id': '123', 'title': config['video']['title'], 'duration': 62, 'formats': formats,
            'subtitles': {'en': [{'url': 'https://fixture.vimeocdn.com/subtitle.vtt', 'ext': 'vtt'}]}}


class VimeoConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global yt_dlp, VimeoIE, ParallelVimeoIE
        import yt_dlp
        from yt_dlp.extractor.vimeo import VimeoIE
        from vimeo_extractor import ParallelVimeoIE

    def test_all_manifest_branches_run_concurrently_with_isolated_cookies_and_same_format_order(self):
        config = config_fixture(); before = copy.deepcopy(config)
        barrier = threading.Barrier(4); records = []; lock = threading.Lock()
        service = Collector({}, lambda *_: None)
        options = {'quiet': True, 'proxy': 'http://127.0.0.1:7890', 'http_headers': {'User-Agent': 'FixtureChromium'},
                   'logger': service, 'progress_hooks': [service.progress], 'postprocessor_hooks': [service.postprocess]}
        with yt_dlp.YoutubeDL(options, auto_init=False) as downloader:
            cookie = http.cookiejar.Cookie(0, 'fixture', 'private', None, False, '.vimeo.com', True, True,
                                            '/', True, True, None, False, None, None, {}, False)
            downloader.cookiejar.set_cookie(cookie)

            def parse(ie, part, video_id):
                files = part['video'].get('files') or part.get('request', {}).get('files') or {}
                if any(files.get(kind, {}).get('cdns') for kind in ('hls', 'dash')):
                    barrier.wait(timeout=5)
                    with lock:
                        records.append((ie._downloader, list(ie._downloader.cookiejar)))
                    self.assertIsNone(ie._downloader.params.get('cookiefile'))
                    self.assertEqual(ie._downloader.params['proxy'], options['proxy'])
                    self.assertEqual(ie._downloader.params['http_headers']['User-Agent'], 'FixtureChromium')
                return fake_parse(ie, part, video_id)

            with mock.patch.object(VimeoIE, '_parse_config', parse):
                result = ParallelVimeoIE(downloader)._parse_config(config, '123')
        self.assertEqual(config, before)
        self.assertEqual(result['formats'], fake_parse(None, config, '123')['formats'])
        self.assertEqual(len(result['subtitles']['en']), 1)
        self.assertEqual(len({id(row[0]) for row in records}), 4)
        self.assertTrue(all(row[1][0].value == 'private' and row[1][0] is not cookie for row in records))

    def test_only_a_failed_branch_retries_serially_without_losing_other_qualities(self):
        config = config_fixture(); attempts = []; lock = threading.Lock()

        def parse(ie, part, video_id):
            files = part['video'].get('files') or part.get('request', {}).get('files') or {}
            if 'one' in files.get('hls', {}).get('cdns', {}):
                with lock:
                    attempts.append(threading.current_thread() is threading.main_thread())
                if not attempts[-1]:
                    raise RuntimeError('controlled transport failure')
            return fake_parse(ie, part, video_id)

        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}, auto_init=False) as downloader:
            with mock.patch.object(VimeoIE, '_parse_config', parse):
                result = ParallelVimeoIE(downloader)._parse_config(config, '123')
        self.assertEqual(attempts, [False, True])
        self.assertEqual(result['formats'], fake_parse(None, config, '123')['formats'])

    def test_video_files_take_priority_and_are_not_duplicated_via_request_files(self):
        config = config_fixture()
        config['video']['files'] = copy.deepcopy(config['request']['files'])
        with yt_dlp.YoutubeDL({'quiet': True}, auto_init=False) as downloader:
            with mock.patch.object(VimeoIE, '_parse_config', fake_parse):
                result = ParallelVimeoIE(downloader)._parse_config(config, '123')
        self.assertEqual(result['formats'], fake_parse(None, config, '123')['formats'])

    def test_single_manifest_and_live_events_keep_upstream_parsing(self):
        single, live = config_fixture(), config_fixture()
        single['request']['files'] = {'hls': {'cdns': {'one': {'url': 'https://fixture.vimeocdn.com/master.m3u8'}}}}
        live['video']['live_event'] = {'status': 'started'}
        for config in (single, live):
            with yt_dlp.YoutubeDL({'quiet': True}, auto_init=False) as downloader:
                with mock.patch.object(VimeoIE, '_parse_config', side_effect=lambda part, video_id: fake_parse(None, part, video_id)) as upstream:
                    result = ParallelVimeoIE(downloader)._parse_config(config, '123')
            upstream.assert_called_once_with(config, '123')
            self.assertTrue(result['formats'])

    def test_cancellation_stops_before_starting_manifest_requests(self):
        with yt_dlp.YoutubeDL({'quiet': True}, auto_init=False) as downloader:
            with mock.patch.object(VimeoIE, '_parse_config') as upstream:
                extractor = ParallelVimeoIE(downloader, lambda: (_ for _ in ()).throw(Cancelled('cancelled')))
                with self.assertRaises(Cancelled):
                    extractor._parse_config(config_fixture(), '123')
            upstream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
