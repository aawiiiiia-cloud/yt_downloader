from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from edge_login import SITE_CONFIGS, has_site_auth_cookie
from xhs_image_downloader import (
    XhsCancelled,
    XhsDownloadError,
    XhsImage,
    XhsImageDownloader,
    XhsPost,
    XhsVideoPost,
    _convert_image,
    _image_candidates,
    _image_extension,
    _safe_name,
    extract_xhs_url,
    is_xhs_url,
    post_from_initial_state,
)


def _state() -> dict:
    return {
        "note": {
            "noteDetailMap": {
                "abc123": {
                    "note": {
                        "title": "示例/标题",
                        "desc": "正文",
                        "user": {"nickname": "作者"},
                        "imageList": [
                            {
                                "urlDefault": "https://img.example/one",
                                "urlPre": "https://img.example/one-preview",
                                "width": 1080,
                                "height": 1440,
                            },
                            {
                                "urlDefault": "https://img.example/two",
                                "width": "720",
                                "height": "960",
                            },
                        ],
                    }
                }
            }
        }
    }


class _Response(io.BytesIO):
    def __init__(
        self, data: bytes, content_length: int | None = None,
        content_type: str = "image/jpeg",
    ) -> None:
        super().__init__(data)
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Ydl:
    def __init__(self, factory) -> None:
        self.factory = factory

    def urlopen(self, _request):
        return self.factory()


class TestXhsParsing(unittest.TestCase):
    def test_supported_urls(self) -> None:
        self.assertTrue(is_xhs_url("https://www.xiaohongshu.com/explore/abc123"))
        self.assertTrue(is_xhs_url("https://xhslink.com/a/xyz"))
        self.assertFalse(is_xhs_url("https://example.com/explore/abc123"))

    def test_extracts_url_from_share_text(self) -> None:
        self.assertEqual(
            extract_xhs_url("复制这条笔记 https://xhslink.com/a/xyz ，打开小红书查看"),
            "https://xhslink.com/a/xyz",
        )

    def test_extracts_ordered_default_images(self) -> None:
        post = post_from_initial_state(
            _state(), "abc123", "https://www.xiaohongshu.com/explore/abc123",
        )
        self.assertEqual(post.title, "示例/标题")
        self.assertEqual(post.author, "作者")
        self.assertEqual([image.index for image in post.images], [1, 2])
        self.assertEqual(post.images[0].url, "https://img.example/one")
        self.assertEqual(post.images[1].width, 720)

    def test_video_only_note_has_clear_error(self) -> None:
        state = {"note": {"noteDetailMap": {"abc": {"note": {
            "video": {"media": {}},
            "imageList": [{"urlDefault": "https://img.example/video-cover"}],
        }}}}}
        with self.assertRaises(XhsVideoPost):
            post_from_initial_state(state, "abc", "https://www.xiaohongshu.com/explore/abc")

    def test_safe_windows_name_and_extension(self) -> None:
        self.assertEqual(_safe_name('a<b>:c/"d"', "fallback"), "a_b__c__d_")
        self.assertEqual(_image_extension("image/webp", "https://img/noext"), ".webp")

    def test_webpic_resource_id_builds_original_cdn_urls_first(self) -> None:
        display = (
            "https://sns-webpic-qc.xhscdn.com/202609142100/"
            "0123456789abcdef/1040g0123456789abcdef!watermark"
        )
        urls = _image_candidates({}, display)
        self.assertEqual(
            urls[0],
            "https://sns-na-i11.xhscdn.com/1040g0123456789abcdef",
        )
        self.assertEqual(urls[-1], display)
        self.assertFalse(any("sns-webpic" in url for url in urls[:-1]))

    def test_file_id_preserves_original_resource_prefix(self) -> None:
        display = (
            "https://sns-webpic-qc.xhscdn.com/202609142100/"
            "0123456789abcdef/display!watermark"
        )
        urls = _image_candidates({"fileId": "spectrum/1040g0123456789abcdef"}, display)
        self.assertEqual(
            urls[0],
            "https://sns-na-i11.xhscdn.com/spectrum/1040g0123456789abcdef",
        )

    def test_xhs_login_requires_real_session_cookie(self) -> None:
        self.assertIn("xiaohongshu", SITE_CONFIGS)
        anonymous = [{"domain": ".xiaohongshu.com", "name": "a1", "value": "x"}]
        logged_in = [{"domain": ".xiaohongshu.com", "name": "web_session", "value": "x"}]
        self.assertFalse(has_site_auth_cookie(anonymous, "xiaohongshu"))
        self.assertTrue(has_site_auth_cookie(logged_in, "xiaohongshu"))


