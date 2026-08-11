"""Edge + CDP 嵌入式登录:在独立 Edge 窗口里登录 Google,全量抓取 YouTube cookies(含 HttpOnly)。

原理(复刻 YoutubeDownloader 的 WebView2 方案,但换成本机 Edge):
- 用独立用户配置目录启动一个 Edge 实例(带 --remote-debugging-port=0 随机端口)
- 通过 Chrome DevTools Protocol(CDP)控制它打开 Google 登录页
- 用户在窗口里正常登录(可过验证码/两步验证)
- 跳回 youtube.com 后,用 Network.getAllCookies 全量抓 cookie(含 HttpOnly)
- 写成 Netscape 格式 cookies.txt,可直接喂给 yt-dlp --cookies

为什么用 Edge+CDP 而不是 pywebview:
- 认证 cookie(SID/SAPISID/__Secure-1PSID 等)全是 HttpOnly,JS 的 document.cookie 拿不到
- CDP 的 Network.getAllCookies 能拿全部,包括 HttpOnly
- Edge 是 Win11 预装,无需分发额外浏览器
- 用独立配置目录,不碰用户真实 Chrome/Edge 的加密数据库 → 绕开 Chrome 127+ ABE 锁

两种用法:
1) 命令行:  python edge_login.py [视频URL]   → 在当前目录写 cookies.txt 并验证
2) 模块调用:  cookies_path = run_login(out_path, progress=log_fn)   → 供 GUI 后台线程调用

注意:
- 登录前必须加 --disable-blink-features=AutomationControlled,否则 Google
  识别出自动化浏览器,报"此浏览器或应用可能不安全"拒绝登录。
- 抓取时机:必须等 .youtube.com 域出现认证 cookie(SID/SAPISID 等),
  光有 .google.com 的 cookie 不够(yt-dlp 请求 www.youtube.com 时不会带上)。
- 验证(verify)用进程内 yt_dlp.YoutubeDL,而不是 subprocess 调 CLI:
  打包版里 sys.executable 是 yt_dlp_gui.exe,子进程会再开一个 GUI 而非跑 CLI。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import websocket  # pip install websocket-client

# Edge/Chrome 常见安装路径(按顺序尝试)
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# 登录后跳回 youtube.com 即视为成功
LOGIN_URL = (
    "https://accounts.google.com/ServiceLogin"
    "?continue=" + urllib.parse.quote("https://www.youtube.com")
)

# 检测登录成功的标志 cookie(存在任一即视为已登录)
AUTH_COOKIE_NAMES = {
    "__Secure-1PSID", "SID", "SAPISID",
    "__Secure-3PAPISID", "HSID", "SSID", "LOGIN_INFO",
}

# 抓取范围:youtube.com 与 google.com 域的 cookie
COOKIE_DOMAINS = ("youtube.com", "google.com")

# 默认验证用的视频(可选)
DEFAULT_TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

Progress = Callable[[str], None]


class LoginError(RuntimeError):
    """登录/抓取流程失败。message 为给用户看的说明。"""


def find_browser() -> str:
    for p in BROWSER_PATHS:
        if Path(p).exists():
            return p
    raise LoginError("未找到 Edge/Chrome,请手动指定浏览器路径")


class CDP:
    """极简 CDP 客户端:WebSocket 收发命令,忽略事件。"""

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self._id = 0

    def send(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(
            json.dumps({"id": mid, "method": method, "params": params or {}})
        )
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") != mid:
                continue  # 事件消息,忽略
            if "error" in msg:
                raise RuntimeError(f"CDP {method} 失败: {msg['error']}")
            return msg.get("result", {})

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def launch_browser(browser: str, profile: Path, url: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            browser,
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",  # 0 = 随机空闲端口,写入 DevToolsActivePort
            "--remote-allow-origins=*",   # 新版 Edge 拒绝非本机 origin,必须放行
            "--disable-blink-features=AutomationControlled",  # 防 Google 识别为自动化
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_debug_port(profile: Path, timeout: float = 30) -> int:
    devtools = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if devtools.exists():
            port = int(devtools.read_text().strip().splitlines()[0])
            # 确认端口真的可连
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=2
                ):
                    return port
            except OSError:
                pass
        time.sleep(0.3)
    raise LoginError("浏览器调试端口未就绪")


def get_page_ws_url(port: int) -> str:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json", timeout=5
    ) as r:
        targets = json.loads(r.read())
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    raise LoginError("未找到可用的页面 target")


def has_auth_cookie_on(cookies: list[dict], domain_part: str) -> bool:
    """判断指定域(如 'youtube.com')上是否已出现认证 cookie。"""
    return any(
        c.get("name") in AUTH_COOKIE_NAMES
        and domain_part in c.get("domain", "")
        for c in cookies
    )


def get_youtube_cookies(cookies: list[dict]) -> list[dict]:
    return [
        c for c in cookies
        if any(d in c.get("domain", "") for d in COOKIE_DOMAINS)
    ]


def write_netscape(cookies: list[dict], out: Path) -> None:
    """写成 Netscape HTTP Cookie File(yt-dlp/curl 通用格式)。"""
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.se/docs/http-cookies.html",
        "# generated by edge_login.py",
    ]
    for c in cookies:
        domain = c.get("domain", "")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = int(c.get("expires", 0) or 0)
        if expires < 0:  # 会话 cookie → 写 0
            expires = 0
        name = c.get("name", "")
        value = c.get("value", "")
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        lines.append(
            f"{prefix}{domain}\t{include_sub}\t{path}\t{secure}\t"
            f"{expires}\t{name}\t{value}"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def kill_tree(proc: subprocess.Popen) -> None:
    """Windows 上连子进程一起结束,避免残留 Edge 进程。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


