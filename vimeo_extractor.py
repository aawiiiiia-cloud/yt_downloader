"""Fetch independent Vimeo manifest branches concurrently without dropping formats.

Each branch gets its own downloader, cookie jar, and request handlers. Only the
main thread merges results, in the same order as the upstream extractor.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import yt_dlp
from yt_dlp.extractor.vimeo import VimeoIE


class ParallelVimeoIE(VimeoIE):
    @classmethod
    def ie_key(cls):
        return VimeoIE.ie_key()

    def __init__(self, downloader, check_cancel=lambda: None):
        super().__init__(downloader)
        self.check_cancel = check_cancel

    def _parse_config(self, config, video_id):
        self.check_cancel()
        video = config.get('video') or {}
        request = config.get('request') or {}
        files = video.get('files') or request.get('files') or {}
        branches = []
        for kind in ('hls', 'dash'):
            group = files.get(kind) or {}
            if not isinstance(group, dict) or not isinstance(group.get('cdns') or {}, dict):
                return super()._parse_config(config, video_id)
            branches.extend((kind, name, data) for name, data in (group.get('cdns') or {}).items()
                            if isinstance(data, dict) and data.get('url'))
        if len(branches) < 2 or video.get('live_event'):
            return super()._parse_config(config, video_id)

        def with_files(selected):
            part = copy.deepcopy(config)
            # Match upstream's preference for video.files over request.files.
            part['video'].pop('files', None)
            part.setdefault('request', {}).pop('files', None)
            if video.get('files'):
                part['video']['files'] = selected
            else:
                part.setdefault('request', {})['files'] = selected
            return part

        base = with_files({key: copy.deepcopy(value) for key, value in files.items() if key not in ('hls', 'dash')})
        parts = [with_files({kind: {'cdns': {name: copy.deepcopy(data)}}}) for kind, name, data in branches]
        logger = self._downloader.params.get('logger')
        params = {key: value for key, value in self._downloader.params.items()
                  if key not in {'logger', 'progress_hooks', 'postprocessor_hooks', 'postprocessors'}}
        params.update(cookiefile=None, cookiesfrombrowser=None, cachedir=False)
        cookies = [copy.copy(cookie) for cookie in self._downloader.cookiejar]

        def extract(part):
            self.check_cancel()
            options = copy.deepcopy(params)
            if logger is not None:
                options['logger'] = logger
            with yt_dlp.YoutubeDL(options, auto_init=False) as downloader:
                for cookie in cookies:
                    downloader.cookiejar.set_cookie(copy.copy(cookie))
                extractor = VimeoIE(downloader)
                extractor._x_forwarded_for_ip = getattr(self, '_x_forwarded_for_ip', None)
                info = extractor._parse_config(part, video_id)
                self.check_cancel()
                return info

        self.to_screen('正在并行获取可用画质')
        with ThreadPoolExecutor(max_workers=min(4, len(parts)), thread_name_prefix='vimeo-manifest') as pool:
            futures = [pool.submit(extract, part) for part in parts]
            result = super()._parse_config(base, video_id)
            for part, future in zip(parts, futures):
                self.check_cancel()
                try:
                    info = future.result()
                except Exception:
                    self.check_cancel()
                    # A per-branch compatibility failure retries through upstream;
                    # it must not silently remove a quality or audio track.
                    self.report_warning('一条画质线路未完成，正在重新获取')
                    info = super()._parse_config(part, video_id)
                result['formats'].extend(info.get('formats') or [])
                self._merge_subtitles(info.get('subtitles') or {}, target=result['subtitles'])
        self.check_cancel()
        return result
