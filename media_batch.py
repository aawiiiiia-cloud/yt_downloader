"""媒体批处理核心：扫描、筛选、图片查重与安全文件操作。

界面层放在 media_batch_ui.py。本模块不依赖 Tkinter，便于单元测试和以后复用。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif",
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts",
    ".mts", ".m2ts", ".3gp", ".wmv",
}

Progress = Callable[[str], None]
ItemProgress = Callable[[int, int, Path], None]


class OperationCancelled(RuntimeError):
    """用户主动取消。"""


@dataclass
class ScanOptions:
    recursive: bool = True
    check_image_resolution: bool = True
    image_min_width: int = 0
    image_min_height: int = 0
    check_video_resolution: bool = True
    video_min_width: int = 0
    video_min_height: int = 0
    check_video_bitrate: bool = True
    video_min_bitrate_kbps: int = 0
    detect_duplicates: bool = True
    dhash_distance: int = 4
    detect_black_bars: bool = False


@dataclass
class MediaRecord:
    path: Path
    kind: str
    size: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    codec: str = ""
    bitrate_kbps: int = 0
    bitrate_kind: str = ""
    bitrate_density: float = 0.0
    frame_count: int = 1
    sha256: str = ""
    perceptual_hash: int | None = None
    duplicate_status: str = "未检测"
    crop_suggestion: str = ""
    black_bar_status: str = "未检测"
    issues: list[str] = field(default_factory=list)
    review_issues: list[str] = field(default_factory=list)
    archived: bool = False

    @property
    def suspicious(self) -> bool:
        return bool(self.issues) and not self.archived

    @property
    def needs_review(self) -> bool:
        return bool(self.review_issues) and not self.archived

    def movable(self, include_review: bool = False) -> bool:
        return self.suspicious or (include_review and self.needs_review)

    @property
    def resolution(self) -> str:
        return f"{self.width}×{self.height}" if self.width and self.height else "未知"


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise OperationCancelled("操作已取消")


def _sha256(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        while chunk := src.read(1024 * 1024):
            _check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def _fraction(value: object) -> float:
    text = str(value or "0")
    try:
        if "/" in text:
            left, right = text.split("/", 1)
            denominator = float(right)
            return float(left) / denominator if denominator else 0.0
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _below_resolution(
    width: int, height: int, minimum_width: int, minimum_height: int
) -> bool:
    """宽高阈值自动适配横竖屏；只填一边时仍按对应尺寸判断。"""
    if minimum_width <= 0 and minimum_height <= 0:
        return False
    if minimum_width > 0 and minimum_height > 0:
        actual = sorted((width, height))
        minimum = sorted((minimum_width, minimum_height))
        return actual[0] < minimum[0] or actual[1] < minimum[1]
    return (
        (minimum_width > 0 and width < minimum_width)
        or (minimum_height > 0 and height < minimum_height)
    )


def discover_media(
    folder: Path,
    recursive: bool = True,
    exclude_dirs: Iterable[Path] = (),
) -> list[tuple[Path, str]]:
    folder = Path(folder)
    excluded = [Path(item).absolute() for item in exclude_dirs]
    iterator: Iterable[Path] = folder.rglob("*") if recursive else folder.glob("*")
    found: list[tuple[Path, str]] = []
    for path in iterator:
        if path.is_symlink() or not path.is_file():
            continue
        absolute = path.absolute()
        if any(absolute == item or absolute.is_relative_to(item) for item in excluded):
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            found.append((path, "图片"))
        elif suffix in VIDEO_EXTENSIONS:
            found.append((path, "视频"))
    return sorted(found, key=lambda item: str(item[0]).casefold())


def inspect_image(
    path: Path,
    options: ScanOptions,
    cancel: threading.Event | None = None,
) -> MediaRecord:
    from PIL import Image, ImageOps

    record = MediaRecord(path=path, kind="图片", size=path.stat().st_size)
    record.duplicate_status = "未发现" if options.detect_duplicates else "未检测"
    record.black_bar_status = "不适用"
    try:
        with Image.open(path) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            record.width, record.height = image.size
            record.frame_count = int(getattr(source, "n_frames", 1) or 1)
            if options.detect_duplicates:
                import dhash

                record.perceptual_hash = dhash.dhash_int(image)
        if options.detect_duplicates:
            record.sha256 = _sha256(path, cancel)
    except Exception as exc:  # Pillow 可在 verify/load 时发现截断或损坏
        record.duplicate_status = "检测失败" if options.detect_duplicates else "未检测"
        record.issues.append(f"图片无法读取：{exc}")
        return record

    if options.check_image_resolution and _below_resolution(
        record.width, record.height, options.image_min_width, options.image_min_height
    ):
        record.issues.append(
            f"分辨率低于 {options.image_min_width}×{options.image_min_height}"
        )
    if record.frame_count > 1:
        record.review_issues.append(
            f"包含 {record.frame_count} 帧/页，当前不生成裁剪副本"
        )
    return record


def _run_process(
    command: list[str],
    cancel: threading.Event | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise OperationCancelled("操作已取消")
        if time.monotonic() >= deadline:
            process.kill()
            process.communicate()
            raise TimeoutError(f"外部工具运行超过 {int(timeout)} 秒")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _parse_probe(path: Path, data: dict) -> MediaRecord:
    record = MediaRecord(path=path, kind="视频", size=path.stat().st_size)
    record.duplicate_status = "不适用"
    streams = data.get("streams") or []
    stream = next((
        item for item in streams
        if item.get("codec_type") == "video"
        and not (item.get("disposition") or {}).get("attached_pic")
    ), None)
    if not stream:
        record.black_bar_status = "无法检测"
        record.issues.append("没有可识别的视频轨道")
        return record
    record.width = int(stream.get("width") or 0)
    record.height = int(stream.get("height") or 0)
    rotation = _fraction((stream.get("tags") or {}).get("rotate"))
    for side_data in stream.get("side_data_list") or []:
        if side_data.get("rotation") is not None:
            rotation = _fraction(side_data.get("rotation"))
            break
    if round(abs(rotation)) % 180 == 90:
        record.width, record.height = record.height, record.width
    record.codec = str(stream.get("codec_name") or "")
    record.fps = _fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    fmt = data.get("format") or {}
    record.duration = _fraction(stream.get("duration") or fmt.get("duration"))
    stream_bitrate = _fraction(stream.get("bit_rate"))
    bitrate = stream_bitrate or _fraction(fmt.get("bit_rate"))
    if bitrate <= 0 and record.duration > 0:
        bitrate = record.size * 8 / record.duration
    record.bitrate_kbps = max(0, round(bitrate / 1000))
    record.bitrate_kind = "视频流" if stream_bitrate > 0 else "文件总"
    if record.width and record.height and record.fps > 0 and bitrate > 0:
        record.bitrate_density = bitrate / (record.width * record.height * record.fps)
    return record


def _codec_density_floor(codec: str) -> float:
    """返回保守的 bits/(pixel*frame) 下限，仅用于筛出需人工复核项。"""
    name = codec.casefold()
    if name in {"av1", "av01"}:
        return 0.018
    if name in {"hevc", "h265", "vp9"}:
        return 0.025
    if name in {"mpeg4", "vp8", "mpeg2video"}:
        return 0.055
    return 0.040  # h264 及未知编码采用偏保守阈值


def inspect_video(
    path: Path,
    options: ScanOptions,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    cancel: threading.Event | None = None,
) -> MediaRecord:
    needs_metadata = (
        options.check_video_resolution
        or options.check_video_bitrate
        or options.detect_black_bars
    )
    if not needs_metadata:
        record = MediaRecord(
            path=path, kind="视频", size=path.stat().st_size,
            duplicate_status="未发现" if options.detect_duplicates else "未检测",
            black_bar_status="未检测",
        )
        if options.detect_duplicates:
            record.sha256 = _sha256(path, cancel)
        return record

    try:
        result = _run_process([
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ], cancel, timeout=90)
        if result.returncode:
            raise RuntimeError((result.stderr or "ffprobe 解析失败").strip())
        record = _parse_probe(path, json.loads(result.stdout or "{}"))
    except OperationCancelled:
        raise
    except Exception as exc:
        return MediaRecord(
            path=path, kind="视频", size=path.stat().st_size,
            duplicate_status="检测失败" if options.detect_duplicates else "未检测",
            black_bar_status="检测失败" if options.detect_black_bars else "未检测",
            issues=[f"视频无法读取：{exc}"],
        )

    record.duplicate_status = "未发现" if options.detect_duplicates else "未检测"
    record.black_bar_status = "未检测" if not options.detect_black_bars else record.black_bar_status
    if options.detect_duplicates:
        try:
            record.sha256 = _sha256(path, cancel)
        except OperationCancelled:
            raise
        except OSError as exc:
            record.duplicate_status = "检测失败"
            record.issues.append(f"视频哈希计算失败：{exc}")

    if record.issues:
        return record

    if options.check_video_resolution and _below_resolution(
        record.width, record.height, options.video_min_width, options.video_min_height
    ):
        record.issues.append(
            f"分辨率低于 {options.video_min_width}×{options.video_min_height}"
        )
    if options.check_video_bitrate:
        # 固定 kbps 无法公平比较 720p、4K 和不同帧率。改用单位像素每帧码率，
        # 并按编码效率调整阈值。它能发现疑似过度压缩，但不能单独证明播放卡顿。
        if record.bitrate_density > 0:
            floor = _codec_density_floor(record.codec)
            if record.bitrate_density < floor:
                record.review_issues.append(
                    "单位像素码率偏低，疑似过度压缩"
                    f"（{record.bitrate_density:.3f} bpp/帧）"
                )
        else:
            record.review_issues.append("无法取得可靠码率/帧率，建议人工复核")
        if 0 < record.fps < 20:
            record.review_issues.append(
                f"帧率仅 {record.fps:.2f} fps，观感可能不流畅"
            )
    if options.detect_black_bars and record.width and record.height:
        record.black_bar_status = "未发现"
        try:
            suggestion = detect_black_bars(path, record, ffmpeg, cancel)
            if suggestion:
                record.crop_suggestion = suggestion
                margins = format_crop_margins(record, suggestion)
                record.black_bar_status = margins
                record.review_issues.append(f"疑似固定黑边（{margins}）")
        except OperationCancelled:
            raise
        except Exception as exc:
            record.black_bar_status = "检测失败"
            record.review_issues.append(f"黑边检测失败：{exc}")
    return record


_CROP_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def format_crop_margins(record: MediaRecord, suggestion: str) -> str:
    """把 ffmpeg crop=宽:高:X:Y 转成用户更容易理解的四边黑边宽度。"""
    match = _CROP_RE.fullmatch(suggestion.strip())
    if not match or not record.width or not record.height:
        return suggestion
    crop_width, crop_height, left, top = map(int, match.groups())
    right = max(0, record.width - crop_width - left)
    bottom = max(0, record.height - crop_height - top)
    return f"上 {top} · 下 {bottom} · 左 {left} · 右 {right} px"


def detect_black_bars(
    path: Path,
    record: MediaRecord,
    ffmpeg: str = "ffmpeg",
    cancel: threading.Event | None = None,
) -> str:
    """抽样检测固定黑边；只返回建议，不修改视频。"""
    duration = max(record.duration, 1.0)
    suggestions: list[str] = []
    for ratio in (0.10, 0.50, 0.90):
        _check_cancel(cancel)
        start = max(0.0, min(duration - 1.0, duration * ratio))
        result = _run_process([
            ffmpeg, "-hide_banner", "-ss", f"{start:.3f}", "-i", str(path),
            "-t", "2", "-vf", "cropdetect=limit=24:round=2:reset=0",
            "-an", "-f", "null", "-",
        ], cancel, timeout=45)
        if result.returncode:
            detail = (result.stderr or "ffmpeg 检测失败").strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "ffmpeg 检测失败")
        matches = _CROP_RE.findall(result.stderr or "")
        if matches:
            w, h, x, y = matches[-1]
            if int(w) < record.width or int(h) < record.height:
                # cropdetect 在相邻帧可能浮动 2 像素，按 4 像素归一化再投票。
                values = [max(0, round(int(value) / 4) * 4) for value in (w, h, x, y)]
                suggestions.append("crop=" + ":".join(map(str, values)))
    if not suggestions:
        return ""
    suggestion, count = Counter(suggestions).most_common(1)[0]
    return suggestion if count >= 2 else ""


def _quality_key(record: MediaRecord) -> tuple[int, int, str]:
    return (record.width * record.height, record.size, str(record.path).casefold())


def mark_image_duplicates(
    records: list[MediaRecord],
    distance: int = 4,
    cancel: threading.Event | None = None,
) -> None:
    """全部媒体按 SHA-256 查完全重复；图片再用 dHash 查视觉近似。"""
    exact_groups: dict[tuple[str, str], list[MediaRecord]] = defaultdict(list)
    for item in records:
        if item.sha256:
            exact_groups[(item.kind, item.sha256)].append(item)
    exact_losers: set[Path] = set()
    for group in exact_groups.values():
        _check_cancel(cancel)
        if len(group) < 2:
            continue
        keep = max(group, key=_quality_key)
        unit = "张" if keep.kind == "图片" else "个"
        keep.duplicate_status = f"完全重复组保留项（共 {len(group)} {unit}）"
        for item in group:
            if item is not keep:
                item.duplicate_status = f"完全重复 → 保留 {keep.path.name}"
                item.issues.append(f"完全重复（建议保留：{keep.path.name}）")
                exact_losers.add(item.path)

    if distance < 0:
        return

    import dhash

    images = [
        item for item in records
        if item.kind == "图片" and item.perceptual_hash is not None
    ]

    # 同一宽高比附近比较，避免大目录无意义地全量两两比较。
    buckets: dict[int, list[MediaRecord]] = defaultdict(list)
    for item in images:
        if item.height:
            buckets[round(item.width / item.height * 20)].append(item)
    for bucket_key, bucket in buckets.items():
        # 同桶两两比较，并额外比较相邻宽高比桶，减少轻微裁剪造成的漏报。
        pair_groups = (
            (
                (left, right)
                for index, left in enumerate(bucket)
                for right in bucket[index + 1:]
            ),
            (
                (left, right)
                for left in bucket
                for right in buckets.get(bucket_key + 1, ())
            ),
        )
        for comparisons in pair_groups:
            for left, right in comparisons:
                _check_cancel(cancel)
                if left.sha256 and left.sha256 == right.sha256:
                    continue
                bits = dhash.get_num_bits_different(
                    int(left.perceptual_hash), int(right.perceptual_hash)
                )
                if bits > distance:
                    continue
                keep, loser = sorted((left, right), key=_quality_key, reverse=True)
                if loser.path in exact_losers:
                    continue
                if keep.duplicate_status == "未发现":
                    keep.duplicate_status = "近似组保留项"
                loser.duplicate_status = f"疑似相似（差异 {bits}）"
                message = f"疑似相似图，差异 {bits}（建议保留：{keep.path.name}）"
                if message not in loser.review_issues:
                    loser.review_issues.append(message)


def scan_media(
    folder: Path,
    options: ScanOptions,
    progress: Progress = lambda _message: None,
    item_progress: ItemProgress = lambda _index, _total, _path: None,
    cancel: threading.Event | None = None,
    ffprobe: str = "ffprobe",
    ffmpeg: str = "ffmpeg",
    exclude_dirs: Iterable[Path] = (),
) -> list[MediaRecord]:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"文件夹不存在：{folder}")
    scan_images = options.check_image_resolution or options.detect_duplicates
    scan_videos = (
        options.check_video_resolution
        or options.check_video_bitrate
        or options.detect_black_bars
        or options.detect_duplicates
    )
    if not scan_images and not scan_videos:
        raise ValueError("至少选择一项检测功能")
    files = [
        item for item in discover_media(folder, options.recursive, exclude_dirs)
        if (item[1] == "图片" and scan_images) or (item[1] == "视频" and scan_videos)
    ]
    progress(f"找到 {len(files)} 个媒体文件")
    records: list[MediaRecord] = []
    for index, (path, kind) in enumerate(files, 1):
        _check_cancel(cancel)
        item_progress(index, len(files), path)
        if kind == "图片":
            record = inspect_image(path, options, cancel)
        else:
            record = inspect_video(path, options, ffprobe, ffmpeg, cancel)
        records.append(record)
    if options.detect_duplicates:
        _check_cancel(cancel)
        progress("正在比对图片和视频哈希...")
        mark_image_duplicates(records, options.dhash_distance, cancel)
    return records


def _unique_destination(folder: Path, name: str) -> Path:
    candidate = folder / name
    counter = 1
    while candidate.exists():
        candidate = folder / f"{Path(name).stem} ({counter}){Path(name).suffix}"
        counter += 1
    return candidate


def safe_move(
    source: Path,
    destination_folder: Path,
    cancel: threading.Event | None = None,
) -> Path:
    """不覆盖同名文件；跨盘复制完成并校验后才删除源文件。"""
    if source.is_symlink():
        raise ValueError(f"为避免影响目录外文件，不移动符号链接：{source}")
    source = Path(os.path.abspath(source))
    destination_folder = destination_folder.resolve()
    if source.parent == destination_folder:
        raise ValueError(f"源文件已在目标目录中：{source}")
    _check_cancel(cancel)
    destination_folder.mkdir(parents=True, exist_ok=True)
    target = _unique_destination(destination_folder, source.name)
    _check_cancel(cancel)
    try:
        os.rename(source, target)
        return target
    except OSError:
        pass

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        with source.open("rb") as src, temporary.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                _check_cancel(cancel)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(source, temporary)
        _check_cancel(cancel)
        if temporary.stat().st_size != source.stat().st_size:
            raise OSError("复制后的文件大小不一致")
        if _sha256(temporary, cancel) != _sha256(source, cancel):
            raise OSError("复制后的文件校验失败")
        os.rename(temporary, target)
        source.unlink()
        return target
    finally:
        temporary.unlink(missing_ok=True)


def move_suspicious(
    records: Iterable[MediaRecord],
    destination: Path,
    progress: Progress = lambda _message: None,
    cancel: threading.Event | None = None,
    include_review: bool = False,
) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    root = Path(destination)
    for record in records:
        if not record.movable(include_review) or not record.path.exists():
            continue
        _check_cancel(cancel)
        category = "图片" if record.kind == "图片" else "视频"
        old = record.path
        new = safe_move(old, root / category, cancel)
        record.path = new
        record.archived = True
        moved.append((old, new))
        progress(f"已移动：{old.name} → {new}")
    return moved


_TEMPLATE_FIELD_RE = re.compile(r"\{(index(?::0?\d+d)?|name|width|height|ext)\}")


def normalize_rename_template(template: str) -> str:
    """普通文字按“前缀”处理；含占位符时按高级规则处理。"""
    template = template.strip()
    if template and not _TEMPLATE_FIELD_RE.search(template):
        return template + "_{index:04d}"
    return template


def _truncate_windows_component(stem: str, max_units: int = 235) -> str:
    """按 UTF-16 单元截断，给扩展名和系统内部操作保留余量。"""
    result: list[str] = []
    used = 0
    for char in stem:
        units = len(char.encode("utf-16-le")) // 2
        if used + units > max_units:
            break
        result.append(char)
        used += units
    return "".join(result).rstrip(". ")


def render_rename(template: str, record: MediaRecord, index: int) -> str:
    """支持 {index}/{index:04d}/{name}/{width}/{height}/{ext}。"""
    values = {
        "name": record.path.stem,
        "width": str(record.width),
        "height": str(record.height),
        "ext": record.path.suffix.lstrip("."),
    }

    def replace(match: re.Match[str]) -> str:
        field = match.group(1)
        if field.startswith("index"):
            if ":" in field:
                return format(index, field.split(":", 1)[1])
            return str(index)
        return values[field]

    rendered = _TEMPLATE_FIELD_RE.sub(replace, template).strip()
    rendered = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", rendered).rstrip(". ")
    if not rendered:
        raise ValueError("重命名模板生成了空文件名")
    rendered_suffix = Path(rendered).suffix
    # 重命名不等于格式转换：即使模板误写 .png，也始终保留真实扩展名。
    if rendered_suffix.lower() in IMAGE_EXTENSIONS:
        rendered = rendered[:-len(rendered_suffix)] + record.path.suffix
    elif not rendered.lower().endswith(record.path.suffix.lower()):
        rendered += record.path.suffix
    suffix = record.path.suffix
    stem = rendered[:-len(suffix)] if suffix and rendered.lower().endswith(suffix.lower()) else rendered
    stem = _truncate_windows_component(stem)
    if not stem:
        raise ValueError("重命名模板生成了空文件名")
    return stem + suffix


def batch_rename_images(
    records: Iterable[MediaRecord],
    template: str,
    progress: Progress = lambda _message: None,
    cancel: threading.Event | None = None,
) -> list[tuple[Path, Path]]:
    images = [item for item in records if item.kind == "图片" and item.path.exists()]
    template = normalize_rename_template(template)
    plans: list[tuple[MediaRecord, Path]] = []
    targets: set[str] = set()
    source_paths = {str(item.path.resolve()).casefold() for item in images}
    for index, record in enumerate(images, 1):
        target = record.path.with_name(render_rename(template, record, index))
        key = str(target.resolve()).casefold()
        if key in targets or (target.exists() and key not in source_paths):
            raise FileExistsError(f"目标文件名冲突：{target}")
        targets.add(key)
        plans.append((record, target))

    staged: list[dict[str, object]] = []
    try:
        for record, target in plans:
            _check_cancel(cancel)
            if record.path.resolve() == target.resolve():
                continue
            # 不拼接原文件名。原名本身接近 Windows 255 字符上限时，旧写法会
            # 让临时文件名超长并报“文件名、目录名或卷标语法不正确”。
            temporary = record.path.with_name(f".rename-{uuid.uuid4().hex}.tmp")
            old = record.path
            os.rename(old, temporary)
            staged.append({
                "record": record, "old": old, "temporary": temporary,
                "target": target, "state": "temporary",
            })
        changed: list[tuple[Path, Path]] = []
        for item in staged:
            _check_cancel(cancel)
            record = item["record"]
            old = item["old"]
            temporary = item["temporary"]
            target = item["target"]
            assert isinstance(record, MediaRecord)
            assert isinstance(old, Path) and isinstance(temporary, Path) and isinstance(target, Path)
            os.rename(temporary, target)
            item["state"] = "target"
            changed.append((old, target))
            progress(f"已重命名：{old.name} → {target.name}")
        for item in staged:
            record, target = item["record"], item["target"]
            assert isinstance(record, MediaRecord) and isinstance(target, Path)
            record.path = target
        return changed
    except Exception as original_error:
        rollback_errors: list[str] = []
        # 第一步：所有已提交目标退回它们自己的唯一临时名，先解除名称交换链。
        for item in reversed(staged):
            if item["state"] != "target":
                continue
            target, temporary = item["target"], item["temporary"]
            assert isinstance(target, Path) and isinstance(temporary, Path)
            try:
                os.rename(target, temporary)
                item["state"] = "temporary"
            except OSError as exc:
                rollback_errors.append(f"{target} → {temporary}: {exc}")
        # 第二步：逐个恢复原名；单个失败不能阻断其他文件的恢复。
        for item in reversed(staged):
            if item["state"] != "temporary":
                continue
            old, temporary = item["old"], item["temporary"]
            assert isinstance(old, Path) and isinstance(temporary, Path)
            try:
                os.rename(temporary, old)
                item["state"] = "old"
            except OSError as exc:
                rollback_errors.append(f"{temporary} → {old}: {exc}")
        if rollback_errors:
            detail = "\n".join(rollback_errors)
            raise RuntimeError(
                f"重命名失败，且有文件未能自动恢复。请勿继续操作，按下列路径恢复：\n{detail}"
            ) from original_error
        raise


def center_crop_box(width: int, height: int, ratio_text: str) -> tuple[int, int, int, int]:
    left_ratio, right_ratio = ratio_text.split(":", 1)
    ratio = float(left_ratio) / float(right_ratio)
    current = width / height
    if current > ratio:
        crop_width, crop_height = round(height * ratio), height
    else:
        crop_width, crop_height = width, round(width / ratio)
    left = max(0, (width - crop_width) // 2)
    top = max(0, (height - crop_height) // 2)
    return left, top, left + crop_width, top + crop_height


def batch_crop_images(
    records: Iterable[MediaRecord],
    ratio: str,
    output_folder: Path,
    progress: Progress = lambda _message: None,
    cancel: threading.Event | None = None,
) -> list[Path]:
    from PIL import Image, ImageOps

    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    try:
        for record in records:
            if record.kind != "图片" or not record.path.exists():
                continue
            if record.frame_count > 1:
                progress(f"已跳过多帧图片：{record.path.name}")
                continue
            _check_cancel(cancel)
            target = _unique_destination(output_folder, record.path.name)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            try:
                with Image.open(record.path) as source:
                    image = ImageOps.exif_transpose(source)
                    cropped = image.crop(center_crop_box(*image.size, ratio))
                    save_options = {}
                    exif = cropped.getexif()
                    if exif:
                        save_options["exif"] = exif.tobytes()
                    if source.info.get("icc_profile"):
                        save_options["icc_profile"] = source.info["icc_profile"]
                    if target.suffix.lower() in {".jpg", ".jpeg"}:
                        if cropped.mode not in {"RGB", "L"}:
                            cropped = cropped.convert("RGB")
                        save_options.update(quality=95, subsampling=0)
                    cropped.save(temporary, format=source.format, **save_options)
                os.replace(temporary, target)
                outputs.append(target)
                progress(f"已裁剪：{record.path.name} → {target}")
            finally:
                temporary.unlink(missing_ok=True)
        return outputs
    except Exception:
        # 本次批量任务要么完整成功，要么不留下半批副本。
        for path in outputs:
            path.unlink(missing_ok=True)
        raise