def run_login(
    out_path: Path | None = None,
    progress: Progress = print,
    verify: bool = True,
    test_url: str = DEFAULT_TEST_URL,
    login_timeout: float = 600,
) -> Path:
    """执行完整「Edge 登录 → 抓取 cookies → 写文件 → 验证」流程。

    参数:
        out_path: cookies.txt 输出路径,默认当前目录 cookies.txt
        progress: 进度回调(供 GUI 转发到日志窗口)
        verify:   True 时用 yt-dlp 解析一个视频标题验证 cookies 有效
        test_url: 验证用的视频 URL
        login_timeout: 等待登录的最长秒数(默认 10 分钟)

    返回:
        cookies.txt 的绝对路径。

    抛出:
        LoginError: 任何一步失败,message 为给用户看的说明。
    """
    out = (out_path or Path("cookies.txt")).resolve()
    browser = find_browser()
    profile = Path(tempfile.mkdtemp(prefix="edge_login_"))

    proc: subprocess.Popen | None = None
    cdp: CDP | None = None
    try:
        progress(f"[1/4] 启动 {Path(browser).name} (独立配置目录)...")
        proc = launch_browser(browser, profile, LOGIN_URL)
        port = wait_for_debug_port(profile)
        progress(f"[2/4] 调试端口 {port} 就绪,连接 CDP...")
        cdp = CDP(get_page_ws_url(port))
        cdp.send("Network.enable")
        cdp.send("Page.enable")

        progress("=" * 60)
        progress("Edge 窗口已打开。请在窗口里登录你的 Google 账号,")
        progress("登录成功后跳回 youtube.com,脚本会自动抓取 cookies。")
        progress("(登录窗口不要手动关,登录完脚本会自己收尾)")
        progress("=" * 60)

        deadline = time.monotonic() + login_timeout
        cookies: list[dict] = []
        while time.monotonic() < deadline:
            cookies = cdp.send("Network.getAllCookies").get("cookies", [])
            if has_auth_cookie_on(cookies, "youtube.com"):
                break  # .youtube.com 域上已有认证 cookie = 登录会话真正可用
            time.sleep(2)
        else:
            raise LoginError(f"等待登录超时({int(login_timeout // 60)} 分钟),已取消")

        # 主动访问一次 youtube.com 首页,确保会话 cookie 完整下发
        cdp.send("Page.navigate", {"url": "https://www.youtube.com"})
        time.sleep(4)
        cookies = cdp.send("Network.getAllCookies").get("cookies", [])

        yt_cookies = get_youtube_cookies(cookies)
        if not yt_cookies:
            raise LoginError("抓取到的 cookie 为空,请重试")
        write_netscape(yt_cookies, out)
        progress(f"[3/4] 已抓取 {len(yt_cookies)} 个 cookie → {out}")

        if verify:
            progress("[4/4] 用 yt-dlp 验证解析标题...")
            try:
                import yt_dlp

                with yt_dlp.YoutubeDL({
                    "cookiefile": str(out),
                    # 与主 GUI 一致:dict 格式指定 node 运行时
                    # (打包版里 node.exe 在 tools/ 已由 _setup_env 加进 PATH)
                    "js_runtimes": {"node": {}},
                    "skip_download": True,
                    "quiet": True,
                    "no_warnings": False,
                }) as ydl:
                    info = ydl.extract_info(test_url, download=False)
                title = (info or {}).get("title") or "(无标题)"
                progress(f"[成功] 验证通过,视频标题: {title}")
            except Exception as exc:  # noqa: BLE001
                # 验证失败但 cookie 已写出,仍返回路径,让调用方决定是否使用
                progress(
                    f"[警告] yt-dlp 验证失败(可能网络/运行时问题,"
                    f"cookie 文件仍已保存):\n{exc}"
                )

        return out
    except LoginError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LoginError(f"登录流程出错: {exc}") from exc
    finally:
        if cdp:
            cdp.close()
        if proc:
            kill_tree(proc)
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    test_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEST_URL
    try:
        out = run_login(verify=True, test_url=test_url)
    except LoginError as exc:
        print(f"[错误] {exc}")
        return 1
    print(f"[完成] cookies 已保存到: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
