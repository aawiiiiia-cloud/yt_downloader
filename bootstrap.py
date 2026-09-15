"""环境自检 + 自动安装(源码版专用)。

在 Tkinter GUI 启动之前运行,此时没有 GUI 日志窗,所有输出走控制台(progress 回调)。
打包版不 import 本模块。

只负责三件事,全部装到用户目录(~/.yt_dlp_tools/),无需管理员权限:
1. yt-dlp + yt-dlp-ejs + PO token 插件  → pip install(缺才装,失败回退清华镜像)
2. Node.js 22+ LTS 便携版              → 缺才下载解压到 ~/.yt_dlp_tools/node/
3. ffmpeg 便携版                        → 缺才下载解压到 ~/.yt_dlp_tools/ffmpeg/

安装后把目录临时加进当前进程 PATH,yt-dlp 立即可用,无需重启。
"""

from __future__ import annotations

import json
import hashlib
import importlib.metadata
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

TOOLS_DIR = Path.home() / ".yt_dlp_tools"
NODE_DIR = TOOLS_DIR / "node"
FFMPEG_DIR = TOOLS_DIR / "ffmpeg"
PROJECT_DIR = Path(__file__).resolve().parent

# nodejs.org API 不可达时的兜底版本(22 LTS)
NODE_FALLBACK = "22.14.0"
# 墙内下载 Node 的镜像
NODE_MIRROR = "https://npmmirror.com/mirrors/node"
PINNED_NODE_VERSION = "22.14.0"
PINNED_NODE_ARCHIVE_SHA256 = "55b639295920b219bb2acbcfa00f90393a2789095b7323f79475c9f34795f217"
PINNED_NODE_EXE_SHA256 = "33b1bc1a8aca11fd5a4f2699e51019c63c0af30cf437701d07af69be7706771b"
# GitHub 大文件下载被限速时使用的代理镜像(按顺序尝试)
GITHUB_PROXIES = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
]

Progress = Callable[[str], None]

PYTHON_REQUIREMENTS = {
    "yt-dlp": "2026.8.19",
    "yt-dlp-ejs": "0.8.0",
    "bgutil-ytdlp-pot-provider": "1.3.2",
    "websocket-client": "1.9.2",
    "Pillow": "11.3.0",
    "dhash": "1.4",
}


def bootstrap(progress: Progress = print) -> dict[str, bool]:
    """检测并自动安装缺失环境及 PO Token 生成服务。"""
    progress("== 环境自检与自动安装 ==")
    _reuse_project_tools(progress)
    result = {
        "ytdlp": _ensure_ytdlp(progress),
        "node": _ensure_node(progress),
        "ffmpeg": _ensure_ffmpeg(progress),
    }
    result["pot_provider"] = _ensure_pot_provider(progress)
    progress("== 环境检查结束 ==")
    return result


def _reuse_project_tools(progress: Progress) -> list[Path]:
    """源码版优先复用 build_cache/ 和旧 dist/ 中已有的便携工具。"""
    candidates = (
        PROJECT_DIR / "dist" / "yt_dlp_gui" / "tools",
        PROJECT_DIR / "build_cache",
    )
    reused: list[Path] = []
    # _add_to_path 会前插；先处理低优先级 dist，让 build_cache 最终排在最前。
    for candidate in candidates:
        has_node = (candidate / "node.exe").is_file()
        has_ffmpeg_pair = all(
            (candidate / name).is_file() for name in ("ffmpeg.exe", "ffprobe.exe")
        )
        if not (has_node or has_ffmpeg_pair):
            continue
        _add_to_path(candidate)
        reused.append(candidate)
        labels = []
        if has_node:
            labels.append("Node.js")
        if has_ffmpeg_pair:
            labels.append("ffmpeg/ffprobe")
        progress(f"[信息] 复用项目便携工具 ({', '.join(labels)}): {candidate}")
    return reused


