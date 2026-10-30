"""Report actual PO Token acquisition without enabling sensitive debug output."""
from __future__ import annotations

import re

from yt_dlp.extractor.youtube import YoutubeIE


class LoggedYoutubeIE(YoutubeIE):
    @classmethod
    def ie_key(cls):
        return YoutubeIE.ie_key()

    def __init__(self, downloader):
        super().__init__(downloader)
        self._reported_po_tokens = set()

    def fetch_po_token(self, *args, **kwargs):
        # Preserve upstream generation, validation, caching and failure handling.
        token = super().fetch_po_token(*args, **kwargs)
        if not isinstance(token, str) or not token.strip():
            return token

        client = kwargs.get('client', args[0] if args else 'web')
        if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', client):
            client = 'unknown'
        context = kwargs.get('context', args[1] if len(args) > 1 else 'gvs')
        context = getattr(context, 'value', context)
        purposes = {'player': '视频信息', 'gvs': '视频/音频下载', 'subs': '字幕'}
        if not isinstance(context, str) or context not in purposes:
            context = 'unknown'
        video_id = kwargs.get('video_id')
        key = (video_id if isinstance(video_id, str) else None, client, context)
        if key not in self._reported_po_tokens:
            purpose = purposes.get(context, '其他用途')
            # "Acquired" includes valid cache/config returns; it does not claim
            # a new token was minted or that YouTube will accept the download.
            self.to_screen(f'[PO Token] 已取得 · {purpose}（{context}）· {client}')
            self._reported_po_tokens.add(key)
        return token
