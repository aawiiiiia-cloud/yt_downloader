"""一键打包 yt-dlp GUI 为可分发的 onedir 文件夹(打包版)。

用法:  python build.py
产出:  dist/yt_dlp_gui/            ← 可整体压缩分发的文件夹
        dist/yt_dlp_gui.zip

流程:
1. 下载便携版 node.exe / ffmpeg.exe / ffprobe.exe 到 build_cache/
2. pyinstaller --onedir 打包 GUI(yt-dlp + yt-dlp-ejs 本地脚本 + PO token 插件)
3. 复制工具到 dist/yt_dlp_gui/tools/  (运行时 main() 会把 tools/ 加进 PATH)
4. 压成 zip

说明:
- 用 onedir 而非 onefile:避免把 ~160MB 工具塞进 exe 导致启动慢
- EJS 脚本靠 yt-dlp 自带 hook + --collect-data yt_dlp_ejs 打进包,墙内离线可用
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import bootstrap  # 复用下载/解压工具

BUILD_DIR = Path(__file__).parent.resolve()
CACHE_DIR = BUILD_DIR / "build_cache"
DIST_DIR = BUILD_DIR / "dist" / "yt_dlp_gui"
TOOLS_DIST = DIST_DIR / "tools"
ZIP_OUT = BUILD_DIR / "dist" / "yt_dlp_gui.zip"

# 固定 Node LTS 版本,避免每次构建漂移
NODE_VERSION = "22.14.0"


def main() -> None:
    print("== yt-dlp GUI 打包 ==")
    _ensure_pyinstaller()
    _ensure_requirements()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    _download_tools()
    _run_pyinstaller()
    _assemble_dist()
    _make_zip()
    _cleanup_cache()

    print("== 打包完成 ==")
    print(f"文件夹: {DIST_DIR}")
    print(f"压缩包: {ZIP_OUT}")


def _pip_install(packages: list[str]) -> bool:
    """pip 安装包,先走默认源,失败则回退阿里云镜像。返回是否成功。

    不静默输出:安装失败时让真实报错可见,便于排查。
    """
    base_cmd = [sys.executable, "-m", "pip", "install", *packages]
    if subprocess.call(base_cmd) == 0:
        return True
    print("[构建] 默认 pip 源失败,改用阿里云镜像重试...")
    mirror_cmd = base_cmd + ["-i", "https://mirrors.aliyun.com/pypi/simple/"]
    return subprocess.call(mirror_cmd) == 0


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("[构建] 安装 pyinstaller...")
    if not _pip_install(["-U", "pyinstaller"]):
        raise SystemExit(
            "[错误] pyinstaller 安装失败。\n"
            f"请手动执行: {sys.executable} -m pip install -U pyinstaller\n"
            "若网络受限,可加镜像: -i https://mirrors.aliyun.com/pypi/simple/"
        )


# requirements.txt 里的包名 → 实际 import 用的模块名
# (pip 包名和 import 名不一致时必须显式映射,否则检测会误判)
_REQUIREMENT_IMPORT_NAMES = {
    "yt-dlp": "yt_dlp",
    "yt-dlp-ejs": "yt_dlp_ejs",
    "bgutil-ytdlp-pot-provider": "yt_dlp_plugins",
}


def _parse_requirements() -> list[str]:
    """读取 requirements.txt,返回 pip 包名列表(去掉版本约束和注释)。"""
    req_file = BUILD_DIR / "requirements.txt"
    if not req_file.is_file():
        return []
    packages = []
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉 >= / == / ~= 等版本约束,只留包名
        for sep in (">=", "==", "~=", ">", "<", "["):
            line = line.split(sep)[0]
        packages.append(line.strip())
    return packages


def _ensure_requirements() -> None:
    """检测 requirements.txt 里的依赖是否已安装,缺失则自动 pip install。

    新电脑/干净环境下直接跑 build.py 也能成功,无需手动先装依赖。
    """
    packages = _parse_requirements()
    missing = []
    for package in packages:
        import_name = _REQUIREMENT_IMPORT_NAMES.get(package, package.replace("-", "_"))
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)

    if not missing:
        print("[构建] 依赖已就绪: " + ", ".join(packages))
        return

    print(f"[构建] 缺少依赖,自动安装: {', '.join(missing)}")
    if not _pip_install(missing):
        raise SystemExit(
            f"[错误] 依赖安装失败: {', '.join(missing)}\n"
            f"请手动执行: {sys.executable} -m pip install {' '.join(missing)}\n"
            "若网络受限,可加镜像: -i https://mirrors.aliyun.com/pypi/simple/"
        )

    # 安装后复检,仍缺失则报错退出(避免后面 pyinstaller 报一堆难懂的错)
    still_missing = []
    for package in missing:
        import_name = _REQUIREMENT_IMPORT_NAMES.get(package, package.replace("-", "_"))
        if importlib.util.find_spec(import_name) is None:
            still_missing.append(package)
    if still_missing:
        raise SystemExit(
            f"[错误] 以下依赖安装后仍无法导入: {', '.join(still_missing)}\n"
            f"请手动执行: {sys.executable} -m pip install {' '.join(still_missing)}"
        )


def _download_failed_hint(tool_name: str) -> str:
    """工具下载失败时的统一报错提示,引导用户手动放 build_cache/ 绕过。"""
    return (
        f"[错误] {tool_name} 下载失败。\n"
        "常见原因: 公司网络 SSL 中间人代理(如阿里郎)拦截 HTTPS,或无外网。\n"
        f"解决办法: 从其他电脑拷贝 {tool_name} 放到 {CACHE_DIR}/ 后重新运行 build.py,\n"
        "build.py 检测到文件已存在会自动跳过下载。"
    )


def _download_tools() -> None:
    """下载 node.exe / ffmpeg.exe / ffprobe.exe 到 build_cache/。"""
    node_exe = CACHE_DIR / "node.exe"
    if not node_exe.exists():
        print(f"[构建] 下载 Node.js v{NODE_VERSION} 便携版...")
        zip_path = CACHE_DIR / f"node-v{NODE_VERSION}-win-x64.zip"
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
        if not bootstrap._download_with_progress(url, zip_path, print, timeout_seconds=240):
            mirror = f"{bootstrap.NODE_MIRROR}/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
            print("[构建] 官方源超时/失败,改用 npmmirror...")
            if not bootstrap._download_with_progress(mirror, zip_path, print, timeout_seconds=240):
                raise SystemExit(_download_failed_hint("node.exe"))
        with zipfile.ZipFile(zip_path) as zf:
            member = next(m for m in zf.namelist() if m.endswith("/node.exe"))
            with zf.open(member) as src, open(node_exe, "wb") as dst:
                shutil.copyfileobj(src, dst)
        zip_path.unlink(missing_ok=True)
        print(f"[构建] node.exe 就绪: {node_exe}")

    ffmpeg_exe = CACHE_DIR / "ffmpeg.exe"
    ffprobe_exe = CACHE_DIR / "ffprobe.exe"
    if not (ffmpeg_exe.exists() and ffprobe_exe.exists()):
        print("[构建] 下载 ffmpeg 便携版(多源兜底)...")
        zip_path = CACHE_DIR / "ffmpeg.zip"
        if not bootstrap._download_ffmpeg_zip(zip_path, print):
            raise SystemExit(_download_failed_hint("ffmpeg.exe / ffprobe.exe"))
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            for target in ("ffmpeg.exe", "ffprobe.exe"):
                member = next(m for m in names if m.endswith(f"/bin/{target}"))
                with zf.open(member) as src, open(CACHE_DIR / target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        zip_path.unlink(missing_ok=True)
        print(f"[构建] ffmpeg/ffprobe 就绪: {CACHE_DIR}")


def _run_pyinstaller() -> None:
    print("[构建] 运行 pyinstaller (onedir, 无需管理员)...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",                 # 文件夹,启动快
        "--windowed",               # 无控制台窗口
        "--name", "yt_dlp_gui",
        "--noupx",                  # 避免 UPX 被杀软误报
        "--collect-data", "yt_dlp_ejs",                 # ★ EJS 脚本本地化,墙内离线可用
        "--hidden-import", "yt_dlp_ejs",
        # PO token 插件(yt_dlp_plugins 是命名空间包,--collect-submodules 常漏子模块,逐个点名)
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil",
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil_http",
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil_script",
        "--collect-submodules", "yt_dlp",               # 所有 extractor/downloader/postprocessor
        "--collect-submodules", "yt_dlp_plugins",       # PO token 插件命名空间
        str(BUILD_DIR / "yt_dlp_gui.py"),
    ]
    subprocess.check_call(cmd, cwd=str(BUILD_DIR))


def _assemble_dist() -> None:
    """复制 build_cache 里的工具到 dist/yt_dlp_gui/tools/。"""
    print("[构建] 组装 tools/ 目录...")
    TOOLS_DIST.mkdir(parents=True, exist_ok=True)
    for name in ("node.exe", "ffmpeg.exe", "ffprobe.exe"):
        src = CACHE_DIR / name
        if src.exists():
            shutil.copy2(src, TOOLS_DIST / name)
    # 复制说明文件
    readme = BUILD_DIR / "使用说明.txt"
    if readme.exists():
        shutil.copy2(readme, DIST_DIR / "使用说明.txt")


def _make_zip() -> None:
    print("[构建] 压缩 zip...")
    if ZIP_OUT.exists():
        ZIP_OUT.unlink()
    with zipfile.ZipFile(ZIP_OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in DIST_DIR.rglob("*"):
            zf.write(p, p.relative_to(DIST_DIR.parent))


def _cleanup_cache() -> None:
    print("[构建] 清理临时文件...")
    shutil.rmtree(CACHE_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
