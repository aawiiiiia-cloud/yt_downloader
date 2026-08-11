"""YouTube 视频下载器 GUI

基于 yt-dlp + Tkinter 的桌面下载工具,支持画质/格式选择、多 URL 批处理、
实时进度和日志显示。

运行:
    pip install -U yt-dlp
    python yt_dlp_gui.py

合并 mp4/webm/mkv 或提取音频需要系统已安装 ffmpeg。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

try:
    import yt_dlp
except ImportError:
    yt_dlp = None  # type: ignore[assignment]


# 分辨率标签 -> yt-dlp height 上限;字符串 "audio" 表示仅音频模式
RESOLUTION_OPTIONS: dict[str, int | str | None] = {
    "最佳": None,
    "4K (2160p)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
    "仅音频": "audio",
}

VIDEO_FORMATS = ["mp4", "webm", "mkv"]
AUDIO_FORMATS = ["mp3", "m4a", "wav", "flac", "opus"]
AUDIO_BITRATES = ["128", "192", "256", "320"]


class Settings:
    """~/.yt_dlp_gui.json 持久化。文件缺失/损坏时回退默认值,不抛错。

    Path.home() 能正确处理中文用户名(如 夏伟瑞),切勿用字符串拼接。
    """

    DEFAULT_PATH = Path.home() / ".yt_dlp_gui.json"

    DEFAULTS: dict[str, Any] = {
        "save_dir": str(Path.home() / "Downloads"),
        "resolution": "1080p",
        "format": "mp4",
        "cookies": "无",
        "cookies_file": None,
        "proxy": "",
        "audio_bitrate": "192",
    }

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        self._data = dict(self.DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    # 只吸收已知 key,忽略未来版本新增的未知字段
                    self._data.update(
                        {k: v for k, v in loaded.items() if k in self.DEFAULTS}
                    )
        except (OSError, json.JSONDecodeError):
            pass  # 文件损坏或不可读 → 保持默认值

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 保存失败不阻断使用


class DownloaderGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("YouTube 视频下载器 (yt-dlp)")
        self.root.geometry("760x640")
        self.root.minsize(680, 580)

        self._msg_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel_flag = threading.Event()
        self._worker: threading.Thread | None = None
        self._js_runtime: str | None = None

        self.settings = Settings()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll_queue()
        self._restore_ui_from_settings()
        self._check_env()

    # ---------- UI 构建 ----------

    def _build_ui(self) -> None:
        root_frm = ttk.Frame(self.root, padding=12)
        root_frm.pack(fill=tk.BOTH, expand=True)

        # 视频 URL
        ttk.Label(root_frm, text="视频 URL(每行一个,支持批量):").grid(
            row=0, column=0, columnspan=4, sticky="w",
        )
        self.url_text = tk.Text(root_frm, height=4, wrap="none", undo=True)
        self.url_text.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 8))

        # 保存目录
        ttk.Label(root_frm, text="保存目录:").grid(row=2, column=0, sticky="w")
        self.save_dir_var = tk.StringVar(
            value=self.settings.get("save_dir", str(Path.home() / "Downloads"))
        )
        ttk.Entry(root_frm, textvariable=self.save_dir_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(6, 6),
        )
        ttk.Button(root_frm, text="浏览...", command=self._choose_dir).grid(
            row=2, column=3, sticky="e",
        )

        # 分辨率 / 格式
        opts_frm = ttk.Frame(root_frm)
        opts_frm.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 4))

        ttk.Label(opts_frm, text="分辨率:").pack(side=tk.LEFT)
        self.resolution_var = tk.StringVar(value=self.settings.get("resolution", "1080p"))
        self.resolution_cb = ttk.Combobox(
            opts_frm, textvariable=self.resolution_var,
            values=list(RESOLUTION_OPTIONS.keys()), state="readonly", width=14,
        )
        self.resolution_cb.pack(side=tk.LEFT, padx=(6, 18))
        self.resolution_cb.bind("<<ComboboxSelected>>", self._on_resolution_change)

        ttk.Label(opts_frm, text="输出格式:").pack(side=tk.LEFT)
        self.format_var = tk.StringVar(value=self.settings.get("format", "mp4"))
        self.format_cb = ttk.Combobox(
            opts_frm, textvariable=self.format_var, values=VIDEO_FORMATS,
            state="readonly", width=10,
        )
        self.format_cb.pack(side=tk.LEFT, padx=(6, 12))

        ttk.Label(opts_frm, text="码率(kbps):").pack(side=tk.LEFT)
        self.audio_bitrate_var = tk.StringVar(
            value=self.settings.get("audio_bitrate", "192")
        )
        self.audio_bitrate_cb = ttk.Combobox(
            opts_frm, textvariable=self.audio_bitrate_var,
            values=AUDIO_BITRATES, state=tk.DISABLED, width=6,
        )
        self.audio_bitrate_cb.pack(side=tk.LEFT, padx=(6, 12))

        self.subtitle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts_frm, text="下载字幕(自动+手动)", variable=self.subtitle_var,
        ).pack(side=tk.LEFT)

        # Cookies 来源(YouTube 反 bot 校验通常需要)
        # 2026 现状:Chrome/Edge 127+ 的 App-Bound Encryption 导致直读失效,
        # Firefox 不加密最可靠。优先"一键获取",兜底"从文件"。
        cookies_frm = ttk.Frame(root_frm)
        cookies_frm.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        ttk.Label(cookies_frm, text="Cookies:").pack(side=tk.LEFT)
        self.cookies_var = tk.StringVar(value=self.settings.get("cookies", "无"))
        self.cookies_file: str | None = self.settings.get("cookies_file")
        self._cookies_choices = ["无", "firefox", "chrome", "edge"]
        self.cookies_cb = ttk.Combobox(
            cookies_frm, textvariable=self.cookies_var,
            values=self._cookies_choices, state="readonly", width=10,
        )
        self.cookies_cb.pack(side=tk.LEFT, padx=(6, 8))
        self.cookies_cb.bind("<<ComboboxSelected>>", self._on_cookies_change)
        ttk.Button(
            cookies_frm, text="一键获取", command=self._one_click_cookies,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            cookies_frm, text="从文件...", command=self._choose_cookies_file,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.cookies_hint = ttk.Label(cookies_frm, text="", foreground="#666")
        self.cookies_hint.pack(side=tk.LEFT)

        # 代理(可选,不提供科学上网,只是把已有代理地址传给 yt-dlp)
        proxy_frm = ttk.Frame(root_frm)
        proxy_frm.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(0, 4))
        ttk.Label(proxy_frm, text="代理(可选):").pack(side=tk.LEFT)
        self.proxy_var = tk.StringVar(value=self.settings.get("proxy", ""))
        ttk.Entry(proxy_frm, textvariable=self.proxy_var, width=30).pack(
            side=tk.LEFT, padx=(6, 6),
        )
        ttk.Label(
            proxy_frm,
            text="例: http://127.0.0.1:7890 或 socks5://127.0.0.1:1080(直连留空)",
            foreground="#666",
        ).pack(side=tk.LEFT)
        self.proxy_var.trace_add("write", self._on_proxy_change)

        # 按钮区
        btn_frm = ttk.Frame(root_frm)
        btn_frm.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 6))
        self.start_btn = ttk.Button(btn_frm, text="开始下载", command=self._start_download)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(
            btn_frm, text="停止", command=self._request_stop, state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frm, text="打开目录", command=self._open_save_dir).pack(
            side=tk.LEFT, padx=(8, 0),
        )
        ttk.Button(btn_frm, text="清空日志", command=self._clear_log).pack(side=tk.RIGHT)

        # 进度
        self.progress = ttk.Progressbar(root_frm, mode="determinate", maximum=100)
        self.progress.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(4, 2))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(root_frm, textvariable=self.status_var).grid(
            row=8, column=0, columnspan=4, sticky="w", pady=(0, 6),
        )

        # 日志
        ttk.Label(root_frm, text="日志:").grid(row=9, column=0, sticky="w")
        log_wrap = ttk.Frame(root_frm)
        log_wrap.grid(row=10, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        self.log_text = tk.Text(
            log_wrap, height=15, wrap="word", state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_wrap, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)

        root_frm.columnconfigure(1, weight=1)
        root_frm.columnconfigure(2, weight=1)
        root_frm.rowconfigure(10, weight=1)

    def _restore_ui_from_settings(self) -> None:
        """把设置文件里的值刷到控件(combobox 联动 + cookies 提示文案)。"""
        self._on_resolution_change()  # 若上次是音频模式,格式下拉切到音频格式
        # 旧版设置里可能有 brave/opera/从文件... 等已废弃值,归一到新选项
        if self.cookies_var.get() not in self._cookies_choices:
            self.cookies_var.set("无")
        self._refresh_cookies_hint()

    def _on_close(self) -> None:
        """窗口关闭前保存设置(代理/码率控件在后续步骤才加,用 hasattr 保护)。"""
        self.settings.set("save_dir", self.save_dir_var.get().strip())
        self.settings.set("resolution", self.resolution_var.get())
        self.settings.set("format", self.format_var.get())
        self.settings.set("cookies", self.cookies_var.get())
        self.settings.set("cookies_file", self.cookies_file)
        if hasattr(self, "proxy_var"):
            self.settings.set("proxy", self.proxy_var.get().strip())
        if hasattr(self, "audio_bitrate_var"):
            self.settings.set("audio_bitrate", self.audio_bitrate_var.get())
        self.root.destroy()

    def _on_resolution_change(self, _event: object | None = None) -> None:
        if self.resolution_var.get() == "仅音频":
            self.format_cb.configure(values=AUDIO_FORMATS)
            self.format_var.set("mp3")
            self.audio_bitrate_cb.configure(state="readonly")
        else:
            self.format_cb.configure(values=VIDEO_FORMATS)
            if self.format_var.get() not in VIDEO_FORMATS:
                self.format_var.set("mp4")
            self.audio_bitrate_cb.configure(state=tk.DISABLED)
        self.settings.set("resolution", self.resolution_var.get())
        self.settings.set("format", self.format_var.get())
        self.settings.set("audio_bitrate", self.audio_bitrate_var.get())

    def _on_cookies_change(self, _event: object | None = None) -> None:
        choice = self.cookies_var.get()
        # 选了"无"或某个浏览器时,清掉文件引用(文件与浏览器二选一)
        self.cookies_file = None
        self._refresh_cookies_hint()
        self.settings.set("cookies", choice)
        self.settings.set("cookies_file", None)

    def _choose_cookies_file(self) -> None:
        """从文件导入 cookies.txt(独立按钮,不再混进下拉框)。"""
        path = filedialog.askopenfilename(
            title="选择 cookies.txt(Netscape 格式)",
            filetypes=[("Cookies 文件", "*.txt"), ("所有文件", "*.*")],
        )
        if path:
            self.cookies_file = path
            self.cookies_var.set("无")
            self._refresh_cookies_hint()
            self.settings.set("cookies", "无")
            self.settings.set("cookies_file", path)
        else:
            self._refresh_cookies_hint()

    def _one_click_cookies(self) -> None:
        """一键检测已登录 YouTube 的浏览器并设置 cookiesfrombrowser。

        只认 Firefox(能确认登录态且能读取)。Chrome/Edge 127+ 的
        App-Bound Encryption 读不了,即使检测到也只会让下载报错,
        所以不给它们"自动选择",而是给出针对性引导。
        """
        browser = self._detect_browser_with_youtube()
        if browser:
            self.cookies_file = None
            self.cookies_var.set(browser)
            self._refresh_cookies_hint()
            self.settings.set("cookies", browser)
            self.settings.set("cookies_file", None)
            self._log(f"[Cookies] 一键获取: 选择 {browser}")
            return

        # 库读不了是独立的一类:不能误导成"没登录",要给出真正的原因
        if getattr(self, "_detect_issue", None) == "unreadable_db":
            messagebox.showinfo(
                "未找到可用的 YouTube Cookies",
                "检测到 Firefox,但读不到它的 Cookies 数据库\n"
                "(cookies.sqlite 无法打开)。\n\n"
                "常见原因:\n"
                "1. Firefox 没完全退出(含后台进程)→ 彻底关闭后重试\n"
                "2. 终端安全软件(如阿里郎/DLP)拦截了对浏览器凭据文件的读取\n\n"
                "注意:此时连 yt-dlp 下载也会因读不到 cookies 而失败。\n"
                "若是安全软件拦截,需联系 IT 放行本程序,\n"
                "或点\"从文件...\"导入现成的 cookies.txt。",
            )
            return

        # 没找到可用的 Firefox 登录 → 按本机实际情况给指引
        has_firefox = self._has_browser_profile("firefox")
        has_chromium = self._has_browser_profile("edge") or self._has_browser_profile("chrome")

        if has_chromium and not has_firefox:
            msg = (
                "检测到 Chrome/Edge,但它们的 App-Bound Encryption(127+ 起)\n"
                "让 yt-dlp 无法读取 cookies,这是已知限制,绕不过去。\n\n"
                "推荐:安装 Firefox(firefox.com)\n"
                "→ 在 Firefox 登录 YouTube → 关掉 Firefox\n"
                "→ 回到这里点\"一键获取\"。\n\n"
                "或者点\"从文件...\"导入 cookies.txt。"
            )
        elif has_firefox:
            msg = (
                "检测到 Firefox,但没找到 YouTube 登录。\n\n"
                "请打开 Firefox 登录 YouTube,关掉 Firefox,\n"
                "再点一次\"一键获取\"。\n\n"
                "或者点\"从文件...\"导入 cookies.txt。"
            )
        else:
            msg = (
                "未检测到可用的浏览器登录。\n\n"
                "推荐:安装 Firefox(firefox.com)\n"
                "→ 在 Firefox 登录 YouTube → 关掉 Firefox\n"
                "→ 回到这里点\"一键获取\"。"
            )
        messagebox.showinfo("未找到可用的 YouTube Cookies", msg)

    def _detect_browser_with_youtube(self) -> str | None:
        """找已登录 YouTube 且下载器能读取 cookies 的浏览器。

        只有 Firefox 满足:cookies 不加密,可确认 youtube 登录 cookie 真实存在。
        Chrome/Edge 127+ 加密后 cookiesfrombrowser 读不到,不当作"已就绪"。

        未命中时把原因记到 self._detect_issue,供一键获取给出准确引导:
          None            库可读,只是确实没有登录
          "unreadable_db" 有 cookies.sqlite 但打不开(被占用/安全软件拦截),
                          此时不能误报成"没登录"
        """
        self._detect_issue = None
        home = Path.home()
        ff_profiles = home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles"
        if ff_profiles.is_dir():
            saw_unreadable = False
            for prof in ff_profiles.glob("*.default*"):
                db = prof / "cookies.sqlite"
                if not db.exists():
                    continue
                state = self._firefox_login_state(db)
                if state == "found":
                    return "firefox"
                if state == "unreadable":
                    saw_unreadable = True
            if saw_unreadable:
                self._detect_issue = "unreadable_db"
        return None

    @staticmethod
    def _has_browser_profile(name: str) -> bool:
        """浏览器是否装过(用于引导文案,不验证登录态)。"""
        home = Path.home()
        if name == "firefox":
            return (home / "AppData" / "Roaming" / "Mozilla" / "Firefox" / "Profiles").is_dir()
        if name == "edge":
            return (home / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data").is_dir()
        if name == "chrome":
            return (home / "AppData" / "Local" / "Google" / "Chrome" / "User Data").is_dir()
        return False

    @staticmethod
    def _firefox_login_state(db_path: Path) -> str:
        """只读方式查 Firefox cookies 库里的 YouTube 登录状态。

        返回三态,避免把"库读不了"误判成"没登录":
          "found"      命中登录 cookie
          "none"       库可读,但没有登录 cookie
          "unreadable" 库打不开(被 Firefox 占用/安全软件拦截/损坏)

        ⚠ Firefox 的 moz_cookies 表用 `host` 列(不是 Chrome 的 `host_key`)。
        """
        import sqlite3

        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        except Exception:  # noqa: BLE001  打开即失败 → 读不了
            return "unreadable"
        try:
            cur = con.execute(
                "SELECT COUNT(*) FROM moz_cookies "
                "WHERE host LIKE '%youtube.com' "
                "AND name IN ("
                "'SID','HSID','SSID','APISID','SAPISID',"
                "'LOGIN_INFO','__Secure-3PAPISID','__Secure-1PSID'"
                ")"
            )
            return "found" if cur.fetchone()[0] > 0 else "none"
        except Exception:  # noqa: BLE001  查询时才发现读不了(锁/拦截/损坏)
            return "unreadable"
        finally:
            con.close()

    def _refresh_cookies_hint(self) -> None:
        if self.cookies_file:
            self.cookies_hint.configure(
                text=f"使用文件: {os.path.basename(self.cookies_file)}",
                foreground="#0a7a0a",
            )
            return
        choice = self.cookies_var.get()
        if choice == "无":
            self.cookies_hint.configure(
                text="无(遇到 Sign in 报错时点\"一键获取\")",
                foreground="#666",
            )
        elif choice == "firefox":
            self.cookies_hint.configure(
                text="✓ Firefox 直读最可靠,免插件",
                foreground="#0a7a0a",
            )
        else:
            self.cookies_hint.configure(
                text=f"⚠ {choice} 127+ 受 App-Bound Encryption 影响可能读不到,推荐 Firefox",
                foreground="#b8860b",
            )

    def _on_proxy_change(self, *_args: object) -> None:
        """代理输入每次按键都触发,用 after 防抖(500ms 后写盘)。"""
        after_id = getattr(self, "_proxy_save_after", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._proxy_save_after = self.root.after(
            500,
            lambda: self.settings.set("proxy", self.proxy_var.get().strip()),
        )

    # ---------- 环境检查 ----------

    def _check_env(self) -> None:
        if yt_dlp is None:
            self._log("[错误] 未检测到 yt-dlp 依赖,请先执行: pip install -U yt-dlp")
            self.start_btn.configure(state=tk.DISABLED)
            return
        self._log(f"[信息] yt-dlp 版本: {yt_dlp.version.__version__}")

        # PO token provider:自动生成 Proof of Origin token,减轻 YouTube 的 bot 检测。
        # 由 yt-dlp 插件机制自动注册(bgutil-ytdlp-pot-provider 包)。
        try:
            import yt_dlp_plugins.extractor.getpot_bgutil  # noqa: F401
            self._log("[信息] PO Token 插件已就位 (bgutil-ytdlp-pot-provider)")
        except ImportError:
            self._log(
                "[警告] PO Token 插件未安装,可能更容易触发 bot 检测。"
                "建议: pip install bgutil-ytdlp-pot-provider"
            )

        if shutil.which("ffmpeg") is None:
            self._log(
                "[警告] 未在 PATH 中检测到 ffmpeg,"
                "合并高清视频或提取音频将会失败。请安装 ffmpeg 后再使用相关功能。"
            )
        else:
            self._log("[信息] ffmpeg 已就位,可正常合并/转码")

        # JS runtime 探测:YouTube 需要 JS 引擎解密视频 URL(n challenge)
        # 优先级:node > deno > 无
        # Node.js 更常见、企业环境放行率更高,Deno 常被公司安全策略拦截
        if shutil.which("node"):
            self._js_runtime = "node"
            self._log("[信息] 检测到 Node.js,将用于 YouTube JS challenge 解算")
        elif shutil.which("deno"):
            self._js_runtime = "deno"
            self._log("[信息] 检测到 Deno,将用于 YouTube JS challenge 解算")
        else:
            self._log(
                "[警告] 未检测到 Node.js / Deno —— YouTube 视频 URL 无法解密,"
                "会报 'The page needs to be reloaded'。请安装 Node.js(推荐)或 Deno。"
            )

    # ---------- 交互动作 ----------

    def _choose_dir(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.save_dir_var.get() or str(Path.home()),
        )
        if chosen:
            self.save_dir_var.set(chosen)
            self.settings.set("save_dir", chosen)

    def _open_save_dir(self) -> None:
        path = Path(self.save_dir_var.get().strip())
        if not path.exists():
            messagebox.showinfo("提示", "目录不存在,先选择或下载后再打开")
            return
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif shutil.which("xdg-open"):
                os.system(f'xdg-open "{path}"')
            elif shutil.which("open"):
                os.system(f'open "{path}"')
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("无法打开", str(exc))

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _log(self, msg: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ---------- 队列轮询 (让工作线程安全更新 UI) ----------

    def _emit(self, kind: str, payload: Any = None) -> None:
        self._msg_queue.put((kind, payload))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    self.progress["value"] = float(payload)
                elif kind == "done":
                    self._on_finish(*payload)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    # ---------- 下载主流程 ----------

    def _start_download(self) -> None:
        if yt_dlp is None:
            messagebox.showerror("缺少依赖", "未检测到 yt-dlp,请先执行: pip install -U yt-dlp")
            return

        urls = [
            u.strip()
            for u in self.url_text.get("1.0", tk.END).splitlines()
            if u.strip()
        ]
        if not urls:
            messagebox.showwarning("提示", "请先输入至少一个视频 URL")
            return

        save_dir = self.save_dir_var.get().strip()
        if not save_dir:
            messagebox.showwarning("提示", "请选择保存目录")
            return
        try:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("目录不可用", f"无法创建目录: {exc}")
            return

        self._cancel_flag.clear()
        self.progress["value"] = 0
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set("准备下载...")

        opts = self._build_ydl_opts(save_dir)

        # 校验:cookies.txt 文件选了但已被移动/删除 → 提醒
        if self.cookies_file and not Path(self.cookies_file).exists():
            messagebox.showwarning(
                "Cookies 文件不存在",
                f"cookies.txt 已被移动或删除:\n{self.cookies_file}\n\n"
                "请重新点\"从文件...\"选择。",
            )
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            return

        self._log(f"[开始] 共 {len(urls)} 个任务,输出到 {save_dir}")
        # 明确输出当前 Cookies 策略,便于排查
        if "cookiefile" in opts:
            self._log(f"[Cookies] 使用文件: {opts['cookiefile']}")
        elif "cookiesfrombrowser" in opts:
            self._log(f"[Cookies] 从浏览器读取: {opts['cookiesfrombrowser'][0]}")
        else:
            self._log("[Cookies] 未启用(遇到 'Sign in to confirm' 报错时请选择浏览器或文件)")
        # 输出当前 JS runtime
        if "js_runtimes" in opts:
            runtime_name = next(iter(opts["js_runtimes"].keys()))
            self._log(f"[JS runtime] 使用: {runtime_name}")
        else:
            self._log("[JS runtime] 未指定(YouTube 大概率会失败)")
        if "remote_components" in opts:
            self._log(
                f"[远程组件] 启用: {opts['remote_components']} "
                "(本地 yt-dlp-ejs 脚本优先,此仅兜底)"
            )

        self._worker = threading.Thread(
            target=self._run_download, args=(urls, opts), daemon=True,
        )
        self._worker.start()

    def _request_stop(self) -> None:
        self._cancel_flag.set()
        self._emit("status", "正在停止...(等待当前分片结束)")

    def _build_ydl_opts(self, save_dir: str) -> dict[str, Any]:
        resolution = self.resolution_var.get()
        fmt = self.format_var.get()
        height = RESOLUTION_OPTIONS.get(resolution)

        opts: dict[str, Any] = {
            "outtmpl": os.path.join(save_dir, "%(title)s [%(id)s].%(ext)s"),
            "progress_hooks": [self._progress_hook],
            "logger": _YdlLogger(self._emit),
            "noprogress": True,   # 关掉 stderr 进度,统一走 hook
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": False,
            "retries": 3,
            "concurrent_fragment_downloads": 4,
        }

        if height == "audio":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": fmt,
                "preferredquality": self.audio_bitrate_var.get(),
            }]
        else:
            if height is None:
                selector = "bestvideo+bestaudio/best"
            else:
                selector = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            opts["format"] = selector
            opts["merge_output_format"] = fmt

        if self.subtitle_var.get():
            opts.update({
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
                "subtitlesformat": "srt/best",
            })

        # Cookies:文件优先(显式导入),其次浏览器直读
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        else:
            cookies_choice = self.cookies_var.get()
            if cookies_choice != "无":
                opts["cookiesfrombrowser"] = (cookies_choice,)

        # 代理:不提供科学上网,只把已有代理地址传给 yt-dlp
        proxy = self.proxy_var.get().strip()
        if proxy:
            opts["proxy"] = proxy

        # 显式告诉 yt-dlp 用探测到的 JS runtime(默认只找 deno,不指定 node 就找不到)
        # yt-dlp 2026 起要求 dict 格式: {runtime_name: {config}}
        if self._js_runtime:
            opts["js_runtimes"] = {self._js_runtime: {}}
            # 允许从 GitHub 下载 EJS challenge solver 脚本供 Node/Deno 执行,
            # 用于破解 YouTube 视频 URL 中的 n-signature 混淆。
            # yt-dlp 2026 起为安全考虑默认关闭此项,需显式启用。
            opts["remote_components"] = ["ejs:github"]

        return opts

    def _progress_hook(self, d: dict[str, Any]) -> None:
        if self._cancel_flag.is_set():
            raise _UserCancelled()

        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0) or 0
            percent = (downloaded / total * 100) if total else 0.0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0
            filename = os.path.basename(d.get("filename") or "")
            self._emit("progress", percent)
            self._emit(
                "status",
                f"{filename}  {percent:5.1f}%  "
                f"{_fmt_size(speed)}/s  ETA {_fmt_eta(eta)}",
            )
        elif status == "finished":
            self._emit("progress", 100)
            self._emit("status", "分片完成,进入后处理(合并/转码)...")
            self._emit("log", f"[完成] {d.get('filename', '')}")

    def _run_download(self, urls: list[str], opts: dict[str, Any]) -> None:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[union-attr]
                ydl.download(urls)
            self._emit("done", ("success", ""))
        except _UserCancelled:
            self._emit("done", ("cancelled", ""))
        except Exception as exc:  # noqa: BLE001
            self._emit("done", ("error", str(exc)))

    def _on_finish(self, result: str, extra: str) -> None:
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        if result == "success":
            self.status_var.set("全部下载完成 ✓")
            self._log("[成功] 所有任务已完成")
        elif result == "cancelled":
            self.status_var.set("已停止")
            self._log("[取消] 用户中止下载")
        else:
            self.status_var.set("下载失败")
            # 识别常见的浏览器 cookies 读取失败,给出可操作的提示
            if "cookie database" in str(extra).lower() or "cookiesfrombrowser" in str(extra).lower():
                self._log("[错误] 读取浏览器 cookies 失败。")
                self._log(
                    "   原因: Chrome/Edge 127+ 的 App-Bound Encryption 会阻止外部工具读取 cookies"
                )
                self._log(
                    "   解决: 用 Firefox 登录 YouTube 后点\"一键获取\""
                    "(推荐,免费);或点\"从文件...\"导入 cookies.txt"
                )
            self._log(f"[错误] {extra}")


class _UserCancelled(Exception):
    """由 progress_hook 抛出,冒泡到主循环表示用户取消。"""


class _YdlLogger:
    """把 yt-dlp 的内部输出转发到 GUI 日志窗口。"""

    def __init__(self, emit: Callable[[str, Any], None]) -> None:
        self._emit = emit

    def debug(self, msg: str) -> None:
        if not msg or msg.startswith("[debug]"):
            return
        self._emit("log", msg)

    def info(self, msg: str) -> None:
        self._emit("log", msg)

    def warning(self, msg: str) -> None:
        self._emit("log", f"[警告] {msg}")

    def error(self, msg: str) -> None:
        self._emit("log", f"[错误] {msg}")


def _fmt_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:6.1f}{unit}"
        n /= 1024
    return f"{n:6.1f}TB"


def _fmt_eta(sec: int) -> str:
    if not sec:
        return "--:--"
    m, s = divmod(int(sec), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _is_bundled() -> bool:
    """pyinstaller 打包版检测。"""
    return getattr(sys, "frozen", False)


def _setup_env() -> None:
    """源码版:自动检测并安装缺失环境;打包版:把内置 tools/ 加进 PATH。"""
    if _is_bundled():
        exe_dir = Path(sys.executable).parent
        tools_dir = exe_dir / "tools"
        if tools_dir.is_dir():
            os.environ["PATH"] = str(tools_dir) + os.pathsep + os.environ.get("PATH", "")
        return
    try:
        from bootstrap import bootstrap

        bootstrap()
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] 环境自动安装未完成: {exc}")


def main() -> None:
    global yt_dlp
    _setup_env()
    # bootstrap 可能刚 pip 装好 yt-dlp,但顶层 import 时它还是 None,重新绑定
    if yt_dlp is None:
        try:
            import yt_dlp as _ydl

            yt_dlp = _ydl
        except ImportError:
            pass

    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass
    DownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
