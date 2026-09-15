"""Prepare the official native bgutil provider for source/build distributions."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

import bootstrap
from pot_provider import (
    PROVIDER_COMMIT,
    PROVIDER_VERSION,
    provider_bundle_valid,
    write_provider_manifest,
)


Progress = Callable[[str], None]
SOURCE_URL = (
    "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/"
    f"archive/{PROVIDER_COMMIT}.zip"
)
SOURCE_ARCHIVE_SHA256 = "2187d07011d927e1f03d328180927d5d8f82ab92448654410af699f044f35bd9"
SOURCE_ARCHIVE_MARKER = ".source-archive-sha256"
NPM_REGISTRIES = (
    ("npmmirror", "https://registry.npmmirror.com"),
    ("npm 官方源", "https://registry.npmjs.org"),
)
NPM_STALL_SECONDS = 180
NPM_ABSOLUTE_TIMEOUT = 20 * 60


def _sha256(path: Path) -> str:
    """Hash a downloaded archive before any extraction or script execution."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def provider_valid(root: Path) -> bool:
    """Use the same strict version-and-manifest validation as runtime."""
    return provider_bundle_valid(root)


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    base = destination.resolve()
    for member in zf.infolist():
        target = (destination / member.filename).resolve()
        if target != base and base not in target.parents:
            raise RuntimeError(f"压缩包包含越界路径: {member.filename}")
    zf.extractall(destination)


def extract_node_runtime(archive: Path, runtime: Path) -> Path:
    """Validate and atomically unpack one official Node zip; return npm.cmd."""
    unpack = runtime.with_name(f".{runtime.name}.unpack")
    shutil.rmtree(unpack, ignore_errors=True)
    unpack.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            if bad := zf.testzip():
                raise RuntimeError(f"Node 压缩包损坏: {bad}")
            _safe_extract(zf, unpack)
        roots = [p for p in unpack.iterdir() if p.is_dir()]
        if len(roots) != 1 or not (roots[0] / "node.exe").is_file():
            raise RuntimeError("Node 压缩包目录结构异常")
        staged = runtime.with_name(f".{runtime.name}.new")
        shutil.rmtree(staged, ignore_errors=True)
        roots[0].rename(staged)
        shutil.rmtree(runtime, ignore_errors=True)
        staged.rename(runtime)
    finally:
        shutil.rmtree(unpack, ignore_errors=True)
    npm_cmd = runtime / "npm.cmd"
    if not npm_cmd.is_file():
        raise RuntimeError("Node 压缩包中缺少 npm.cmd")
    return npm_cmd


PINNED_NODE_VERSION = "22.14.0"


def _paired_node(npm: Path) -> Path | None:
    """Return the Node executable installed beside npm, never one from elsewhere."""
    for name in (("node.exe",) if os.name == "nt" else ("node", "node.exe")):
        candidate = npm.parent / name
        if candidate.is_file():
            return candidate
    return None


