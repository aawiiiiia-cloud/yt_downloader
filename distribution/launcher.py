"""Launch the source distribution using only its sibling runtime directories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def layout() -> tuple[Path, Path, Path]:
    root = Path(__file__).resolve().parent.parent
    return root, root / "代码", root / "依赖工具"


def environment(tools: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "ELECTRON_RUN_AS_NODE"):
        env.pop(name, None)
    python = tools / "python"
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env.update({
        "YT_DOWNLOADER_PYTHON": str(python / "python.exe"),
        "YT_DOWNLOADER_TOOLS_DIR": str(tools),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8", "NODE_PATH": str(tools / "node_modules"),
        "TCL_LIBRARY": str(python / "tcl" / "tcl8.6"),
        "TK_LIBRARY": str(python / "tcl" / "tk8.6"),
        "PATH": os.pathsep.join(map(str, [python, tools / "node", tools / "ffmpeg",
            windows / "System32", windows, windows / "System32" / "Wbem",
            windows / "System32" / "WindowsPowerShell" / "v1.0"])),
    })
    return env


def check(code: Path, tools: Path, env: dict[str, str]) -> int:
    probe = r'''
import json, sys, shutil, tkinter, ssl, subprocess
from pathlib import Path
from importlib import metadata
import PIL, PIL.Image, pillow_heif, dhash, requests, websocket, yt_dlp, gallery_dl
from collector_core import runtime_health
from pot_provider import find_provider
root = Path(sys.argv[1]).resolve()
health = runtime_health()
assert all(health[k] for k in ('ffmpeg', 'ffprobe', 'node', 'provider')), health
assert all(health['packages'].values()), health
assert Path(sys.base_prefix).resolve().is_relative_to(root), sys.base_prefix
modules = (PIL, pillow_heif, dhash, requests, websocket, yt_dlp, gallery_dl)
for module in modules:
    assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
for tool in ('node', 'ffmpeg', 'ffprobe'):
    assert Path(shutil.which(tool)).resolve().is_relative_to(root), tool
provider = find_provider(strict=True)
assert provider and provider.is_relative_to(root), provider
window = tkinter.Tk(); window.withdraw(); window.update(); window.destroy()
PIL.Image.new('RGB', (2, 2)).resize((1, 1))
print(json.dumps({'python': sys.version.split()[0], 'executable': sys.executable,
    'health': health, 'provider': str(provider), 'tk': tkinter.TkVersion,
    'packages': {d.metadata['Name']:d.version for d in metadata.distributions()}}, ensure_ascii=False))
'''
    result = subprocess.run([env["YT_DOWNLOADER_PYTHON"], "-B", "-X", "utf8", "-c", probe, str(tools)],
        cwd=code, env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        return result.returncode
    report = json.loads(result.stdout)
    for name, executable in (("node", tools / "node" / "node.exe"),
            ("ffmpeg", tools / "ffmpeg" / "ffmpeg.exe"), ("ffprobe", tools / "ffmpeg" / "ffprobe.exe")):
        version = subprocess.run([str(executable), "--version" if name == "node" else "-version"],
            cwd=code, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        report[name] = version.stdout.splitlines()[0]
    report["electron"] = json.loads((tools / "node_modules" / "electron" / "package.json").read_text(encoding="utf-8"))["version"]
    target = tools / "验证结果" / "环境检查.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("运行环境检查通过：Python、图片依赖、Node、FFmpeg、PO Token 工具和 Tk 均来自本文件夹。")
    print(f"详细结果：{target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--test-electron", action="store_true")
    args = parser.parse_args()
    _root, code, tools = layout()
    env = environment(tools)
    required = [code / "collector_worker.py", Path(env["YT_DOWNLOADER_PYTHON"]),
        tools / "node" / "node.exe", tools / "node_modules" / "electron" / "dist" / "electron.exe",
        tools / "ffmpeg" / "ffmpeg.exe", tools / "ffmpeg" / "ffprobe.exe"]
    for file in required:
        if not file.is_file():
            print(f"缺少文件：{file}\n请完整复制整个 media-dl 文件夹。", file=sys.stderr)
            return 1
    if args.check:
        return check(code, tools, env)
    if args.test_electron:
        command = [str(tools / "node" / "node.exe"), str(code / "electron" / "test-desktop.cjs")]
    elif args.legacy:
        command = [env["YT_DOWNLOADER_PYTHON"], "-B", "-X", "utf8", str(code / "yt_dlp_gui.py")]
    else:
        command = [str(tools / "node_modules" / "electron" / "dist" / "electron.exe"), str(code)]
    return subprocess.call(command, cwd=code, env=env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.SubprocessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        raise SystemExit(1)
