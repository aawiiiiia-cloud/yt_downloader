"""Repair only missing/broken dependencies, then launch either desktop interface."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

CODE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE))
import bootstrap
import pot_provider
import provider_setup


def notice(message):
    print('[依赖] ' + message, flush=True)


def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=kwargs.pop('timeout', 20), **kwargs)


def package_state(pins):
    probe = '''import importlib,importlib.metadata,json,sys,csv,io
pins=json.loads(sys.argv[1]);bad={}
for name,(minimum,module) in pins.items():
 try:
  version=importlib.metadata.version(name)
 except importlib.metadata.PackageNotFoundError:
  bad[name]=[minimum,'缺失'];continue
 try:
  importlib.import_module(module)
  dist=importlib.metadata.distribution(name)
  record=dist.read_text('RECORD')
  if record is None or any(row[0].endswith(('.js','.pyd','.dll','.pem')) and not dist.locate_file(row[0]).is_file() for row in csv.reader(io.StringIO(record)) if row):
   raise ImportError('missing runtime data')
 except Exception:
  bad[name]=[version,'无法导入']
print(json.dumps(bad))
'''
    result = run([sys.executable, '-B', '-X', 'utf8', '-c', probe, json.dumps(pins)], cwd=CODE, timeout=35)
    if result.returncode:
        raise RuntimeError('Python 依赖检查进程失败：' + result.stderr[-1200:])
    bad = json.loads(result.stdout)
    from importlib import metadata
    for name, (minimum, _module) in pins.items():
        if name not in bad:
            current = metadata.version(name)
            if not bootstrap._version_at_least(current, minimum):
                bad[name] = [minimum, '版本不满足项目要求']
    return bad


def ensure_packages(pins):
    bad = package_state(pins)
    if not bad:
        notice('Python 库已就绪，保留现有版本。'); return
    packages = [f'{name}=={version}' for name, (version, _reason) in bad.items()]
    notice('补全 Python 库：' + '、'.join(bad))
    errors = []
    for index in ('https://pypi.org/simple', 'https://pypi.tuna.tsinghua.edu.cn/simple'):
        command = [sys.executable, '-B', '-m', 'pip', 'install', '--no-input', '--disable-pip-version-check',
            '--only-binary=:all:', '--force-reinstall', '--no-deps', '--retries', '2', '--timeout', '30',
            '--index-url', index, *packages]
        # Only failed packages are reinstalled; never blanket-upgrade the working environment.
        # pip bounds idle network reads itself; do not abort a slow but progressing download.
        result = subprocess.run(command)
        if result.returncode == 0 and not package_state(pins): return
        errors.append(index)
        notice('本次补全未成功，尝试备用 Python 下载源。')
    raise RuntimeError('Python 依赖补全失败，已尝试官方源和备用源；请检查网络后重试。')


@contextmanager
def setup_lock(tools):
    import msvcrt
    tools.mkdir(parents=True, exist_ok=True)
    with (tools / '.runtime-setup.lock').open('a+b') as stream:
        stream.seek(0); stream.write(b'0'); stream.flush()
        end = time.monotonic() + 600
        while True:
            try: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1); break
            except OSError:
                if time.monotonic() > end: raise RuntimeError('另一个启动程序仍在补全依赖，请稍后重试')
                notice('等待另一个启动程序完成依赖补全…'); time.sleep(3)
        try: yield
        finally: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def remove_managed(path, roots):
    if path.is_symlink() or path.is_junction(): raise RuntimeError('拒绝清理链接目录')
    path = path.resolve()
    if not any(path != root.resolve() and path.is_relative_to(root.resolve()) for root in roots):
        raise RuntimeError('拒绝清理依赖目录以外的路径')
    if path.exists(): shutil.rmtree(path)


def publish(target, build, validate, roots):
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name('.' + target.name + '.stage-' + uuid.uuid4().hex)
    backup = target.with_name('.' + target.name + '.backup-' + uuid.uuid4().hex)
    try:
        build(stage)
        if not validate(stage): raise RuntimeError(f'{target.name} 补全后的校验失败，原文件未替换')
        if target.exists(): target.rename(backup)
        try: stage.rename(target)
        except OSError:
            if backup.exists(): backup.rename(target)
            raise
        if backup.exists():
            try: remove_managed(backup, roots)
            except OSError: notice('旧依赖备份暂被占用，保留备份，不影响本次启动。')
    finally:
        if stage.exists(): remove_managed(stage, roots)


def node_ready(folder):
    node = folder / 'node.exe'
    if not (node.is_file() and (folder / 'npm.cmd').is_file() and
            (folder / 'node_modules/npm/bin/npm-cli.js').is_file()): return False
    try:
        version = run([node, '--version']).stdout.strip().lstrip('v')
        npm = run([node, folder / 'node_modules/npm/bin/npm-cli.js', '--version'])
        return bootstrap._version_at_least(version, '22.12.0') and npm.returncode == 0
    except (OSError, subprocess.SubprocessError): return False


def copy_node(origin, target):
    target.mkdir()
    shutil.copy2(origin / 'node.exe', target / 'node.exe')
    for name in ('npm','npm.cmd','npm.ps1','npx','npx.cmd','npx.ps1'):
        if (origin / name).is_file(): shutil.copy2(origin / name, target / name)
    shutil.copytree(origin / 'node_modules/npm', target / 'node_modules/npm')


@contextmanager
def installing_tools(tools, **folders):
    saved = {name: getattr(bootstrap, name) for name in ('TOOLS_DIR', *folders)}
    old_path = os.environ.get('PATH', '')
    try:
        bootstrap.TOOLS_DIR = tools
        for name, folder in folders.items(): setattr(bootstrap, name, folder)
        # Download helpers must populate their stage, rather than accept another system copy.
        windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        os.environ['PATH'] = str(windows / 'System32') + os.pathsep + str(windows)
        yield
    finally:
        for name, value in saved.items(): setattr(bootstrap, name, value)
        os.environ['PATH'] = old_path


def ensure_node(tools, bundled):
    target = tools / 'node'
    if node_ready(target): return target
    candidates = [CODE / 'build_cache', Path.home() / '.yt_dlp_tools/node']
    found = shutil.which('node')
    if found: candidates.append(Path(found).parent)
    origin = next((p for p in candidates if p.resolve() != target.resolve() and node_ready(p)), None)
    if origin and not bundled: return origin
    notice('补全 Node 和同目录 npm。')
    def build(stage):
        if origin: copy_node(origin, stage)
        else:
            with installing_tools(tools / 'repair-cache', NODE_DIR=stage):
                if not bootstrap._ensure_node(notice): raise RuntimeError('Node/npm 下载失败')
    publish(target, build, node_ready, (tools,))
    return target


def ffmpeg_ready(folder):
    return all((folder / name).is_file() and bootstrap._executable_works(folder / name, '-version')
        for name in ('ffmpeg.exe', 'ffprobe.exe'))


def ensure_ffmpeg(tools, bundled):
    target = tools / 'ffmpeg'
    if ffmpeg_ready(target): return target
    candidates = [CODE / 'build_cache', CODE / 'dist/yt_dlp_gui/tools', Path.home() / '.yt_dlp_tools/ffmpeg']
    found = shutil.which('ffmpeg')
    if found: candidates.append(Path(found).parent)
    origin = next((p for p in candidates if p.resolve() != target.resolve() and ffmpeg_ready(p)), None)
    if origin and not bundled: return origin
    notice('补全 FFmpeg 和 FFprobe。')
    def build(stage):
        if origin:
            stage.mkdir()
            for name in ('ffmpeg.exe','ffprobe.exe'): shutil.copy2(origin / name, stage / name)
        else:
            with installing_tools(tools / 'repair-cache', FFMPEG_DIR=stage):
                if not bootstrap._ensure_ffmpeg(notice): raise RuntimeError('FFmpeg 下载失败')
    publish(target, build, ffmpeg_ready, (tools,))
    return target


def electron_ready(modules, code=CODE):
    try:
        lock = json.loads((code / 'package-lock.json').read_text(encoding='utf-8'))
        for relative, spec in lock['packages'].items():
            if not relative: continue
            if not relative.startswith('node_modules/'): return False
            package = json.loads((modules / relative.removeprefix('node_modules/') / 'package.json').read_text(encoding='utf-8'))
            if package['version'] != spec['version']: return False
        version = lock['packages']['node_modules/electron']['version']
        if (modules / 'electron/path.txt').read_text().strip() != 'electron.exe': return False
        for name in ('index.js','cli.js','checksums.json'):
            if not (modules / 'electron' / name).is_file(): return False
        dist = modules / 'electron/dist'
        for name in ('electron.exe','icudtl.dat','resources.pak','locales/en-US.pak','resources/default_app.asar'):
            if not (dist / name).is_file(): return False
        if (dist / 'version').read_text().strip().lstrip('v') != version: return False
        env = {**os.environ, 'ELECTRON_RUN_AS_NODE': '1'}
        result = run([dist / 'electron.exe', '-p', 'process.versions.electron'], env=env)
        return result.returncode == 0 and result.stdout.strip() == version
    except (OSError, ValueError, KeyError, subprocess.SubprocessError): return False


def ensure_electron(tools, node, bundled):
    target = tools / 'node_modules' if bundled else CODE / 'node_modules'
    if electron_ready(target): return target
    candidates = [CODE / 'node_modules'] if bundled else []
    origin = next((p for p in candidates if p.resolve() != target.resolve() and electron_ready(p)), None)
    notice('补全项目锁定版本的 Electron 及 JavaScript 依赖。')
    def build(stage):
        if origin: shutil.copytree(origin, stage); return
        work = stage.with_name(stage.name + '-npm')
        work.mkdir()
        try:
            for name in ('package.json','package-lock.json'): shutil.copy2(CODE / name, work / name)
            # electron install.js uses bundled checksums.json; mirrors never disable checksum verification.
            for name in ('electron_use_remote_checksums','npm_config_electron_use_remote_checksums',
                    'ELECTRON_SKIP_BINARY_DOWNLOAD','ELECTRON_OVERRIDE_DIST_PATH','ELECTRON_CUSTOM_DIR','ELECTRON_CUSTOM_FILENAME'):
                os.environ.pop(name, None)
            os.environ['npm_config_ignore_scripts'] = 'false'
            os.environ['electron_config_cache'] = str(tools / 'repair-cache/electron')
            errors = []
            for mirror in ('https://npmmirror.com/mirrors/electron/', ''):
                if mirror: os.environ['ELECTRON_MIRROR'] = mirror
                else: os.environ.pop('ELECTRON_MIRROR', None)
                try:
                    provider_setup._run_npm(node / 'npm.cmd', ['ci', '--include=dev', '--include=optional'], work, notice, tools / 'repair-cache/npm')
                    if electron_ready(work / 'node_modules'):
                        (work / 'node_modules').rename(stage); return
                except RuntimeError as error: errors.append(str(error))
                notice('Electron 当前下载源未成功，切换官方源，保留缓存。')
            raise RuntimeError('Electron 安装失败：' + '\n'.join(errors)[-2000:])
        finally:
            if work.exists(): remove_managed(work, (tools, CODE))
            os.environ.pop('ELECTRON_MIRROR', None)
    publish(target, build, electron_ready, (tools, CODE))
    return target


def ensure_provider(tools):
    target = tools / 'bgutil-provider'
    if pot_provider.provider_bundle_valid(target): return
    candidates = [CODE / 'tools/bgutil-provider', CODE / 'dist/yt_dlp_gui/tools/bgutil-provider', Path.home() / '.yt_dlp_tools/bgutil-provider']
    origin = next((p for p in candidates if p.resolve() != target.resolve() and pot_provider.provider_bundle_valid(p)), None)
    notice('补全 YouTube 本地解算辅助服务。')
    def build(stage):
        if origin: shutil.copytree(origin, stage)
        else: provider_setup.ensure_provider(stage, tools / 'provider_cache', notice)
    publish(target, build, pot_provider.provider_bundle_valid, (tools,))


def prepare(code=CODE, tools=None, bundled=None, need_electron=True):
    bundled = (code.parent / '启动程序/启动.py').is_file() if bundled is None else bundled
    tools = tools or Path(os.environ.get('YT_DOWNLOADER_TOOLS_DIR') or Path.home() / '.yt_dlp_tools')
    os.environ['YT_DOWNLOADER_TOOLS_DIR'] = str(tools)
    os.environ['PYTHONNOUSERSITE'] = '1'; os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    for name in ('PIP_TARGET','PIP_PREFIX','PIP_EXTRA_INDEX_URL','NODE_ENV','npm_config_omit','NPM_CONFIG_OMIT'):
        os.environ.pop(name, None)
    os.environ.update(PIP_USER='0', PIP_REQUIRE_VIRTUALENV='0', PIP_CONFIG_FILE=os.devnull)
    with setup_lock(tools):
        pins = json.loads((code / 'distribution/runtime-lock.json').read_text(encoding='utf-8'))['pythonPackages']
        ensure_packages(pins)
        node = ensure_node(tools, bundled)
        bootstrap._add_to_path(node)
        ffmpeg = ensure_ffmpeg(tools, bundled)
        bootstrap._add_to_path(ffmpeg)
        modules = ensure_electron(tools, node, bundled) if need_electron else (tools / 'node_modules' if bundled else code / 'node_modules')
        if not pot_provider.provider_bundle_valid(tools / 'bgutil-provider') and not node_ready(tools / 'node'):
            publish(tools / 'node', lambda stage: copy_node(node, stage), node_ready, (tools,))
        ensure_provider(tools)
    env = os.environ.copy()
    env.pop('ELECTRON_RUN_AS_NODE', None)
    env.update(YT_DOWNLOADER_PYTHON=sys.executable, NODE_PATH=str(modules))
    notice('依赖检查完成，可以启动。')
    return tools, modules, node, env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('collector','legacy'), default='collector')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--test-electron', action='store_true')
    args = parser.parse_args()
    tools, modules, node, env = prepare(need_electron=args.mode != 'legacy')
    if args.check: return 0
    if args.test_electron: command = [node / 'node.exe', CODE / 'electron/test-desktop.cjs']
    elif args.mode == 'legacy': command = [sys.executable, '-B', '-X', 'utf8', CODE / 'yt_dlp_gui.py']
    else: command = [modules / 'electron/dist/electron.exe', CODE]
    return subprocess.call(list(map(str, command)), cwd=CODE, env=env)


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        notice('自动补全未完成：' + str(error)); raise SystemExit(1)
