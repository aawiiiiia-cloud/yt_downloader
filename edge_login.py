"""Edge + CDP 内置登录:用简洁的 Edge 应用窗口抓取站点 cookies(含 HttpOnly)。

原理(参考 YoutubeDownloader 的 WebView2 流程,但使用本机 Edge 应用模式):
- 用独立用户配置目录启动一个 Edge 实例(带 --remote-debugging-port=0 随机端口)
- 通过 Chrome DevTools Protocol(CDP)打开目标站点登录页
- 用户在窗口里正常登录(可过验证码/两步验证)
- 检测到目标站点认证 cookie 后,用 Network.getAllCookies 全量抓取(含 HttpOnly)
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
import os
import csv
import io
import shutil
import subprocess
import sys
import tempfile
import threading
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

# 各站点登录配置。Bilibili 官方 extractor 以 SESSDATA 判断登录态。
SITE_CONFIGS = {
    "youtube": {
        "name": "YouTube",
        "login_url": (
            "https://accounts.google.com/ServiceLogin?continue="
            + urllib.parse.quote("https://www.youtube.com")
        ),
        "home_url": "https://www.youtube.com",
        # 官方建议登录后在同一临时会话转到 robots.txt，再导出并关闭会话，
        # 避免普通 YouTube 页面继续轮换账号 cookie。
        "capture_url": "https://www.youtube.com/robots.txt",
        "auth_names": {
            "__Secure-1PSID", "SID", "SAPISID", "__Secure-3PAPISID",
            "HSID", "SSID", "LOGIN_INFO",
        },
        "auth_domain": "youtube.com",
        "cookie_domains": ("youtube.com", "google.com"),
        "default_file": ".yt_dlp_gui_cookies.txt",
        "verify_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    },
    "bilibili": {
        "name": "哔哩哔哩",
        "login_url": "https://passport.bilibili.com/login",
        "home_url": "https://www.bilibili.com/",
        "capture_url": "https://www.bilibili.com/",
        "auth_names": {"SESSDATA"},
        "auth_domain": "bilibili.com",
        "cookie_domains": ("bilibili.com",),
        "default_file": ".yt_dlp_gui_bilibili_cookies.txt",
        "verify_url": None,
    },
    "xiaohongshu": {
        "name": "小红书",
        # 小红书网页版会在首页按需显示二维码/手机号登录面板。使用真实
        # Edge 页面而不是自行调用登录 API，可减少签名变化带来的维护成本。
        "login_url": "https://www.xiaohongshu.com/explore",
        "home_url": "https://www.xiaohongshu.com/explore",
        "capture_url": "https://www.xiaohongshu.com/explore",
        # a1 等匿名 Cookie 未登录也会存在；web_session 才能区分登录完成。
        "auth_names": {"web_session"},
        "auth_domain": "xiaohongshu.com",
        "cookie_domains": ("xiaohongshu.com",),
        "default_file": ".yt_dlp_gui_xiaohongshu_cookies.txt",
        "verify_url": None,
    },
}

# 兼容旧代码/测试所用的常量。
AUTH_COOKIE_NAMES = SITE_CONFIGS["youtube"]["auth_names"]
COOKIE_DOMAINS = SITE_CONFIGS["youtube"]["cookie_domains"]
DEFAULT_TEST_URL = SITE_CONFIGS["youtube"]["verify_url"]

Progress = Callable[[str], None]

_ACTIVE_LOCK = threading.Lock()
_ACTIVE_PROC: subprocess.Popen | None = None
_ACTIVE_CDP: "CDP | None" = None

_AUTH_FAILURE_MARKERS = (
    "cookies are no longer valid",
    "account cookies have been rotated",
    "provided youtube account cookies are no longer valid",
    "sign in to confirm",
)


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
        # 短超时避免关闭 GUI 时长期卡在 WebSocket recv。
        self.ws = websocket.create_connection(ws_url, timeout=2)
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
            # 应用模式移除地址栏/标签栏，视觉上更接近应用内认证弹窗；
            # 仍是系统 Edge，因此验证码、二维码、两步验证都可正常工作。
            "--window-size=520,720",
            f"--app={url}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LoginError("用户已取消登录")


def wait_for_debug_port(
    profile: Path,
    timeout: float = 30,
    cancel_event: threading.Event | None = None,
) -> int:
    devtools = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _raise_if_cancelled(cancel_event)
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


def has_site_auth_cookie(cookies: list[dict], site: str) -> bool:
    config = SITE_CONFIGS[site]
    return any(
        c.get("name") in config["auth_names"]
        and config["auth_domain"] in c.get("domain", "")
        for c in cookies
    )


def get_youtube_cookies(cookies: list[dict]) -> list[dict]:
    return [
        c for c in cookies
        if any(d in c.get("domain", "") for d in COOKIE_DOMAINS)
    ]


def get_site_cookies(cookies: list[dict], site: str) -> list[dict]:
    domains = SITE_CONFIGS[site]["cookie_domains"]
    return [
        c for c in cookies
        if any(domain in c.get("domain", "") for domain in domains)
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
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def restrict_cookie_file(path: Path, progress: Progress = print) -> bool:
    """尽量把明文 cookie 文件限制为当前 Windows 用户可读写。

    yt-dlp 需要普通文件，无法直接读取 DPAPI 密文，因此这里采用 Windows
    文件 ACL。失败不会删除已经验证的凭据，但会明确提示用户不要共享文件。
    """
    if os.name != "nt":
        try:
            path.chmod(0o600)
            return True
        except OSError as exc:
            progress(f"[警告] 无法收紧登录凭据权限: {exc}")
            return False
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        row = next(csv.reader(io.StringIO(result.stdout.strip())))
        sid = row[1].strip()
        if not sid.startswith("S-"):
            raise RuntimeError("无法识别当前 Windows 用户 SID")
        completed = subprocess.run(
            [
                "icacls", str(path), "/inheritance:r",
                "/grant:r", f"*{sid}:(R,W)",
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(detail or f"icacls 返回 {completed.returncode}")
        return True
    except Exception as exc:  # noqa: BLE001
        progress(f"[警告] 无法限制凭据文件权限，请勿复制或分享该文件: {exc}")
        return False


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


def force_cleanup() -> None:
    """供 GUI 关闭流程调用：立即关闭当前 CDP 并结束临时浏览器进程。"""
    with _ACTIVE_LOCK:
        cdp = _ACTIVE_CDP
        proc = _ACTIVE_PROC
    if cdp:
        cdp.close()
    if proc:
        kill_tree(proc)


class _CredentialValidationLogger:
    """验证时捕获认证失效警告，避免公开视频匿名可播造成假成功。"""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress
        self.auth_invalid = False

    def _record(self, msg: str) -> None:
        text = str(msg or "")
        lowered = text.lower()
        if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
            self.auth_invalid = True
        if text and not text.startswith("[debug]"):
            self.progress(text)

    debug = _record
    info = _record
    warning = _record
    error = _record


def run_login(
    out_path: Path | None = None,
    progress: Progress = print,
    verify: bool = True,
    test_url: str | None = None,
    login_timeout: float = 600,
    site: str = "youtube",
    cancel_event: threading.Event | None = None,
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
    if site not in SITE_CONFIGS:
        raise LoginError(f"不支持的登录站点: {site}")
    config = SITE_CONFIGS[site]
    site_name = config["name"]
    out = (out_path or (Path.home() / config["default_file"])).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    browser = find_browser()
    profile = Path(tempfile.mkdtemp(prefix="edge_login_"))
    # 必须与正式文件位于同一目录，os.replace 才能提供可靠的原子替换。
    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{out.name}.", suffix=".tmp", dir=out.parent, delete=False,
    )
    temp_handle.close()
    temp_out = Path(temp_handle.name)

    global _ACTIVE_PROC, _ACTIVE_CDP

    proc: subprocess.Popen | None = None
    cdp: CDP | None = None
    try:
        progress(f"[1/4] 启动 {Path(browser).name} (独立配置目录)...")
        _raise_if_cancelled(cancel_event)
        proc = launch_browser(browser, profile, config["login_url"])
        with _ACTIVE_LOCK:
            _ACTIVE_PROC = proc
        port = wait_for_debug_port(profile, cancel_event=cancel_event)
        progress(f"[2/4] 调试端口 {port} 就绪,连接 CDP...")
        cdp = CDP(get_page_ws_url(port))
        with _ACTIVE_LOCK:
            _ACTIVE_CDP = cdp
        cdp.send("Network.enable")
        cdp.send("Page.enable")

        progress("=" * 60)
        progress(f"Edge 登录窗口已打开。请完成 {site_name} 登录，")
        progress("成功后脚本会自动识别并保存登录状态。")
        progress("(登录窗口不要手动关,登录完脚本会自己收尾)")
        progress("=" * 60)

        deadline = time.monotonic() + login_timeout
        cookies: list[dict] = []
        while time.monotonic() < deadline:
            _raise_if_cancelled(cancel_event)
            cookies = cdp.send("Network.getAllCookies").get("cookies", [])
            if has_site_auth_cookie(cookies, site):
                break
            if cancel_event is not None:
                cancel_event.wait(2)
            else:
                time.sleep(2)
        else:
            raise LoginError(f"等待登录超时({int(login_timeout // 60)} 分钟),已取消")

        progress(
            f"[3/4] 已检测到 {site_name} 登录，正在安全读取 Cookies，"
            "请勿关闭登录窗口..."
        )
        # YouTube 按官方建议转到 robots.txt 后立即导出；Bilibili 返回首页。
        cdp.send("Page.navigate", {"url": config["capture_url"]})
        if cancel_event is not None:
            cancel_event.wait(4)
        else:
            time.sleep(4)
        _raise_if_cancelled(cancel_event)
        cookies = cdp.send("Network.getAllCookies").get("cookies", [])

        site_cookies = get_site_cookies(cookies, site)
        if not site_cookies:
            raise LoginError("抓取到的 cookie 为空,请重试")
        write_netscape(site_cookies, temp_out)
        progress(f"[读取] 已安全读取 {len(site_cookies)} 个 {site_name} Cookie")

        verify_url = test_url or config["verify_url"]
        if verify and verify_url:
            progress("[4/4] 用 yt-dlp 验证解析标题...")
            try:
                import yt_dlp

                validation_logger = _CredentialValidationLogger(progress)

                with yt_dlp.YoutubeDL({
                    "cookiefile": str(temp_out),
                    # 与主 GUI 一致:dict 格式指定 node 运行时
                    # (打包版里 node.exe 在 tools/ 已由 _setup_env 加进 PATH)
                    "js_runtimes": {"node": {}},
                    "skip_download": True,
                    "quiet": True,
                    "no_warnings": False,
                    "socket_timeout": 15,
                    "logger": validation_logger,
                }) as ydl:
                    _raise_if_cancelled(cancel_event)
                    info = ydl.extract_info(verify_url, download=False)
                title = (info or {}).get("title") or "(无标题)"
                if validation_logger.auth_invalid:
                    raise LoginError("YouTube 返回登录凭据已失效，未保存本次结果")
                progress(f"[成功] 验证通过,视频标题: {title}")
            except LoginError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LoginError(
                    "新登录状态验证失败，旧登录凭据已保留。"
                    f"请检查网络后重试：{exc}"
                ) from exc

        else:
            # Bilibili 当前没有固定公开视频用于无副作用验证；到这里已经确认
            # CDP 捕获结果里存在该站点的认证 cookie。
            if not has_site_auth_cookie(site_cookies, site):
                raise LoginError(f"未在新凭据中找到 {site_name} 登录标志")
            progress(f"[4/4] {site_name} 登录凭据结构已确认")

        _raise_if_cancelled(cancel_event)
        progress("[保存] 验证通过，正在安全保存登录状态...")
        os.replace(temp_out, out)
        restrict_cookie_file(out, progress)
        progress(f"[完成] 新登录状态验证成功，已安全替换 → {out}")
        return out
    except LoginError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LoginError(f"登录流程出错: {exc}") from exc
    finally:
        temp_out.unlink(missing_ok=True)
        if cdp:
            cdp.close()
        if proc:
            kill_tree(proc)
        with _ACTIVE_LOCK:
            if _ACTIVE_CDP is cdp:
                _ACTIVE_CDP = None
            if _ACTIVE_PROC is proc:
                _ACTIVE_PROC = None
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
