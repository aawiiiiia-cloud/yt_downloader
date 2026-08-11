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
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

TOOLS_DIR = Path.home() / ".yt_dlp_tools"
NODE_DIR = TOOLS_DIR / "node"
FFMPEG_DIR = TOOLS_DIR / "ffmpeg"

# nodejs.org API 不可达时的兜底版本(22 LTS)
NODE_FALLBACK = "22.14.0"
# 墙内下载 Node 的镜像
NODE_MIRROR = "https://npmmirror.com/mirrors/node"
# GitHub 大文件下载被限速时使用的代理镜像(按顺序尝试)
GITHUB_PROXIES = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
]

Progress = Callable[[str], None]


def bootstrap(progress: Progress = print) -> dict[str, bool]:
    """检测并自动安装缺失环境。返回 {"ytdlp","node","ffmpeg"} 各是否可用。"""
    progress("== 环境自检与自动安装 ==")
    result = {
        "ytdlp": _ensure_ytdlp(progress),
        "node": _ensure_node(progress),
        "ffmpeg": _ensure_ffmpeg(progress),
    }
    progress("== 环境检查结束 ==")
    return result


# ---------- yt-dlp ----------

def _ensure_ytdlp(progress: Progress) -> bool:
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None

    if yt_dlp is None:
        progress("[安装] 未检测到 yt-dlp,开始 pip 安装...")
        if not _pip_install("yt-dlp", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"):
            progress(
                "[错误] 自动安装 yt-dlp 失败。请手动执行:\n"
                "    python -m pip install -U yt-dlp yt-dlp-ejs bgutil-ytdlp-pot-provider"
            )
            return False
        import yt_dlp
        progress(f"[信息] yt-dlp 安装完成: {yt_dlp.version.__version__}")
        return True

    progress(f"[信息] yt-dlp 已就绪: {yt_dlp.version.__version__}")
    # PO token 插件单独补装
    try:
        import yt_dlp_plugins.extractor.getpot_bgutil  # noqa: F401
    except ImportError:
        progress("[安装] PO token 插件缺失,补装...")
        _pip_install("bgutil-ytdlp-pot-provider")
    return True


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
    if node:
        progress(f"[信息] Node.js 已就绪: {node}")
        return True

    node_exe = NODE_DIR / "node.exe"
    if node_exe.exists():
        _add_to_path(NODE_DIR)
        progress(f"[信息] 使用便携 Node.js: {node_exe}")
        return True

    progress("[安装] 未检测到 Node.js,下载便携版(22+ LTS)...")
    version = _latest_lts_node_version()
    progress(f"[安装] 目标版本: v{version}")
    zip_path = TOOLS_DIR / f"node-v{version}-win-x64.zip"

    url = f"https://nodejs.org/dist/v{version}/node-v{version}-win-x64.zip"
    if not _download_with_progress(url, zip_path, progress, timeout_seconds=240):
        mirror = f"{NODE_MIRROR}/v{version}/node-v{version}-win-x64.zip"
        progress("[安装] 官方源超时/失败,改用 npmmirror 镜像...")
        if not _download_with_progress(mirror, zip_path, progress, timeout_seconds=240):
            progress("[错误] Node.js 下载失败,请手动安装 Node.js 22+ 后重试")
            return False

    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = next(m for m in zf.namelist() if m.endswith("/node.exe"))
            NODE_DIR.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(NODE_DIR / "node.exe", "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as exc:  # noqa: BLE001
        progress(f"[错误] 解压 Node.js 失败: {exc}")
        return False
    finally:
        zip_path.unlink(missing_ok=True)

    _add_to_path(NODE_DIR)
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
    if ffmpeg:
        progress(f"[信息] ffmpeg 已就绪: {ffmpeg}")
        return True

    ffmpeg_exe = FFMPEG_DIR / "ffmpeg.exe"
    ffprobe_exe = FFMPEG_DIR / "ffprobe.exe"
    if ffmpeg_exe.exists() and ffprobe_exe.exists():
        _add_to_path(FFMPEG_DIR)
        progress(f"[信息] 使用便携 ffmpeg: {ffmpeg_exe}")
        return True

    progress("[安装] 未检测到 ffmpeg,下载便携版...")

    zip_path = TOOLS_DIR / "ffmpeg.zip"
    if not _download_ffmpeg_zip(zip_path, progress):
        progress("[错误] ffmpeg 下载失败,请手动安装后重试")
        return False

    try:
        FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
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
    progress(f"[信息] ffmpeg 便携版就绪: {FFMPEG_DIR}")
    return True


def _ffmpeg_download_url() -> str | None:
    """BtbN/FFmpeg-Builds 最新 release 的 win64-gpl.zip 直链。

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
                return asset.get("browser_download_url")
    except Exception:  # noqa: BLE001
        pass
    return None


def _download_ffmpeg_zip(dest: Path, progress: Progress) -> bool:
    """多源下载 ffmpeg 压缩包。

    顺序:gyan.dev(墙内快,~110MB) → BtbN 直连 → BtbN 走 GitHub 代理镜像。
    """
    btb_url = _ffmpeg_download_url()
    candidates: list[tuple[str, str]] = [
        ("gyan.dev", "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ]
    if btb_url:
        candidates.append(("BtbN", btb_url))
        for prox in GITHUB_PROXIES:
            candidates.append((prox.split("//")[1].split("/")[0], prox + btb_url))
    # 每个源最多给 300s,太慢就换下一个,避免卡死整个自动安装
    for name, url in candidates:
        progress(f"[下载] ffmpeg 源: {name}")
        if _download_with_progress(url, dest, progress, timeout_seconds=300):
            return True
        dest.unlink(missing_ok=True)
    return False


# ---------- 通用 ----------

def _download_with_progress(
    url: str, dest: Path, progress: Progress, chunk: int = 65536,
    timeout_seconds: float = 600,
) -> bool:
    """下载文件到 dest,按块打印百分比进度。

    timeout_seconds:单源超时。墙内大文件可能很慢,超过时限返回 False,
    让调用方换下一个源,避免卡死整个自动安装。
    """
    import time as _time
    start = _time.monotonic()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "yt-dlp-gui-bootstrap"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            got = 0
            with open(dest, "wb") as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    if total:
                        progress(f"[下载] {dest.name}  {got / total * 100:.0f}%")
                    if _time.monotonic() - start > timeout_seconds:
                        progress(
                            f"[下载] {dest.name} 超过 {int(timeout_seconds)}s "
                            "未完成,换下一个源"
                        )
                        return False
            if not total:
                progress(f"[下载] {dest.name}  {_fmt_size(got)}")
        return True
    except Exception as exc:  # noqa: BLE001
        progress(f"[错误] 下载 {url} 失败: {exc}")
        dest.unlink(missing_ok=True)
        return False


def _add_to_path(directory: Path) -> None:
    """把目录临时加进当前进程 PATH(不写系统环境)。"""
    d = str(directory)
    if d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _fmt_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


if __name__ == "__main__":
    bootstrap()
