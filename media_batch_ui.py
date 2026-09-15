"""媒体批处理窗口。"""

from __future__ import annotations

import queue
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from media_batch import (
    MediaRecord,
    OperationCancelled,
    ScanOptions,
    batch_crop_images,
    batch_rename_images,
    move_suspicious,
    normalize_rename_template,
    render_rename,
    scan_media,
)


class _ModernCheck(ttk.Frame):
    """无额外依赖的现代复选框；勾选时只改变方框，不染色文字背景。"""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent, style="Card.TFrame", takefocus=True)
        self.variable = variable
        self.command = command
        self.hovered = False
        self.box = tk.Canvas(
            self, width=22, height=22, bg="#FFFFFF", bd=0,
            highlightthickness=0, cursor="hand2",
        )
        self.box.pack(side=tk.LEFT)
        self.label = ttk.Label(self, text=text, style="Card.TLabel", cursor="hand2")
        self.label.pack(side=tk.LEFT, padx=(5, 0))
        for widget in (self, self.box, self.label):
            widget.bind("<Button-1>", self._toggle, add="+")
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
        self.bind("<space>", self._toggle, add="+")
        self.variable.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def _toggle(self, _event: tk.Event | None = None) -> str:
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()
        return "break"

    def _enter(self, _event: tk.Event | None = None) -> None:
        self.hovered = True
        self._draw()

    def _leave(self, _event: tk.Event | None = None) -> None:
        self.hovered = False
        self._draw()

    def _draw(self) -> None:
        self.box.delete("all")
        selected = self.variable.get()
        outline = "#2563EB" if selected or self.hovered else "#94A3B8"
        fill = "#2563EB" if selected else "#FFFFFF"
        self.box.create_rectangle(2, 2, 20, 20, outline=outline, fill=fill, width=2)
        if selected:
            self.box.create_line(
                6, 11, 10, 15, 17, 7, fill="#FFFFFF", width=2.4,
                capstyle=tk.ROUND, joinstyle=tk.ROUND,
            )


class _ModernSlider(tk.Canvas):
    """紧凑的离散滑块，样式与主界面蓝色强调色一致。"""

    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.IntVar,
        command: Callable[[str], None] | None = None,
        length: int = 170,
    ) -> None:
        self._length = length
        self._padding = 10
        self._enabled = True
        self.variable = variable
        self.command = command
        super().__init__(
            parent, width=length, height=28, bg="#FFFFFF", bd=0,
            highlightthickness=0, cursor="hand2",
        )
        self.bind("<Button-1>", self._change, add="+")
        self.bind("<B1-Motion>", self._change, add="+")
        self.variable.trace_add("write", lambda *_args: self._draw())
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _change(self, event: tk.Event) -> None:
        if not self._enabled:
            return
        usable = self._length - self._padding * 2
        value = round((event.x - self._padding) / max(usable, 1) * 10)
        value = max(0, min(10, value))
        if value != self.variable.get():
            self.variable.set(value)
            if self.command is not None:
                self.command(str(value))

    def _draw(self) -> None:
        self.delete("all")
        value = max(0, min(10, self.variable.get()))
        left, right, y = self._padding, self._length - self._padding, 14
        active = "#2563EB" if self._enabled else "#AAB8CC"
        track = "#CBD5E1" if self._enabled else "#E2E8F0"
        knob_x = left + (right - left) * value / 10
        self.create_line(left, y, right, y, fill=track, width=5, capstyle=tk.ROUND)
        self.create_line(left, y, knob_x, y, fill=active, width=5, capstyle=tk.ROUND)
        self.create_oval(
            knob_x - 7, y - 7, knob_x + 7, y + 7,
            fill="#FFFFFF", outline=active, width=3,
        )


