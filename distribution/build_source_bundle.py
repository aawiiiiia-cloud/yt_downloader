"""Copy the current source and installed runtimes into a self-contained folder."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


SOURCE = Path(__file__).resolve().parent.parent


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(SOURCE), *args])


def copy_tree(source: Path, destination: Path, *, python: bool = False) -> None:
    def ignore(folder: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}
        if python and source.name.casefold() == "lib" and Path(folder).resolve() == source.resolve():
            skipped.add("site-packages")
        return skipped
    shutil.copytree(source, destination, ignore=ignore if python else None)


def command_file(arguments: str = "") -> str:
    return '\r\n'.join([
        '@echo off', 'chcp 65001 >nul', 'setlocal', 'title media-dl',
        'set "PYTHONHOME="', 'set "PYTHONPATH="', 'set "PYTHONNOUSERSITE=1"',
        'set "PYTHONUTF8=1"',
        'set "media_python=%~dp0..\\依赖工具\\python\\python.exe"',
        'if not exist "%media_python%" (',
        '    echo 缺少依赖工具，请完整复制整个 media-dl 文件夹。',
        '    pause', '    exit /b 1', ')',
        f'"%media_python%" -B -X utf8 "%~dp0启动.py" {arguments} %*',
        'set "media_result=%errorlevel%"',
        'if not "%media_result%"=="0" pause' if arguments != '--check' else 'pause',
        'exit /b %media_result%', '',
    ])


def manifest(root: Path, commit: str, source_files: list[str]) -> dict:
    entries = []
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.name in {"整理清单.json", "正在整理.txt"}:
            continue
        relative = file.relative_to(root).as_posix()
        if relative.startswith("代码/electron-artifacts/") or relative.startswith("依赖工具/验证结果/"):
            continue
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        entries.append({"path": relative, "size": file.stat().st_size, "sha256": digest.hexdigest()})
    result = {"createdAt": datetime.now(timezone.utc).isoformat(), "sourceCommit": commit,
        "sourceFiles": source_files, "fileCount": len(entries),
        "totalBytes": sum(file["size"] for file in entries), "files": entries}
    (root / "整理清单.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    root = args.destination.resolve()
    if root.exists():
        raise RuntimeError(f"目标已经存在，为避免覆盖已停止：{root}")
    node = Path(shutil.which("node") or "")
    npm = node.parent / "node_modules" / "npm"
    ffmpeg = Path(shutil.which("ffmpeg") or "")
    sys.path.insert(0, str(SOURCE))
    from pot_provider import find_provider
    provider = find_provider(strict=True)
    python_base = Path(sys.base_prefix)
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    for required in (node, npm, ffmpeg, ffmpeg.with_name("ffprobe.exe"), python_base / "python313.dll",
            site_packages, SOURCE / "node_modules" / "electron" / "dist" / "electron.exe"):
        if not required.exists():
            raise RuntimeError(f"缺少已安装依赖：{required}")
    if provider is None:
        raise RuntimeError("未找到通过完整性校验的 PO Token 工具")
    commit = git("rev-parse", "HEAD").decode().strip()
    files = [name for name in git("ls-files", "-z").decode("utf-8").split("\0") if name]
    root.mkdir(parents=True)
    (root / "正在整理.txt").write_text("整理尚未完成，请等待环境检查通过。\n", encoding="utf-8")
    code, tools, startup = root / "代码", root / "依赖工具", root / "启动程序"
    code.mkdir(); tools.mkdir(); startup.mkdir()
    print(f"复制 {len(files)} 个源码及文档文件…", flush=True)
    for name in files:
        destination = code / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, destination)
    git("bundle", "create", str(code / "版本历史.bundle"), "--all")
    print("复制 Electron 和 JavaScript 依赖…", flush=True)
    copy_tree(SOURCE / "node_modules", tools / "node_modules")
    print("复制独立 Python 标准库与项目专用依赖…", flush=True)
    python = tools / "python"
    python.mkdir()
    for file in python_base.iterdir():
        if file.is_file() and file.suffix.lower() in {".exe", ".dll", ".zip", ".txt"}:
            shutil.copy2(file, python / file.name)
    for directory in ("DLLs", "Lib", "tcl", "include", "libs"):
        if (python_base / directory).is_dir():
            copy_tree(python_base / directory, python / directory, python=True)
    # The base installation may contain unrelated AI packages. Only copy this venv's dependencies.
    existing_site = python / "Lib" / "site-packages"
    if existing_site.exists():
        raise RuntimeError("基础标准库不应包含第三方包，请检查复制过滤规则")
    copy_tree(site_packages, existing_site, python=True)
    print("复制 Node/npm、FFmpeg 与 PO Token 工具…", flush=True)
    node_dir = tools / "node"
    node_dir.mkdir()
    shutil.copy2(node, node_dir / "node.exe")
    copy_tree(npm, node_dir / "node_modules" / "npm")
    for name in ("npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1"):
        if (node.parent / name).is_file():
            shutil.copy2(node.parent / name, node_dir / name)
    ffmpeg_dir = tools / "ffmpeg"
    ffmpeg_dir.mkdir()
    for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
        if (ffmpeg.parent / name).is_file():
            shutil.copy2(ffmpeg.parent / name, ffmpeg_dir / name)
    copy_tree(provider, tools / "bgutil-provider")
    package_versions = sorted((d.metadata["Name"], d.version) for d in metadata.distributions())
    (tools / "Python依赖版本.txt").write_text("".join(f"{name}=={version}\n" for name, version in package_versions), encoding="utf-8")
    shutil.copy2(SOURCE / "distribution" / "launcher.py", startup / "启动.py")
    for filename, arguments in (("启动media-dl.cmd", ""), ("启动旧版.cmd", "--legacy"), ("检查运行环境.cmd", "--check")):
        (startup / filename).write_bytes(command_file(arguments).encode("utf-8"))
    (root / "使用说明.txt").write_text(
        "media-dl 完整源码与运行依赖\n\n"
        "一、日常使用\n双击：启动程序\\启动media-dl.cmd\n"
        "旧版 Tkinter 界面：启动程序\\启动旧版.cmd\n"
        "环境检查：启动程序\\检查运行环境.cmd\n\n"
        "二、目录说明\n代码：完整项目源码、测试、文档、原始启动脚本、Git 历史备份。\n"
        "依赖工具：独立 Python 及项目 Python 包、Node/npm、Electron 及 JS 包、FFmpeg/FFprobe、PO Token 工具。\n"
        "启动程序：本目录专用的 CMD 与 Python 启动入口，按相对路径查找其余两个文件夹。\n"
        "Python 业务模块仍放在代码内，以保留模块导入关系；启动程序中只放启动入口。\n\n"
        "三、复制与开发\n请整体复制或移动 media-dl 文件夹，保留上述三个子目录名称。\n"
        "无需依赖原来的 D 盘 Python、Node 和 FFmpeg，也无需安装系统级 Python/Node。\n"
        "适用于 Windows x64；Edge 登录功能需要本机安装 Microsoft Edge。\n"
        "也可使用代码内的 start-collector.cmd / start.cmd，它们会自动转交启动程序内的入口。\n"
        "Git 历史保存在 代码\\版本历史.bundle；有 Git 时可用 git clone 恢复到另一个新目录。\n\n"
        "四、本机数据\n应用仍使用当前 Windows 用户的 .media-collector 保存设置、登录和下载历史。\n"
        "本包没有复制账号 Cookies、浏览器个人资料、已下载媒体、临时测试缓存、MissAV 或图片重命名器等独立项目。\n"
        "同一 Windows 用户下，请退出旧位置的 media-dl 后，再启动本目录的版本。\n\n"
        f"源码版本：{commit}\n文件完整性与依赖摘要见 整理清单.json 和 依赖工具\\Python依赖版本.txt。\n",
        encoding="utf-8-sig")
    report = manifest(root, commit, files)
    print("检查包内运行环境…", flush=True)
    launch_env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "ELECTRON_RUN_AS_NODE"):
        launch_env.pop(name, None)
    launch_env["PYTHONNOUSERSITE"] = "1"
    subprocess.run([str(python / "python.exe"), "-B", "-X", "utf8", str(startup / "启动.py"), "--check"],
        cwd=code, env=launch_env, check=True, timeout=60)
    (root / "正在整理.txt").unlink()
    print(json.dumps({"destination": str(root), "files": report["fileCount"],
        "sizeMB": round(report["totalBytes"] / 1024 ** 2, 1), "commit": commit}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
