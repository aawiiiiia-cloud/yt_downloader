"""Launch the source distribution using only its sibling runtime directories."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def layout(script: Path | None = None) -> tuple[Path, Path, Path]:
    script = (script or Path(__file__)).resolve()
    if script.parent.name == 'distribution':
        code = script.parent.parent
        root = code.parent
    else:
        root = script.parent.parent
        code = root / '代码'
    bundled = (root / '启动程序' / '启动.py').is_file()
    tools = root / '依赖工具' if bundled else Path(
        os.environ.get('YT_DOWNLOADER_TOOLS_DIR') or Path.home() / '.yt_dlp_tools')
    return root, code, tools


def clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'ELECTRON_RUN_AS_NODE',
                 'TCL_LIBRARY', 'TK_LIBRARY'):
        env.pop(name, None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
               PYTHONIOENCODING='utf-8')
    return env


def python_ready(executable: Path, tools: Path, env: dict[str, str]) -> bool:
    if not executable.is_file():
        return False
    probe = ('import sys,struct,ssl,tkinter,venv,ensurepip,pip; from pathlib import Path; '
             'assert sys.version_info[:2]==(3,13) and struct.calcsize("P")==8; '
             'assert sys.prefix!=sys.base_prefix or '
             'Path(sys.prefix).resolve().is_relative_to(Path(sys.argv[1]).resolve()); tkinter.Tcl()')
    try:
        result = subprocess.run([str(executable), '-I', '-B', '-c', probe, str(tools)],
            env=env, capture_output=True, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def environment(tools: Path) -> dict[str, str]:
    env = clean_environment()
    python = tools / "python"
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env.update({
        "YT_DOWNLOADER_PYTHON": str(python / "python.exe"),
        "YT_DOWNLOADER_TOOLS_DIR": str(tools),
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8", "NODE_PATH": str(tools / "node_modules"),
        "PATH": os.pathsep.join(map(str, [python, tools / "node", tools / "ffmpeg",
            windows / "System32", windows, windows / "System32" / "Wbem",
            windows / "System32" / "WindowsPowerShell" / "v1.0"])),
    })
    for key, leaf in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        env.pop(key, None)
        for directory in (python / 'tcl' / leaf, python / 'Lib' / leaf):
            if directory.is_dir(): env[key] = str(directory); break
    return env


def check(code: Path, tools: Path, env: dict[str, str], *, need_electron=True) -> int:
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
# prepare(full_check=True) already verified every provider digest once.
provider = find_provider()
assert provider and provider.is_relative_to(root), provider
window = tkinter.Tk(); window.withdraw(); window.update(); window.destroy()
PIL.Image.new('RGB', (2, 2)).resize((1, 1))
print(json.dumps({'python': sys.version.split()[0], 'executable': sys.executable,
    'health': health, 'provider': str(provider), 'tk': tkinter.TkVersion,
    'packages': {d.metadata['Name']:d.version for d in metadata.distributions()}}, ensure_ascii=False))
'''
    result = subprocess.run([env["YT_DOWNLOADER_PYTHON"], "-B", "-X", "utf8", "-c", probe, str(tools)],
        cwd=code, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        return result.returncode
    report = json.loads(result.stdout)
    for name, executable in (("node", tools / "node" / "node.exe"),
            ("ffmpeg", tools / "ffmpeg" / "ffmpeg.exe"), ("ffprobe", tools / "ffmpeg" / "ffprobe.exe")):
        version = subprocess.run([str(executable), "--version" if name == "node" else "-version"],
            cwd=code, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        report[name] = version.stdout.splitlines()[0]
    if need_electron:
        report["electron"] = json.loads((tools / "node_modules" / "electron" / "package.json").read_text(encoding="utf-8"))["version"]
    target = tools / "验证结果" / "环境检查.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("运行环境检查通过：Python、图片依赖、Node、FFmpeg、PO Token 工具和 Tk 均来自本文件夹。")
    print(f"详细结果：{target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", "--check-env", action="store_true")
    parser.add_argument('--mode', choices=('collector', 'legacy'), default='collector')
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--test-electron", action="store_true")
    args = parser.parse_args()
    root, code, tools = layout()
    bundled = (root / '启动程序' / '启动.py').is_file()
    legacy = args.legacy or args.mode == 'legacy'
    flags = ['--mode', 'legacy' if legacy else 'collector']
    if args.check: flags.append('--check')
    if args.test_electron: flags.append('--test-electron')
    env = environment(tools) if bundled else clean_environment()
    candidates = [tools / 'python' / 'python.exe'] if bundled else [
        Path(os.environ['YT_DOWNLOADER_PYTHON']) if os.environ.get('YT_DOWNLOADER_PYTHON') else None,
        Path(r'D:\python-envs\yt-downloader\Scripts\python.exe'), tools / 'python' / 'python.exe']
    expected_python = next((candidate for candidate in candidates if candidate and python_ready(candidate, tools, env)), None)
    if expected_python and Path(sys.executable).resolve() != expected_python.resolve():
        # A .py association can select any system Python. Never install into that environment.
        return subprocess.call([str(expected_python), '-I', '-B', '-X', 'utf8',
            str(code / 'distribution' / 'launcher.py'), *flags], cwd=code, env=env)
    if expected_python is None:
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        command = [str(windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'), '-NoProfile',
            '-ExecutionPolicy', 'Bypass', '-File', str(code / 'distribution/ensure-runtime.ps1'),
            '-Mode', 'legacy' if legacy else 'collector']
        if args.check: command.append('-CheckOnly')
        if args.test_electron: command.append('-DesktopTest')
        print('随包或专用 Python 不可用，尝试系统允许的 Python 恢复流程。', flush=True)
        return subprocess.call(command, cwd=code, env=env)
    os.environ.clear()
    os.environ.update(env)
    os.environ['YT_DOWNLOADER_TOOLS_DIR'] = str(tools)
    sys.path.insert(0, str(code))
    from distribution.runtime_setup import prepare
    tools, modules, node, prepared_env = prepare(code, tools, bundled=bundled,
        need_electron=not legacy, full_check=args.check)
    env = environment(tools) if bundled else prepared_env
    env['YT_DOWNLOADER_PYTHON'] = str(expected_python)
    if args.check:
        return check(code, tools, env, need_electron=not legacy) if bundled else 0
    if args.test_electron:
        command = [str(node / "node.exe"), str(code / "electron" / "test-desktop.cjs")]
    elif legacy:
        env['MEDIA_DL_RUNTIME_PREPARED'] = '1'
        command = [env["YT_DOWNLOADER_PYTHON"], "-B", "-X", "utf8", str(code / "yt_dlp_gui.py")]
    else:
        command = [str(modules / "electron" / "dist" / "electron.exe"), str(code)]
    return subprocess.call(command, cwd=code, env=env)


if __name__ == "__main__":
    try:
        result = main()
    except (OSError, ValueError, RuntimeError, ImportError, subprocess.SubprocessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        result = 1
    if result and os.environ.get('MEDIA_DL_CMD_LAUNCH') != '1' and sys.stdin and sys.stdin.isatty():
        try: input('启动未完成，请查看上方原因。按回车关闭…')
        except (EOFError, OSError): pass
    raise SystemExit(result)
