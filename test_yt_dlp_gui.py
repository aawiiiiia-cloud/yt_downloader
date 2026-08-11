"""yt_dlp_gui 的回归测试(unittest,零额外依赖)。

运行: python -m unittest test_yt_dlp_gui -v

覆盖:Settings 持久化、Cookies/代理/码率 opts 组装、Firefox 登录检测三态、
edge_login 纯逻辑(认证 cookie 判断/Netscape 写出)、内置登录 GUI 侧回调。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from edge_login import has_auth_cookie_on, get_youtube_cookies, write_netscape  # noqa: E402
from yt_dlp_gui import DownloaderGUI, Settings  # noqa: E402

# GUI 测试统一用临时设置文件,避免污染用户真实的 ~/.yt_dlp_gui.json
_FAKE_SETTINGS = Path(tempfile.gettempdir()) / "yt_dlp_gui_test_settings_gui.json"


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
        settings.set("cookies", "firefox")
        settings.set("proxy", "http://127.0.0.1:7890")

        reloaded = Settings(path=self.path)
        self.assertEqual(reloaded.get("save_dir"), "C:/Users/测试/Downloads")
        self.assertEqual(reloaded.get("cookies"), "firefox")
        self.assertEqual(reloaded.get("proxy"), "http://127.0.0.1:7890")

    def test_corrupted_file_falls_back_to_defaults(self) -> None:
        self.path.write_text("{ 坏json", encoding="utf-8")
        settings = Settings(path=self.path)
        self.assertEqual(settings.get("save_dir"), str(Path.home() / "Downloads"))


class TestFirefoxLoginState(unittest.TestCase):
    """_firefox_login_state 三态: found / none / unreadable。"""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "cookies.sqlite"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT)")
        con.commit()
        return con

    def test_empty_db_returns_none(self) -> None:
        con = self._create_db()
        con.close()
        self.assertEqual(DownloaderGUI._firefox_login_state(self.db_path), "none")

    def test_login_cookie_returns_found(self) -> None:
        con = self._create_db()
        con.execute(
            "INSERT INTO moz_cookies VALUES ('.youtube.com', '__Secure-3PAPISID')"
        )
        con.commit()
        con.close()
        self.assertEqual(DownloaderGUI._firefox_login_state(self.db_path), "found")

    def test_non_login_cookie_returns_none(self) -> None:
        con = self._create_db()
        con.execute("INSERT INTO moz_cookies VALUES ('.youtube.com', 'PREF')")
        con.commit()
        con.close()
        self.assertEqual(DownloaderGUI._firefox_login_state(self.db_path), "none")

    def test_missing_file_returns_unreadable(self) -> None:
        missing = self.tmp_dir / "no_such.sqlite"
        self.assertEqual(DownloaderGUI._firefox_login_state(missing), "unreadable")


class TestBuildYdlOpts(unittest.TestCase):
    """GUI 层 opts 组装:cookies 来源优先级、代理、音频码率。"""

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

    def test_browser_cookies_uses_cookiesfrombrowser(self) -> None:
        self.app.cookies_var.set("firefox")
        self.app._on_cookies_change()
        self.app.cookies_file = None
        opts = self.app._build_ydl_opts(self.save_dir)
        self.assertEqual(opts.get("cookiesfrombrowser"), ("firefox",))
        self.assertNotIn("cookiefile", opts)

    def test_cookie_file_takes_priority_over_browser(self) -> None:
        self.app.cookies_var.set("firefox")
        self.app._on_cookies_change()
        self.app.cookies_file = "C:/fake/cookies.txt"
        opts = self.app._build_ydl_opts(self.save_dir)
        self.assertEqual(opts.get("cookiefile"), "C:/fake/cookies.txt")
        self.assertNotIn("cookiesfrombrowser", opts)
        self.app.cookies_file = None

    def test_proxy_and_audio_bitrate(self) -> None:
        self.app.proxy_var.set("socks5://127.0.0.1:1080")
        self.app.resolution_var.set("仅音频")
        self.app._on_resolution_change()
        self.app.audio_bitrate_var.set("320")
        opts = self.app._build_ydl_opts(self.save_dir)
        self.assertEqual(opts["proxy"], "socks5://127.0.0.1:1080")
        self.assertEqual(opts["postprocessors"][0]["preferredquality"], "320")

    def test_detect_browser_does_not_raise(self) -> None:
        result = self.app._detect_browser_with_youtube()
        self.assertIn(result, (None, "firefox"))


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
        cls.app._login_running = False

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app._on_close()
        cls._settings_patch.stop()
        _FAKE_SETTINGS.unlink(missing_ok=True)

    def test_has_edge_returns_bool(self) -> None:
        """纯存在性检查,不启动浏览器。Win11 预装 Edge 应为 True。"""
        self.assertIsInstance(DownloaderGUI._has_edge(), bool)

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_on_login_ok_sets_cookies_file(self, mock_info: mock.MagicMock) -> None:
        self.app.cookies_var.set("firefox")
        self.app._on_cookies_change()
        self.app._login_running = True
        path = str(Path.home() / ".yt_dlp_gui_cookies.txt")
        self.app._on_login_ok(path)
        self.assertFalse(self.app._login_running)
        self.assertEqual(self.app.cookies_file, path)
        self.assertEqual(self.app.cookies_var.get(), "无")
        self.assertEqual(self.app.settings.get("cookies_file"), path)
        self.assertTrue(mock_info.called)
        self.app.cookies_file = None  # 复位,避免影响后续用例

    @mock.patch("yt_dlp_gui.messagebox.showerror")
    def test_on_login_fail_resets_flag(self, mock_error: mock.MagicMock) -> None:
        self.app._login_running = True
        self.app._on_login_fail("测试失败")
        self.assertFalse(self.app._login_running)
        self.assertTrue(mock_error.called)

    @mock.patch("yt_dlp_gui.messagebox.showinfo")
    def test_poll_queue_routes_login_events(self, mock_info: mock.MagicMock) -> None:
        """login_log / login_ok 事件经队列路由到日志窗与回调。"""
        self.app.cookies_file = None
        self.app._msg_queue.put(("login_log", "测试进度"))
        self.app._msg_queue.put(("login_ok", "C:/fake/logged_in_cookies.txt"))
        self.app._poll_queue()
        self.assertEqual(self.app.cookies_file, "C:/fake/logged_in_cookies.txt")
        self.assertIn("测试进度", self.app.log_text.get("1.0", tk.END))
        self.assertTrue(mock_info.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