class TestXhsDownload(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.post = XhsPost(
            note_id="abc123",
            title="测试",
            description="正文",
            author="作者",
            source_url="https://www.xiaohongshu.com/explore/abc123?xsec_token=secret",
            images=(XhsImage(1, "https://img.example/one"),),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_atomic_image_download_and_length_check(self) -> None:
        logs: list[str] = []
        downloader = XhsImageDownloader(progress=logs.append)
        target = downloader._download_one(
            _Ydl(lambda: _Response(b"image-data", len(b"image-data"))),
            self.post,
            self.post.images[0],
            self.folder,
        )
        self.assertEqual(target.name, "01.jpg")
        self.assertEqual(target.read_bytes(), b"image-data")
        self.assertFalse((self.folder / ".01.part").exists())
        self.assertTrue(any("网页展示图兜底" in line for line in logs))

    def test_truncated_image_is_rejected_and_temp_removed(self) -> None:
        downloader = XhsImageDownloader(progress=lambda _msg: None)
        with self.assertRaisesRegex(XhsDownloadError, "原图和展示图地址均下载失败"):
            downloader._download_one(
                _Ydl(lambda: _Response(b"short", 999)),
                self.post,
                self.post.images[0],
                self.folder,
            )
        self.assertFalse((self.folder / ".01.part").exists())

    def test_download_can_convert_jpeg_to_png_atomically(self) -> None:
        from PIL import Image

        source = io.BytesIO()
        Image.new("RGB", (12, 8), "red").save(source, format="JPEG")
        data = source.getvalue()
        logs: list[str] = []
        downloader = XhsImageDownloader(
            progress=logs.append, image_format="png", image_quality=90,
        )
        target = downloader._download_one(
            _Ydl(lambda: _Response(data, len(data), "image/jpeg")),
            self.post, self.post.images[0], self.folder,
        )
        self.assertEqual(target.name, "01.png")
        with Image.open(target) as converted:
            self.assertEqual(converted.format, "PNG")
            self.assertEqual(converted.size, (12, 8))
        self.assertFalse((self.folder / ".01.png.convert.part").exists())
        self.assertTrue(any("已转换为 PNG" in line for line in logs))

    def test_matching_target_format_preserves_original_bytes(self) -> None:
        data = b"original-webp-bytes"
        downloader = XhsImageDownloader(
            progress=lambda _msg: None, image_format="webp", image_quality=75,
        )
        target = downloader._download_one(
            _Ydl(lambda: _Response(data, len(data), "image/webp")),
            self.post, self.post.images[0], self.folder,
        )
        self.assertEqual(target.name, "01.webp")
        self.assertEqual(target.read_bytes(), data)

    def test_jpeg_conversion_flattens_transparency_to_white(self) -> None:
        from PIL import Image

        source = self.folder / "source.png"
        target = self.folder / "target.jpg"
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(source, format="PNG")
        _convert_image(source, target, "jpg", 95)
        with Image.open(target) as converted:
            self.assertEqual(converted.format, "JPEG")
            red, green, blue = converted.convert("RGB").getpixel((0, 0))
        self.assertGreater(min(red, green, blue), 245)

    def test_failed_original_url_falls_back_to_display_url(self) -> None:
        attempts = 0
        logs: list[str] = []

        def response():
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise OSError("original unavailable")
            return _Response(b"fallback-image", len(b"fallback-image"))

        image = XhsImage(
            1, "https://ci.xiaohongshu.com/token", ("https://img.example/display",),
        )
        downloader = XhsImageDownloader(progress=logs.append)
        target = downloader._download_one(_Ydl(response), self.post, image, self.folder)
        self.assertEqual(target.read_bytes(), b"fallback-image")
        self.assertTrue(any("回退" in line for line in logs))

    def test_cancel_does_not_leave_partial_file(self) -> None:
        cancel = threading.Event()
        cancel.set()
        downloader = XhsImageDownloader(progress=lambda _msg: None, cancel_event=cancel)
        with self.assertRaises(XhsCancelled):
            downloader._download_one(
                _Ydl(lambda: _Response(b"data", 4)),
                self.post,
                self.post.images[0],
                self.folder,
            )

    def test_summary_omits_signed_urls(self) -> None:
        XhsImageDownloader._write_summary(self.post, self.folder)
        raw = (self.folder / "post.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(data["note_id"], "abc123")
        self.assertNotIn("xsec_token", raw)
        self.assertNotIn("img.example", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