def _runtime_usable(npm: Path, exact_node: str | None = None) -> bool:
    """Validate that npm and Node are a same-directory, supported runtime pair."""
    node = _paired_node(npm)
    if node is None or not npm.is_file():
        return False
    env = os.environ.copy()
    env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    try:
        node_version = subprocess.check_output(
            [str(node), "--version"], text=True, timeout=10,
            stderr=subprocess.DEVNULL, env=env,
        ).strip().lstrip("v")
        npm_version = subprocess.check_output(
            [str(npm), "--version"], text=True, timeout=15,
            stderr=subprocess.DEVNULL, env=env,
        ).strip()
        node_major = int(node_version.split(".", 1)[0])
        npm_major = int(npm_version.split(".", 1)[0])
        return node_major >= 22 and npm_major >= 9 and (
            exact_node is None or node_version == exact_node
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _node_build_runtime(cache: Path, progress: Progress) -> Path:
    """Return a validated Node/npm pair, preferring the pinned portable runtime."""
    runtime = cache / "node-build-runtime"
    npm_cmd = runtime / "npm.cmd"
    if _runtime_usable(npm_cmd, PINNED_NODE_VERSION):
        return npm_cmd

    # Source mode may already have downloaded a complete portable runtime into
    # ~/.yt_dlp_tools/node. It is accepted only when node and npm are paired.
    source_runtime_npm = cache.parent / "node" / "npm.cmd"
    if _runtime_usable(source_runtime_npm):
        return source_runtime_npm

    version = PINNED_NODE_VERSION
    archive = cache / f"node-v{version}-win-x64-full.zip"
    urls = [
        f"https://nodejs.org/dist/v{version}/node-v{version}-win-x64.zip",
        f"{bootstrap.NODE_MIRROR}/v{version}/node-v{version}-win-x64.zip",
    ]
    downloaded = False
    for index, url in enumerate(urls):
        progress("[PO Token] 下载构建所需的完整 Node/npm..." if index == 0 else
                 "[PO Token] Node 官方源失败，改用 npmmirror...")
        if bootstrap._download_with_progress(url, archive, progress):
            downloaded = True
            break
    if downloaded:
        if _sha256(archive) != bootstrap.PINNED_NODE_ARCHIVE_SHA256:
            archive.unlink(missing_ok=True)
            raise RuntimeError("provider 构建用 Node 归档 SHA-256 不匹配，已拒绝")
        extract_node_runtime(archive, runtime)
        archive.unlink(missing_ok=True)
        if not _runtime_usable(npm_cmd, PINNED_NODE_VERSION):
            raise RuntimeError("固定版 Node/npm 解压后版本或配对校验失败")
        return npm_cmd

    # Offline fallback: use a system installation only if npm has a sibling
    # Node executable and both satisfy upstream engine requirements.
    system_npm = shutil.which("npm.cmd") or shutil.which("npm")
    if system_npm and _runtime_usable(Path(system_npm)):
        progress("[PO Token] 固定版 Node 下载失败，使用已验证的同目录系统 Node/npm")
        return Path(system_npm)
    raise RuntimeError("无法取得配对且版本合格的 provider 构建 Node/npm")


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
        else:
            process.terminate()
            process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def _run_npm_attempt(
    npm: Path,
    args: list[str],
    cwd: Path,
    registry: str,
    cache: Path,
    progress: Progress,
) -> tuple[bool, str]:
    """运行一次 npm，并以“无输出时长”识别真正卡死，而非固定总时长。"""
    env = os.environ.copy()
    env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    env["npm_config_registry"] = registry
    env["npm_config_cache"] = str(cache)
    cache.mkdir(parents=True, exist_ok=True)
    command = [
        str(npm), *args,
        "--prefer-offline", "--no-audit", "--no-fund",
        "--fetch-retries=4", "--fetch-retry-mintimeout=10000",
        "--fetch-retry-maxtimeout=60000", "--fetch-timeout=120000",
        "--loglevel=info",
    ]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command, cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=flags,
    )
    output: queue.Queue[str | None] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output.put(line.rstrip())
        finally:
            output.put(None)

    threading.Thread(target=reader, daemon=True).start()
    started = last_activity = last_notice = time.monotonic()
    tail: list[str] = []
    stream_ended = False
    try:
        while process.poll() is None or not stream_ended:
            try:
                line = output.get(timeout=1)
                if line is None:
                    stream_ended = True
                elif line:
                    last_activity = time.monotonic()
                    tail.append(line)
                    del tail[:-12]
            except queue.Empty:
                pass
            now = time.monotonic()
            if now - last_notice >= 20:
                progress(f"[PO Token] npm 仍在处理（已用时 {int(now - started)} 秒）...")
                last_notice = now
            if now - last_activity > NPM_STALL_SECONDS:
                _stop_process_tree(process)
                return False, f"连续 {NPM_STALL_SECONDS} 秒无任何输出"
            if now - started > NPM_ABSOLUTE_TIMEOUT:
                _stop_process_tree(process)
                return False, f"超过 {NPM_ABSOLUTE_TIMEOUT // 60} 分钟安全上限"
        if process.returncode == 0:
            return True, ""
        return False, "\n".join(tail[-6:]) or f"退出码 {process.returncode}"
    finally:
        if process.poll() is None:
            _stop_process_tree(process)


def _run_npm(
    npm: Path, args: list[str], cwd: Path, progress: Progress, cache: Path,
) -> None:
    errors: list[str] = []
    for name, registry in NPM_REGISTRIES:
        progress(f"[PO Token] npm 使用{name}（失败会自动切换）...")
        ok, detail = _run_npm_attempt(npm, args, cwd, registry, cache, progress)
        if ok:
            return
        errors.append(f"{name}: {detail}")
        progress(f"[PO Token] {name}安装失败，准备切换下载源")
    raise RuntimeError("npm 依赖安装失败:\n" + "\n".join(errors))