class MediaBatchWindow:
    """扫描和批处理图片/视频；所有耗时操作均在后台线程运行。"""

    def __init__(
        self,
        parent: tk.Misc,
        initial_dir: str = "",
        busy_check: Callable[[], bool] | None = None,
        on_closed: Callable[[], None] | None = None,
        embedded: bool = False,
    ) -> None:
        self.parent = parent
        self._embedded = embedded
        if embedded:
            self.window = parent
        else:
            self.window = tk.Toplevel(parent)
            self.window.title("媒体批处理")
            self.window.geometry("1120x760")
            self.window.minsize(930, 650)
            self.window.transient(parent)
            self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Destroy>", self._on_destroy, add="+")

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._busy_check = busy_check or (lambda: False)
        self._on_closed_callback = on_closed
        self._close_requested = False
        self._closed_notified = False
        self.records: list[MediaRecord] = []
        self._last_options: ScanOptions | None = None

        self.source_var = tk.StringVar(value=initial_dir)
        default_suspicious = str(Path(initial_dir or Path.home()) / "疑似问题文件")
        self.suspicious_var = tk.StringVar(value=default_suspicious)
        self.recursive_var = tk.BooleanVar(value=True)
        self.image_resolution_var = tk.BooleanVar(value=True)
        self.duplicates_var = tk.BooleanVar(value=True)
        self.video_resolution_var = tk.BooleanVar(value=True)
        self.video_bitrate_var = tk.BooleanVar(value=True)
        self.black_bars_var = tk.BooleanVar(value=False)
        self.include_review_var = tk.BooleanVar(value=False)
        self.image_width_var = tk.StringVar(value="0")
        self.image_height_var = tk.StringVar(value="0")
        self.video_width_var = tk.StringVar(value="1280")
        self.video_height_var = tk.StringVar(value="720")
        self.distance_var = tk.IntVar(value=4)
        self.distance_label_var = tk.StringVar(value="推荐近似（差异 ≤ 4）")
        self.rename_var = tk.StringVar(value="图片")
        self.crop_ratio_var = tk.StringVar(value="1:1")
        self.crop_output_var = tk.StringVar(
            value=str(Path(initial_dir or Path.home()) / "裁剪结果")
        )
        self.status_var = tk.StringVar(value="请选择目录后开始扫描")
        self.summary_var = tk.StringVar(value="尚未扫描")
        self.scan_scope_var = tk.StringVar(value="扫描范围：图片 + 视频")

        self._build_ui()
        self.window.after(100, self._poll_queue)

    def _build_ui(self) -> None:
        root = ttk.Frame(self.window, padding=18)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="媒体批处理", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            root,
            text="筛选低分辨率、画质/流畅度风险、重复文件、黑边并安全归档",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(1, 12))

        source_card = ttk.Frame(root, style="Card.TFrame", padding=14)
        source_card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(source_card, text="扫描目录", style="Card.TLabel").grid(row=0, column=0)
        ttk.Entry(source_card, textvariable=self.source_var).grid(
            row=0, column=1, sticky="ew", padx=10
        )
        ttk.Button(source_card, text="选择", command=self._choose_source).grid(row=0, column=2)
        self._toggle_option(
            source_card, "包含子文件夹", self.recursive_var, 0, 3,
            padx=(12, 0),
        )
        source_card.columnconfigure(1, weight=1)

        options = ttk.Frame(root, style="Card.TFrame", padding=14)
        options.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(options, text="检测条件", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 9)
        )
        ttk.Label(
            options, textvariable=self.scan_scope_var, style="Muted.Card.TLabel",
        ).grid(row=0, column=1, columnspan=9, sticky="e", pady=(0, 9))
        ttk.Label(options, text="图片", style="Section.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8)
        )
        self._toggle_option(
            options, "像素筛选", self.image_resolution_var, 1, 1,
            command=self._refresh_detection_controls,
        )
        ttk.Label(options, text="最低", style="Card.TLabel").grid(row=1, column=2)
        self.image_width_entry = ttk.Entry(options, textvariable=self.image_width_var, width=7)
        self.image_width_entry.grid(row=1, column=3, padx=(5, 3))
        ttk.Label(options, text="×", style="Card.TLabel").grid(row=1, column=4)
        self.image_height_entry = ttk.Entry(options, textvariable=self.image_height_var, width=7)
        self.image_height_entry.grid(row=1, column=5, padx=(3, 16))
        ttk.Label(options, text="通用", style="Section.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=(10, 0)
        )
        self._toggle_option(
            options, "重复检测（图片 + 视频）", self.duplicates_var, 3, 1,
            command=self._refresh_detection_controls, pady=(10, 0),
        )
        duplicate_scale = ttk.Frame(options, style="Card.TFrame")
        duplicate_scale.grid(
            row=3, column=2, columnspan=8, padx=(8, 0), pady=(10, 0), sticky="w"
        )
        ttk.Label(duplicate_scale, text="完全一致", style="Muted.Card.TLabel").pack(
            side=tk.LEFT
        )
        self.distance_scale = _ModernSlider(
            duplicate_scale, self.distance_var,
            command=self._refresh_distance_label, length=160,
        )
        self.distance_scale.pack(side=tk.LEFT, padx=7)
        ttk.Label(duplicate_scale, text="宽松近似", style="Muted.Card.TLabel").pack(
            side=tk.LEFT
        )
        ttk.Label(
            duplicate_scale, textvariable=self.distance_label_var,
            style="Muted.Card.TLabel",
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(options, text="视频", style="Section.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0)
        )
        self._toggle_option(
            options, "像素筛选", self.video_resolution_var, 2, 1,
            command=self._refresh_detection_controls, pady=(10, 0),
        )
        ttk.Label(options, text="最低", style="Card.TLabel").grid(
            row=2, column=2, pady=(10, 0)
        )
        self.video_width_entry = ttk.Entry(options, textvariable=self.video_width_var, width=7)
        self.video_width_entry.grid(row=2, column=3, padx=(5, 3), pady=(10, 0))
        ttk.Label(options, text="×", style="Card.TLabel").grid(row=2, column=4, pady=(10, 0))
        self.video_height_entry = ttk.Entry(options, textvariable=self.video_height_var, width=7)
        self.video_height_entry.grid(row=2, column=5, padx=(3, 16), pady=(10, 0))
        self._toggle_option(
            options, "画质/流畅度风险", self.video_bitrate_var, 2, 6,
            command=self._refresh_detection_controls, pady=(10, 0),
        )
        ttk.Label(
            options, text="自动结合码率、分辨率、帧率和编码格式",
            style="Muted.Card.TLabel",
        ).grid(row=2, column=7, columnspan=2, sticky="w", padx=(6, 8), pady=(10, 0))
        self._toggle_option(
            options, "黑边检测（较慢）", self.black_bars_var, 2, 9,
            command=self._refresh_detection_controls, pady=(10, 0),
        )
        action = ttk.Frame(options, style="Card.TFrame")
        action.grid(row=0, column=10, rowspan=4, sticky="e", padx=(16, 0))
        self.scan_button = ttk.Button(
            action, text="开始扫描", style="Accent.TButton", command=self._start_scan
        )
        self.scan_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(
            action, text="取消", style="Danger.TButton", state=tk.DISABLED,
            command=self._request_cancel,
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))

        result_card = ttk.Frame(root, style="Card.TFrame", padding=14)
        result_card.grid(row=4, column=0, sticky="nsew", pady=(0, 10))
        header = ttk.Frame(result_card, style="Card.TFrame")
        header.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(header, text="检测结果", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.summary_var, style="Muted.Card.TLabel").pack(
            side=tk.RIGHT
        )
        # result_card 本身统一使用 pack；表格的 grid 放到独立子容器中，
        # 避免 Tkinter 报 cannot use geometry manager grid ... pack already used。
        table_wrap = ttk.Frame(result_card, style="Card.TFrame")
        table_wrap.pack(fill=tk.BOTH, expand=True)
        columns = ("kind", "resolution", "bitrate", "duplicate", "crop", "issues")
        self.tree = ttk.Treeview(table_wrap, columns=columns, show="tree headings")
        self.tree.heading("#0", text="文件")
        self.tree.heading("kind", text="类型")
        self.tree.heading("resolution", text="分辨率")
        self.tree.heading("bitrate", text="码率 / 像素帧密度")
        self.tree.heading("duplicate", text="查重结果")
        self.tree.heading("crop", text="黑边检测")
        self.tree.heading("issues", text="问题")
        self.tree.column("#0", width=300, minwidth=180)
        self.tree.column("kind", width=55, anchor="center")
        self.tree.column("resolution", width=95, anchor="center")
        self.tree.column("bitrate", width=165, anchor="center")
        self.tree.column("duplicate", width=175, anchor="center")
        self.tree.column("crop", width=220, anchor="center")
        self.tree.column("issues", width=340)
        self.tree.tag_configure("issue", background="#FFF1F2", foreground="#991B1B")
        self.tree.tag_configure("review", background="#FFFBEB", foreground="#92400E")
        self.tree.tag_configure("ok", foreground="#166534")
        scroll_y = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_wrap, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)

        tools = ttk.Frame(root, style="Card.TFrame", padding=14)
        tools.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(tools, text="问题文件移至", style="Card.TLabel").grid(row=0, column=0)
        ttk.Entry(tools, textvariable=self.suspicious_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 6)
        )
        ttk.Button(tools, text="选择", command=self._choose_suspicious).grid(row=0, column=2)
        self._toggle_option(
            tools, "也移动黄色复核项", self.include_review_var, 0, 3,
            command=self._refresh_action_state, padx=(8, 0),
        )
        self.move_button = ttk.Button(
            tools, text="移动疑似文件", command=self._move_suspicious, state=tk.DISABLED
        )
        self.move_button.grid(row=0, column=4, padx=(8, 0))

        ttk.Label(tools, text="图片名前缀/规则", style="Card.TLabel").grid(
            row=1, column=0, pady=(10, 0)
        )
        ttk.Entry(tools, textvariable=self.rename_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 6), pady=(10, 0)
        )
        self.rename_button = ttk.Button(
            tools, text="批量重命名", command=self._rename_images, state=tk.DISABLED
        )
        self.rename_button.grid(row=1, column=2, columnspan=3, sticky="ew", pady=(10, 0))

        ttk.Label(
            tools,
            text="直接填“下载_测试”会得到 下载_测试_0001、0002…；也支持高级规则 {index:04d}_{name}",
            style="Muted.Card.TLabel",
        ).grid(row=2, column=1, columnspan=4, sticky="w", padx=(8, 0), pady=(4, 0))

        ttk.Label(tools, text="图片居中裁剪", style="Card.TLabel").grid(
            row=3, column=0, pady=(10, 0)
        )
        crop_row = ttk.Frame(tools, style="Card.TFrame")
        crop_row.grid(row=3, column=1, sticky="ew", padx=(8, 6), pady=(10, 0))
        ttk.Combobox(
            crop_row, textvariable=self.crop_ratio_var,
            values=("1:1", "4:3", "3:4", "16:9", "9:16"),
            state="readonly", width=7,
        ).pack(side=tk.LEFT)
        ttk.Entry(crop_row, textvariable=self.crop_output_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )
        ttk.Button(tools, text="选择", command=self._choose_crop_output).grid(
            row=3, column=2, pady=(10, 0)
        )
        self.crop_button = ttk.Button(
            tools, text="批量裁剪副本", command=self._crop_images, state=tk.DISABLED
        )
        self.crop_button.grid(row=3, column=3, columnspan=2, padx=(8, 0), pady=(10, 0))
        tools.columnconfigure(1, weight=1)

        self.progress = ttk.Progressbar(
            root, mode="determinate", maximum=100, style="Blue.Horizontal.TProgressbar"
        )
        self.progress.grid(row=6, column=0, sticky="ew")
        ttk.Label(root, textvariable=self.status_var, style="Subtitle.TLabel").grid(
            row=7, column=0, sticky="w", pady=(5, 0)
        )
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        self._refresh_detection_controls()

    def _toggle_option(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.BooleanVar,
        row: int,
        column: int,
        command: Callable[[], None] | None = None,
        padx: tuple[int, int] | int = 0,
        pady: tuple[int, int] | int = 0,
    ) -> _ModernCheck:
        widget = _ModernCheck(parent, label, variable, command)
        widget.grid(row=row, column=column, sticky="w", padx=padx, pady=pady)
        return widget

    def _refresh_detection_controls(self) -> None:
        image_state = tk.NORMAL if self.image_resolution_var.get() else tk.DISABLED
        video_state = tk.NORMAL if self.video_resolution_var.get() else tk.DISABLED
        self.image_width_entry.configure(state=image_state)
        self.image_height_entry.configure(state=image_state)
        self.video_width_entry.configure(state=video_state)
        self.video_height_entry.configure(state=video_state)
        self.distance_scale.set_enabled(self.duplicates_var.get())
        scan_images = self.image_resolution_var.get() or self.duplicates_var.get()
        scan_videos = (
            self.video_resolution_var.get()
            or self.video_bitrate_var.get()
            or self.black_bars_var.get()
            or self.duplicates_var.get()
        )
        names = [
            name for enabled, name in ((scan_images, "图片"), (scan_videos, "视频"))
            if enabled
        ]
        self.scan_scope_var.set(
            "扫描范围：" + (" + ".join(names) if names else "未选择检测项目")
        )

    def _refresh_distance_label(self, _value: str = "") -> None:
        value = self.distance_var.get()
        if value == 0:
            text = "仅完全重复"
        elif value <= 2:
            text = f"严格近似（≤ {value}）"
        elif value <= 5:
            text = f"推荐近似（≤ {value}）"
        else:
            text = f"宽松近似（≤ {value}）"
        self.distance_label_var.set(text)

    def _choose_source(self) -> None:
        value = filedialog.askdirectory(
            parent=self.window, initialdir=self.source_var.get() or str(Path.home())
        )
        if value:
            self.source_var.set(value)
            self.suspicious_var.set(str(Path(value) / "疑似问题文件"))
            self.crop_output_var.set(str(Path(value) / "裁剪结果"))

    def _choose_suspicious(self) -> None:
        value = filedialog.askdirectory(parent=self.window, mustexist=False)
        if value:
            self.suspicious_var.set(value)

    def _choose_crop_output(self) -> None:
        value = filedialog.askdirectory(parent=self.window, mustexist=False)
        if value:
            self.crop_output_var.set(value)

    @staticmethod
    def _integer(variable: tk.StringVar, label: str) -> int:
        try:
            value = int(variable.get().strip() or "0")
        except ValueError as exc:
            raise ValueError(f"{label}必须是整数") from exc
        if value < 0:
            raise ValueError(f"{label}不能小于 0")
        return value

    def _options(self) -> ScanOptions:
        slider_distance = self.distance_var.get()
        distance = -1 if slider_distance == 0 else slider_distance
        check_image_resolution = self.image_resolution_var.get()
        check_video_resolution = self.video_resolution_var.get()
        check_video_bitrate = self.video_bitrate_var.get()
        return ScanOptions(
            recursive=self.recursive_var.get(),
            check_image_resolution=check_image_resolution,
            image_min_width=(
                self._integer(self.image_width_var, "图片最低宽")
                if check_image_resolution else 0
            ),
            image_min_height=(
                self._integer(self.image_height_var, "图片最低高")
                if check_image_resolution else 0
            ),
            check_video_resolution=check_video_resolution,
            video_min_width=(
                self._integer(self.video_width_var, "视频最低宽")
                if check_video_resolution else 0
            ),
            video_min_height=(
                self._integer(self.video_height_var, "视频最低高")
                if check_video_resolution else 0
            ),
            check_video_bitrate=check_video_bitrate,
            video_min_bitrate_kbps=0,
            detect_duplicates=self.duplicates_var.get(),
            dhash_distance=distance,
            detect_black_bars=self.black_bars_var.get(),
        )

    def _set_running(self, running: bool) -> None:
        self.scan_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.cancel_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        state = tk.DISABLED if running or not self.records else tk.NORMAL
        self.rename_button.configure(state=state)
        self.crop_button.configure(state=state)
        include_review = self.include_review_var.get()
        suspicious = any(item.movable(include_review) for item in self.records)
        self.move_button.configure(
            state=tk.NORMAL if not running and suspicious else tk.DISABLED
        )

    def _refresh_action_state(self) -> None:
        self._set_running(self.is_busy())

    def _start_worker(self, label: str, operation: Callable[[], Any]) -> None:
        if self._close_requested:
            return
        if self._worker is not None and self._worker.is_alive():
            return
        if self._busy_check():
            messagebox.showwarning(
                "下载正在运行", "请先停止或等待当前下载完成，再执行媒体批处理。",
                parent=self.window,
            )
            return
        self._cancel.clear()
        self._set_running(True)
        self.status_var.set(label)

        def worker() -> None:
            try:
                result = operation()
                self._queue.put(("done", result))
            except OperationCancelled:
                self._queue.put(("cancelled", None))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", str(exc)))

        # 非 daemon：关闭窗口时会先发取消信号，确保 ffmpeg/ffprobe 被清理后再退出。
        self._worker = threading.Thread(target=worker, daemon=False)
        self._worker.start()

    def _start_scan(self) -> None:
        if self._busy_check():
            messagebox.showwarning(
                "下载正在运行", "请先停止或等待当前下载完成，再开始扫描。",
                parent=self.window,
            )
            return
        try:
            source_text = self.source_var.get().strip()
            if not source_text:
                raise ValueError("请先选择扫描目录")
            source = Path(source_text)
            options = self._options()
            if not (
                options.check_image_resolution
                or options.check_video_resolution
                or options.check_video_bitrate
                or options.detect_black_bars
                or options.detect_duplicates
            ):
                raise ValueError("至少选择一项检测功能")
            exclude_dirs = tuple(
                Path(value) for value in (
                    self.suspicious_var.get().strip(),
                    self.crop_output_var.get().strip(),
                ) if value
            )
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self.window)
            return
        if not source.is_dir():
            messagebox.showerror("目录不存在", f"请选择有效目录：\n{source}", parent=self.window)
            return
        self.records = []
        self._last_options = options
        self.tree.delete(*self.tree.get_children())
        self.progress["value"] = 0

        def operation() -> tuple[str, list[MediaRecord]]:
            result = scan_media(
                source, options,
                progress=lambda text: self._queue.put(("status", text)),
                item_progress=lambda index, total, path: self._queue.put(
                    ("progress", (index, total, path.name))
                ),
                cancel=self._cancel,
                ffprobe=shutil.which("ffprobe") or "ffprobe",
                ffmpeg=shutil.which("ffmpeg") or "ffmpeg",
                exclude_dirs=exclude_dirs,
            )
            return "scan", result

        self._start_worker("正在扫描媒体文件...", operation)

    def _request_cancel(self) -> None:
        self._cancel.set()
        self.status_var.set("正在取消，请稍候...")

    def _move_suspicious(self) -> None:
        target_text = self.suspicious_var.get().strip()
        if not target_text:
            messagebox.showerror("目录不能为空", "请选择问题文件的目标目录。", parent=self.window)
            return
        target = Path(target_text).absolute()
        source_text = self.source_var.get().strip()
        if target == Path.cwd().absolute() or (
            source_text and target == Path(source_text).absolute()
        ):
            messagebox.showerror(
                "目标目录不安全", "问题文件目录不能是程序目录或扫描目录本身。",
                parent=self.window,
            )
            return
        include_review = self.include_review_var.get()
        count = sum(item.movable(include_review) and item.path.exists() for item in self.records)
        if not count:
            return
        if not messagebox.askyesno(
            "确认移动", f"将 {count} 个疑似问题文件移至：\n{target}\n\n原目录中将不再保留这些文件。",
            parent=self.window,
        ):
            return

        def operation() -> tuple[str, Any]:
            moved = move_suspicious(
                self.records, target,
                progress=lambda text: self._queue.put(("status", text)),
                cancel=self._cancel,
                include_review=include_review,
            )
            return "move", moved

        self._start_worker("正在安全移动疑似文件...", operation)

    def _rename_images(self) -> None:
        template = self.rename_var.get().strip()
        if not template:
            messagebox.showerror("模板不能为空", "请输入图片重命名模板。", parent=self.window)
            return
        count = sum(item.kind == "图片" and item.path.exists() for item in self.records)
        if not count:
            return
        effective_template = normalize_rename_template(template)
        examples = [
            render_rename(effective_template, item, index)
            for index, item in enumerate(
                (record for record in self.records if record.kind == "图片" and record.path.exists()),
                1,
            )
        ][:3]
        preview = "\n".join(f"  {name}" for name in examples)
        if not messagebox.askyesno(
            "确认重命名",
            f"将重命名 {count} 张图片。前 3 个新文件名：\n{preview}\n\n"
            "直接输入文字会自动添加四位编号；不会改变图片格式。",
            parent=self.window,
        ):
            return

        def operation() -> tuple[str, Any]:
            changed = batch_rename_images(
                self.records, template,
                progress=lambda text: self._queue.put(("status", text)),
                cancel=self._cancel,
            )
            return "rename", changed

        self._start_worker("正在批量重命名图片...", operation)

    def _crop_images(self) -> None:
        output_text = self.crop_output_var.get().strip()
        if not output_text:
            messagebox.showerror("目录不能为空", "请选择裁剪副本保存目录。", parent=self.window)
            return
        output = Path(output_text).absolute()
        source_text = self.source_var.get().strip()
        if output == Path.cwd().absolute() or (
            source_text and output == Path(source_text).absolute()
        ):
            messagebox.showerror(
                "目标目录不安全", "裁剪目录不能是程序目录或扫描目录本身。",
                parent=self.window,
            )
            return
        ratio = self.crop_ratio_var.get()
        count = sum(item.kind == "图片" and item.path.exists() for item in self.records)
        if not count:
            return
        if not messagebox.askyesno(
            "确认裁剪",
            f"将 {count} 张图片居中裁剪为 {ratio}，副本保存到：\n{output}\n\n不会修改原图。",
            parent=self.window,
        ):
            return

        def operation() -> tuple[str, Any]:
            outputs = batch_crop_images(
                self.records, ratio, output,
                progress=lambda text: self._queue.put(("status", text)),
                cancel=self._cancel,
            )
            return "crop", outputs

        self._start_worker("正在生成裁剪副本...", operation)

    def _show_records(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for record in self.records:
            options = self._last_options or ScanOptions()
            if record.kind == "图片":
                bitrate = "不适用"
                resolution = record.resolution if options.check_image_resolution else "未检测"
            else:
                resolution = record.resolution if options.check_video_resolution else "未检测"
                if not options.check_video_bitrate:
                    bitrate = "未检测"
                elif record.bitrate_kbps:
                    density = (
                        f" · {record.bitrate_density:.3f} bpp/帧"
                        if record.bitrate_density else ""
                    )
                    bitrate = f"{record.bitrate_kbps} kbps{density}"
                else:
                    bitrate = "未知"
            details = list(record.issues)
            details.extend(f"需复核：{item}" for item in record.review_issues)
            if record.archived:
                details.append("已移至问题目录")
            issues = "；".join(details) if details else "正常"
            tag = "issue" if record.suspicious else "review" if record.needs_review else "ok"
            self.tree.insert(
                "", tk.END, text=record.path.name,
                values=(
                    record.kind, resolution, bitrate,
                    record.duplicate_status, record.black_bar_status, issues,
                ),
                tags=(tag,),
            )
        suspicious = sum(item.suspicious for item in self.records)
        review = sum(item.needs_review for item in self.records)
        images = sum(item.kind == "图片" for item in self.records)
        videos = sum(item.kind == "视频" for item in self.records)
        self.summary_var.set(
            f"图片 {images} · 视频 {videos} · 待移动 {suspicious} · 需复核 {review}"
        )

    def _poll_queue(self) -> None:
        try:
            exists = self.window.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "progress":
                    index, total, name = payload
                    self.progress["value"] = index / max(total, 1) * 100
                    self.status_var.set(f"正在检查 {index}/{total}：{name}")
                elif kind == "done":
                    operation, result = payload
                    if operation == "scan":
                        self.records = result
                        self._show_records()
                        self.progress["value"] = 100
                        self.status_var.set("扫描完成；红色项目可统一移至问题文件夹")
                    elif operation == "move":
                        self._show_records()
                        self.status_var.set(f"已安全移动 {len(result)} 个疑似问题文件")
                    elif operation == "rename":
                        self._show_records()
                        self.status_var.set(f"已重命名 {len(result)} 张图片")
                    elif operation == "crop":
                        self.status_var.set(f"已生成 {len(result)} 张裁剪副本")
                    self._set_running(False)
                elif kind == "cancelled":
                    self._show_records()
                    self.status_var.set("操作已取消；已完成的移动会保留在目标目录")
                    self._set_running(False)
                elif kind == "error":
                    self.status_var.set("操作失败")
                    self._set_running(False)
                    messagebox.showerror("批处理失败", str(payload), parent=self.window)
        except queue.Empty:
            pass
        self.window.after(100, self._poll_queue)

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def request_close(self) -> None:
        """先取消并等待后台任务退出；独立窗口随后销毁。"""
        self._close_requested = True
        self._cancel.set()
        if self.is_busy():
            self.status_var.set("正在取消并清理后台工具，请勿强制关闭...")
            self.window.after(100, self.request_close)
            return
        if not self._embedded and self.window.winfo_exists():
            self.window.destroy()

    def _close(self) -> None:
        self.request_close()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.window:
            self._cancel.set()
            if (
                not self._embedded
                and not self._closed_notified
                and self._on_closed_callback is not None
            ):
                self._closed_notified = True
                self._on_closed_callback()
