"""临时冒烟测试:Settings/代理/码率/Cookies 新逻辑。用完即删。"""
import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yt_dlp_gui import DownloaderGUI, Settings  # noqa: E402

# --- 1. Settings 往返 + 损坏回退 ---
tmp = Path(tempfile.gettempdir()) / "yt_dlp_gui_test.json"
if tmp.exists():
    tmp.unlink()
s1 = Settings(path=tmp)
s1.set("save_dir", "C:/Users/测试/Downloads")
s1.set("cookies", "firefox")
s1.set("proxy", "http://127.0.0.1:7890")
assert Settings(path=tmp).get("save_dir") == "C:/Users/测试/Downloads"
assert Settings(path=tmp).get("cookies") == "firefox"
tmp.write_text("{ 坏json", encoding="utf-8")
assert Settings(path=tmp).get("save_dir") == str(Path.home() / "Downloads")
tmp.unlink()
print("Settings 往返/损坏回退 OK")

# --- 2. GUI + opts ---
root = tk.Tk()
root.withdraw()
app = DownloaderGUI(root)
root.update_idletasks()

# Cookies:浏览器选择 → cookiesfrombrowser
app.cookies_var.set("firefox")
app._on_cookies_change()
opts = app._build_ydl_opts(str(Path.home() / "Downloads"))
assert opts.get("cookiesfrombrowser") == ("firefox",), opts
assert "cookiefile" not in opts
print("Cookies 浏览器直读 OK")

# Cookies:文件优先
app.cookies_file = "C:/fake/cookies.txt"
opts = app._build_ydl_opts(str(Path.home() / "Downloads"))
assert opts.get("cookiefile") == "C:/fake/cookies.txt", opts
assert "cookiesfrombrowser" not in opts
app.cookies_file = None
print("Cookies 文件优先 OK")

# 代理 + 音频码率
app.proxy_var.set("socks5://127.0.0.1:1080")
app.resolution_var.set("仅音频")
app._on_resolution_change()
app.audio_bitrate_var.set("320")
opts = app._build_ydl_opts(str(Path.home() / "Downloads"))
assert opts["proxy"] == "socks5://127.0.0.1:1080"
assert opts["postprocessors"][0]["preferredquality"] == "320"
print("代理 + 音频码率 OK")

# 浏览器检测:只查不抛错(这台机器不一定有 Firefox/Edge/Chrome)
try:
    result = app._detect_browser_with_youtube()
    print(f"浏览器检测不抛错, 结果={result!r}")
except Exception as e:  # noqa: BLE001
    raise AssertionError(f"_detect_browser_with_youtube 抛错: {e}") from e

app._on_close()
print("全部通过")