def _audit_production_dependencies(npm: Path, cwd: Path, progress: Progress) -> None:
    """只审计最终生产依赖；不自动改写上游 package-lock。"""
    env = os.environ.copy()
    env["PATH"] = str(npm.parent) + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            [str(npm), "audit", "--omit=dev", "--json"],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=60, check=False,
        )
        report = json.loads(completed.stdout or "{}")
        if report.get("error") and not report.get("metadata"):
            progress("[警告] npm 在线审计不可用；未自动修改官方锁文件")
            return
        counts = report.get("metadata", {}).get("vulnerabilities", {})
        high = int(counts.get("high", 0) or 0)
        critical = int(counts.get("critical", 0) or 0)
        moderate = int(counts.get("moderate", 0) or 0)
        if high or critical:
            raise RuntimeError(
                f"provider 生产依赖审计发现 high={high}, critical={critical}，拒绝发布"
            )
        progress(
            f"[PO Token] 生产依赖审计: critical=0, high=0, moderate={moderate}"
        )
    except subprocess.TimeoutExpired:
        progress("[警告] npm 生产依赖审计 60 秒超时；未自动修改官方锁文件")
    except OSError as exc:
        progress(f"[警告] 无法启动 npm 生产依赖审计: {exc}")
    except json.JSONDecodeError:
        progress("[警告] npm 审计未返回可解析结果；未自动修改官方锁文件")


