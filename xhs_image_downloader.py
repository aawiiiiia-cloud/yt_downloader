"""小红书图文下载实验模块。

目标是验证“同一个链接输入框自动下载图文帖子”的可行性。模块复用项目已经
锁定的 yt-dlp 网络层和小红书页面解析基础，不依赖完整的第三方采集项目。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from collector_errors import safe_error_text
from image_transfer import ImageRateMeter, ImageTransferPolicy, OriginalSourcePlan
from image_summary import write_image_summary

try:
    import yt_dlp
    from yt_dlp.extractor.xiaohongshu import XiaoHongShuIE
    from yt_dlp.networking.common import Request
    from yt_dlp.utils import js_to_json
except ImportError as exc:  # pragma: no cover - CLI 环境提示
    raise RuntimeError("缺少 yt-dlp，请先运行主项目的环境安装流程") from exc


Progress = Callable[[str], None]
XHS_COOKIE_FILE = Path.home() / ".yt_dlp_gui_xiaohongshu_cookies.txt"
XHS_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}
IMAGE_OUTPUT_FORMATS = {
    "original": ("", ""),
    "jpg": (".jpg", "JPEG"),
    "png": (".png", "PNG"),
    "webp": (".webp", "WEBP"),
}

# 小红书网页展示图通常来自 sns-webpic-*，其中可能已经叠加平台水印。
# 原始素材使用同一个资源 ID，但应优先从 sns-na-i* / sns-img-* 取回。
# 顺序参考当前 RedNote Downloader 扩展的公开发行包；不要把 sns-webpic-*
# 放进这个列表，否则一次成功响应就可能让真正的原始素材源永远没有机会尝试。
XHS_ORIGINAL_IMAGE_ORIGINS = (
    "https://sns-na-i11.xhscdn.com",
    "https://sns-na-i10.xhscdn.com",
    "https://sns-na-i9.xhscdn.com",
    "https://sns-na-i8.xhscdn.com",
    "https://sns-na-i6.xhscdn.com",
    "https://sns-na-i4.xhscdn.com",
    "https://sns-na-i2.xhscdn.com",
    "https://sns-na-i1.xhscdn.com",
    "https://sns-img-qc.xhscdn.com",
    "https://sns-img-ak.xhscdn.com",
    "https://sns-img-bd.xhscdn.com",
    "https://sns-img-hw.xhscdn.com",
    "https://sns-img-qn.xhscdn.com",
)


class XhsDownloadError(RuntimeError):
    """给 GUI/CLI 展示的可读错误。"""


class _SlowOriginalTransfer(Exception):
    """Internal signal: try an equal-quality original, not a preview."""


class XhsCancelled(XhsDownloadError):
    """用户主动取消。"""


class XhsVideoPost(XhsDownloadError):
    """帖子是视频，应交给 yt-dlp 继续处理。"""


@dataclass(frozen=True)
class XhsImage:
    index: int
    url: str
    fallback_urls: tuple[str, ...] = ()
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class XhsPost:
    note_id: str
    title: str
    description: str
    author: str
    source_url: str
    images: tuple[XhsImage, ...]


class _QuietLogger:
    def __init__(self, progress: Progress) -> None:
        self.progress = progress

    def debug(self, _message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.progress(f"[警告] {message}")

    def error(self, message: str) -> None:
        self.progress(f"[错误] {message}")


def is_xhs_url(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in XHS_HOSTS or host.endswith(".xiaohongshu.com")


def extract_xhs_url(text: str) -> str | None:
    """允许直接粘贴小红书 App 生成的整段分享文案。"""
    for candidate in re.findall(r"https?://[^\s]+", text):
        cleaned = candidate.rstrip("，。；;、)]}〉》\"'")
        if is_xhs_url(cleaned):
            return cleaned
    stripped = text.strip()
    return stripped if is_xhs_url(stripped) else None


def _pick_note(note_map: dict[str, Any], note_id: str) -> dict[str, Any]:
    entry = note_map.get(note_id)
    if not isinstance(entry, dict) and len(note_map) == 1:
        entry = next(iter(note_map.values()))
    if not isinstance(entry, dict):
        raise XhsDownloadError("页面中没有找到对应帖子数据，链接可能已过期或不可见")
    note = entry.get("note", entry)
    if not isinstance(note, dict):
        raise XhsDownloadError("帖子数据格式异常，请更新程序后重试")
    return note


def _image_resource_id(raw: dict[str, Any], url: str) -> str | None:
    """提取原始素材资源 ID，并保留 ``fileId`` 可能携带的目录前缀。

    新版页面常把 ``traceId`` 留空；只取 URL 最后一段虽然有时可用，却会在
    ``fileId`` 为 ``spectrum/...`` 等形式时丢失定位原图所需的前缀。
    """
    file_id = raw.get("fileId") or raw.get("file_id")
    if isinstance(file_id, str) and file_id.strip():
        resource_id = file_id.strip().lstrip("/").split("!", 1)[0]
    else:
        # 与当前可用扩展的行为一致：展示图路径通常是
        # /时间戳/签名/资源ID!变换规则，前两段不是资源 ID 的组成部分。
        parts = urlparse(url).path.split("/")
        resource_id = "/".join(parts[3:]).split("!", 1)[0].lstrip("/")
        if not resource_id:
            resource_id = Path(urlparse(url).path).name.split("!", 1)[0]

    # 资源 ID 可包含安全目录前缀，但绝不能允许跳目录、查询串或新 URL。
    if (
        not resource_id
        or ".." in resource_id.split("/")
        or "\\" in resource_id
        or any(char in resource_id for char in "?#:")
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", resource_id)
    ):
        return None
    return resource_id


def _image_candidates(raw: dict[str, Any], display_url: str) -> tuple[str, ...]:
    """原始素材 CDN 优先，可能带水印的网页展示图最后兜底。"""
    candidates: list[str] = []
    if resource_id := _image_resource_id(raw, display_url):
        candidates.extend(
            f"{origin}/{resource_id}" for origin in XHS_ORIGINAL_IMAGE_ORIGINS
        )
    # sns-webpic-* 即使去掉 ! 变换规则也可能仍是带水印素材，不能在原始
    # 素材源前抢先成功；它只作为所有原始 CDN 均失效时的最后兼容兜底。
    candidates.append(display_url)
    return tuple(dict.fromkeys(candidates))


def post_from_initial_state(
    initial_state: dict[str, Any], note_id: str, source_url: str,
) -> XhsPost:
    """从小红书页面状态中提取一个图文帖子，保持图片原顺序。"""
    note_map = (
        initial_state.get("note", {}).get("noteDetailMap", {})
        if isinstance(initial_state, dict) else {}
    )
    if not isinstance(note_map, dict):
        raise XhsDownloadError("页面没有返回帖子详情，可能需要登录小红书")
    note = _pick_note(note_map, note_id)
    # 视频帖子通常也带有封面 imageList；必须先识别 video，否则会误把封面
    # 当成完整图文下载并提前结束，永远进不到 yt-dlp 视频流程。
    if note.get("video") or str(note.get("type") or "").lower() == "video":
        raise XhsVideoPost("这是视频帖子，将改用视频下载流程")
    raw_images = note.get("imageList") or []
    images: list[XhsImage] = []
    seen: set[str] = set()
    for raw in raw_images:
        if not isinstance(raw, dict):
            continue
        # urlDefault 通常是完整展示图；urlPre 作为兼容旧页面的兜底。
        url = raw.get("urlDefault") or raw.get("urlPre")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        candidates = _image_candidates(raw, url)
        primary = candidates[0]
        if primary in seen:
            continue
        seen.add(primary)
        images.append(XhsImage(
            index=len(images) + 1,
            url=primary,
            fallback_urls=candidates[1:],
            width=_as_positive_int(raw.get("width")),
            height=_as_positive_int(raw.get("height")),
        ))
    if not images:
        raise XhsDownloadError("帖子中没有找到可下载图片，链接可能失效或需要登录")

    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    title = str(note.get("title") or "").strip()
    description = str(note.get("desc") or "").strip()
    return XhsPost(
        note_id=note_id,
        title=title or description[:60] or "小红书图文",
        description=description,
        author=str(user.get("nickname") or user.get("nickName") or "").strip(),
        source_url=source_url,
        images=tuple(images),
    )


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _safe_name(value: str, fallback: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned.upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    return (cleaned or fallback)[:limit].rstrip(" .")


def _image_extension(content_type: str, url: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    by_mime = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heic",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }
    if mime in by_mime:
        return by_mime[mime]
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in set(by_mime.values()) else ".jpg"


def _same_image_format(source_extension: str, target_format: str) -> bool:
    source = source_extension.lower()
    return (
        (target_format == "jpg" and source in {".jpg", ".jpeg"})
        or source == f".{target_format}"
    )


def _detected_image_extension(path: Path, fallback: str, strict: bool = False) -> str:
    """依据文件头确认真实编码；CDN 的 Content-Type/URL 可能没有扩展信息。"""
    from PIL import Image

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass
    formats = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
        "HEIF": ".heic",
        "HEIC": ".heic",
        "AVIF": ".avif",
        "GIF": ".gif",
        "TIFF": ".tiff",
        "BMP": ".bmp",
    }
    try:
        with Image.open(path) as image:
            extension = formats.get(str(image.format or "").upper(), fallback)
            image.verify()
        # 文件头和 Content-Length 都可能正常，但像素数据仍然被截断。
        with Image.open(path) as image:
            image.load()
        return extension
    except Exception as exc:
        if strict:
            raise XhsDownloadError("服务器返回内容无法识别为有效图片") from exc
        return fallback


def _convert_image(source: Path, target: Path, output_format: str, quality: int) -> None:
    """解码下载结果并原子写出目标格式；失败时不留下半成品。"""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        # 普通 JPEG/PNG/WebP 不依赖 pillow-heif；HEIC 打开失败时给专门提示。
        pass

    extension, pillow_format = IMAGE_OUTPUT_FORMATS[output_format]
    if not extension or not pillow_format:
        raise ValueError("保留原格式不应进入图片转换流程")
    temporary = target.with_name(f".{target.name}.convert.part")
    temporary.unlink(missing_ok=True)
    try:
        try:
            opened = Image.open(source)
        except UnidentifiedImageError as exc:
            raise XhsDownloadError(
                "无法解码原图；如果原图是 HEIC/HEIF，请确认 pillow-heif 已安装"
            ) from exc
        with opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
            icc_profile = opened.info.get("icc_profile")
            save_options: dict[str, Any] = {}
            if icc_profile:
                save_options["icc_profile"] = icc_profile
            quality = max(1, min(95, int(quality)))
            if pillow_format == "JPEG":
                if image.mode in {"RGBA", "LA"} or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                elif image.mode not in {"RGB", "L", "CMYK"}:
                    image = image.convert("RGB")
                save_options.update(
                    quality=quality, optimize=True, progressive=True,
                    subsampling=0 if quality >= 90 else 2,
                )
            elif pillow_format == "PNG":
                if image.mode == "CMYK":
                    image = image.convert("RGB")
                    # RGB 结果不能继续使用源图片的 CMYK 色彩配置。
                    save_options.pop("icc_profile", None)
                save_options.update(optimize=True, compress_level=6)
            elif pillow_format == "WEBP":
                save_options.update(quality=quality, method=6)
            with temporary.open("wb") as output:
                image.save(output, format=pillow_format, **save_options)
                output.flush()
                os.fsync(output.fileno())
        # 写完后重新解码一次，拒绝发布损坏的转换结果。
        with Image.open(temporary) as verified:
            verified.load()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class XhsImageDownloader:
    def __init__(
        self,
        cookie_file: Path | None = None,
        proxy: str = "",
        progress: Progress = print,
        cancel_event: threading.Event | None = None,
        item_progress: Callable[[int, int, str], None] | None = None,
        image_format: str = "original",
        image_quality: int = 95,
        user_agent: str | None = None,
        transfer_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.cookie_file = cookie_file
        self.proxy = proxy.strip()
        self.user_agent = user_agent
        self.progress = progress
        self.cancel_event = cancel_event
        self.item_progress = item_progress
        self.transfer_progress = transfer_progress
        self._completed_images = 0
        self.transfer_policy = ImageTransferPolicy()
        self._origin_health: dict[str, tuple[int, float]] = {}
        normalized_format = image_format.strip().lower()
        if normalized_format not in IMAGE_OUTPUT_FORMATS:
            raise ValueError(f"不支持的图片输出格式: {image_format}")
        self.image_format = normalized_format
        self.image_quality = max(1, min(95, int(image_quality)))

    def _check_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise XhsCancelled("已取消小红书图片下载")

    def _ydl_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "logger": _QuietLogger(self.progress),
            "socket_timeout": 30,
            "retries": 3,
            "http_headers": {
                "User-Agent": self.user_agent or (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
        }
        if self.cookie_file and self.cookie_file.is_file():
            options["cookiefile"] = str(self.cookie_file)
        if self.proxy:
            options["proxy"] = self.proxy
        return options

    def inspect(self, url: str) -> XhsPost:
        if not is_xhs_url(url):
            raise XhsDownloadError("这不是受支持的小红书帖子链接")
        self._check_cancelled()
        self.progress("[解析] 正在打开小红书帖子...")
        with yt_dlp.YoutubeDL(self._ydl_options()) as ydl:
            ie = XiaoHongShuIE(ydl)
            try:
                webpage, response = ie._download_webpage_handle(url, "xhs-image")
            except Exception as exc:  # noqa: BLE001
                raise XhsDownloadError(f"无法打开帖子，请检查链接、网络或登录状态：{exc}") from exc
            final_url = getattr(response, "url", None) or url
            if "/404" in urlparse(final_url).path:
                raise XhsDownloadError("小红书将链接跳转到了 404；请复制刚生成的最新分享链接")
            match = re.search(r"/(?:explore|discovery/item)/([\da-f]+)", final_url, re.I)
            if not match:
                raise XhsDownloadError("无法从跳转结果识别帖子 ID，请使用最新分享链接")
            note_id = match.group(1)
            try:
                initial_state = ie._search_json(
                    r"window\.__INITIAL_STATE__\s*=", webpage,
                    "initial state", note_id, transform_source=js_to_json,
                )
            except Exception as exc:  # noqa: BLE001
                raise XhsDownloadError(
                    "页面没有提供可解析的帖子数据，可能需要登录或页面结构已变化"
                ) from exc
        post = post_from_initial_state(initial_state, note_id, final_url)
        self.progress(f"[解析] {post.title} · 共 {len(post.images)} 张图片")
        return post

    def download(self, url: str, output_dir: Path) -> tuple[XhsPost, tuple[Path, ...]]:
        post = self.inspect(url)
        return self.download_post(post, output_dir)

    def download_post(self, post: XhsPost, output_dir: Path) -> tuple[XhsPost, tuple[Path, ...]]:
        """Download an inspected browser note, preserving its original image indices."""
        self._check_cancelled()
        folder = self._post_folder(post, output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        completed: list[Path] = []
        self._origin_health.clear()
        self._completed_images = 0
        self.progress(f"[准备] 共 {len(post.images)} 张选中图片，按原始编号保存")
        with yt_dlp.YoutubeDL(self._ydl_options()) as ydl:
            for image in post.images:
                self._check_cancelled()
                completed.append(self._download_one(ydl, post, image, folder))
                self._completed_images += 1
        self._write_summary(post, folder, check_cancel=self._check_cancelled)
        self.progress(f"[完成] 已保存 {len(completed)} 张图片 → {folder}")
        return post, tuple(completed)

    def _post_folder(self, post: XhsPost, output_dir: Path) -> Path:
        return output_dir.resolve() / _safe_name(
            f"{post.title} [{post.note_id}]", f"小红书图文 [{post.note_id}]",
        )

    def _transfer(self, post: XhsPost, image: XhsImage, status: str,
                  received: int = 0, expected: int | None = None,
                  speed: float | None = None) -> None:
        if self.transfer_progress is None:
            return
        total = max(len(post.images), 1)
        # Receiving bytes is not completion: validation and atomic publication follow.
        fraction = min(received / expected, 0.99) if expected else None
        if status in {"image-validating", "image-converting"}:
            fraction = 0.99
        self.transfer_progress({
            "status": status, "percent": (
                (self._completed_images + fraction) / total * 100
                if fraction is not None else None
            ), "downloadedBytes": received, "totalBytes": expected,
            "imageIndex": image.index, "speed": speed,
        })

    def _original_candidate_indices(self, image: XhsImage) -> tuple[int, ...]:
        """Only generated, same-resource XHS originals qualify for low-rate switching."""
        candidates = (image.url, *image.fallback_urls)
        origins = set(XHS_ORIGINAL_IMAGE_ORIGINS)
        paths = []
        for index, url in enumerate(candidates):
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin in origins and not parsed.query and "!" not in parsed.path:
                paths.append((index, parsed.path))
        if not paths:
            return ()
        resource = paths[0][1]
        return tuple(index for index, path in paths if path == resource)

    def _download_one(
        self, ydl: yt_dlp.YoutubeDL, post: XhsPost, image: XhsImage, folder: Path,
    ) -> Path:
        last_error: Exception | None = None
        temp_path = folder / f".{image.index:02d}.part"
        candidates = (image.url, *image.fallback_urls)
        originals = self._original_candidate_indices(image)
        policy = self.transfer_policy
        plan = None
        if originals:
            ranks = {}
            for index in originals:
                host = urlparse(candidates[index]).hostname or ""
                penalty, rate = self._origin_health.get(host, (0, 0))
                ranks[index] = (penalty, -rate, index)
            plan = OriginalSourcePlan(len(candidates), originals, ranks, policy.max_slow_sources)
            attempts = plan.attempts()
        else:
            # Other sites retain their own original/preview ordering.
            attempts = ((index, attempt, False) for index in range(len(candidates))
                        for attempt in range(1, 3))
        previous_index = None
        for candidate_index, attempt, watch_slow in attempts:
            self._check_cancelled()
            image_url = candidates[candidate_index]
            host = urlparse(image_url).hostname or ""
            is_original = candidate_index in originals
            if previous_index is not None and previous_index != candidate_index:
                label = "同质量原图" if is_original else "备用图片"
                self.progress(f"[回退] 图片 {image.index} 正在切换{label}节点 · 地址 {candidate_index + 1}/{len(candidates)}")
            if attempt > 1:
                self.progress(f"[重试] 图片 {image.index} 再试地址 {candidate_index + 1}/{len(candidates)}")
            if plan and candidate_index in plan.slow and not watch_slow:
                self.progress(f"[继续] 图片 {image.index} 本轮备用原图未能更快完成，继续已测较快的原图地址，不再因低速反复切换")
            previous_index = candidate_index
            received, expected, started, meter = 0, None, None, None
            try:
                self._transfer(post, image, "image-connecting")
                # A per-request timeout leaves the other sites' network settings intact.
                timeout = policy.original_idle_timeout if is_original else 30
                extensions = {"timeout": timeout} if is_original else {}
                self.progress(f"[连接] 图片 {image.index} · 下载地址 {candidate_index + 1}/{len(candidates)} · "
                              f"尝试 {attempt}/2（单次网络等待超时 {timeout:g} 秒）"
                              f"{' · 原图节点 ' + host if is_original else ''}")
                request = Request(image_url, headers={"Referer": post.source_url}, extensions=extensions)
                with ydl.urlopen(request) as response:
                    content_type = str(response.headers.get("Content-Type") or "")
                    mime = content_type.split(";", 1)[0].strip().lower()
                    generic_binary = mime in {"application/octet-stream", "binary/octet-stream"}
                    if content_type and not mime.startswith("image/") and not generic_binary:
                        raise XhsDownloadError(f"服务器返回的不是图片：{content_type}")
                    extension = _image_extension(content_type, image_url)
                    expected = _as_positive_int(response.headers.get("Content-Length"))
                    self.progress(f"[接收] 图片 {image.index} 已连接，正在接收图片数据")
                    self._transfer(post, image, "image-receiving", received, expected)
                    started = last_progress = last_log = time.monotonic()
                    meter = ImageRateMeter(started, policy)
                    with open(temp_path, "wb") as handle:
                        while True:
                            self._check_cancelled()
                            block = response.read(policy.read_size)
                            now = time.monotonic()
                            if not block:
                                break
                            handle.write(block)
                            received += len(block)
                            speed = meter.update(now, received)
                            if received == len(block) or now - last_progress >= 0.2:
                                self._transfer(post, image, "image-receiving", received, expected, speed)
                                last_progress = now
                            # A cancellation during a progress callback wins over switching.
                            self._check_cancelled()
                            if now - last_log >= policy.log_interval:
                                total_text = f" / {expected / 1024:.0f} KB" if expected else "（总大小未知）"
                                self.progress(f"[接收进度] 图片 {image.index} · {received / 1024:.0f} KB{total_text} · "
                                              f"最近速度 {(speed or 0) / 1024:.1f} KB/s · 接收 {now - started:.1f} 秒")
                                last_log = now
                            if watch_slow and meter.should_switch(received, expected):
                                raise _SlowOriginalTransfer()
                        elapsed = now - started
                        self._transfer(post, image, "image-receiving", received, expected,
                                       meter.update(now, received) if received else None)
                        handle.flush()
                        os.fsync(handle.fileno())
                    self._check_cancelled()
                    if expected is not None and received != expected:
                        raise XhsDownloadError(f"图片 {image.index} 下载不完整：应为 {expected} 字节，实际 {received} 字节")
                    if received <= 0:
                        raise XhsDownloadError(f"图片 {image.index} 内容为空")
                    average = received / elapsed if elapsed > 0 else 0
                    self.progress(f"[接收完成] 图片 {image.index} · {received / 1024:.0f} KB · "
                                  f"{elapsed:.1f} 秒 · 平均 {average / 1024:.1f} KB/s")
                    self._transfer(post, image, "image-validating", received, expected)
                    self.progress(f"[校验] 图片 {image.index} 接收完成，检查完整性")
                    extension = _detected_image_extension(temp_path, extension, strict=True)
                    self._validate_download(temp_path, image)
                    target_extension = extension if self.image_format == "original" else IMAGE_OUTPUT_FORMATS[self.image_format][0]
                    target = folder / f"{image.index:02d}{target_extension}"
                    converted = False
                    if self.image_format == "original" or _same_image_format(extension, self.image_format):
                        os.replace(temp_path, target)
                    else:
                        self._transfer(post, image, "image-converting", received, expected)
                        self.progress(f"[转换] 图片 {image.index} 正在转换为 {self.image_format.upper()}")
                        _convert_image(temp_path, target, self.image_format, self.image_quality)
                        temp_path.unlink(missing_ok=True)
                        converted = True
                    if is_original:
                        self._origin_health[host] = (0 if average >= policy.minimum_rate else 1, average)
                    source_label = self._source_label(image, candidate_index, len(candidates))
                    self.progress(f"[图片] {image.index}/{len(post.images)} 已保存（{source_label}"
                                  f"{'，已转换为 ' + self.image_format.upper() if converted else '，保留原始编码'}）：{target.name}")
                    if self.item_progress:
                        self.item_progress(image.index, len(post.images), target.name)
                    return target
            except XhsCancelled:
                temp_path.unlink(missing_ok=True)
                raise
            except _SlowOriginalTransfer:
                # A slow response is still an original that works. Revisit it before
                # considering a display fallback if the alternate originals all fail.
                temp_path.unlink(missing_ok=True)
                self._origin_health[host] = (1, meter.speed or 0)
                plan.mark_slow(candidate_index, meter.speed)
                self.progress(f"[低速] 图片 {image.index} · 连续 {policy.slow_window * policy.slow_windows:g} 秒低于 "
                              f"{policy.minimum_rate / 1024:g} KB/s · 最近 {(meter.speed or 0) / 1024:.1f} KB/s · "
                              f"已接收 {received / 1024:.0f} KB，尝试其他同质量原图节点")
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                temp_path.unlink(missing_ok=True)
                self._check_cancelled()
                if is_original:
                    self._origin_health[host] = (2, 0)
                elapsed_text = f"，接收 {time.monotonic() - started:.1f} 秒" if started is not None else "，尚未收到图片数据"
                self.progress(f"[失败] 图片 {image.index} 地址 {candidate_index + 1}/{len(candidates)} · "
                              f"已接收 {received / 1024:.0f} KB{elapsed_text}：{safe_error_text(exc)}")
        raise XhsDownloadError(f"图片 {image.index} 的原图和展示图地址均下载失败：{safe_error_text(last_error)}") from last_error

    def _validate_download(self, file: Path, image: XhsImage) -> None:
        """Site-specific checks before converting or atomically publishing an image."""

    def _source_label(self, image, candidate_index, total):
        return ("原始素材 CDN" if candidate_index in self._original_candidate_indices(image)
                else "网页展示图兜底（可能带水印）")

    @staticmethod
    def _write_summary(post: XhsPost, folder: Path, *, check_cancel=None) -> None:
        # 不保存 Cookie、图片临时签名 URL 或分享链接中的 xsec_token。
        data = {
            "note_id": post.note_id,
            "title": post.title,
            "description": post.description,
            "author": post.author,
            "image_count": len(post.images),
        }
        write_image_summary(folder / 'post.json', data, [{'index': image.index} for image in post.images], ('note_id',), check_cancel)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="小红书图文下载实验")
    parser.add_argument("url", nargs="?", help="小红书帖子/分享链接")
    parser.add_argument("--output", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--cookies", type=Path, default=XHS_COOKIE_FILE)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--login", action="store_true", help="先打开 Edge 登录并保存凭据")
    parser.add_argument("--inspect-only", action="store_true", help="只解析，不下载图片")
    parser.add_argument(
        "--image-format", choices=tuple(IMAGE_OUTPUT_FORMATS), default="original",
        help="图片输出格式（默认保留原始格式）",
    )
    parser.add_argument("--image-quality", type=int, default=95, help="JPG/WebP 画质 1-95")
    return parser


def main() -> int:
    if len(sys.argv) == 1:
        return _interactive_main()
    args = _build_parser().parse_args()
    if args.login:
        from edge_login import run_login

        run_login(out_path=args.cookies, site="xiaohongshu", verify=False)
        if not args.url:
            return 0
    if not args.url:
        raise SystemExit("请提供小红书帖子链接，或使用 --login")
    downloader = XhsImageDownloader(
        cookie_file=args.cookies if args.cookies.is_file() else None,
        proxy=args.proxy,
        image_format=args.image_format,
        image_quality=args.image_quality,
    )
    if args.inspect_only:
        post = downloader.inspect(args.url)
        public = asdict(post)
        public.pop("source_url", None)
        for image in public["images"]:
            image["url"] = "(已隐藏)"
        print(json.dumps(public, ensure_ascii=False, indent=2))
    else:
        downloader.download(args.url, args.output)
    return 0


def _interactive_main() -> int:
    """双击启动时使用的简单菜单；正式 GUI 接入前供真人验证。"""
    default_output = Path.home() / "Desktop" / "youtube_download"
    if not default_output.parent.is_dir():
        default_output = Path.home() / "Downloads"
    while True:
        print("\n=== 小红书图文下载实验 ===")
        print("1. 下载图文帖子")
        print("2. 登录/更新小红书登录状态")
        print("0. 退出")
        choice = input("请选择 [1]: ").strip() or "1"
        if choice == "0":
            return 0
        if choice == "2":
            from edge_login import run_login

            try:
                run_login(out_path=XHS_COOKIE_FILE, site="xiaohongshu", verify=False)
                print(f"[完成] 小红书登录状态已保存：{XHS_COOKIE_FILE}")
            except Exception as exc:  # noqa: BLE001
                print(f"[失败] {exc}")
                input("按 Enter 返回菜单...")
            continue
        if choice != "1":
            print("[提示] 请输入 0、1 或 2")
            continue

        pasted = input("请粘贴小红书帖子链接或完整分享文案：").strip()
        url = extract_xhs_url(pasted)
        if not url:
            print("[错误] 没有识别到小红书帖子链接")
            continue
        output_text = input(f"保存目录（直接回车使用 {default_output}）：").strip()
        output = Path(output_text.strip('"')) if output_text else default_output
        downloader = XhsImageDownloader(
            cookie_file=XHS_COOKIE_FILE if XHS_COOKIE_FILE.is_file() else None,
        )
        try:
            downloader.download(url, output)
            input("下载完成，按 Enter 返回菜单...")
        except XhsDownloadError as exc:
            print(f"[失败] {exc}")
            input("按 Enter 返回菜单...")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except XhsDownloadError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
