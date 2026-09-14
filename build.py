"""一键打包 yt-dlp GUI 为可分发的 onedir 文件夹(打包版)。

用法:  python build.py
产出:  dist/yt_dlp_gui/            ← 可整体压缩分发的文件夹
        dist/yt_dlp_gui.zip

流程:
1. 工具优先复用:dist/yt_dlp_gui/tools/ 里已有的 node/ffmpeg 直接复制到 build_cache/,
   没有才下载(网络慢/受限时不用重下)
2. 下载便携版 node.exe / ffmpeg.exe / ffprobe.exe 到 build_cache/(若复用成功则跳过)
3. pyinstaller --onedir 打包 GUI(yt-dlp + EJS + PO token + YouTube/Bilibili 内置登录)
4. 复制工具到 dist/yt_dlp_gui/tools/  (运行时 main() 会把 tools/ 加进 PATH)
5. 压成 zip

说明:
- 用 onedir 而非 onefile:避免把 ~160MB 工具塞进 exe 导致启动慢
- EJS 脚本靠 yt-dlp 自带 hook + --collect-data yt_dlp_ejs 打进包,墙内离线可用
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import bootstrap  # 复用下载/解压工具
import provider_setup

BUILD_DIR = Path(__file__).parent.resolve()
CACHE_DIR = BUILD_DIR / "build_cache"
DIST_ROOT = BUILD_DIR / "dist"
DIST_DIR = DIST_ROOT / "yt_dlp_gui"
TOOLS_DIST = DIST_DIR / "tools"
ZIP_OUT = DIST_ROOT / "yt_dlp_gui.zip"
CHECKSUM_OUT = DIST_ROOT / "yt_dlp_gui.zip.sha256"
STAGE_ROOT = DIST_ROOT / ".yt_dlp_gui_stage"
STAGE_DIST = STAGE_ROOT / "yt_dlp_gui"
STAGE_TOOLS = STAGE_DIST / "tools"
STAGE_ZIP = DIST_ROOT / ".yt_dlp_gui.zip.tmp"
BACKUP_DIST = DIST_ROOT / ".yt_dlp_gui_previous"
BACKUP_ZIP = DIST_ROOT / ".yt_dlp_gui_previous.zip"
PUBLISH_MARKER = DIST_ROOT / ".yt_dlp_gui_publishing.json"

# 固定 Node LTS 版本,避免每次构建漂移
NODE_VERSION = bootstrap.PINNED_NODE_VERSION
# Node.js 官方签名 SHASUMS256.txt 中的 Windows x64 ZIP/EXE 摘要。
NODE_ARCHIVE_SHA256 = bootstrap.PINNED_NODE_ARCHIVE_SHA256
NODE_EXE_SHA256 = bootstrap.PINNED_NODE_EXE_SHA256


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha256(path: Path, expected: str, label: str) -> None:
    actual = _sha256(path)
    if actual.lower() != expected.lower():
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} 安全校验失败，文件已删除。\n"
            f"期望 SHA-256: {expected}\n实际 SHA-256: {actual}"
        )


def main() -> None:
    print("== yt-dlp GUI 打包 ==")
    _check_build_python()
    _ensure_pyinstaller()
    _ensure_requirements()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    _recover_previous_dist()
    # 旧 dist 在整个构建和验证过程中保持不动。
    _reuse_tools_from_dist()
    _reset_stage()

    try:
        _download_tools()
        _run_pyinstaller()
        _assemble_dist()
        _validate_stage()
        _make_zip()
        _publish_stage()
    except BaseException:
        print("[构建] 构建未完成，原 dist 文件夹和 zip 均未删除")
        raise
    finally:
        _cleanup_stage()

    _cleanup_cache()

    print("== 打包完成 ==")
    print(f"文件夹: {DIST_DIR}")
    print(f"压缩包: {ZIP_OUT}")


def _check_build_python() -> None:
    """固定正式构建的 Python 大版本和架构，避免不同电脑产物漂移。"""
    if sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        raise SystemExit(
            "[错误] 正式打包要求 64 位 Python 3.13。\n"
            f"当前解释器: {sys.version.split()[0]} "
            f"({'64位' if sys.maxsize > 2**32 else '32位'})\n"
            "请修复/安装 Python 3.13 x64 后重新运行 build.py。"
        )


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
    "pyinstaller": "PyInstaller",
    "yt-dlp": "yt_dlp",
    "yt-dlp-ejs": "yt_dlp_ejs",
    "bgutil-ytdlp-pot-provider": "yt_dlp_plugins",
    "websocket-client": "websocket",  # 内置登录(edge_login)依赖
}


def _parse_requirements() -> list[str]:
    """打包优先读取精确锁定文件，保证不同电脑产物一致。"""
    locked = BUILD_DIR / "requirements-build.txt"
    req_file = locked if locked.is_file() else BUILD_DIR / "requirements.txt"
    if not req_file.is_file():
        return []
    packages = []
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        packages.append(line.split("#", 1)[0].strip())
    return packages


def _requirement_name(requirement: str) -> str:
    return re.split(r"[<>=~!\[]", requirement, maxsplit=1)[0].strip()


def _requirement_satisfied(requirement: str) -> bool:
    package = _requirement_name(requirement)
    import_name = _REQUIREMENT_IMPORT_NAMES.get(package, package.replace("-", "_"))
    if importlib.util.find_spec(import_name) is None:
        return False
    try:
        installed = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return False
    exact = re.search(r"==\s*([^,;\s]+)", requirement)
    if exact:
        return installed == exact.group(1)
    minimum = re.search(r">=\s*([^,;\s]+)", requirement)
    return not minimum or bootstrap._version_at_least(installed, minimum.group(1))


def _ensure_requirements() -> None:
    """检测 requirements.txt 里的依赖是否已安装,缺失则自动 pip install。

    新电脑/干净环境下直接跑 build.py 也能成功,无需手动先装依赖。
    """
    packages = _parse_requirements()
    missing = []
    for requirement in packages:
        if not _requirement_satisfied(requirement):
            missing.append(requirement)

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
    for requirement in missing:
        if not _requirement_satisfied(requirement):
            still_missing.append(requirement)
    if still_missing:
        raise SystemExit(
            f"[错误] 以下依赖安装后仍无法导入: {', '.join(still_missing)}\n"
            f"请手动执行: {sys.executable} -m pip install {' '.join(still_missing)}"
        )


def _reuse_tools_from_dist() -> None:
    """复用上一次打包产出 dist/yt_dlp_gui/tools/ 里的工具,避免重复下载。

    网速慢/公司网络受限时,直接把已打包的 node/ffmpeg/ffprobe 复制到
    build_cache/,_download_tools() 检测到文件已存在会自动跳过下载。
    """
    if not TOOLS_DIST.is_dir():
        return
    for name in ("node.exe", "ffmpeg.exe", "ffprobe.exe"):
        src = TOOLS_DIST / name
        if src.exists() and not (CACHE_DIR / name).exists():
            print(f"[构建] 复用上次已下载的工具: {name}")
            shutil.copy2(src, CACHE_DIR / name)
    provider_src = TOOLS_DIST / "bgutil-provider"
    provider_cache = CACHE_DIR / "bgutil-provider"
    if provider_setup.provider_valid(provider_src) and not provider_cache.exists():
        print("[构建] 复用上次已编译的 PO Token provider")
        shutil.copytree(provider_src, provider_cache)


def _download_failed_hint(tool_name: str) -> str:
    """工具下载失败时的统一报错提示,引导用户手动放 build_cache/ 绕过。"""
    return (
        f"[错误] {tool_name} 下载失败。\n"
        "常见原因: 公司网络 SSL 中间人代理(如阿里郎)拦截 HTTPS,或无外网。\n"
        f"解决办法: 从其他电脑拷贝 {tool_name} 放到 {CACHE_DIR}/ 后重新运行 build.py,\n"
        "build.py 检测到文件已存在会自动跳过下载。"
    )


def _download_tools() -> None:
    """准备 Node、ffmpeg 和与插件版本匹配的 PO Token provider。"""
    node_exe = CACHE_DIR / "node.exe"
    if node_exe.exists() and (
        not bootstrap._node_is_usable(node_exe)
        or _sha256(node_exe).lower() != NODE_EXE_SHA256
    ):
        print("[构建] 缓存的 node.exe 版本或安全摘要不符合锁定版本，重新获取")
        node_exe.unlink()
    if not node_exe.exists():
        print(f"[构建] 下载 Node.js v{NODE_VERSION} 便携版...")
        zip_path = CACHE_DIR / f"node-v{NODE_VERSION}-win-x64.zip"
        url = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
        if not bootstrap._download_with_progress(url, zip_path, print):
            mirror = f"{bootstrap.NODE_MIRROR}/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"
            print("[构建] 官方源失败,改用 npmmirror(断点续传)...")
            if not bootstrap._download_with_progress(mirror, zip_path, print):
                raise SystemExit(_download_failed_hint("node.exe"))
        _require_sha256(zip_path, NODE_ARCHIVE_SHA256, f"Node.js v{NODE_VERSION}")
        runtime = CACHE_DIR / "node-build-runtime"
        provider_setup.extract_node_runtime(zip_path, runtime)
        shutil.copy2(runtime / "node.exe", node_exe)
        _require_sha256(node_exe, NODE_EXE_SHA256, f"Node.js v{NODE_VERSION} node.exe")
        zip_path.unlink(missing_ok=True)
        print(f"[构建] node.exe 就绪: {node_exe}")

    ffmpeg_exe = CACHE_DIR / "ffmpeg.exe"
    ffprobe_exe = CACHE_DIR / "ffprobe.exe"
    if (
        ffmpeg_exe.exists() and ffprobe_exe.exists()
        and not (
            bootstrap._executable_works(ffmpeg_exe, "-version")
            and bootstrap._executable_works(ffprobe_exe, "-version")
        )
    ):
        print("[构建] 缓存的 ffmpeg/ffprobe 无法运行，重新获取")
        ffmpeg_exe.unlink(missing_ok=True)
        ffprobe_exe.unlink(missing_ok=True)
    if not (ffmpeg_exe.exists() and ffprobe_exe.exists()):
        print("[构建] 下载 ffmpeg 便携版(多源兜底)...")
        zip_path = CACHE_DIR / "ffmpeg.zip"
        if not bootstrap._download_ffmpeg_zip(zip_path, print, require_hash=True):
            raise SystemExit(_download_failed_hint("ffmpeg.exe / ffprobe.exe"))
        with zipfile.ZipFile(zip_path) as zf:
            bad_member = zf.testzip()
            if bad_member:
                raise zipfile.BadZipFile(f"ffmpeg 压缩包损坏: {bad_member}")
            names = set(zf.namelist())
            for target in ("ffmpeg.exe", "ffprobe.exe"):
                member = next(m for m in names if m.endswith(f"/bin/{target}"))
                with zf.open(member) as src, open(CACHE_DIR / target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        zip_path.unlink(missing_ok=True)
        print(f"[构建] ffmpeg/ffprobe 就绪: {CACHE_DIR}")

    provider_setup.ensure_provider(
        CACHE_DIR / "bgutil-provider", CACHE_DIR, print,
    )


def _run_pyinstaller() -> None:
    print("[构建] 运行 pyinstaller (onedir, 无需管理员)...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",                 # 文件夹,启动快
        "--windowed",               # 无控制台窗口
        "--name", "yt_dlp_gui",
        "--distpath", str(STAGE_ROOT),
        "--noupx",                  # 避免 UPX 被杀软误报
        "--collect-data", "yt_dlp_ejs",                 # ★ EJS 脚本本地化,墙内离线可用
        "--hidden-import", "yt_dlp_ejs",
        # PO token 插件(yt_dlp_plugins 是命名空间包,--collect-submodules 常漏子模块,逐个点名)
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil",
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil_http",
        "--hidden-import", "yt_dlp_plugins.extractor.getpot_bgutil_script",
        "--copy-metadata", "bgutil-ytdlp-pot-provider",
        # 多站点内置登录(Edge+CDP):edge_login 在 GUI 的 worker 线程里 import,
        # 加 hidden-import 确保被打进包;websocket 是它的纯 Python 依赖,自动收集
        "--hidden-import", "edge_login",
        "--hidden-import", "pot_provider",
        # 小红书图文下载器由 URL 路由按需导入。
        "--hidden-import", "xhs_image_downloader",
        "--collect-submodules", "yt_dlp",               # 所有 extractor/downloader/postprocessor
        "--collect-submodules", "yt_dlp_plugins",       # PO token 插件命名空间
        str(BUILD_DIR / "yt_dlp_gui.py"),
    ]
    subprocess.check_call(cmd, cwd=str(BUILD_DIR))


def _assemble_dist() -> None:
    """复制工具到暂存目录；验证成功前不接触正式 dist。"""
    print("[构建] 组装 tools/ 目录...")
    STAGE_TOOLS.mkdir(parents=True, exist_ok=True)
    for name in ("node.exe", "ffmpeg.exe", "ffprobe.exe"):
        src = CACHE_DIR / name
        if src.exists():
            shutil.copy2(src, STAGE_TOOLS / name)
    provider = CACHE_DIR / "bgutil-provider"
    if provider_setup.provider_valid(provider):
        shutil.copytree(provider, STAGE_TOOLS / "bgutil-provider")
    # 复制说明文件
    readme = BUILD_DIR / "使用说明.txt"
    if readme.exists():
        shutil.copy2(readme, STAGE_DIST / "使用说明.txt")
    notices = BUILD_DIR / "THIRD_PARTY_NOTICES.txt"
    if notices.exists():
        shutil.copy2(notices, STAGE_DIST / "THIRD_PARTY_NOTICES.txt")
    build_info = {
        "python": sys.version.split()[0],
        "architecture": "win-x64",
        "dependencies": {
            _requirement_name(req): importlib.metadata.version(_requirement_name(req))
            for req in _parse_requirements()
        },
        "tools": {
            name: _sha256(STAGE_TOOLS / name)
            for name in ("node.exe", "ffmpeg.exe", "ffprobe.exe")
        },
        "provider": {
            "version": provider_setup.PROVIDER_VERSION,
            "commit": provider_setup.PROVIDER_COMMIT,
        },
    }
    (STAGE_DIST / "BUILD_INFO.json").write_text(
        json.dumps(build_info, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_zip() -> None:
    print("[构建] 压缩暂存 zip...")
    STAGE_ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(STAGE_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in STAGE_DIST.rglob("*"):
            zf.write(p, Path("yt_dlp_gui") / p.relative_to(STAGE_DIST))
    with zipfile.ZipFile(STAGE_ZIP) as zf:
        bad_member = zf.testzip()
        if bad_member:
            raise RuntimeError(f"交付 zip 校验失败: {bad_member}")


def _reset_stage() -> None:
    _cleanup_stage()
    STAGE_ROOT.mkdir(parents=True, exist_ok=True)


def _recover_previous_dist() -> None:
    """上次若在目录/ZIP 替换之间断电，按事务标记恢复同一版本。"""
    if PUBLISH_MARKER.exists():
        try:
            state = json.loads(PUBLISH_MARKER.read_text(encoding="utf-8"))
            if state.get("phase") == "committed":
                # 两项正式产物已经一起落盘；只清理可能残留的旧备份。
                shutil.rmtree(BACKUP_DIST, ignore_errors=True)
                BACKUP_ZIP.unlink(missing_ok=True)
                PUBLISH_MARKER.unlink(missing_ok=True)
                PUBLISH_MARKER.with_suffix(".tmp").unlink(missing_ok=True)
                print("[构建] 已确认上次发布的新产物并清理残留备份")
            else:
                _rollback_publish(bool(state.get("had_dist")), bool(state.get("had_zip")))
                print("[构建] 已恢复上次发布中断前的旧产物")
            return
        except Exception as exc:
            raise RuntimeError(f"无法恢复上次中断的构建发布: {exc}") from exc
    # 兼容旧版构建脚本可能留下、但没有事务标记的目录备份。
    if BACKUP_DIST.exists() and not DIST_DIR.exists():
        BACKUP_DIST.rename(DIST_DIR)
    if BACKUP_ZIP.exists() and not ZIP_OUT.exists():
        BACKUP_ZIP.rename(ZIP_OUT)


def _cleanup_stage() -> None:
    shutil.rmtree(STAGE_ROOT, ignore_errors=True)
    STAGE_ZIP.unlink(missing_ok=True)


def _validate_stage() -> None:
    """在发布前验证关键文件和便携工具确实可运行。"""
    exe = STAGE_DIST / "yt_dlp_gui.exe"
    if not exe.is_file() or exe.stat().st_size < 1024 * 1024:
        raise RuntimeError("PyInstaller 产物缺少有效的 yt_dlp_gui.exe")
    node = STAGE_TOOLS / "node.exe"
    ffmpeg = STAGE_TOOLS / "ffmpeg.exe"
    ffprobe = STAGE_TOOLS / "ffprobe.exe"
    provider = STAGE_TOOLS / "bgutil-provider"
    if not bootstrap._node_is_usable(node):
        raise RuntimeError("暂存产物中的 Node.js 无法运行或版本低于 22")
    if not bootstrap._executable_works(ffmpeg, "-version"):
        raise RuntimeError("暂存产物中的 ffmpeg 无法运行")
    if not bootstrap._executable_works(ffprobe, "-version"):
        raise RuntimeError("暂存产物中的 ffprobe 无法运行")
    if not provider_setup.provider_valid(provider):
        raise RuntimeError("暂存产物缺少有效的 PO Token provider")
    # 文件存在不代表 Node 原生模块和运行时真正兼容。发布前实际启动一次、
    # 请求 /ping、核对版本，再确认进程能够被完整关闭。
    from pot_provider import PotProviderManager

    previous_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(STAGE_TOOLS) + os.pathsep + previous_path
    provider_messages: list[str] = []
    manager = PotProviderManager(provider)
    try:
        if not manager.ensure_started(provider_messages.append, timeout=20):
            detail = "\n".join(provider_messages[-10:])
            raise RuntimeError(f"暂存产物中的 PO Token provider 无法启动:\n{detail}")
    finally:
        manager.stop()
        os.environ["PATH"] = previous_path
    completed = subprocess.run(
        [str(exe), "--self-test"], timeout=60, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"冻结 EXE 自检失败(returncode={completed.returncode})，"
            "可能缺少 yt-dlp-ejs/PO Token/websocket 或便携工具"
        )
    print("[构建] 暂存产物验证通过")


def _publish_stage() -> None:
    """新文件夹和 zip 均完成后才替换正式交付物。"""
    print("[构建] 发布新产物...")
    shutil.rmtree(BACKUP_DIST, ignore_errors=True)
    BACKUP_ZIP.unlink(missing_ok=True)
    had_dist = DIST_DIR.exists()
    had_zip = ZIP_OUT.exists()
    state = {"phase": "prepared", "had_dist": had_dist, "had_zip": had_zip}
    with open(PUBLISH_MARKER, "w", encoding="utf-8") as marker:
        marker.write(json.dumps(state, ensure_ascii=False))
        marker.flush()
        os.fsync(marker.fileno())
    try:
        if had_dist:
            DIST_DIR.rename(BACKUP_DIST)
        if had_zip:
            ZIP_OUT.rename(BACKUP_ZIP)
        STAGE_DIST.rename(DIST_DIR)
        os.replace(STAGE_ZIP, ZIP_OUT)
        # 两个正式产物都已替换后先记录提交点。之后即使断电，恢复逻辑也会
        # 保留同一批新产物，而不会把 ZIP 单独恢复成旧版。
        state["phase"] = "committed"
        marker_tmp = PUBLISH_MARKER.with_suffix(".tmp")
        with open(marker_tmp, "w", encoding="utf-8") as marker:
            marker.write(json.dumps(state, ensure_ascii=False))
            marker.flush()
            os.fsync(marker.fileno())
        os.replace(marker_tmp, PUBLISH_MARKER)
    except Exception:
        _rollback_publish(had_dist, had_zip)
        raise
    shutil.rmtree(BACKUP_DIST, ignore_errors=True)
    BACKUP_ZIP.unlink(missing_ok=True)
    PUBLISH_MARKER.unlink(missing_ok=True)
    PUBLISH_MARKER.with_suffix(".tmp").unlink(missing_ok=True)
    try:
        checksum = _sha256(ZIP_OUT)
        checksum_tmp = CHECKSUM_OUT.with_suffix(".tmp")
        checksum_tmp.write_text(f"{checksum}  {ZIP_OUT.name}\n", encoding="ascii")
        os.replace(checksum_tmp, CHECKSUM_OUT)
        print(f"[构建] 交付 ZIP SHA-256: {checksum}")
    except OSError as exc:
        # 校验清单是附属交付物；正式文件夹和 ZIP 已原子提交，不能因为清单
        # 写入失败而错误声称旧产物仍在。明确告警即可。
        print(f"[警告] 无法写入 ZIP SHA-256 清单: {exc}")


def _rollback_publish(had_dist: bool, had_zip: bool) -> None:
    """撤回未完整发布的新目录/ZIP，并恢复旧的一致版本。"""
    if had_dist:
        # 备份不存在说明断电发生在旧目录移动之前，此时正式目录仍是旧版。
        if BACKUP_DIST.exists():
            if DIST_DIR.exists():
                shutil.rmtree(DIST_DIR)
            BACKUP_DIST.rename(DIST_DIR)
    elif DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)

    if had_zip:
        if BACKUP_ZIP.exists():
            ZIP_OUT.unlink(missing_ok=True)
            BACKUP_ZIP.rename(ZIP_OUT)
    else:
        ZIP_OUT.unlink(missing_ok=True)
    PUBLISH_MARKER.unlink(missing_ok=True)
    PUBLISH_MARKER.with_suffix(".tmp").unlink(missing_ok=True)


def _cleanup_cache() -> None:
    print("[构建] 清理 PyInstaller 临时文件（保留已校验工具缓存）...")
    # Node 完整运行时、provider 源码/依赖和 ffmpeg 体积很大。它们都已有
    # 版本/摘要/清单复检，保留 build_cache 可避免下次构建重新走慢速网络。
    shutil.rmtree(BUILD_DIR / "build", ignore_errors=True)
    (BUILD_DIR / "yt_dlp_gui.spec").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
