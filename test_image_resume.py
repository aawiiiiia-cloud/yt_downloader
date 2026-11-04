from __future__ import annotations

import io
import unittest

from image_resume import OriginalResponse, Representation


URL = "https://sns-na-i11.xhscdn.com/resource"


class Response(io.BytesIO):
    def __init__(self, data, *, size=None, status=200, etag='"Exact"', url=URL, content_range=None):
        super().__init__(data)
        self.url, self.status = url, status
        self.headers = {"Content-Length": str(size if size is not None else len(data)),
                        "Content-Type": "image/png", "ETag": etag}
        if content_range:
            self.headers["Content-Range"] = content_range


class Ydl:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def urlopen(self, request):
        self.requests.append(request)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class ResumeTests(unittest.TestCase):
    def drain(self, response):
        content, generation = b"", 0
        while True:
            block = response.read(4)
            if response.generation != generation:
                content, generation = b"", response.generation
            if not block:
                return content
            content += block

    def test_correct_range_keeps_bytes_and_full_length(self):
        first = Response(b"abcd", size=12)
        suffix = Response(b"efghijkl", status=206, content_range="bytes 4-11/12")
        ydl = Ydl([suffix])
        with OriginalResponse(ydl, URL, first, {"Accept-Encoding": "identity"}, 1) as stream:
            self.assertEqual(self.drain(stream), b"abcdefghijkl")
            self.assertEqual(stream.generation, 0)
            self.assertEqual(stream.headers["Content-Length"], "12")
        self.assertEqual(ydl.requests[0].headers["Range"], "bytes=4-")
        self.assertEqual(ydl.requests[0].headers["If-Range"], '"Exact"')
        self.assertTrue(first.closed and suffix.closed)

    def test_ignored_range_restarts_without_stitching(self):
        ydl = Ydl([Response(b"NEW-CONTENT!", size=12)])
        with OriginalResponse(ydl, URL, Response(b"abcd", size=12), {}, 1) as stream:
            self.assertEqual(self.drain(stream), b"NEW-CONTENT!")
            self.assertEqual(stream.generation, 1)

    def test_changed_validator_or_final_url_or_range_never_stitches(self):
        for changes in ({"etag": '"exact"'}, {"url": URL.replace("i11", "i10")},
                        {"content_range": "bytes 3-10/12"},
                        {"size": 7}, {"content_range": "bytes 4-11/13"}):
            with self.subTest(changes=changes):
                options = {"status": 206, "content_range": "bytes 4-11/12", **changes}
                rejected = Response(b"efghijkl", **options)
                ydl = Ydl([rejected, Response(b"abcdefghijkl")])
                with OriginalResponse(ydl, URL, Response(b"abcd", size=12), {}, 1) as stream:
                    self.assertEqual(self.drain(stream), b"abcdefghijkl")
                    self.assertEqual(stream.generation, 1)
                self.assertTrue(rejected.closed)
                self.assertEqual(len(ydl.requests), 2)
                self.assertNotIn("Range", ydl.requests[1].headers)

    def test_weak_missing_unknown_or_encoded_representation_cannot_resume(self):
        for etag, length, encoding in [('W/"Exact"', 12, ''), ('', 12, ''),
                                       ('"Exact"', None, ''), ('"Exact"', 12, 'gzip')]:
            with self.subTest(etag=etag, length=length, encoding=encoding):
                first = Response(b"abcd", size=length, etag=etag)
                if length is None:
                    first.headers.pop("Content-Length")
                if encoding:
                    first.headers["Content-Encoding"] = encoding
                ydl = Ydl([])
                with OriginalResponse(ydl, URL, first, {}, 1) as stream:
                    if length is None:
                        self.assertEqual(self.drain(stream), b"abcd")
                    else:
                        with self.assertRaises(OSError):
                            self.drain(stream)
                self.assertEqual(ydl.requests, [])

    def test_recovery_is_bounded_and_416_is_not_completion(self):
        for replies in ([OSError("HTTP 416")],
                        [Response(b"efgh", size=8, status=206, content_range="bytes 4-11/12"),
                         Response(b"ij", size=4, status=206, content_range="bytes 8-11/12")]):
            ydl = Ydl(replies)
            with OriginalResponse(ydl, URL, Response(b"abcd", size=12), {}, 1) as stream:
                with self.assertRaises(OSError):
                    self.drain(stream)
            self.assertLessEqual(len(ydl.requests), 2)

    def test_cancel_after_range_headers_closes_the_unadopted_response(self):
        cancelled = False
        suffix = Response(b"efghijkl", status=206, content_range="bytes 4-11/12")
        class CancelYdl(Ydl):
            def urlopen(self, request):
                nonlocal cancelled
                cancelled = True
                return super().urlopen(request)
        def check():
            if cancelled:
                raise RuntimeError("cancelled")
        ydl = CancelYdl([suffix])
        with OriginalResponse(ydl, URL, Response(b"abcd", size=12), {}, 1, check_cancel=check) as stream:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.drain(stream)
        self.assertTrue(suffix.closed)

    def test_partial_initial_response_is_rejected(self):
        response = Response(b"abcd", status=206)
        with self.assertRaises(OSError):
            OriginalResponse(Ydl([]), URL, response, {}, 1)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
