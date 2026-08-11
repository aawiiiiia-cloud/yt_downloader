"""yt_dlp_gui 的回归测试(unittest,零额外依赖)。

运行: python -m unittest test_yt_dlp_gui -v

覆盖:Settings 持久化、Cookies/代理/码率 opts 组装、Firefox 登录检测三态。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yt_dlp_gui import DownloaderGUI, Settings  # noqa: E402


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
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = DownloaderGUI(cls.root)
        cls.save_dir = str(Path.home() / "Downloads")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app._on_close()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