def _ensure_pot_provider(progress: Progress) -> bool:
    """源码版缺少 provider 时安装到用户工具目录；已有 dist 则直接复用。"""
    try:
        from pot_provider import find_provider

        if find_provider() is not None:
            progress("[信息] PO Token 本地生成服务文件已就绪")
            return True
        import provider_setup

        provider_setup.ensure_provider(
            TOOLS_DIR / "bgutil-provider",
            TOOLS_DIR / "provider_cache",
            progress,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        progress(f"[警告] PO Token 生成服务准备失败: {exc}")
        progress("[警告] 普通下载仍可使用，但 YouTube 4K/高码率格式可能不可用")
        return False


# ---------- yt-dlp ----------

def _ensure_ytdlp(progress: Progress) -> bool:
    missing_or_old: list[str] = []
    for package, minimum in PYTHON_REQUIREMENTS.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed is None or (minimum and not _version_at_least(installed, minimum)):
            missing_or_old.append(f"{package}>={minimum}" if minimum else package)

    if missing_or_old:
        progress("[安装] 补全/更新 Python 依赖: " + ", ".join(missing_or_old))
        if not _pip_install(*missing_or_old):
            progress(
                "[错误] 自动安装依赖失败。请手动执行:\n"
                "    python -m pip install -U -r requirements.txt"
            )
            return False

    try:
        import yt_dlp
        import websocket  # noqa: F401
        import yt_dlp_ejs  # noqa: F401
        import yt_dlp_plugins.extractor.getpot_bgutil  # noqa: F401
        import PIL  # noqa: F401
        import dhash  # noqa: F401
    except ImportError as exc:
        progress(f"[错误] Python 依赖安装后仍无法导入: {exc}")
        return False
    progress(f"[信息] Python 依赖已就绪,yt-dlp: {yt_dlp.version.__version__}")
    return True


def _version_at_least(installed: str, minimum: str) -> bool:
    """对本项目所用的数字版本做宽松比较，不额外依赖 packaging。"""
    def parts(value: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", value)[:4])

    current = parts(installed)
    required = parts(minimum)
    length = max(len(current), len(required))
    return current + (0,) * (length - len(current)) >= required + (0,) * (length - len(required))


def _pip_install(*pkgs: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "-U", *pkgs]
    try:
        subprocess.check_call(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        # 默认源失败 → 清华镜像重试(国内网络友好)
        try:
            subprocess.check_call(
                cmd + ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            return False


# ---------- Node.js ----------

def _ensure_node(progress: Progress) -> bool:
    node = shutil.which("node")
    if node and _node_is_usable(Path(node)):
        progress(f"[信息] Node.js 22+ 已就绪: {node}")
        return True
    if node:
        progress(f"[警告] 当前 Node.js 版本过低或无法运行: {node}")

    node_exe = NODE_DIR / "node.exe"
    if node_exe.exists() and _node_is_usable(node_exe):
        _add_to_path(NODE_DIR)
        progress(f"[信息] 使用便携 Node.js: {node_exe}")
        return True

    progress("[安装] 未检测到 Node.js,下载便携版(22+ LTS)...")
    # 固定到已核对官方签名 SHASUMS 的版本，镜像只负责传输，不能替换内容。
    version = PINNED_NODE_VERSION
    progress(f"[安装] 目标版本: v{version}")
    zip_path = TOOLS_DIR / f"node-v{version}-win-x64.zip"

    url = f"https://nodejs.org/dist/v{version}/node-v{version}-win-x64.zip"
    if not _download_with_progress(url, zip_path, progress):
        mirror = f"{NODE_MIRROR}/v{version}/node-v{version}-win-x64.zip"
        progress("[安装] 官方源失败,改用 npmmirror 镜像(断点续传)...")
        if not _download_with_progress(mirror, zip_path, progress):
            progress("[错误] Node.js 下载失败,请手动安装 Node.js 22+ 后重试")
            return False

    actual_archive_hash = _sha256_file(zip_path)
    if actual_archive_hash.lower() != PINNED_NODE_ARCHIVE_SHA256:
        zip_path.unlink(missing_ok=True)
        progress("[错误] Node.js 下载文件 SHA-256 不匹配，已删除并停止安装")
        return False

    try:
        # 完整解压 Node/npm，后续首次编译 PO provider 可直接复用同一份下载，
        # 避免在慢速网络上为了 npm 再下载一次相同压缩包。
        from provider_setup import extract_node_runtime

        extract_node_runtime(zip_path, NODE_DIR)
    except Exception as exc:  # noqa: BLE001
        progress(f"[错误] 解压 Node.js 失败: {exc}")
        return False
    finally:
        zip_path.unlink(missing_ok=True)

    if _sha256_file(NODE_DIR / "node.exe").lower() != PINNED_NODE_EXE_SHA256:
        progress("[错误] 解压后的 node.exe 安全摘要不匹配，拒绝使用")
        shutil.rmtree(NODE_DIR, ignore_errors=True)
        return False
    _add_to_path(NODE_DIR)
    if not _node_is_usable(NODE_DIR / "node.exe"):
        progress("[错误] Node.js 解压完成但无法运行或版本低于 22")
        return False
    progress(f"[信息] Node.js 便携版就绪: {NODE_DIR / 'node.exe'}")
    return True


def _latest_lts_node_version() -> str:
    """从 nodejs.org 官方 index.json 取最新 LTS(≥22)。失败返回兜底版本。"""
    try:
        req = urllib.request.Request(
            "https://nodejs.org/dist/index.json",
            headers={"User-Agent": "yt-dlp-gui-bootstrap"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for entry in data:
            lts = entry.get("lts")
            major = int(entry.get("version", "v0").lstrip("v").split(".")[0])
            if lts and major >= 22:
                return entry["version"].lstrip("v")
    except Exception:  # noqa: BLE001
        pass
    return NODE_FALLBACK


# ---------- ffmpeg ----------

def _ensure_ffmpeg(progress: Progress) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe and _executable_works(Path(ffmpeg), "-version") and _executable_works(Path(ffprobe), "-version"):
        progress(f"[信息] ffmpeg 已就绪: {ffmpeg}")
        return True

    ffmpeg_exe = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe_exe = FFMPEG_DIR / "ffprobe.exe"
    if (
        ffmpeg_exe.exists() and ffprobe_exe.exists()
        and _executable_works(ffmpeg_exe, "-version")
        and _executable_works(ffprobe_exe, "-version")
    ):
        _add_to_path(FFMPEG_DIR)
        progress(f"[信息] 使用便携 ffmpeg: {ffmpeg_exe}")
        return True

    progress("[安装] 未检测到 ffmpeg,下载便携版...")

    zip_path = TOOLS_DIR / "ffmpeg.zip"
    if not _download_ffmpeg_zip(zip_path, progress, require_hash=True):
        progress("[错误] ffmpeg 下载失败,请手动安装后重试")
        return False

    try:
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            bad_member = zf.testzip()
            if bad_member:
                raise zipfile.BadZipFile(f"压缩包损坏: {bad_member}")
            names = set(zf.namelist())
            for target in ("ffmpeg.exe", "ffprobe.exe"):
                member = next(m for m in names if m.endswith(f"/bin/{target}"))
                with zf.open(member) as src, open(FFMPEG_DIR / target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception as exc:  # noqa: BLE001
        progress(f"[错误] 解压 ffmpeg 失败: {exc}")
        return False
    finally:
        zip_path.unlink(missing_ok=True)

    _add_to_path(FFMPEG_DIR)
    if not (_executable_works(FFMPEG_DIR / "ffmpeg.exe", "-version") and _executable_works(FFMPEG_DIR / "ffprobe.exe", "-version")):
        progress("[错误] ffmpeg/ffprobe 解压完成但无法运行")
        return False
    progress(f"[信息] ffmpeg 便携版就绪: {FFMPEG_DIR}")
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ffmpeg_download_info() -> tuple[str, str | None] | None:
    """返回 BtbN 最新 win64-gpl.zip 的直链及 GitHub SHA-256 摘要。

    注意:GitHub 不支持 /releases/latest/download/ 直链,必须先查 API。
    """
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest",
            headers={
                "User-Agent": "yt-dlp-gui-bootstrap",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for asset in data.get("assets", []):
            if asset.get("name") == "ffmpeg-master-latest-win64-gpl.zip":
                url = asset.get("browser_download_url")
                raw_digest = str(asset.get("digest") or "")
                digest = raw_digest.removeprefix("sha256:") or None
                return (url, digest) if url else None
    except Exception:  # noqa: BLE001
        pass
    return None


def _ffmpeg_download_url() -> str | None:
    """兼容旧调用方：只返回 BtbN 直链。"""
    info = _ffmpeg_download_info()
    return info[0] if info else None


def _download_ffmpeg_zip(
    dest: Path, progress: Progress, require_hash: bool = False,
) -> bool:
    """多源下载 ffmpeg 压缩包。

    顺序:gyan.dev(墙内快,~110MB) → BtbN 直连 → BtbN 走 GitHub 代理镜像。

    新策略:只要源还在稳定传数据就让它下完(卡死检测,非固定超时);
    同源失败先保留断点重试一次;换源前才删断点(不同源构建内容不同)。
    """
    btb_info = _ffmpeg_download_info()
    candidates: list[tuple[str, str, str | None]] = []
    # 源码版保留国内较快的 gyan.dev；正式 build.py 会要求可信摘要，
    # 此时只接受 GitHub API 明确返回 digest 的 BtbN 资产。
    if not require_hash:
        candidates.append((
            "gyan.dev",
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            None,
        ))
    if btb_info:
        btb_url, btb_digest = btb_info
        if require_hash and not btb_digest:
            progress("[错误] GitHub 未提供 ffmpeg 资产 SHA-256，正式构建拒绝未校验下载")
            return False
        candidates.append(("BtbN", btb_url, btb_digest))
        for prox in GITHUB_PROXIES:
            candidates.append((
                prox.split("//")[1].split("/")[0], prox + btb_url, btb_digest,
            ))
    for name, url, expected_hash in candidates:
        progress(f"[下载] ffmpeg 源: {name}")
        if _download_with_progress(url, dest, progress):
            if not expected_hash or _sha256_file(dest).lower() == expected_hash.lower():
                if expected_hash:
                    progress("[安全] ffmpeg 压缩包 SHA-256 校验通过")
                return True
            progress(f"[错误] {name} 返回的 ffmpeg 压缩包 SHA-256 不匹配")
            dest.unlink(missing_ok=True)
        # 同源重试一次:断点续传,不浪费已下部分(应对临时断网)
        progress(f"[下载] {name} 中断,保留断点重试一次...")
        if _download_with_progress(url, dest, progress):
            if not expected_hash or _sha256_file(dest).lower() == expected_hash.lower():
                if expected_hash:
                    progress("[安全] ffmpeg 压缩包 SHA-256 校验通过")
                return True
            progress(f"[错误] {name} 重试后 SHA-256 仍不匹配")
        progress(f"[下载] {name} 连续失败,换下一个源")
        dest.unlink(missing_ok=True)
    return False


# ---------- 通用 ----------

def _download_with_progress(
    url: str, dest: Path, progress: Progress, chunk: int = 65536,
    stall_seconds: float = 60, timeout_seconds: float | None = None,
) -> bool:
    """下载文件到 dest,按块打印百分比进度。

    策略(解决"慢速下载被固定超时切源"):
    - 卡死检测:只要还在收到字节就继续下,连续 stall_seconds 秒无数据才放弃。
      墙内慢速但稳定的下载不再被 300s/240s 硬切掉。
    - 断点续传:dest 已有部分内容时发 Range 请求接着下(服务器不支持则整文件重下),
      中断重试只补剩余部分。
    - 默认不设总时长上限；慢但持续有数据就继续。调用方可选传绝对上限。
    """
    import time as _time

    start = _time.monotonic()
    headers = {"User-Agent": "yt-dlp-gui-bootstrap"}
    existing = dest.stat().st_size if dest.exists() else 0
    if existing:
        headers["Range"] = f"bytes={existing}-"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers=headers)
        # socket 读取超时就是“连续无数据”的可靠判据；原实现每次收到数据后
        # 立即刷新 last_byte 再检查，条件永远不可能成立。
        with urllib.request.urlopen(req, timeout=stall_seconds) as resp:
            if getattr(resp, "status", 200) == 206:
                # 服务器支持断点：必须确认返回范围确实从本地断点开始。
                content_range = resp.headers.get("Content-Range", "")
                range_match = re.fullmatch(
                    r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range.strip(), re.I,
                )
                if not range_match:
                    progress(f"[错误] {dest.name} 的 Content-Range 无效，保留断点")
                    return False
                range_start = int(range_match.group(1))
                range_end = int(range_match.group(2))
                if range_start != existing or range_end < range_start:
                    progress(
                        f"[错误] {dest.name} 服务端返回错误断点 "
                        f"{range_start}，期望 {existing}；将从零重试"
                    )
                    dest.unlink(missing_ok=True)
                    return _download_with_progress(
                        url, dest, progress, chunk, stall_seconds, timeout_seconds,
                    )
                remain = int(resp.headers.get("Content-Length", 0) or 0)
                if remain and remain != range_end - range_start + 1:
                    progress(f"[错误] {dest.name} 的断点响应长度不一致，保留断点")
                    return False
                declared_total = range_match.group(3)
                total = int(declared_total) if declared_total != "*" else existing + remain
                got, mode = existing, "ab"
            else:
                # 服务器不认 Range:整文件重下
                total = int(resp.headers.get("Content-Length", 0) or 0)
                got, mode = 0, "wb"
            with open(dest, mode) as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    if total:
                        progress(f"[下载] {dest.name}  {got / total * 100:.0f}%")
                    if timeout_seconds is not None and _time.monotonic() - start > timeout_seconds:
                        progress(
                            f"[下载] {dest.name} 超过 {int(timeout_seconds // 60)} 分钟,"
                            "保留断点"
                        )
                        return False
            if total and got != total:
                progress(
                    f"[错误] {dest.name} 下载不完整: "
                    f"应为 {_fmt_size(total)},实际 {_fmt_size(got)},保留断点"
                )
                return False
            if got <= 0:
                progress(f"[错误] {dest.name} 未收到任何数据")
                return False
            if not total:
                progress(f"[下载] {dest.name}  {_fmt_size(got)}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 416:
            # 只有服务端明确给出的总大小与本地完全一致，才能视为完整。
            match = re.search(r"\*/(\d+)", e.headers.get("Content-Range", ""))
            remote_size = int(match.group(1)) if match else None
            local_size = dest.stat().st_size if dest.exists() else 0
            if remote_size is not None and local_size == remote_size:
                progress(f"[下载] {dest.name} 已完整,跳过")
                return True
            progress(f"[错误] {dest.name} 断点范围无效，无法确认文件完整")
            return False
        progress(f"[错误] 下载 {url} 失败: {e}")
        return False
    except (socket.timeout, TimeoutError):
        progress(
            f"[下载] {dest.name} 连续 {int(stall_seconds)}s 无数据，保留断点"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        progress(f"[错误] 下载 {url} 失败: {exc}")
        return False


def _add_to_path(directory: Path) -> None:
    """把目录临时加进当前进程 PATH(不写系统环境)。"""
    d = str(directory)
    if d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _executable_works(path: Path, *args: str) -> bool:
    try:
        completed = subprocess.run(
            [str(path), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _node_is_usable(path: Path) -> bool:
    try:
        completed = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True,
            timeout=10, check=False,
        )
        match = re.search(r"v?(\d+)", completed.stdout)
        return completed.returncode == 0 and match is not None and int(match.group(1)) >= 22
    except (OSError, subprocess.SubprocessError):
        return False


def _fmt_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


if __name__ == "__main__":
    bootstrap()
