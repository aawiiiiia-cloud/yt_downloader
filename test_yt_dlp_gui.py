"""yt_dlp_gui 的回归测试(unittest,零额外依赖)。

运行: python -m unittest test_yt_dlp_gui -v

覆盖:Settings 持久化、按站点选择 Cookies、代理/码率 opts 组装、
edge_login 纯逻辑(双站点认证判断/Netscape 写出)、内置登录 GUI 侧回调。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import tkinter as tk
import types
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from edge_login import (  # noqa: E402
    LoginError,
    get_site_cookies,
    get_youtube_cookies,
    has_auth_cookie_on,
    has_site_auth_cookie,
    write_netscape,
    run_login,
)
from yt_dlp_gui import DownloaderGUI, Settings, _prepend_tools_to_path  # noqa: E402
import bootstrap  # noqa: E402
import build  # noqa: E402
import pot_provider  # noqa: E402
import provider_setup  # noqa: E402

# GUI 测试统一用临时设置文件,避免污染用户真实的 ~/.yt_dlp_gui.json
_FAKE_SETTINGS = Path(tempfile.gettempdir()) / "yt_dlp_gui_test_settings_gui.json"


def _make_valid_provider(root: Path) -> None:
    """创建满足严格版本/文件清单校验的最小测试 provider。"""
    files = {
        ".provider-version": pot_provider.PROVIDER_VERSION,
        "LICENSE": "GPL-3.0-only",
        "SOURCE_AND_MODIFICATIONS.md": "test",
        "server/package.json": '{"version":"1.3.2"}',
        "server/package-lock.json": "{}",
        "server/build/main.js": "server",
        "server/node_modules/example/index.js": "module",
        "corresponding-source/server/src/main.ts": "source",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    pot_provider.write_provider_manifest(root)


class TestPortableToolsPath(unittest.TestCase):
    def test_existing_tools_dir_is_prepended_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PATH": "C:\\Windows"}):
            tools = Path(tmp) / "tools"
            tools.mkdir()
            self.assertTrue(_prepend_tools_to_path(tools))
            self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(tools.resolve()))
            _prepend_tools_to_path(tools)
            self.assertEqual(os.environ["PATH"].casefold().count(str(tools.resolve()).casefold()), 1)

    def test_missing_tools_dir_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"PATH": "C:\\Windows"}):
            before = os.environ["PATH"]
            self.assertFalse(_prepend_tools_to_path(Path(tmp) / "missing"))
            self.assertEqual(os.environ["PATH"], before)

    def test_bootstrap_reuses_build_cache_ffmpeg_before_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"PATH": "C:\\Windows"}
        ):
            project = Path(tmp)
            cache = project / "build_cache"
            cache.mkdir()
            (cache / "ffmpeg.exe").write_bytes(b"test")
            (cache / "ffprobe.exe").write_bytes(b"test")
            messages = []
            with mock.patch.object(bootstrap, "PROJECT_DIR", project):
                reused = bootstrap._reuse_project_tools(messages.append)
            self.assertEqual(reused, [cache])
            self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(cache.resolve()))
            self.assertIn("ffmpeg/ffprobe", messages[0])


class TestPotProvider(unittest.TestCase):
    def test_apply_to_opts_preserves_existing_extractor_args(self) -> None:
        manager = pot_provider.PotProviderManager()
        manager._url = "http://127.0.0.1:45001"
        opts = {"extractor_args": {"youtube": {"player_client": ["default"]}}}
        manager.apply_to_opts(opts)
        self.assertEqual(
            opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://127.0.0.1:45001"],
        )
        self.assertEqual(opts["extractor_args"]["youtube"]["fetch_pot"], ["always"])
        self.assertEqual(opts["extractor_args"]["youtube"]["player_client"], ["default"])

    def test_manager_starts_health_checks_and_stops_owned_process(self) -> None:
        class FakeProcess:
            returncode = None
            def poll(self): return None
            def terminate(self): self.terminated = True
            def wait(self, timeout): return 0

        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp)
            _make_valid_provider(provider)
            fake = FakeProcess()
            manager = pot_provider.PotProviderManager(provider)
            with (
                mock.patch("pot_provider.shutil.which", return_value="C:/tools/node.exe"),
                mock.patch("pot_provider.subprocess.Popen", return_value=fake) as popen,
                mock.patch.object(manager, "_ping", side_effect=[None, {"version": "1.3.2"}]),
            ):
                self.assertTrue(manager.ensure_started(lambda _msg: None, timeout=1))
                self.assertTrue(manager.url.startswith("http://127.0.0.1:"))
                self.assertIn("--port", popen.call_args.args[0])
                manager.stop()
            self.assertTrue(fake.terminated)
            self.assertIsNone(manager.url)

    def test_stop_can_interrupt_provider_startup_wait(self) -> None:
        class FakeProcess:
            returncode = None
            def poll(self): return None
            def terminate(self): self.terminated = True
            def wait(self, timeout): return 0

        with tempfile.TemporaryDirectory() as tmp:
            provider = Path(tmp)
            _make_valid_provider(provider)
            fake = FakeProcess()
            manager = pot_provider.PotProviderManager(provider)
            result = []
            with (
                mock.patch("pot_provider.shutil.which", return_value="C:/tools/node.exe"),
                mock.patch("pot_provider.subprocess.Popen", return_value=fake),
                mock.patch.object(manager, "_ping", return_value=None),
            ):
                worker = threading.Thread(
                    target=lambda: result.append(manager.ensure_started(lambda _msg: None, timeout=10))
                )
                worker.start()
                for _ in range(100):
                    if manager.url:
                        break
                    threading.Event().wait(0.01)
                manager.stop()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result, [False])
            self.assertTrue(fake.terminated)

    def test_provider_version_marker_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_valid_provider(root)
            (root / ".provider-version").unlink()
            self.assertFalse(provider_setup.provider_valid(root))
            (root / ".provider-version").write_text("1.3.2", encoding="utf-8")
            self.assertTrue(provider_setup.provider_valid(root))

    def test_provider_manifest_rejects_runtime_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_valid_provider(root)
            self.assertTrue(pot_provider.provider_bundle_valid(root))
            (root / "server" / "build" / "main.js").write_text(
                "tampered", encoding="utf-8",
            )
            self.assertFalse(pot_provider.provider_bundle_valid(root))

    def test_node_archive_is_unpacked_once_with_npm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "node.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("node-v22-win-x64/node.exe", b"node")
                zf.writestr("node-v22-win-x64/npm.cmd", b"npm")
                zf.writestr("node-v22-win-x64/node_modules/npm/bin/npm-cli.js", b"cli")
            runtime = root / "runtime"
            npm = provider_setup.extract_node_runtime(archive, runtime)
            self.assertEqual(npm, runtime / "npm.cmd")
            self.assertEqual((runtime / "node.exe").read_bytes(), b"node")
            self.assertTrue((runtime / "node_modules/npm/bin/npm-cli.js").is_file())

    def test_npm_uses_mirror_first_and_falls_back_to_official(self) -> None:
        calls: list[str] = []

        def attempt(_npm, _args, _cwd, registry, _cache, _progress):
            calls.append(registry)
            return (len(calls) == 2, "mirror failed" if len(calls) == 1 else "")

        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "provider_setup._run_npm_attempt", side_effect=attempt,
        ):
            provider_setup._run_npm(
                Path("npm.cmd"), ["ci"], Path(tmp), lambda _m: None,
                Path(tmp) / "npm-cache",
            )
        self.assertEqual(
            calls,
            ["https://registry.npmmirror.com", "https://registry.npmjs.org"],
        )


class _FakeDownloadResponse:
    def __init__(
        self, chunks: list[bytes], length: int, status: int = 200,
        content_range: str | None = None,
    ) -> None:
        self._chunks = iter(chunks)
        self.status = status
        self.headers = {"Content-Length": str(length)}
        if content_range:
            self.headers["Content-Range"] = content_range

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def read(self, _size: int) -> bytes:
        return next(self._chunks, b"")


class TestBootstrapReliability(unittest.TestCase):
    def test_version_check_rejects_old_node_style_version(self) -> None:
        self.assertTrue(bootstrap._version_at_least("22.14.0", "22.0.0"))
        self.assertFalse(bootstrap._version_at_least("18.20.0", "22.0.0"))

    def test_range_resume_appends_and_checks_total_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "tool.zip"
            dest.write_bytes(b"abc")
            response = _FakeDownloadResponse(
                [b"def"], length=3, status=206, content_range="bytes 3-5/6",
            )
            with mock.patch("bootstrap.urllib.request.urlopen", return_value=response):
                self.assertTrue(
                    bootstrap._download_with_progress("https://invalid/tool", dest, lambda _m: None)
                )
            self.assertEqual(dest.read_bytes(), b"abcdef")

    def test_truncated_response_is_not_reported_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "tool.zip"
            response = _FakeDownloadResponse([b"abc"], length=6)
            with mock.patch("bootstrap.urllib.request.urlopen", return_value=response):
                self.assertFalse(
                    bootstrap._download_with_progress("https://invalid/tool", dest, lambda _m: None)
                )

    def test_http_416_requires_matching_remote_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "tool.zip"
            dest.write_bytes(b"abcdef")
            complete = urllib.error.HTTPError(
                "https://invalid/tool", 416, "range", {"Content-Range": "bytes */6"}, None,
            )
            with mock.patch("bootstrap.urllib.request.urlopen", side_effect=complete):
                self.assertTrue(
                    bootstrap._download_with_progress("https://invalid/tool", dest, lambda _m: None)
                )

    def test_secure_ffmpeg_download_rejects_wrong_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "ffmpeg.zip"

            def fake_download(_url, output, _progress):
                output.write_bytes(b"not-the-official-archive")
                return True

            with (
                mock.patch(
                    "bootstrap._ffmpeg_download_info",
                    return_value=("https://github.invalid/ffmpeg.zip", "0" * 64),
                ),
                mock.patch("bootstrap._download_with_progress", side_effect=fake_download),
                mock.patch.object(bootstrap, "GITHUB_PROXIES", []),
            ):
                self.assertFalse(
                    bootstrap._download_ffmpeg_zip(dest, lambda _m: None, require_hash=True)
                )
            self.assertFalse(dest.exists())


class TestSafeBuildPublish(unittest.TestCase):
    def _paths(self, root: Path) -> dict[str, Path]:
        return {
            "DIST_ROOT": root,
            "DIST_DIR": root / "yt_dlp_gui",
            "BACKUP_DIST": root / ".previous",
            "STAGE_ROOT": root / ".stage",
            "STAGE_DIST": root / ".stage" / "yt_dlp_gui",
            "STAGE_ZIP": root / ".new.zip",
            "ZIP_OUT": root / "yt_dlp_gui.zip",
            "CHECKSUM_OUT": root / "yt_dlp_gui.zip.sha256",
            "BACKUP_ZIP": root / ".previous.zip",
            "PUBLISH_MARKER": root / ".publishing.json",
        }

    def test_publish_failure_restores_previous_dist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            with mock.patch.multiple(build, **paths):
                paths["DIST_DIR"].mkdir()
                (paths["DIST_DIR"] / "old.txt").write_text("old", encoding="utf-8")
                paths["STAGE_DIST"].mkdir(parents=True)
                (paths["STAGE_DIST"] / "new.txt").write_text("new", encoding="utf-8")
                paths["STAGE_ZIP"].write_bytes(b"zip")
                with mock.patch("build.os.replace", side_effect=OSError("publish failed")):
                    with self.assertRaises(OSError):
                        build._publish_stage()
                self.assertTrue((paths["DIST_DIR"] / "old.txt").is_file())
                self.assertFalse((paths["DIST_DIR"] / "new.txt").exists())

    def test_interrupted_publish_restores_matching_folder_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            with mock.patch.multiple(build, **paths):
                paths["BACKUP_DIST"].mkdir()
                (paths["BACKUP_DIST"] / "old.txt").write_text("old", encoding="utf-8")
                paths["DIST_DIR"].mkdir()
                (paths["DIST_DIR"] / "new.txt").write_text("new", encoding="utf-8")
                paths["BACKUP_ZIP"].write_bytes(b"old-zip")
                paths["ZIP_OUT"].write_bytes(b"new-zip")
                paths["PUBLISH_MARKER"].write_text(
                    '{"had_dist": true, "had_zip": true}', encoding="utf-8",
                )
                build._recover_previous_dist()
                self.assertTrue((paths["DIST_DIR"] / "old.txt").is_file())
                self.assertEqual(paths["ZIP_OUT"].read_bytes(), b"old-zip")
                self.assertFalse(paths["PUBLISH_MARKER"].exists())

    def test_committed_publish_keeps_new_pair_and_only_cleans_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            with mock.patch.multiple(build, **paths):
                paths["DIST_DIR"].mkdir()
                (paths["DIST_DIR"] / "new.txt").write_text("new", encoding="utf-8")
                paths["ZIP_OUT"].write_bytes(b"new-zip")
                paths["BACKUP_DIST"].mkdir()
                (paths["BACKUP_DIST"] / "old.txt").write_text("old", encoding="utf-8")
                paths["BACKUP_ZIP"].write_bytes(b"old-zip")
                paths["PUBLISH_MARKER"].write_text(
                    '{"phase":"committed","had_dist":true,"had_zip":true}',
                    encoding="utf-8",
                )
                build._recover_previous_dist()
                self.assertTrue((paths["DIST_DIR"] / "new.txt").is_file())
                self.assertEqual(paths["ZIP_OUT"].read_bytes(), b"new-zip")
                self.assertFalse(paths["BACKUP_DIST"].exists())
                self.assertFalse(paths["BACKUP_ZIP"].exists())
                self.assertFalse(paths["PUBLISH_MARKER"].exists())


class TestSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.gettempdir()) / "yt_dlp_gui_test_settings.json"
        if self.path.exists():
            self.path.unlink()

    def tearDown(self) -> None:
        self.path.unlink(missing_ok=True)

    def test_round_trip(self) -> None:
        settings = Settings(path=self.path)
        settings.set("save_dir", "C:/Users/测试/Downloads")
        settings.set("youtube_cookies_file", "C:/fake/youtube.txt")
        settings.set("bilibili_cookies_file", "C:/fake/bilibili.txt")
        settings.set("xiaohongshu_cookies_file", "C:/fake/xiaohongshu.txt")
        settings.set("proxy", "http://127.0.0.1:7890")

        reloaded = Settings(path=self.path)
        self.assertEqual(reloaded.get("save_dir"), "C:/Users/测试/Downloads")
        self.assertEqual(reloaded.get("youtube_cookies_file"), "C:/fake/youtube.txt")
        self.assertEqual(reloaded.get("bilibili_cookies_file"), "C:/fake/bilibili.txt")
        self.assertEqual(
            reloaded.get("xiaohongshu_cookies_file"), "C:/fake/xiaohongshu.txt"
        )
        self.assertEqual(reloaded.get("proxy"), "http://127.0.0.1:7890")

    def test_legacy_cookie_file_migrates_to_youtube(self) -> None:
        self.path.write_text('{"cookies_file":"C:/legacy.txt"}', encoding="utf-8")
        settings = Settings(path=self.path)
        self.assertEqual(settings.get("youtube_cookies_file"), "C:/legacy.txt")

    def test_corrupted_file_falls_back_to_defaults(self) -> None:
        self.path.write_text("{ 坏json", encoding="utf-8")
        settings = Settings(path=self.path)
        self.assertEqual(settings.get("save_dir"), str(Path.home() / "Downloads"))

    def test_save_is_atomic_and_leaves_no_temp_file(self) -> None:
        settings = Settings(path=self.path)
        settings.set("proxy", "http://127.0.0.1:7890")
        self.assertTrue(self.path.is_file())
        self.assertFalse(self.path.with_name(f".{self.path.name}.tmp").exists())


class TestBuildYdlOpts(unittest.TestCase):
    """GUI 层 opts 组装:按 URL 选登录态、代理、音频码率。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 用临时设置文件,避免测试写坏用户真实的 ~/.yt_dlp_gui.json
        cls._settings_patch = mock.patch.object(
            Settings, "DEFAULT_PATH", _FAKE_SETTINGS
        )
        cls._settings_patch.start()
        _FAKE_SETTINGS.unlink(missing_ok=True)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = DownloaderGUI(cls.root)
        cls.save_dir = str(Path.home() / "Downloads")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app._on_close()
        cls._settings_patch.stop()
        _FAKE_SETTINGS.unlink(missing_ok=True)

    def test_unsupported_url_has_plain_language_error(self) -> None:
        message = DownloaderGUI._friendly_download_error(
            "\x1b[0;31mERROR:\x1b[0m Unsupported URL: https://example.invalid/video"
        )
        self.assertIn("不支持这个网址", message)
        self.assertIn("不是登录或代理问题", message)

    def test_expired_youtube_cookie_has_actionable_error(self) -> None:
        message = DownloaderGUI._friendly_download_error(
            "The provided YouTube account cookies are no longer valid"
        )
        self.assertIn("内置登录", message)

    def test_site_specific_cookies_are_selected_by_url(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        youtube = tmp / "youtube.txt"
        bilibili = tmp / "bilibili.txt"
        xiaohongshu = tmp / "xiaohongshu.txt"
        youtube.touch()
        bilibili.touch()
        xiaohongshu.touch()
        self.app.youtube_cookies_file = str(youtube)
        self.app.bilibili_cookies_file = str(bilibili)
        self.app.xiaohongshu_cookies_file = str(xiaohongshu)
        base = self.app._build_ydl_opts(self.save_dir)
        self.assertEqual(
            self.app._opts_for_url(base, "https://youtu.be/abc")["cookiefile"],
            str(youtube),
        )
        self.assertEqual(
            self.app._opts_for_url(base, "https://www.bilibili.com/video/BV1x")["cookiefile"],
            str(bilibili),
        )
        self.assertEqual(
            self.app._opts_for_url(base, "https://www.xiaohongshu.com/explore/abc")["cookiefile"],
            str(xiaohongshu),
        )
        self.assertNotIn(
            "cookiefile", self.app._opts_for_url(base, "https://vimeo.com/123")
        )

    def test_proxy_and_audio_bitrate(self) -> None:
        self.app.proxy_var.set("socks5://127.0.0.1:1080")
        self.app.resolution_var.set("仅音频")
        self.app._on_resolution_change()
        self.app.audio_bitrate_var.set("320")
        opts = self.app._build_ydl_opts(self.save_dir)
        self.assertEqual(opts["proxy"], "socks5://127.0.0.1:1080")
        self.assertEqual(opts["postprocessors"][0]["preferredquality"], "320")

    def test_incomplete_hls_fragments_abort_instead_of_being_skipped(self) -> None:
        self.app.resolution_var.set("1080p")
        self.app._on_resolution_change()
        opts = self.app._build_ydl_opts(self.save_dir)
        self.assertFalse(opts["skip_unavailable_fragments"])
        self.assertEqual(opts["fragment_retries"], 20)
        self.assertEqual(opts["concurrent_fragment_downloads"], 2)

    def test_site_detection(self) -> None:
        self.assertEqual(self.app._site_for_url("https://youtube.com/watch?v=x"), "youtube")
        self.assertEqual(self.app._site_for_url("https://b23.tv/abc"), "bilibili")
        self.assertEqual(self.app._site_for_url("https://xhslink.com/a/abc"), "xiaohongshu")
        self.assertIsNone(self.app._site_for_url("https://example.com/video"))

    def test_xhs_share_text_is_normalized_to_one_task(self) -> None:
        self.assertEqual(
            self.app._extract_task_urls(
                "复制这条笔记\nhttps://xhslink.com/a/abc ，打开小红书查看"
            ),
            ["https://xhslink.com/a/abc"],
        )

    def test_youtube_mix_watch_url_downloads_only_current_video(self) -> None:
        base = self.app._build_ydl_opts(self.save_dir)
        watch = self.app._opts_for_url(
            base,
            "https://www.youtube.com/watch?v=abc&list=RDabc&start_radio=1",
        )
        playlist = self.app._opts_for_url(
            base,
            "https://www.youtube.com/playlist?list=PL123",
        )
        self.assertTrue(watch["noplaylist"])
        self.assertNotIn("noplaylist", playlist)


class TestEdgeLoginCore(unittest.TestCase):
    """edge_login 纯逻辑:认证 cookie 判断、域过滤、Netscape 格式写出。

    不启动浏览器(那需要人工登录),只测不联网、不弹窗的纯函数。
    """

    def test_has_auth_cookie_on_youtube_domain(self) -> None:
        cookies = [{"name": "SID", "domain": ".youtube.com", "value": "x"}]
        self.assertTrue(has_auth_cookie_on(cookies, "youtube.com"))

    def test_has_auth_cookie_google_only_is_false_for_youtube(self) -> None:
        cookies = [{"name": "SID", "domain": ".google.com", "value": "x"}]
        self.assertFalse(has_auth_cookie_on(cookies, "youtube.com"))

    def test_has_auth_cookie_non_auth_name_is_false(self) -> None:
        cookies = [{"name": "PREF", "domain": ".youtube.com", "value": "x"}]
        self.assertFalse(has_auth_cookie_on(cookies, "youtube.com"))

    def test_get_youtube_cookies_filters_by_domain(self) -> None:
        cookies = [
            {"name": "SID", "domain": ".youtube.com", "value": "a"},
            {"name": "GAPS", "domain": ".google.com", "value": "b"},
            {"name": "OTHER", "domain": ".example.com", "value": "c"},
        ]
        names = [c["name"] for c in get_youtube_cookies(cookies)]
        self.assertEqual(names, ["SID", "GAPS"])

    def test_bilibili_sessdata_marks_login_and_filters_domain(self) -> None:
        cookies = [
            {"name": "SESSDATA", "domain": ".bilibili.com", "value": "secret"},
            {"name": "SID", "domain": ".youtube.com", "value": "secret"},
        ]
        self.assertTrue(has_site_auth_cookie(cookies, "bilibili"))
        self.assertEqual(
            [c["name"] for c in get_site_cookies(cookies, "bilibili")],
            ["SESSDATA"],
        )

    def test_write_netscape_format(self) -> None:
        """Netscape 格式最容易出错:#HttpOnly_ 前缀、include_sub、secure、
        session cookie 的 expires 归 0——逐字段断言。"""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        out = tmp / "cookies.txt"
        write_netscape(
            [
                {"domain": ".youtube.com", "path": "/", "secure": True,
                 "expires": 123, "name": "SID", "value": "abc", "httpOnly": True},
                {"domain": "www.google.com", "path": "/", "secure": False,
                 "expires": -1, "name": "PREF", "value": "def", "httpOnly": False},
            ],
            out,
        )
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "# Netscape HTTP Cookie File")
        # HttpOnly → #HttpOnly_ 前缀;.youtube.com → include_sub TRUE;secure → TRUE
        self.assertEqual(
            lines[3],
            "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t123\tSID\tabc",
        )
        # 无点前缀 → include_sub FALSE;session cookie(expires<0)→ 0
        self.assertEqual(
            lines[4],
            "www.google.com\tFALSE\t/\tFALSE\t0\tPREF\tdef",
        )

    def _run_login_with_fake_browser(self, out: Path, downloader: type) -> Path:
        fake_cdp = mock.MagicMock()

        def send(method: str, _params: dict | None = None) -> dict:
            if method == "Network.getAllCookies":
                return {"cookies": [{
                    "domain": ".youtube.com", "path": "/", "secure": True,
                    "expires": 123, "name": "SID", "value": "new-value",
                    "httpOnly": True,
                }]}
            return {}

        fake_cdp.send.side_effect = send
        fake_module = types.SimpleNamespace(YoutubeDL=downloader)
        with (
            mock.patch("edge_login.find_browser", return_value="msedge.exe"),
            mock.patch("edge_login.launch_browser", return_value=mock.MagicMock(pid=1)),
            mock.patch("edge_login.wait_for_debug_port", return_value=12345),
            mock.patch("edge_login.get_page_ws_url", return_value="ws://local"),
            mock.patch("edge_login.CDP", return_value=fake_cdp),
            mock.patch("edge_login.kill_tree"),
            mock.patch("edge_login.time.sleep"),
            mock.patch.dict(sys.modules, {"yt_dlp": fake_module}),
        ):
            return run_login(out_path=out, site="youtube", verify=True)

    def test_failed_verification_preserves_previous_cookie_file(self) -> None:
        class FailingDownloader:
            def __init__(self, _opts: dict) -> None: pass
            def __enter__(self): return self
            def __exit__(self, *_args: object) -> None: pass
            def extract_info(self, *_args: object, **_kwargs: object) -> dict:
                raise RuntimeError("verification failed")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cookies.txt"
            out.write_text("previous-valid-cookie-file", encoding="utf-8")
            with self.assertRaises(LoginError):
                self._run_login_with_fake_browser(out, FailingDownloader)
            self.assertEqual(out.read_text(encoding="utf-8"), "previous-valid-cookie-file")
            self.assertEqual(list(out.parent.glob(f".{out.name}.*.tmp")), [])

    def test_successful_verification_atomically_replaces_cookie_file(self) -> None:
        class SuccessfulDownloader:
            def __init__(self, _opts: dict) -> None: pass
            def __enter__(self): return self
            def __exit__(self, *_args: object) -> None: pass
            def extract_info(self, *_args: object, **_kwargs: object) -> dict:
                return {"title": "verified"}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cookies.txt"
            out.write_text("previous-valid-cookie-file", encoding="utf-8")
            result = self._run_login_with_fake_browser(out, SuccessfulDownloader)
            self.assertEqual(result, out.resolve())
            self.assertIn("Netscape HTTP Cookie File", out.read_text(encoding="utf-8"))

    def test_auth_warning_rejects_even_public_video_parse_success(self) -> None:
        class WarningDownloader:
            def __init__(self, opts: dict) -> None:
                self.logger = opts["logger"]
            def __enter__(self): return self
            def __exit__(self, *_args: object) -> None: pass
            def extract_info(self, *_args: object, **_kwargs: object) -> dict:
                self.logger.warning("The provided YouTube account cookies are no longer valid")
                return {"title": "public video still works anonymously"}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cookies.txt"
            out.write_text("previous-valid-cookie-file", encoding="utf-8")
            with self.assertRaises(LoginError):
                self._run_login_with_fake_browser(out, WarningDownloader)
            self.assertEqual(out.read_text(encoding="utf-8"), "previous-valid-cookie-file")

    @mock.patch("edge_login.find_browser", return_value="msedge.exe")
    def test_cancelled_login_does_not_touch_previous_cookie(self, _browser: mock.MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cookies.txt"
            out.write_text("previous-valid-cookie-file", encoding="utf-8")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(LoginError):
                run_login(out_path=out, cancel_event=cancel)
            self.assertEqual(out.read_text(encoding="utf-8"), "previous-valid-cookie-file")


class TestEmbeddedLogin(unittest.TestCase):
    """内置登录(Edge+CDP)的 GUI 侧:回调与队列路由。

    不启动真实浏览器(那需要人工登录),只测纯逻辑;
    messagebox 弹窗全部 mock 掉,settings 用临时文件。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._settings_patch = mock.patch.object(
            Settings, "DEFAULT_PATH", _FAKE_SETTINGS
        )
        cls._settings_patch.start()
        _FAKE_SETTINGS.unlink(missing_ok=True)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = DownloaderGUI(cls.root)
        cls.app._login_running = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app._on_close()
        cls._settings_patch.stop()
        _FAKE_SETTINGS.unlink(missing_ok=True)

    def test_has_edge_returns_bool(self) -> None:
        """纯存在性检查,不启动浏览器。Win11 预装 Edge 应为 True。"""
        self.assertIsInstance(DownloaderGUI._has_edge(), bool)

    def test_login_progress_messages_map_to_friendly_stages(self) -> None:
        self.assertEqual(
            DownloaderGUI._login_stage_from_message("Edge 登录窗口已打开。"),
            "waiting",
        )
        self.assertEqual(
            DownloaderGUI._login_stage_from_message("[3/4] 正在安全读取 Cookies"),
            "reading",
        )
        self.assertEqual(
            DownloaderGUI._login_stage_from_message("[4/4] 用 yt-dlp 验证"),
            "verifying",
        )
        self.assertEqual(
            DownloaderGUI._login_stage_from_message("[保存] 正在安全保存"),
            "saving",
        )

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_on_login_ok_sets_youtube_cookies_file(self, mock_info: mock.MagicMock) -> None:
        self.app._login_running = "youtube"
        path = str(Path.home() / ".yt_dlp_gui_cookies.txt")
        self.app._on_login_ok("youtube", path)
        self.assertIsNone(self.app._login_running)
        self.assertEqual(self.app.youtube_cookies_file, path)
        self.assertEqual(self.app.settings.get("youtube_cookies_file"), path)
        self.assertIsInstance(self.app.settings.get("youtube_login_at"), int)
        self.assertTrue(mock_info.called)
        self.app.youtube_cookies_file = None

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_on_login_ok_sets_bilibili_cookies_file(self, mock_info: mock.MagicMock) -> None:
        self.app._login_running = "bilibili"
        path = str(Path.home() / ".yt_dlp_gui_bilibili_cookies.txt")
        self.app._on_login_ok("bilibili", path)
        self.assertEqual(self.app.bilibili_cookies_file, path)
        self.assertEqual(self.app.settings.get("bilibili_cookies_file"), path)
        self.assertTrue(mock_info.called)
        self.app.bilibili_cookies_file = None

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_on_login_ok_sets_xiaohongshu_cookies_file(
        self, mock_info: mock.MagicMock,
    ) -> None:
        self.app._login_running = "xiaohongshu"
        path = str(Path.home() / ".yt_dlp_gui_xiaohongshu_cookies.txt")
        self.app._on_login_ok("xiaohongshu", path)
        self.assertEqual(self.app.xiaohongshu_cookies_file, path)
        self.assertEqual(self.app.settings.get("xiaohongshu_cookies_file"), path)
        self.assertTrue(mock_info.called)
        self.app.xiaohongshu_cookies_file = None

    @mock.patch("yt_dlp_gui.messagebox.showerror")
    def test_on_login_fail_resets_flag(self, mock_error: mock.MagicMock) -> None:
        self.app._login_running = "youtube"
        self.app._on_login_fail("youtube", "测试失败")
        self.assertIsNone(self.app._login_running)
        self.assertTrue(mock_error.called)

    def test_invalid_auth_is_persistently_disabled(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        cookie = tmp / "youtube.txt"
        cookie.touch()
        self.app.youtube_cookies_file = str(cookie)
        self.app.settings.set("youtube_cookies_valid", True)
        self.assertEqual(self.app._cookie_file_for_site("youtube"), str(cookie))
        self.app._on_auth_invalid("youtube")
        self.assertEqual(self.app._cookie_state("youtube"), "invalid")
        self.assertIsNone(self.app._cookie_file_for_site("youtube"))

    def test_seven_day_login_refresh_reminder(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        cookie = tmp / "youtube.txt"
        cookie.touch()
        self.app.youtube_cookies_file = str(cookie)
        self.app.settings.set("youtube_cookies_valid", True)
        self.app.settings.set("youtube_login_at", 1_000_000)
        with mock.patch("yt_dlp_gui.time.time", return_value=1_000_000 + 7 * 86400):
            self.assertIn("建议更新", self.app._login_status_text("youtube", short=True))

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_poll_queue_routes_login_events(self, mock_info: mock.MagicMock) -> None:
        """login_log / login_ok 事件经队列路由到日志窗与回调。"""
        self.app.youtube_cookies_file = None
        self.app._msg_queue.put(("login_log", "测试进度"))
        self.app._msg_queue.put(("login_ok", ("youtube", "C:/fake/logged_in_cookies.txt")))
        self.app._poll_queue()
        self.assertEqual(self.app.youtube_cookies_file, "C:/fake/logged_in_cookies.txt")
        self.assertIn("测试进度", self.app.log_text.get("1.0", tk.END))
        self.assertTrue(mock_info.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
