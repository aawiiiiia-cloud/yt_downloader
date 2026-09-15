"""yt-dlp 多网站视频下载器 GUI

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
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# 预载 yt_dlp_plugins 命名空间包,必须早于 import yt_dlp:
# yt-dlp 2026 会通过 register_plugin_spec 把 PluginFinder 插入 sys.meta_path 首位,
# 冻结打包环境下它扫描不到插件目录,对 yt_dlp_plugins 和 yt_dlp_plugins.extractor
# 两层(都在它的拦截名单里)直接 raise ModuleNotFoundError(见 yt_dlp/plugins.py
# find_spec:search_locations 为空即抛)。在 yt-dlp 注册 finder 前把这两层命名空间
# 包先载入 sys.modules,后续 import 具体插件模块(getpot_*)即可绕开 finder,
# 走标准机制从 PyInstaller 的 PYZ 加载。
try:
    import yt_dlp_plugins  # noqa: F401
except ImportError:
    pass
try:
    import yt_dlp_plugins.extractor  # noqa: F401
except ImportError:
    pass

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
IMAGE_FORMAT_OPTIONS = {
    "保留原格式（推荐）": "original",
    "统一为 JPG": "jpg",
    "统一为 PNG（无损）": "png",
    "统一为 WebP": "webp",
}
IMAGE_QUALITY_OPTIONS = {
    "最高（95）": 95,
    "高（90）": 90,
    "标准（85）": 85,
    "节省空间（75）": 75,
}

LOGIN_SITES: dict[str, dict[str, str | bool]] = {
    "youtube": {
        "name": "YouTube",
        "note": "Google 账号 · 用于反机器人验证和受限内容",
        "cookie_file": ".yt_dlp_gui_cookies.txt",
        "verify": True,
    },
    "bilibili": {
        "name": "哔哩哔哩",
        "note": "支持二维码/密码登录 · 用于高清及登录内容",
        "cookie_file": ".yt_dlp_gui_bilibili_cookies.txt",
        "verify": False,
    },
    "xiaohongshu": {
        "name": "小红书",
        "note": "支持扫码/手机号登录 · 用于图文和受限内容",
        "cookie_file": ".yt_dlp_gui_xiaohongshu_cookies.txt",
        "verify": False,
    },
}


class Settings:
    """~/.yt_dlp_gui.json 持久化。文件缺失/损坏时回退默认值,不抛错。

    Path.home() 能正确处理中文用户名(如 夏伟瑞),切勿用字符串拼接。
    """

    DEFAULT_PATH = Path.home() / ".yt_dlp_gui.json"

    DEFAULTS: dict[str, Any] = {
        "save_dir": str(Path.home() / "Downloads"),
        "resolution": "1080p",
        "format": "mp4",
        "youtube_cookies_file": None,
        "bilibili_cookies_file": None,
        "xiaohongshu_cookies_file": None,
        "youtube_cookies_valid": None,
        "bilibili_cookies_valid": None,
        "xiaohongshu_cookies_valid": None,
        "youtube_login_at": None,
        "bilibili_login_at": None,
        "xiaohongshu_login_at": None,
        "proxy": "",
        "audio_bitrate": "192",
        "image_format": "original",
        "image_quality": 95,
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
                    # 旧版只有一个 cookies_file，内容来自 YouTube 内置登录。
                    if (
                        not self._data.get("youtube_cookies_file")
                        and loaded.get("cookies_file")
                    ):
                        self._data["youtube_cookies_file"] = loaded["cookies_file"]
        except (OSError, json.JSONDecodeError):
            pass  # 文件损坏或不可读 → 保持默认值

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        temp_path = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, self._path)
        except OSError:
            pass  # 保存失败不阻断使用
        finally:
            temp_path.unlink(missing_ok=True)


class DownloaderGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("媒体下载器")
        self.root.geometry("1120x820")
        self.root.minsize(930, 700)

        self._msg_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel_flag = threading.Event()
        self._worker: threading.Thread | None = None
        self._login_cancel = threading.Event()
        self._login_worker: threading.Thread | None = None
        self._closing = False
        self._forced_login_cleanup = False
        self._js_runtime: str | None = None
        self._login_running: str | None = None
        self._login_dialog: tk.Toplevel | None = None
        self._login_content: ttk.Frame | None = None
        self._login_footer: ttk.Frame | None = None
        self._login_flow_title: tk.StringVar | None = None
        self._login_flow_detail: tk.StringVar | None = None
        self._login_flow_icon: ttk.Label | None = None
        self._login_flow_progress: ttk.Progressbar | None = None
        self._login_status_vars: dict[str, tk.StringVar] = {}
        self._login_buttons: dict[str, ttk.Button] = {}
        self._reported_formats: set[tuple[str, str]] = set()
        self._last_ydl_error: str | None = None
        self._media_batch_window: Any | None = None
        try:
            from pot_provider import PotProviderManager

            self._pot_provider = PotProviderManager()
        except Exception:
            self._pot_provider = None

        self.settings = Settings()
        self.youtube_cookies_file: str | None = self.settings.get("youtube_cookies_file")
        self.bilibili_cookies_file: str | None = self.settings.get("bilibili_cookies_file")
        self.xiaohongshu_cookies_file: str | None = self.settings.get(
            "xiaohongshu_cookies_file"
        )
        # 兼容早期内置登录的默认文件，即使旧设置文件没有记录也不丢登录态。
        legacy_youtube = Path.home() / ".yt_dlp_gui_cookies.txt"
        if not self.youtube_cookies_file and legacy_youtube.is_file():
            self.youtube_cookies_file = str(legacy_youtube)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._configure_styles()
        self._build_ui()
        self._poll_queue()
        self._restore_ui_from_settings()
        self._check_env()

    # ---------- UI 构建 ----------

    def _configure_styles(self) -> None:
        """统一界面主题；只调整视觉，不依赖额外第三方 UI 库。"""
        self._colors = {
            "window": "#F4F6FA",
            "card": "#FFFFFF",
            "text": "#172033",
            "muted": "#667085",
            "border": "#DDE3EC",
            "accent": "#2563EB",
            "accent_active": "#1D4ED8",
            "danger": "#DC2626",
            "danger_active": "#B91C1C",
            "log": "#111827",
            "log_text": "#D1D5DB",
        }
        self.root.configure(bg=self._colors["window"])
        style = ttk.Style(self.root)
        # clam 在 Windows 上允许可靠设置按钮、输入框和进度条颜色。
        if "clam" in style.theme_names():
            style.theme_use("clam")

        font = ("Microsoft YaHei UI", 10)
        self.root.option_add("*Font", font)
        style.configure(
            "TNotebook", background=self._colors["window"], borderwidth=0,
            tabmargins=(18, 10, 0, 0),
        )
        style.configure(
            "TNotebook.Tab", background="#E8EDF5", foreground=self._colors["muted"],
            padding=(22, 10), font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self._colors["card"]), ("active", "#EFF6FF")],
            foreground=[("selected", self._colors["accent"]), ("active", self._colors["text"])],
        )
        style.configure("TFrame", background=self._colors["window"])
        style.configure("Card.TFrame", background=self._colors["card"])
        style.configure(
            "TLabel", background=self._colors["window"], foreground=self._colors["text"]
        )
        style.configure(
            "Card.TLabel", background=self._colors["card"], foreground=self._colors["text"]
        )
        style.configure(
            "Muted.Card.TLabel", background=self._colors["card"],
            foreground=self._colors["muted"], font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Title.TLabel", background=self._colors["window"],
            foreground=self._colors["text"], font=("Microsoft YaHei UI", 21, "bold"),
        )
        style.configure(
            "Subtitle.TLabel", background=self._colors["window"],
            foreground=self._colors["muted"], font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Section.TLabel", background=self._colors["card"],
            foreground=self._colors["text"], font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure(
            "TEntry", fieldbackground="#FFFFFF", foreground=self._colors["text"],
            bordercolor=self._colors["border"], lightcolor=self._colors["border"],
            darkcolor=self._colors["border"], padding=7,
        )
        style.configure(
            "TCombobox", fieldbackground="#FFFFFF", foreground=self._colors["text"],
            bordercolor=self._colors["border"], arrowcolor=self._colors["muted"], padding=6,
        )
        style.configure("TButton", padding=(12, 7), font=("Microsoft YaHei UI", 9))
        style.configure(
            "Accent.TButton", background=self._colors["accent"], foreground="#FFFFFF",
            bordercolor=self._colors["accent"], padding=(20, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", self._colors["accent_active"]),
                        ("disabled", "#AFC3F5")],
            foreground=[("disabled", "#F8FAFC")],
        )
        style.configure(
            "Danger.TButton", background="#FFF1F2", foreground=self._colors["danger"],
            bordercolor="#FECDD3", padding=(14, 9),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#FFE4E6"), ("disabled", "#F3F4F6")],
            foreground=[("disabled", "#9CA3AF")],
        )
        style.configure(
            "Account.TButton", background="#EFF6FF", foreground=self._colors["accent"],
            bordercolor="#BFDBFE", padding=(14, 7),
        )
        style.map("Account.TButton", background=[("active", "#DBEAFE")])
        style.configure(
            "Blue.Horizontal.TProgressbar", troughcolor="#E5EAF2",
            background=self._colors["accent"], bordercolor="#E5EAF2",
            lightcolor=self._colors["accent"], darkcolor=self._colors["accent"],
        )
        style.configure(
            "LoginCard.TFrame", background="#F8FAFC", relief="solid", borderwidth=1,
        )
        style.configure("LoginCard.TLabel", background="#F8FAFC", foreground=self._colors["text"])
        style.configure(
            "LoginMuted.TLabel", background="#F8FAFC", foreground=self._colors["muted"],
            font=("Microsoft YaHei UI", 9),
        )

    def _build_ui(self) -> None:
        self.main_notebook = ttk.Notebook(self.root)
        self.main_notebook.pack(fill=tk.BOTH, expand=True)
        self.download_tab = ttk.Frame(self.main_notebook)
        self.media_batch_tab = ttk.Frame(self.main_notebook)
        self.main_notebook.add(self.download_tab, text="  下载  ")
        self.main_notebook.add(self.media_batch_tab, text="  媒体批处理  ")

        root_frm = ttk.Frame(self.download_tab, padding=(22, 18, 22, 20))
        root_frm.pack(fill=tk.BOTH, expand=True)

        # 顶部标题
        header = ttk.Frame(root_frm)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(header, text="媒体下载器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header, text="支持 YouTube、哔哩哔哩、小红书图文及 yt-dlp 兼容网站",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # 链接与保存位置
        input_card = ttk.Frame(root_frm, style="Card.TFrame", padding=16)
        input_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(input_card, text="下载链接", style="Section.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        ttk.Label(
            input_card, text="每行粘贴一个网址，可批量下载",
            style="Muted.Card.TLabel",
        ).grid(row=0, column=1, columnspan=2, sticky="e")
        self.url_text = tk.Text(
            input_card, height=4, wrap="none", undo=True,
            bg="#F8FAFC", fg=self._colors["text"], insertbackground=self._colors["text"],
            selectbackground="#BFDBFE", relief="solid", borderwidth=1,
            highlightthickness=1, highlightbackground=self._colors["border"],
            highlightcolor=self._colors["accent"], padx=10, pady=9,
            font=("Microsoft YaHei UI", 10),
        )
        self.url_text.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(9, 13))

        ttk.Label(input_card, text="保存到", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.save_dir_var = tk.StringVar(
            value=self.settings.get("save_dir", str(Path.home() / "Downloads"))
        )
        ttk.Entry(input_card, textvariable=self.save_dir_var).grid(
            row=2, column=1, sticky="ew", padx=(10, 8),
        )
        ttk.Button(input_card, text="选择文件夹", command=self._choose_dir).grid(
            row=2, column=2, sticky="e",
        )
        input_card.columnconfigure(1, weight=1)

        # 下载设置卡片
        settings_card = ttk.Frame(root_frm, style="Card.TFrame", padding=16)
        settings_card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(settings_card, text="下载设置", style="Section.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 11),
        )

        ttk.Label(settings_card, text="视频画质", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        self.resolution_var = tk.StringVar(value=self.settings.get("resolution", "1080p"))
        self.resolution_cb = ttk.Combobox(
            settings_card, textvariable=self.resolution_var,
            values=list(RESOLUTION_OPTIONS.keys()), state="readonly", width=14,
        )
        self.resolution_cb.grid(row=2, column=0, sticky="ew", padx=(0, 12), pady=(5, 0))
        self.resolution_cb.bind("<<ComboboxSelected>>", self._on_resolution_change)

        ttk.Label(settings_card, text="视频/音频格式", style="Card.TLabel").grid(row=1, column=1, sticky="w")
        self.format_var = tk.StringVar(value=self.settings.get("format", "mp4"))
        self.format_cb = ttk.Combobox(
            settings_card, textvariable=self.format_var, values=VIDEO_FORMATS,
            state="readonly", width=10,
        )
        self.format_cb.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(5, 0))

        ttk.Label(settings_card, text="音频码率", style="Card.TLabel").grid(row=1, column=2, sticky="w")
        self.audio_bitrate_var = tk.StringVar(
            value=self.settings.get("audio_bitrate", "192")
        )
        self.audio_bitrate_cb = ttk.Combobox(
            settings_card, textvariable=self.audio_bitrate_var,
            values=AUDIO_BITRATES, state=tk.DISABLED, width=6,
        )
        self.audio_bitrate_cb.grid(row=2, column=2, sticky="ew", padx=(0, 18), pady=(5, 0))

        self.subtitle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            settings_card, text="同时下载字幕（自动 + 手动）", variable=self.subtitle_var,
            style="Card.TCheckbutton",
        ).grid(row=2, column=3, sticky="w", pady=(5, 0))
        ttk.Style(self.root).configure(
            "Card.TCheckbutton", background=self._colors["card"],
            foreground=self._colors["text"],
        )

        ttk.Separator(settings_card).grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(13, 10)
        )
        ttk.Label(settings_card, text="图片格式（图文链接）", style="Card.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        saved_image_format = str(self.settings.get("image_format", "original"))
        image_format_label = next(
            (label for label, value in IMAGE_FORMAT_OPTIONS.items() if value == saved_image_format),
            "保留原格式（推荐）",
        )
        self.image_format_var = tk.StringVar(value=image_format_label)
        self.image_format_cb = ttk.Combobox(
            settings_card, textvariable=self.image_format_var,
            values=tuple(IMAGE_FORMAT_OPTIONS), state="readonly", width=18,
        )
        self.image_format_cb.grid(row=5, column=0, sticky="ew", padx=(0, 12), pady=(5, 0))
        self.image_format_cb.bind("<<ComboboxSelected>>", self._on_image_format_change)

        ttk.Label(settings_card, text="JPG / WebP 画质", style="Card.TLabel").grid(
            row=4, column=1, sticky="w"
        )
        try:
            saved_quality = int(self.settings.get("image_quality", 95) or 95)
        except (TypeError, ValueError):
            saved_quality = 95
        image_quality_label = next(
            (label for label, value in IMAGE_QUALITY_OPTIONS.items() if value == saved_quality),
            "最高（95）",
        )
        self.image_quality_var = tk.StringVar(value=image_quality_label)
        self.image_quality_cb = ttk.Combobox(
            settings_card, textvariable=self.image_quality_var,
            values=tuple(IMAGE_QUALITY_OPTIONS), state="readonly", width=14,
        )
        self.image_quality_cb.grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=(5, 0))
        self.image_quality_cb.bind("<<ComboboxSelected>>", self._on_image_quality_change)
        self.image_format_hint_var = tk.StringVar()
        ttk.Label(
            settings_card, textvariable=self.image_format_hint_var,
            style="Muted.Card.TLabel", wraplength=330,
        ).grid(row=5, column=2, columnspan=2, sticky="w", pady=(5, 0))

        # 登录凭据由内置登录统一管理；按 URL 自动使用对应站点 cookie。
        account_row = ttk.Frame(settings_card, style="Card.TFrame")
        account_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(
            account_row, text="账号登录", command=self._open_login_manager,
            style="Account.TButton",
        ).pack(side=tk.LEFT)
        self.cookies_hint = ttk.Label(
            account_row, text="", style="Muted.Card.TLabel",
        )
        self.cookies_hint.pack(side=tk.LEFT)

        # 代理(可选,不提供科学上网,只是把已有代理地址传给 yt-dlp)
        proxy_row = ttk.Frame(settings_card, style="Card.TFrame")
        proxy_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(proxy_row, text="代理（可选）", style="Card.TLabel").pack(side=tk.LEFT)
        self.proxy_var = tk.StringVar(value=self.settings.get("proxy", ""))
        ttk.Entry(proxy_row, textvariable=self.proxy_var, width=31).pack(
            side=tk.LEFT, padx=(10, 8),
        )
        ttk.Label(
            proxy_row, text="直连时留空，例如 http://127.0.0.1:7890",
            style="Muted.Card.TLabel",
        ).pack(side=tk.LEFT)
        self.proxy_var.trace_add("write", self._on_proxy_change)
        for column in range(3):
            settings_card.columnconfigure(column, weight=1)

        # 主操作与进度
        action_card = ttk.Frame(root_frm, style="Card.TFrame", padding=16)
        action_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        btn_frm = ttk.Frame(action_card, style="Card.TFrame")
        btn_frm.pack(fill=tk.X)
        self.start_btn = ttk.Button(
            btn_frm, text="开始下载", command=self._start_download, style="Accent.TButton",
        )
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(
            btn_frm, text="停止", command=self._request_stop, state=tk.DISABLED,
            style="Danger.TButton",
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frm, text="打开下载目录", command=self._open_save_dir).pack(
            side=tk.LEFT, padx=(8, 0),
        )
        self.progress = ttk.Progressbar(
            action_card, mode="determinate", maximum=100,
            style="Blue.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X, pady=(14, 6))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(
            action_card, textvariable=self.status_var, style="Muted.Card.TLabel",
        ).pack(anchor="w")

        # 日志卡片
        log_card = ttk.Frame(root_frm, style="Card.TFrame", padding=16)
        log_card.grid(row=4, column=0, sticky="nsew")
        log_header = ttk.Frame(log_card, style="Card.TFrame")
        log_header.pack(fill=tk.X, pady=(0, 9))
        ttk.Label(log_header, text="运行日志", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_header, text="清空", command=self._clear_log).pack(side=tk.RIGHT)
        log_wrap = ttk.Frame(log_card, style="Card.TFrame")
        log_wrap.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            log_wrap, height=11, wrap="word", state=tk.DISABLED,
            bg=self._colors["log"], fg=self._colors["log_text"],
            insertbackground=self._colors["log_text"], relief="flat",
            padx=12, pady=10, font=("Consolas", 9), selectbackground="#374151",
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(log_wrap, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)

        root_frm.columnconfigure(0, weight=1)
        root_frm.rowconfigure(4, weight=1)

        # 批处理与下载器共享主窗口，通过页签切换；任务仍彼此互斥。
        from media_batch_ui import MediaBatchWindow

        self._media_batch_window = MediaBatchWindow(
            self.media_batch_tab,
            self.save_dir_var.get().strip(),
            busy_check=lambda: self._worker is not None and self._worker.is_alive(),
            embedded=True,
        )

    def _restore_ui_from_settings(self) -> None:
        """把设置文件里的值刷到控件。"""
        self._on_resolution_change()  # 若上次是音频模式,格式下拉切到音频格式
        self._on_image_format_change()
        self._refresh_cookies_hint()

    def _on_close(self) -> None:
        """统一取消登录/下载，等待后台线程收尾后再关闭窗口。"""
        if self._closing:
            return
        self._closing = True
        self.settings.set("save_dir", self.save_dir_var.get().strip())
        self.settings.set("resolution", self.resolution_var.get())
        self.settings.set("format", self.format_var.get())
        self.settings.set("youtube_cookies_file", self.youtube_cookies_file)
        self.settings.set("bilibili_cookies_file", self.bilibili_cookies_file)
        self.settings.set("xiaohongshu_cookies_file", self.xiaohongshu_cookies_file)
        if hasattr(self, "proxy_var"):
            self.settings.set("proxy", self.proxy_var.get().strip())
        if hasattr(self, "audio_bitrate_var"):
            self.settings.set("audio_bitrate", self.audio_bitrate_var.get())
        if hasattr(self, "image_format_var"):
            self.settings.set(
                "image_format",
                IMAGE_FORMAT_OPTIONS.get(self.image_format_var.get(), "original"),
            )
        if hasattr(self, "image_quality_var"):
            self.settings.set(
                "image_quality",
                IMAGE_QUALITY_OPTIONS.get(self.image_quality_var.get(), 95),
            )
        self._cancel_flag.set()
        self._login_cancel.set()
        if self._pot_provider is not None:
            self._pot_provider.stop()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status_var.set("正在关闭并清理后台任务...")
        self._close_login_manager()
        if self._media_batch_window is not None:
            self._media_batch_window.request_close()
        self._finish_close(time.monotonic() + 8)

    def _finish_close(self, deadline: float) -> None:
        active = any(
            thread is not None and thread.is_alive()
            for thread in (self._worker, self._login_worker)
        )
        if self._media_batch_window is not None:
            active = active or self._media_batch_window.is_busy()
        if active:
            if time.monotonic() >= deadline:
                if (
                    not self._forced_login_cleanup
                    and self._login_worker is not None
                    and self._login_worker.is_alive()
                ):
                    self._forced_login_cleanup = True
                    try:
                        import edge_login

                        edge_login.force_cleanup()
                    except Exception:  # noqa: BLE001
                        pass
                self.status_var.set("正在等待当前网络/后处理安全退出...")
            # 不让 daemon 线程随解释器被强杀；Edge/临时 profile 和 ffmpeg
            # 完成清理后才真正销毁主窗口。
            self.root.after(100, self._finish_close, deadline)
            return
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

    def _on_image_format_change(self, _event: object | None = None) -> None:
        image_format = IMAGE_FORMAT_OPTIONS.get(self.image_format_var.get(), "original")
        if image_format == "original":
            self.image_quality_cb.configure(state=tk.DISABLED)
            hint = "保持源站原始编码和画质，不进行二次压缩"
        elif image_format == "png":
            self.image_quality_cb.configure(state=tk.DISABLED)
            hint = "PNG 无损，但照片文件通常会明显变大"
        elif image_format == "jpg":
            self.image_quality_cb.configure(state="readonly")
            hint = "兼容性最好；透明区域会使用白色背景"
        else:
            self.image_quality_cb.configure(state="readonly")
            hint = "体积通常较小，并支持透明背景"
        self.image_format_hint_var.set(hint)
        self.settings.set("image_format", image_format)
        self._on_image_quality_change()

    def _on_image_quality_change(self, _event: object | None = None) -> None:
        self.settings.set(
            "image_quality",
            IMAGE_QUALITY_OPTIONS.get(self.image_quality_var.get(), 95),
        )

    def _refresh_cookies_hint(self) -> None:
        states = []
        has_login = False
        for site, config in LOGIN_SITES.items():
            name = str(config["name"])
            state = self._cookie_state(site)
            has_login = has_login or state == "saved"
            states.append(f"{name} {self._login_status_text(site, short=True)}")
        self.cookies_hint.configure(
            text=" · ".join(states), foreground="#0a7a0a" if has_login else "#666"
        )

    @staticmethod
    def _site_name(site: str | None) -> str:
        config = LOGIN_SITES.get(site or "")
        return str(config["name"]) if config else "其他网站"

    # ---------- 内置登录(Edge+CDP) ----------

    def _open_login_manager(self) -> None:
        """显示应用内账号管理/验证流程；登录网页仍由系统 Edge 承载。"""
        if self._login_dialog and self._login_dialog.winfo_exists():
            self._login_dialog.lift()
            self._login_dialog.focus_force()
            return

        dialog = tk.Toplevel(self.root)
        self._login_dialog = dialog
        dialog.title("账号登录")
        dialog.geometry("560x520")
        dialog.resizable(False, False)
        dialog.configure(bg=self._colors["window"])
        dialog.transient(self.root)
        dialog.protocol("WM_DELETE_WINDOW", self._request_close_login_manager)
        dialog.grab_set()

        outer = ttk.Frame(dialog, padding=24)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text="账号登录", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="选择网站后会打开安全的 Edge 登录窗口，完成后自动保存登录状态。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 16))

        self._login_content = ttk.Frame(outer)
        self._login_content.pack(fill=tk.BOTH, expand=True)
        self._login_footer = ttk.Frame(outer)
        self._login_footer.pack(fill=tk.X, pady=(10, 0))
        self._show_login_overview()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dialog.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")

    @staticmethod
    def _clear_frame(frame: ttk.Frame | None) -> None:
        if frame is not None:
            for child in frame.winfo_children():
                child.destroy()

    def _show_login_overview(self) -> None:
        """账号选择页。"""
        self._clear_frame(self._login_content)
        self._clear_frame(self._login_footer)
        self._login_flow_title = None
        self._login_flow_detail = None
        self._login_flow_icon = None
        self._login_flow_progress = None

        self._login_status_vars = {}
        self._login_buttons = {}
        for site, config in LOGIN_SITES.items():
            title = str(config["name"])
            note = str(config["note"])
            card = ttk.Frame(self._login_content, padding=15, style="LoginCard.TFrame")
            card.pack(fill=tk.X, pady=(0, 10))
            text = ttk.Frame(card, style="LoginCard.TFrame")
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Label(
                text, text=title, style="LoginCard.TLabel",
                font=("Microsoft YaHei UI", 12, "bold"),
            ).pack(anchor="w")
            ttk.Label(text, text=note, style="LoginMuted.TLabel").pack(anchor="w", pady=(3, 0))
            status = tk.StringVar()
            self._login_status_vars[site] = status
            ttk.Label(
                text, textvariable=status, style="LoginCard.TLabel",
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w", pady=(6, 0))
            button = ttk.Button(
                card, width=12, style="Account.TButton",
                command=lambda s=site: self._start_site_login(s),
            )
            button.pack(side=tk.RIGHT, padx=(12, 0))
            self._login_buttons[site] = button

        ttk.Button(self._login_footer, text="关闭", command=self._close_login_manager).pack(
            fill=tk.X, pady=(8, 0),
        )
        self._refresh_login_manager()

    def _show_login_flow(self, site: str) -> None:
        """在同一弹窗内展示登录、读取和验证状态。"""
        self._clear_frame(self._login_content)
        self._clear_frame(self._login_footer)
        self._login_status_vars = {}
        self._login_buttons = {}
        site_name = self._site_name(site)

        panel = ttk.Frame(self._login_content, padding=(20, 18), style="Card.TFrame")
        panel.pack(fill=tk.BOTH, expand=True)
        self._login_flow_icon = ttk.Label(
            panel, text="…", style="Card.TLabel",
            font=("Microsoft YaHei UI", 38, "bold"), foreground=self._colors["accent"],
        )
        self._login_flow_icon.pack(pady=(0, 8))
        self._login_flow_title = tk.StringVar(value=f"正在打开 {site_name} 登录窗口")
        ttk.Label(
            panel, textvariable=self._login_flow_title, style="Card.TLabel",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack()
        self._login_flow_detail = tk.StringVar(
            value="请稍候。登录窗口打开后，请在 Edge 中完成登录。"
        )
        ttk.Label(
            panel, textvariable=self._login_flow_detail, style="Muted.Card.TLabel",
            justify=tk.CENTER, wraplength=440,
        ).pack(pady=(9, 15))
        self._login_flow_progress = ttk.Progressbar(
            panel, mode="indeterminate", length=330,
            style="Blue.Horizontal.TProgressbar",
        )
        self._login_flow_progress.pack()
        self._login_flow_progress.start(12)
        ttk.Button(
            self._login_footer, text="取消登录", command=self._cancel_login,
            style="Danger.TButton",
        ).pack(fill=tk.X)

    def _set_login_flow_state(self, stage: str, site: str, detail: str = "") -> None:
        """把后台登录阶段映射成普通用户可理解的界面提示。"""
        if not self._login_flow_title or not self._login_flow_detail:
            return
        site_name = self._site_name(site)
        states = {
            "waiting": (
                "↗", f"请在 Edge 中登录 {site_name}",
                "登录完成后请停留片刻，程序会自动识别。登录窗口请勿手动关闭。",
            ),
            "reading": (
                "…", "正在读取登录状态",
                "已检测到登录，正在安全读取 Cookies。请勿关闭登录窗口或本程序。",
            ),
            "verifying": (
                "…", "正在验证登录状态",
                "正在确认凭据可用性并安全保存，通常只需要几秒钟。",
            ),
            "saving": (
                "…", "正在保存登录状态",
                "验证已经通过，正在完成最后的安全写入。",
            ),
        }
        icon, title, default_detail = states.get(stage, ("…", "正在处理", "请稍候…"))
        if self._login_flow_icon:
            self._login_flow_icon.configure(text=icon, foreground=self._colors["accent"])
        self._login_flow_title.set(title)
        self._login_flow_detail.set(detail or default_detail)

    def _show_login_result(self, site: str, success: bool, detail: str = "") -> bool:
        """成功/失败留在应用内呈现；弹窗不存在时由调用方使用系统提示。"""
        if not self._login_dialog or not self._login_dialog.winfo_exists():
            return False
        if not self._login_flow_title or not self._login_flow_detail:
            self._show_login_flow(site)
        if self._login_flow_progress:
            self._login_flow_progress.stop()
            self._login_flow_progress.pack_forget()
        site_name = self._site_name(site)
        if self._login_flow_icon:
            self._login_flow_icon.configure(
                text="✓" if success else "!",
                foreground="#16A34A" if success else self._colors["danger"],
            )
        self._login_flow_title.set(
            f"{site_name} 已通过验证" if success else f"{site_name} 登录未完成"
        )
        self._login_flow_detail.set(
            detail or (
                "登录状态已安全保存。以后粘贴该网站链接时会自动使用。"
                if success else "未能保存新的登录状态，原有凭据没有被覆盖。"
            )
        )
        self._clear_frame(self._login_footer)
        ttk.Button(
            self._login_footer,
            text="完成" if success else "返回账号列表",
            command=self._close_login_manager if success else self._show_login_overview,
            style="Accent.TButton" if success else "TButton",
        ).pack(fill=tk.X)
        return True

    def _request_close_login_manager(self) -> None:
        if self._login_running:
            messagebox.showinfo(
                "登录正在进行",
                "正在读取或验证登录状态，请先等待完成，或点击“取消登录”。",
                parent=self._login_dialog,
            )
            return
        self._close_login_manager()

    def _cancel_login(self) -> None:
        if not self._login_running:
            self._show_login_overview()
            return
        self._login_cancel.set()
        if self._login_flow_title:
            self._login_flow_title.set("正在取消登录")
        if self._login_flow_detail:
            self._login_flow_detail.set("正在关闭登录窗口并清理临时数据，请稍候…")

    def _close_login_manager(self) -> None:
        if self._login_flow_progress:
            self._login_flow_progress.stop()
        if self._login_dialog and self._login_dialog.winfo_exists():
            self._login_dialog.grab_release()
            self._login_dialog.destroy()
        self._login_dialog = None
        self._login_content = None
        self._login_footer = None
        self._login_flow_title = None
        self._login_flow_detail = None
        self._login_flow_icon = None
        self._login_flow_progress = None
        self._login_status_vars = {}
        self._login_buttons = {}

    def _refresh_login_manager(self) -> None:
        for site in LOGIN_SITES:
            state = self._cookie_state(site)
            if site in self._login_status_vars:
                self._login_status_vars[site].set(self._login_status_text(site))
            if site in self._login_buttons:
                self._login_buttons[site].configure(
                    text="登录" if state == "missing" else "重新登录",
                    state=tk.DISABLED if self._login_running else tk.NORMAL,
                )

    def _start_site_login(self, site: str) -> None:
        if self._login_running:
            return
        if not self._has_edge():
            messagebox.showinfo(
                "缺少 Edge",
                "未找到 Microsoft Edge/Chrome。\n内置登录需要系统浏览器支持。",
            )
            return

        config = LOGIN_SITES.get(site)
        if not config:
            return
        site_name = str(config["name"])
        out_path = Path.home() / str(config["cookie_file"])
        self._login_running = site
        self._login_cancel.clear()
        self._show_login_flow(site)
        self._log(f"[内置登录] 正在打开 {site_name} 登录窗口...")

        def worker() -> None:
            try:
                import edge_login

                def progress(msg: str) -> None:
                    self._msg_queue.put(("login_log", msg))
                    stage = self._login_stage_from_message(msg)
                    if stage:
                        self._msg_queue.put(("login_stage", (stage, site)))

                path = edge_login.run_login(
                    out_path=out_path,
                    progress=progress,
                    verify=bool(config["verify"]),
                    site=site,
                    cancel_event=self._login_cancel,
                )
                self._msg_queue.put(("login_ok", (site, str(path))))
            except Exception as exc:  # noqa: BLE001
                self._msg_queue.put(("login_fail", (site, str(exc))))

        self._login_worker = threading.Thread(target=worker, daemon=True)
        self._login_worker.start()

    @staticmethod
    def _login_stage_from_message(message: str) -> str | None:
        """从底层进度文字中提取稳定的 UI 阶段，不把技术日志直接展示给用户。"""
        if "登录窗口已打开" in message:
            return "waiting"
        if "[3/4]" in message or "正在验证" in message:
            return "reading"
        if "[4/4]" in message or "验证解析" in message:
            return "verifying"
        if "[保存]" in message or "安全替换" in message:
            return "saving"
        return None

    @staticmethod
    def _has_edge() -> bool:
        """本机是否有 Edge 或 Chrome(用于「内置登录」前置检查)。"""
        candidates = [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
        return any(p.exists() for p in candidates)

    def _on_login_ok(self, site: str, path: str) -> None:
        self._login_running = None
        self._login_worker = None
        site_name = self._site_name(site)
        setattr(self, f"{site}_cookies_file", path)
        self.settings.set(f"{site}_cookies_file", path)
        self.settings.set(f"{site}_cookies_valid", True)
        self.settings.set(f"{site}_login_at", int(time.time()))
        self._refresh_cookies_hint()
        self._refresh_login_manager()
        self._log(f"[内置登录] {site_name} 登录成功")
        if not self._show_login_result(site, True):
            messagebox.showinfo(
                "登录成功",
                f"{site_name} 登录状态已保存。\n\n"
                "之后粘贴该站点网址时会自动使用，无需手动选择 Cookies。",
                parent=self.root,
            )

    def _on_login_fail(self, site: str, err: str) -> None:
        self._login_running = None
        self._login_worker = None
        self._refresh_login_manager()
        self._log(f"[内置登录] 失败: {err}")
        if not self._show_login_result(site, False, err):
            messagebox.showerror("登录失败", err, parent=self.root)

    def _cookie_file_for_site(self, site: str) -> str | None:
        if self._cookie_state(site) != "saved":
            return None
        return getattr(self, f"{site}_cookies_file", None)

    def _cookie_state(self, site: str) -> str:
        path = getattr(self, f"{site}_cookies_file", None)
        if not path or not Path(path).is_file():
            return "missing"
        if self.settings.get(f"{site}_cookies_valid") is False:
            return "invalid"
        return "saved"

    def _login_status_text(self, site: str, short: bool = False) -> str:
        """显示登录状态；7 天是主动更新提醒，不代表固定有效期。"""
        state = self._cookie_state(site)
        if state == "missing":
            return "未登录" if short else "○ 未登录"
        if state == "invalid":
            return "登录已失效" if short else "● 登录已失效，请重新登录"

        login_at = self.settings.get(f"{site}_login_at")
        if not isinstance(login_at, (int, float)) or login_at <= 0:
            return "已登录（建议更新）" if short else "● 已登录；时间未知，建议重新登录一次"

        age_days = max(0, int((time.time() - login_at) // 86400))
        if age_days >= 7:
            return (
                f"已登录（{age_days}天前，建议更新）" if short
                else f"● 已登录 {age_days} 天；建议现在更新登录"
            )
        age_text = "今天" if age_days == 0 else f"{age_days}天前"
        return f"已登录（{age_text}）" if short else f"● 已登录 · {age_text}更新"

    def _on_auth_invalid(self, site: str) -> None:
        site_name = self._site_name(site)
        self.settings.set(f"{site}_cookies_valid", False)
        self._refresh_cookies_hint()
        self._refresh_login_manager()
        self._log(f"[登录] {site_name} 登录已失效，已停用；请通过“内置登录...”重新登录")

    @staticmethod
    def _site_for_url(url: str) -> str | None:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        if host == "youtu.be" or host.endswith(".youtube.com") or host == "youtube.com":
            return "youtube"
        if host == "b23.tv" or host.endswith(".bilibili.com") or host == "bilibili.com":
            return "bilibili"
        if host == "xhslink.com" or host.endswith(".xhslink.com"):
            return "xiaohongshu"
        if host == "xiaohongshu.com" or host.endswith(".xiaohongshu.com"):
            return "xiaohongshu"
        return None

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
        # 源码版由 yt-dlp 插件机制自动注册;打包版(PyInstaller 冻结环境)里 yt-dlp
        # 的插件自动发现失效,必须显式 import 各 provider 模块以触发注册。
        try:
            import yt_dlp_plugins.extractor.getpot_bgutil  # noqa: F401   # 基类
            import yt_dlp_plugins.extractor.getpot_bgutil_http  # noqa: F401
            import yt_dlp_plugins.extractor.getpot_bgutil_script  # noqa: F401
            self._log("[信息] PO Token 插件已就位 (bgutil-ytdlp-pot-provider)")
        except Exception as exc:
            # 失败时给出一行可定位的异常信息(不打印完整 traceback)
            self._log(
                "[警告] PO Token 插件加载失败:"
                f"{type(exc).__name__}: {exc}"
            )
        try:
            from pot_provider import provider_is_installed

            if provider_is_installed():
                self._log("[信息] PO Token 本地生成服务文件已就位（下载 YouTube 时自动启动）")
            else:
                self._log("[警告] PO Token 插件已安装，但本地生成服务缺失；4K/高码率可能不可用")
        except Exception as exc:
            self._log(f"[警告] 无法检查 PO Token 本地生成服务: {exc}")

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

    def _open_media_batch(self) -> None:
        """切换到主窗口中的媒体批处理页签。"""
        self.main_notebook.select(self.media_batch_tab)

    def _on_media_batch_closed(self) -> None:
        self._media_batch_window = None

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
                elif kind == "login_log":
                    self._log(payload)
                elif kind == "login_stage":
                    self._set_login_flow_state(*payload)
                elif kind == "login_ok":
                    self._on_login_ok(*payload)
                elif kind == "login_fail":
                    self._on_login_fail(*payload)
                elif kind == "auth_invalid":
                    self._on_auth_invalid(payload)
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(120, self._poll_queue)

    # ---------- 下载主流程 ----------

    @staticmethod
    def _extract_task_urls(text: str) -> list[str]:
        """提取任务网址；允许直接粘贴小红书 App 的整段分享文案。"""
        try:
            from xhs_image_downloader import extract_xhs_url
        except ImportError:
            extract_xhs_url = lambda _text: None  # type: ignore[assignment]
        urls: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            xhs_url = extract_xhs_url(stripped)
            if xhs_url:
                urls.append(xhs_url)
            elif stripped.startswith(("http://", "https://")):
                urls.append(stripped)
        return list(dict.fromkeys(urls))

    def _start_download(self) -> None:
        if yt_dlp is None:
            messagebox.showerror("缺少依赖", "未检测到 yt-dlp,请先执行: pip install -U yt-dlp")
            return
        if self._media_batch_window is not None and self._media_batch_window.is_busy():
            messagebox.showwarning(
                "批处理正在运行", "请先取消或等待媒体批处理完成，再开始下载。"
            )
            return

        urls = self._extract_task_urls(self.url_text.get("1.0", tk.END))
        if not urls:
            messagebox.showwarning("提示", "请先输入至少一个视频或图文网址")
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
        self._reported_formats.clear()
        self._last_ydl_error = None
        self.progress["value"] = 0
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_var.set("准备下载...")

        opts = self._build_ydl_opts(save_dir)
        # Tk 变量只能在主线程读取；工作线程使用这里冻结下来的普通值。
        self._download_image_format = IMAGE_FORMAT_OPTIONS.get(
            self.image_format_var.get(), "original"
        )
        self._download_image_quality = IMAGE_QUALITY_OPTIONS.get(
            self.image_quality_var.get(), 95
        )

        self._log(f"[开始] 共 {len(urls)} 个任务,输出到 {save_dir}")
        self._log("[Cookies] 将按网址自动选择 YouTube / Bilibili / 小红书登录状态")
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
            "logger": _YdlLogger(
                self._emit, self._cancel_flag, self._record_ydl_error,
            ),
            "noprogress": True,   # 关掉 stderr 进度,统一走 hook
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": False,
            "retries": 5,
            "fragment_retries": 20,
            # 默认值会跳过坏分片并仍生成成品，造成画面停顿/音频跳跃。
            # 这里改成失败即中止，宁可明确重试，也不交付残缺视频。
            "skip_unavailable_fragments": False,
            "concurrent_fragment_downloads": 2,
            "retry_sleep_functions": {
                "fragment": lambda attempt: min(2 ** max(attempt - 1, 0), 20),
            },
            "socket_timeout": 30,
            "postprocessor_hooks": [self._postprocessor_hook],
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

    def _opts_for_url(self, base_opts: dict[str, Any], url: str) -> dict[str, Any]:
        """按 URL 自动装配对应站点的登录凭据。"""
        opts = dict(base_opts)
        site = self._site_for_url(url)
        if site and (cookie_file := self._cookie_file_for_site(site)):
            opts["cookiefile"] = cookie_file
        # YouTube 的 watch 链接经常附带 list=RD.../start_radio=1。它表达的是
        # “当前视频来自 Mix”，不是用户明确要求下载整个播放列表。
        # 仅对明确指向单个视频的 URL 禁用播放列表；/playlist 链接仍正常批量下载。
        if site == "youtube" and self._youtube_url_points_to_single_video(url):
            opts["noplaylist"] = True
        return opts

    @staticmethod
    def _youtube_url_points_to_single_video(url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            path = parsed.path.rstrip("/")
            if host == "youtu.be":
                return bool(path.strip("/"))
            if host == "youtube.com" or host.endswith(".youtube.com"):
                if path == "/watch":
                    return bool(parse_qs(parsed.query).get("v"))
                return any(path.startswith(prefix) for prefix in ("/shorts/", "/live/", "/embed/"))
        except ValueError:
            pass
        return False

    def _progress_hook(self, d: dict[str, Any]) -> None:
        if self._cancel_flag.is_set():
            raise _UserCancelled()

        status = d.get("status")
        if status == "downloading":
            self._report_selected_format(d)
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

    def _report_selected_format(self, d: dict[str, Any]) -> None:
        """每个实际下载流仅记录一次分辨率/编码，避免“最佳”含义不透明。"""
        info = d.get("info_dict") or {}
        filename = str(d.get("filename") or info.get("filename") or "")
        format_id = str(info.get("format_id") or "未知")
        key = (filename, format_id)
        if key in self._reported_formats:
            return
        self._reported_formats.add(key)
        width, height = info.get("width"), info.get("height")
        resolution = (
            f"{width}x{height}" if width and height
            else str(info.get("resolution") or "仅音频/未知")
        )
        fps = info.get("fps")
        codec = "/".join(
            value for value in (info.get("vcodec"), info.get("acodec"))
            if value and value != "none"
        ) or "未知"
        fps_text = f" · {fps}fps" if fps else ""
        self._emit(
            "log",
            f"[格式] id={format_id} · {resolution}{fps_text} · 编码 {codec}",
        )

    def _postprocessor_hook(self, _d: dict[str, Any]) -> None:
        if self._cancel_flag.is_set():
            raise _UserCancelled()

    @staticmethod
    def _fragment_snapshot(save_dir: Path) -> dict[Path, tuple[int, int]]:
        """记录 yt-dlp 临时文件，供下载结束后识别新增或变化的残片。"""
        result: dict[Path, tuple[int, int]] = {}
        try:
            paths: set[Path] = set()
            for pattern in ("*.part", "*.ytdl"):
                paths.update(save_dir.rglob(pattern))
            for path in paths:
                if path.is_file():
                    try:
                        stat = path.stat()
                        result[path] = (stat.st_size, stat.st_mtime_ns)
                    except OSError:
                        pass
        except OSError:
            pass
        return result

    def _run_download(self, urls: list[str], opts: dict[str, Any]) -> None:
        current_site: str | None = None
        used_cookie = False
        try:
            for url in urls:
                if self._cancel_flag.is_set():
                    raise _UserCancelled()
                current_site = self._site_for_url(url)
                url_opts = self._opts_for_url(opts, url)
                if current_site == "youtube" and self._pot_provider is not None:
                    if self._pot_provider.ensure_started(
                        lambda msg: self._emit("log", msg), self._cancel_flag,
                    ):
                        self._pot_provider.apply_to_opts(url_opts)
                    else:
                        self._emit(
                            "log",
                            "[PO Token] 未启用生成服务，YouTube 可能降级到 1080p；"
                            "日志中的实际格式以 [格式] 为准",
                        )
                used_cookie = "cookiefile" in url_opts
                site_name = self._site_name(current_site)
                if used_cookie:
                    self._emit("log", f"[Cookies] {site_name}:使用已保存的内置登录")
                else:
                    self._emit("log", f"[Cookies] {site_name}:未使用登录状态")
                save_dir = Path(os.path.dirname(str(url_opts["outtmpl"])))
                if current_site == "xiaohongshu":
                    try:
                        from xhs_image_downloader import (
                            XhsCancelled, XhsImageDownloader, XhsVideoPost,
                        )

                        def image_progress(current: int, total: int, name: str) -> None:
                            self._emit("progress", current / total * 100 if total else 0)
                            self._emit("status", f"小红书图片 {current}/{total} · {name}")

                        downloader = XhsImageDownloader(
                            cookie_file=(
                                Path(str(url_opts["cookiefile"]))
                                if "cookiefile" in url_opts else None
                            ),
                            proxy=str(url_opts.get("proxy") or ""),
                            progress=lambda msg: self._emit("log", msg),
                            cancel_event=self._cancel_flag,
                            item_progress=image_progress,
                            image_format=getattr(
                                self, "_download_image_format", "original"
                            ),
                            image_quality=getattr(
                                self, "_download_image_quality", 95
                            ),
                        )
                        format_name = getattr(self, "_download_image_format", "original")
                        quality = getattr(self, "_download_image_quality", 95)
                        self._emit(
                            "log",
                            "[图片设置] "
                            + (
                                "保留原格式和原始编码"
                                if format_name == "original"
                                else f"统一转换为 {format_name.upper()}"
                                + (f"，画质 {quality}" if format_name in {"jpg", "webp"} else "（无损）")
                            ),
                        )
                        downloader.download(url, save_dir)
                        continue
                    except XhsVideoPost:
                        self._emit("log", "[识别] 小红书视频帖子，改用视频下载流程")
                    except XhsCancelled as exc:
                        raise _UserCancelled() from exc
                fragments_before = self._fragment_snapshot(save_dir)
                with yt_dlp.YoutubeDL(url_opts) as ydl:  # type: ignore[union-attr]
                    ydl.download([url])
                fragments_after = self._fragment_snapshot(save_dir)
                changed_fragments = [
                    path for path, signature in fragments_after.items()
                    if fragments_before.get(path) != signature
                ]
                if changed_fragments:
                    names = "、".join(path.name for path in changed_fragments[:5])
                    raise _IncompleteDownload(
                        "检测到未完成的视频分片，成品可能卡顿，已将任务判定为失败。"
                        f"请重试。残留文件：{names}"
                    )
            self._emit("done", ("success", ""))
        except _UserCancelled:
            self._emit("done", ("cancelled", ""))
        except Exception as exc:  # noqa: BLE001
            if self._cancel_flag.is_set():
                self._emit("done", ("cancelled", ""))
                return
            error = str(exc)
            if (
                current_site == "youtube"
                and used_cookie
                and (
                    "cookies are no longer valid" in error.lower()
                    or "sign in to confirm" in error.lower()
                )
            ):
                self._emit("auth_invalid", "youtube")
            self._emit("done", ("error", str(exc)))
        finally:
            if self._pot_provider is not None:
                self._pot_provider.stop()

    def _on_finish(self, result: str, extra: str) -> None:
        self._worker = None
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
            # yt-dlp 已通过 logger 输出过的错误不再重复；若异常来自 GUI 自身，
            # 这里仍会显示一次。
            last = re.sub(r"\x1b\[[0-9;]*m", "", self._last_ydl_error or "").strip()
            current = re.sub(r"\x1b\[[0-9;]*m", "", str(extra or "")).strip()
            already_shown = bool(last and current and (last in current or current in last))
            if not already_shown:
                self._log(f"[错误] {self._friendly_download_error(extra)}")

    def _record_ydl_error(self, error: str) -> None:
        self._last_ydl_error = str(error or "")
        self._emit("log", f"[错误] {self._friendly_download_error(error)}")

    @staticmethod
    def _friendly_download_error(error: str) -> str:
        """把常见 yt-dlp 技术错误转成普通用户能行动的提示。"""
        clean = re.sub(r"\x1b\[[0-9;]*m", "", str(error or "")).strip()
        lowered = clean.lower()
        if "unsupported url" in lowered:
            return (
                "当前版本的 yt-dlp 不支持这个网址。这通常不是登录或代理问题；"
                "请确认网址是否为公开视频页面，或等待 yt-dlp 增加该网站支持。"
            )
        if "sign in to confirm" in lowered or "cookies are no longer valid" in lowered:
            return "YouTube 登录状态已失效，请点击“内置登录”重新登录后再试。"
        if "noneType".lower() in lowered and "subscriptable" in lowered:
            return "网站返回的数据不完整。请确认粘贴的是单个视频链接并重试。"
        return clean or "下载器没有返回具体原因，请查看上方日志。"


class _UserCancelled(Exception):
    """由 progress_hook 抛出,冒泡到主循环表示用户取消。"""


class _IncompleteDownload(RuntimeError):
    """下载器留下不完整分片，禁止将任务报告为成功。"""


class _YdlLogger:
    """把 yt-dlp 的内部输出转发到 GUI 日志窗口。"""

    def __init__(
        self,
        emit: Callable[[str, Any], None],
        cancel_flag: threading.Event | None = None,
        error_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._emit = emit
        self._cancel_flag = cancel_flag
        self._error_callback = error_callback

    def _check_cancel(self) -> None:
        if self._cancel_flag is not None and self._cancel_flag.is_set():
            raise _UserCancelled()

    def debug(self, msg: str) -> None:
        self._check_cancel()
        if not msg or msg.startswith("[debug]"):
            return
        self._emit("log", msg)

    def info(self, msg: str) -> None:
        self._check_cancel()
        self._emit("log", msg)

    def warning(self, msg: str) -> None:
        self._check_cancel()
        self._emit("log", f"[警告] {msg}")

    def error(self, msg: str) -> None:
        self._check_cancel()
        if self._error_callback is not None:
            self._error_callback(msg)
        else:
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


def _prepend_tools_to_path(tools_dir: Path) -> bool:
    """把便携工具目录加入 PATH；目录不存在时返回 False。"""
    if not tools_dir.is_dir():
        return False

    tools = str(tools_dir.resolve())
    current = os.environ.get("PATH", "")
    entries = [item for item in current.split(os.pathsep) if item]
    if tools.casefold() not in {item.casefold() for item in entries}:
        os.environ["PATH"] = tools + (os.pathsep + current if current else "")
    return True


def _setup_env() -> None:
    """打包版使用同目录 tools；源码版由 bootstrap 统一查找和补全。"""
    if _is_bundled():
        _prepend_tools_to_path(Path(sys.executable).parent / "tools")
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


def _self_test() -> int:
    """供 build.py 验证冻结产物的关键模块和便携工具，不启动 GUI。"""
    _setup_env()
    try:
        import websocket  # noqa: F401
        import yt_dlp_ejs  # noqa: F401
        import yt_dlp_plugins.extractor.getpot_bgutil  # noqa: F401
        import yt_dlp_plugins.extractor.getpot_bgutil_http  # noqa: F401
        import yt_dlp_plugins.extractor.getpot_bgutil_script  # noqa: F401
        import PIL  # noqa: F401
        import pillow_heif  # noqa: F401
        import dhash  # noqa: F401
        import media_batch  # noqa: F401
        import media_batch_ui  # noqa: F401
        from io import BytesIO
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()

        image = Image.new("RGB", (8, 8), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        with Image.open(buffer) as decoded:
            decoded.load()
            dhash.dhash_int(decoded)
    except Exception:
        return 2
    if yt_dlp is None:
        return 3
    if not all(shutil.which(name) for name in ("node", "ffmpeg", "ffprobe")):
        return 4
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    main()
