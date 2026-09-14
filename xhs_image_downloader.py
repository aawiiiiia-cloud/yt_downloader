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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

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


class XhsDownloadError(RuntimeError):
    """给 GUI/CLI 展示的可读错误。"""


class XhsCancelled(XhsDownloadError):
    """用户主动取消。"""


class XhsVideoPost(XhsDownloadError):
    """帖子是视频，应交给 yt-dlp 继续处理。"""


@dataclass(frozen=True)
class XhsImage:
    index: int
    url: str
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
        if url in seen:
            continue
        seen.add(url)
        images.append(XhsImage(
            index=len(images) + 1,
            url=url,
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


class XhsImageDownloader:
    def __init__(
        self,
        cookie_file: Path | None = None,
        proxy: str = "",
        progress: Progress = print,
        cancel_event: threading.Event | None = None,
        item_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.cookie_file = cookie_file
        self.proxy = proxy.strip()
        self.progress = progress
        self.cancel_event = cancel_event
        self.item_progress = item_progress

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
                "User-Agent": (
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
        folder = output_dir.resolve() / _safe_name(
            f"{post.title} [{post.note_id}]", f"小红书图文 [{post.note_id}]",
        )
        folder.mkdir(parents=True, exist_ok=True)
        completed: list[Path] = []
        with yt_dlp.YoutubeDL(self._ydl_options()) as ydl:
            for image in post.images:
                self._check_cancelled()
                completed.append(self._download_one(ydl, post, image, folder))
        self._write_summary(post, folder)
        self.progress(f"[完成] 已保存 {len(completed)} 张图片 → {folder}")
        return post, tuple(completed)

    def _download_one(
        self, ydl: yt_dlp.YoutubeDL, post: XhsPost, image: XhsImage, folder: Path,
    ) -> Path:
        last_error: Exception | None = None
        temp_path = folder / f".{image.index:02d}.part"
        for attempt in range(1, 4):
            self._check_cancelled()
            try:
                request = Request(image.url, headers={"Referer": post.source_url})
                with ydl.urlopen(request) as response:
                    content_type = str(response.headers.get("Content-Type") or "")
                    if content_type and not content_type.lower().startswith("image/"):
                        raise XhsDownloadError(f"服务器返回的不是图片：{content_type}")
                    extension = _image_extension(content_type, image.url)
                    target = folder / f"{image.index:02d}{extension}"
                    expected = _as_positive_int(response.headers.get("Content-Length"))
                    received = 0
                    with open(temp_path, "wb") as handle:
                        while True:
                            self._check_cancelled()
                            block = response.read(256 * 1024)
                            if not block:
                                break
                            handle.write(block)
                            received += len(block)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if expected is not None and received != expected:
                        raise XhsDownloadError(
                            f"图片 {image.index} 下载不完整：应为 {expected} 字节，实际 {received} 字节"
                        )
                    if received <= 0:
                        raise XhsDownloadError(f"图片 {image.index} 内容为空")
                    os.replace(temp_path, target)
                    self.progress(
                        f"[图片] {image.index}/{len(post.images)} 已保存：{target.name}"
                    )
                    if self.item_progress:
                        self.item_progress(image.index, len(post.images), target.name)
                    return target
            except XhsCancelled:
                temp_path.unlink(missing_ok=True)
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                temp_path.unlink(missing_ok=True)
                self.progress(f"[重试] 图片 {image.index} 第 {attempt}/3 次失败：{exc}")
        raise XhsDownloadError(f"图片 {image.index} 连续下载失败：{last_error}")

    @staticmethod
    def _write_summary(post: XhsPost, folder: Path) -> None:
        # 不保存 Cookie、图片临时签名 URL 或分享链接中的 xsec_token。
        data = {
            "note_id": post.note_id,
            "title": post.title,
            "description": post.description,
            "author": post.author,
            "image_count": len(post.images),
        }
        temp = folder / ".post.json.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, folder / "post.json")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="小红书图文下载实验")
    parser.add_argument("url", nargs="?", help="小红书帖子/分享链接")
    parser.add_argument("--output", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--cookies", type=Path, default=XHS_COOKIE_FILE)
    parser.add_argument("--proxy", default="")
    parser.add_argument("--login", action="store_true", help="先打开 Edge 登录并保存凭据")
    parser.add_argument("--inspect-only", action="store_true", help="只解析，不下载图片")
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
