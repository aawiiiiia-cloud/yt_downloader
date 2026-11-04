"""Strict, same-representation recovery using the existing yt-dlp transport."""
from __future__ import annotations

import re
from dataclasses import dataclass

from yt_dlp.networking.common import Request


def _header(response, name):
    return str(response.headers.get(name) or "").strip()


def _length(response):
    text = _header(response, "Content-Length")
    return int(text) if text.isdecimal() and int(text) > 0 else None


def _status(response):
    return getattr(response, "status", 200)


@dataclass(frozen=True)
class Representation:
    url: str
    etag: str
    size: int | None
    mime: str
    identity: bool

    @classmethod
    def capture(cls, response, requested_url):
        etag = _header(response, "ETag")
        # Entity tags are opaque and case sensitive. Weak tags cannot authorize
        # stitching byte ranges, even when the lengths happen to agree.
        if not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag):
            etag = ""
        return cls(
            getattr(response, "url", None) or requested_url,
            etag, _length(response),
            _header(response, "Content-Type").split(";", 1)[0].lower(),
            _header(response, "Content-Encoding").lower() in {"", "identity"},
        )

    def accepts(self, response, offset):
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", _header(response, "Content-Range"), re.I)
        if not match or _status(response) != 206 or not self.etag or not self.identity:
            return False
        start, end, size = map(int, match.groups())
        other = self.capture(response, self.url)
        return (
            start == offset and offset <= end < size and size == self.size
            and other.size == end - start + 1
            and other.url == self.url and other.etag == self.etag
            and other.identity and other.mime == self.mime
        )


class OriginalResponse:
    """Expose a full resource while recovering interrupted reads on the SAME URL.

    A generation change means the caller must truncate its partial file before
    writing the next returned block. No range is ever combined across origins.
    """

    def __init__(self, ydl, url, response, headers, timeout, *, prefix=b"",
                 check_cancel=lambda: None, on_recovery=lambda *_: None, retries=2):
        self.ydl, self.url = ydl, url
        self.request_headers, self.timeout = dict(headers), timeout
        self.check_cancel, self.on_recovery = check_cancel, on_recovery
        self.retries = retries
        self.generation = 0
        self.closed = False
        self._install(response, prefix)

    def _install(self, response, prefix=b""):
        if _status(response) != 200:
            response.close()
            raise OSError("Original image did not return a complete representation")
        self.response = response
        self.headers = response.headers
        self.representation = Representation.capture(response, self.url)
        self.prefix = prefix
        self.offset = 0

    def _open(self, extra=None):
        return self.ydl.urlopen(Request(
            self.url, headers={**self.request_headers, **(extra or {})},
            extensions={"timeout": self.timeout},
        ))

    def adopt(self, url, response, prefix):
        self.response.close()
        self.url = url
        self._install(response, prefix)
        self.generation += 1

    def _recover(self):
        self.check_cancel()
        original = self.representation
        offset = self.offset
        self.response.close()
        response = None
        try:
            response = self._open({"Range": f"bytes={offset}-", "If-Range": original.etag})
            self.check_cancel()
            if original.accepts(response, offset):
                self.response = response
                self.on_recovery("resume", offset)
                return
            if _status(response) == 200:
                self._install(response)
                response = None
            else:
                response.close()
                response = None
                self._install(self._open())
            self.generation += 1
            self.on_recovery("restart", offset)
        except Exception:
            if response is not None:
                response.close()
            raise

    def read(self, size):
        while True:
            self.check_cancel()
            try:
                if self.prefix:
                    block, self.prefix = self.prefix[:size], self.prefix[size:]
                else:
                    block = self.response.read(size)
                if not block and self.representation.size and self.offset < self.representation.size:
                    raise OSError("Original image response ended before Content-Length")
                self.offset += len(block)
                return block
            except Exception:
                self.check_cancel()
                if not (self.retries > 0 and self.representation.etag
                        and self.representation.identity and self.representation.size
                        and 0 < self.offset < self.representation.size):
                    raise
                self.retries -= 1
                self._recover()

    def close(self):
        if not self.closed:
            self.closed = True
            self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