def _source_cache_valid(source: Path) -> bool:
    """Accept extracted source only when it came from the pinned verified ZIP."""
    package = source / "server" / "package.json"
    marker = source / SOURCE_ARCHIVE_MARKER
    try:
        metadata = json.loads(package.read_text(encoding="utf-8"))
        return (
            metadata.get("version") == PROVIDER_VERSION
            and marker.read_text(encoding="ascii").strip() == SOURCE_ARCHIVE_SHA256
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _patch_localhost_only(main_ts: Path) -> None:
    """Apply the two expected upstream host edits and fail closed on drift."""
    text = main_ts.read_text(encoding="utf-8")
    originals = ('host: "::"', 'host: "0.0.0.0"')
    counts = tuple(text.count(pattern) for pattern in originals)
    localhost_count = text.count('host: "127.0.0.1"')
    if counts == (1, 1) and localhost_count == 0:
        for pattern in originals:
            text = text.replace(pattern, 'host: "127.0.0.1"', 1)
        main_ts.write_text(text, encoding="utf-8")
    elif counts == (0, 0) and localhost_count == 2:
        # A verified extracted cache may already have been patched by an
        # interrupted previous build. Its exact expected result is idempotent.
        pass
    else:
        raise RuntimeError(
            "provider localhost 补丁与预期源码不一致，拒绝继续构建 "
            f"(wildcard occurrences={counts}, localhost={localhost_count})"
        )
    verified = main_ts.read_text(encoding="utf-8")
    if any(pattern in verified for pattern in originals) or verified.count(
        'host: "127.0.0.1"'
    ) != 2:
        raise RuntimeError("provider localhost 补丁验证失败，拒绝发布")


def _copy_corresponding_source(source: Path, staged: Path) -> None:
    """Ship the exact patched GPL source and build configuration used here."""
    destination = staged / "corresponding-source"
    source_root = source.resolve()

    def ignore_generated(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).resolve().relative_to(source_root)
        ignored: set[str] = set()
        if relative == Path("."):
            ignored.add(SOURCE_ARCHIVE_MARKER)
        if relative == Path("server"):
            ignored.update({"build", "node_modules"})
        return ignored.intersection(names)

    shutil.copytree(source, destination, ignore=ignore_generated)
    notice = (
        "# Corresponding source and local modifications\n\n"
        f"Upstream: https://github.com/Brainicism/bgutil-ytdlp-pot-provider\n\n"
        f"Upstream commit: `{PROVIDER_COMMIT}`\n\n"
        f"Provider version: `{PROVIDER_VERSION}`\n\n"
        f"Verified source archive SHA-256: `{SOURCE_ARCHIVE_SHA256}`\n\n"
        "This directory is the complete corresponding upstream source used to "
        "build the bundled provider, excluding generated `server/build` and "
        "installed `server/node_modules` directories. The distributed runtime "
        "copies of those generated files are located under `server/`.\n\n"
        "## Local modification\n\n"
        "In `server/src/main.ts`, the two upstream listener hosts `::` and "
        "`0.0.0.0` were each changed to `127.0.0.1`. No other source change "
        "is made by provider_setup.py. This prevents the transient service from "
        "being reachable from other network interfaces.\n\n"
        "## Rebuild\n\n"
        "From `corresponding-source/server`, with Node 22.14.0 and npm 9 or "
        "newer: run `npm ci`, run the local TypeScript compiler at "
        "`node_modules/typescript/bin/tsc`, then run `npm prune --omit=dev`.\n"
    )
    (destination / "LOCAL_MODIFICATIONS.md").write_text(notice, encoding="utf-8")
    (staged / "SOURCE_AND_MODIFICATIONS.md").write_text(notice, encoding="utf-8")


def ensure_provider(destination: Path, cache: Path, progress: Progress = print) -> Path:
    """Build a version-matched provider, retaining a valid existing copy."""
    destination = destination.resolve()
    cache = cache.resolve()
    if provider_valid(destination):
        progress(f"[PO Token] 复用 provider v{PROVIDER_VERSION}: {destination}")
        return destination

    # 版本化目录避免旧实现遗留的 .git/只读文件阻塞安全构建迁移。
    source = cache / f"bgutil-provider-source-{PROVIDER_VERSION}-{PROVIDER_COMMIT[:12]}"
    server = source / "server"
    if not _source_cache_valid(source):
        shutil.rmtree(source, ignore_errors=True)
        if source.exists():
            raise RuntimeError(f"无法清理失效的 provider 源码缓存: {source}")
        archive = cache / f"bgutil-provider-{PROVIDER_VERSION}.zip"
        source_urls = [SOURCE_URL, *(proxy + SOURCE_URL for proxy in bootstrap.GITHUB_PROXIES)]
        verified_archive = False
        for index, url in enumerate(source_urls):
            if index == 0:
                progress(f"[PO Token] 下载官方 provider v{PROVIDER_VERSION} 源码...")
            else:
                progress("[PO Token] 官方源失败，尝试 GitHub 代理镜像...")
                archive.unlink(missing_ok=True)
            if bootstrap._download_with_progress(url, archive, progress):
                actual_hash = _sha256(archive)
                if actual_hash == SOURCE_ARCHIVE_SHA256:
                    verified_archive = True
                    break
                progress(
                    "[PO Token] provider 源码归档 SHA-256 不匹配，"
                    "已拒绝并删除该下载"
                )
                archive.unlink(missing_ok=True)
        if not verified_archive:
            raise RuntimeError("官方 bgutil provider 源码下载失败或完整性不匹配")
        unpack = cache / ".provider-unpack"
        shutil.rmtree(unpack, ignore_errors=True)
        unpack.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as zf:
                if bad := zf.testzip():
                    raise RuntimeError(f"provider 源码压缩包损坏: {bad}")
                _safe_extract(zf, unpack)
            roots = [p for p in unpack.iterdir() if p.is_dir()]
            if len(roots) != 1:
                raise RuntimeError("provider 源码压缩包目录结构异常")
            roots[0].rename(source)
            (source / SOURCE_ARCHIVE_MARKER).write_text(
                SOURCE_ARCHIVE_SHA256, encoding="ascii",
            )
        finally:
            shutil.rmtree(unpack, ignore_errors=True)
            archive.unlink(missing_ok=True)

        if not _source_cache_valid(source):
            shutil.rmtree(source, ignore_errors=True)
            raise RuntimeError("provider 源码解压后来源/版本校验失败")

    # Upstream 1.3.2 listens on all interfaces.  The GUI only needs localhost;
    # keep the transient token service inaccessible to other LAN machines.
    main_ts = server / "src" / "main.ts"
    _patch_localhost_only(main_ts)

    npm = _node_build_runtime(cache, progress)
    progress("[PO Token] 安装官方锁定依赖并编译 provider（首次构建耗时较长）...")
    npm_cache = cache / "npm-cache"
    _run_npm(npm, ["ci"], server, progress, npm_cache)
    # Avoid npx downloading an unrelated package: invoke the locally installed compiler.
    tsc = server / "node_modules" / "typescript" / "bin" / "tsc"
    node = _paired_node(npm)
    if node is None:
        raise RuntimeError("provider 编译时 Node/npm 不属于同一安装目录")
    subprocess.check_call([str(node), str(tsc)], cwd=str(server))
    _run_npm(npm, ["prune", "--omit=dev"], server, progress, npm_cache)
    _audit_production_dependencies(npm, server, progress)

    if not (server / "build" / "main.js").is_file():
        raise RuntimeError("provider 编译完成但缺少 server/build/main.js")
    staged = destination.with_name(f".{destination.name}.new")
    shutil.rmtree(staged, ignore_errors=True)
    (staged / "server").mkdir(parents=True)
    for name in ("build", "node_modules"):
        shutil.copytree(server / name, staged / "server" / name)
    for name in ("package.json", "package-lock.json", "README.md"):
        if (server / name).is_file():
            shutil.copy2(server / name, staged / "server" / name)
    license_file = source / "LICENSE"
    if not license_file.is_file():
        raise RuntimeError("provider 源码缺少 GPL LICENSE，拒绝发布")
    shutil.copy2(license_file, staged / "LICENSE")
    _copy_corresponding_source(source, staged)
    (staged / ".provider-version").write_text(PROVIDER_VERSION, encoding="utf-8")
    write_provider_manifest(staged)
    if not provider_valid(staged):
        raise RuntimeError("provider 安全清单生成后验证失败，拒绝发布")
    shutil.rmtree(destination, ignore_errors=True)
    staged.rename(destination)
    progress(f"[PO Token] provider v{PROVIDER_VERSION} 已就绪")
    return destination
