"""Validate and manage the bundled bgutil PO Token HTTP provider.

The Python yt-dlp plugin is only the client half. This module validates the
versioned provider bundle, starts its Node.js server on a private localhost
port, captures bounded diagnostics, and owns its process lifetime.

This module deliberately does not import :mod:`provider_setup`: build-time
code imports the validation helpers from here, keeping the dependency one-way.
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable


PROVIDER_VERSION = "1.3.2"
PROVIDER_COMMIT = "7511309af023b09788dc8f2efc96cc3671291e6c"
PROVIDER_MANIFEST = ".provider-manifest.json"
PROVIDER_MANIFEST_FORMAT = 1
Progress = Callable[[str], None]

_MANIFEST_TOP_LEVEL_FILES = (
    ".provider-version",
    "LICENSE",
    "SOURCE_AND_MODIFICATIONS.md",
    "server/package.json",
    "server/package-lock.json",
)
_MANIFEST_TREES = (
    "server/build",
    "server/node_modules",
    "corresponding-source",
)
_MAX_CAPTURE_BYTES = 32 * 1024
_MAX_DIAGNOSTIC_LINES = 8
_MAX_DIAGNOSTIC_CHARS = 2400


def _application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest without loading large modules whole."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_files(root: Path) -> dict[str, Path]:
    """Enumerate exactly the security-critical files covered by the manifest."""
    result: dict[str, Path] = {}
    for relative in _MANIFEST_TOP_LEVEL_FILES:
        path = root / Path(relative)
        if not path.is_file() or path.is_symlink():
            raise OSError(f"provider 缺少安全清单文件: {relative}")
        result[Path(relative).as_posix()] = path
    for relative in _MANIFEST_TREES:
        tree = root / Path(relative)
        if not tree.is_dir() or tree.is_symlink():
            raise OSError(f"provider 缺少安全清单目录: {relative}")
        for path in sorted(tree.rglob("*")):
            if path.is_symlink():
                raise OSError(f"provider 不允许符号链接: {path.relative_to(root)}")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = path
    return result


def write_provider_manifest(root: Path) -> Path:
    """Create a deterministic SHA-256 manifest for a completed provider bundle."""
    root = root.resolve()
    files = _manifest_files(root)
    payload = {
        "format": PROVIDER_MANIFEST_FORMAT,
        "algorithm": "sha256",
        "provider_version": PROVIDER_VERSION,
        "upstream_commit": PROVIDER_COMMIT,
        "files": {name: _sha256(path) for name, path in sorted(files.items())},
    }
    manifest = root / PROVIDER_MANIFEST
    temporary = root / f".{PROVIDER_MANIFEST}.new"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, manifest)
    return manifest


def provider_bundle_valid(root: Path) -> bool:
    """Strictly verify provider version, provenance metadata and every hash."""
    try:
        root = root.resolve(strict=True)
        marker = root / ".provider-version"
        if marker.is_symlink() or marker.read_text(encoding="utf-8").strip() != PROVIDER_VERSION:
            return False
        manifest_path = root / PROVIDER_MANIFEST
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return False
        if manifest.get("format") != PROVIDER_MANIFEST_FORMAT:
            return False
        if manifest.get("algorithm") != "sha256":
            return False
        if manifest.get("provider_version") != PROVIDER_VERSION:
            return False
        if manifest.get("upstream_commit") != PROVIDER_COMMIT:
            return False
        recorded = manifest.get("files")
        if not isinstance(recorded, dict):
            return False
        actual = _manifest_files(root)
        if set(recorded) != set(actual):
            return False
        digest_pattern = re.compile(r"[0-9a-f]{64}")
        for name, path in actual.items():
            expected = recorded.get(name)
            if not isinstance(expected, str) or not digest_pattern.fullmatch(expected):
                return False
            if not hmac.compare_digest(_sha256(path), expected):
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


_RUNTIME_CRITICAL_FILES = (
    ".provider-version",
    "server/package.json",
    "server/package-lock.json",
    "server/build/main.js",
)


def provider_bundle_runtime_valid(root: Path) -> bool:
    """快速核验日常运行所需关键文件，不遍历数千个 node_modules 文件。

    完整逐文件校验仍由 provider_bundle_valid 在安装、构建和发布阶段执行。
    日常启动只核对固定版本/来源，以及入口和依赖描述文件的清单摘要。
    """
    try:
        root = root.resolve(strict=True)
        manifest_path = root / PROVIDER_MANIFEST
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            return False
        if manifest.get("format") != PROVIDER_MANIFEST_FORMAT:
            return False
        if manifest.get("algorithm") != "sha256":
            return False
        if manifest.get("provider_version") != PROVIDER_VERSION:
            return False
        if manifest.get("upstream_commit") != PROVIDER_COMMIT:
            return False
        recorded = manifest.get("files")
        if not isinstance(recorded, dict) or len(recorded) < len(_RUNTIME_CRITICAL_FILES):
            return False
        digest_pattern = re.compile(r"[0-9a-f]{64}")
        for relative in _RUNTIME_CRITICAL_FILES:
            path = root / Path(relative)
            expected = recorded.get(relative)
            if path.is_symlink() or not path.is_file():
                return False
            if not isinstance(expected, str) or not digest_pattern.fullmatch(expected):
                return False
            if not hmac.compare_digest(_sha256(path), expected):
                return False
        return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def provider_candidates() -> list[Path]:
    """Return provider roots in preference order for bundled/source modes."""
    app = _application_dir()
    candidates = [app / "tools" / "bgutil-provider"]
    if not getattr(sys, "frozen", False):
        candidates.extend([
            app / "dist" / "yt_dlp_gui" / "tools" / "bgutil-provider",
            Path.home() / ".yt_dlp_tools" / "bgutil-provider",
        ])
    return candidates


def find_provider(strict: bool = False) -> Path | None:
    """Return the first valid provider; strict mode is reserved for build/install."""
    validator = provider_bundle_valid if strict else provider_bundle_runtime_valid
    for root in provider_candidates():
        if validator(root):
            return root
    return None


def provider_is_installed() -> bool:
    return find_provider() is not None


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _BoundedOutput:
    """Continuously drain a child pipe while retaining only its newest bytes."""

    def __init__(self, limit: int = _MAX_CAPTURE_BYTES) -> None:
        self._limit = limit
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, stream: BinaryIO | None) -> None:
        if stream is None:
            return

        def drain() -> None:
            try:
                while chunk := stream.read(4096):
                    with self._lock:
                        self._buffer.extend(chunk)
                        if len(self._buffer) > self._limit:
                            del self._buffer[:-self._limit]
            except (OSError, ValueError):
                pass
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        self._thread = threading.Thread(target=drain, name="pot-provider-log", daemon=True)
        self._thread.start()

    def finish(self, timeout: float = 0.5) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def text(self) -> str:
        with self._lock:
            data = bytes(self._buffer)
        return data.decode("utf-8", errors="replace")


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")
_SECRET_RE = re.compile(
    r"(?i)\b(cookie|authorization|password|passwd|secret|token|access[_-]?token|"
    r"refresh[_-]?token|visitor[_-]?data|content[_-]?binding)\b"
    r"(\s*[=:]\s*|\s+)([^\s,;]+)"
)
_JSON_SECRET_RE = re.compile(
    r'''(?i)((?:"|')?(?:cookie|authorization|password|passwd|secret|token|'''
    r'''access[_-]?token|refresh[_-]?token|visitor[_-]?data|content[_-]?binding)'''
    r'''(?:"|')?\s*:\s*(?:"|'))([^"']*)((?:"|'))'''
)


def _sanitized_tail(text: str) -> list[str]:
    """Return short diagnostics with credentials and control bytes removed."""
    text = _ANSI_RE.sub("", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1***:***@", text)
    text = _JSON_SECRET_RE.sub(r"\1<redacted>\3", text)
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    lines: list[str] = []
    for raw in text.splitlines():
        clean = "".join(ch for ch in raw if ch == "\t" or ord(ch) >= 32).strip()
        if clean:
            lines.append(clean[:500])
    selected = lines[-_MAX_DIAGNOSTIC_LINES:]
    while selected and sum(len(line) for line in selected) > _MAX_DIAGNOSTIC_CHARS:
        selected.pop(0)
    return selected


if os.name == "nt":
    from ctypes import wintypes

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class _WindowsKillJob:
    """Best-effort Windows Job Object that kills children when closed."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, handle: int) -> None:
        self._handle = handle

    @classmethod
    def attach(cls, proc: subprocess.Popen[bytes]) -> "_WindowsKillJob | None":
        if os.name != "nt" or not hasattr(proc, "_handle"):
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = cls._KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle, cls._EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle, wintypes.HANDLE(int(proc._handle)),  # type: ignore[attr-defined]
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            return None
        return cls(int(handle))

    def close(self) -> None:
        handle, self._handle = self._handle, 0
        if handle and os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(handle))


class PotProviderManager:
    """Own at most one validated provider process and never kill an external one."""

    def __init__(self, provider_root: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._url: str | None = None
        self._provider_root = provider_root.resolve() if provider_root else None
        self._capture: _BoundedOutput | None = None
        self._job: _WindowsKillJob | None = None

    @property
    def url(self) -> str | None:
        return self._url

    @staticmethod
    def _ping(url: str, timeout: float = 1.5) -> dict | None:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"{url}/ping", timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None

    @staticmethod
    def _report_capture(progress: Progress, capture: _BoundedOutput | None) -> None:
        if capture is None:
            return
        capture.finish()
        lines = _sanitized_tail(capture.text())
        if lines:
            progress("[PO Token] 服务诊断（已脱敏）:")
            for line in lines:
                progress(f"[PO Token]   {line}")

    def ensure_started(
        self,
        progress: Progress = print,
        cancel_event: threading.Event | None = None,
        timeout: float = 35.0,
    ) -> bool:
        """Start and health-check the provider, retrying three fresh ports."""
        with self._lock:
            if self._url and self._ping(self._url):
                return True
            if self._provider_root is not None:
                provider = self._provider_root
                valid = provider_bundle_runtime_valid(provider)
            else:
                provider = find_provider()
                valid = provider is not None
            if provider is None or not valid:
                progress("[PO Token] 生成服务缺失或完整性校验失败，4K/高码率格式可能不可用")
                return False
            node = shutil.which("node")
            if not node:
                progress("[PO Token] 找不到 Node.js，无法启动 token 生成服务")
                return False

        total_deadline = time.monotonic() + max(timeout, 0.3)
        for attempt in range(1, 4):
            if cancel_event is not None and cancel_event.is_set():
                self.stop()
                return False
            remaining = total_deadline - time.monotonic()
            if remaining <= 0:
                break
            port = _free_local_port()
            url = f"http://127.0.0.1:{port}"
            server = provider / "server"
            entry = server / "build" / "main.js"
            creationflags = 0
            startupinfo = None
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            capture = _BoundedOutput()
            try:
                proc = subprocess.Popen(
                    [node, str(entry), "--port", str(port)],
                    cwd=str(server),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
            except OSError as exc:
                progress(f"[PO Token] 第 {attempt}/3 次服务启动失败: {exc}")
                continue
            capture.start(getattr(proc, "stdout", None))
            try:
                job = _WindowsKillJob.attach(proc)
            except (OSError, AttributeError, ValueError):
                # 某些企业策略/嵌套 Job 会拒绝创建或分配；正常关闭仍走
                # terminate/taskkill，不能让加固措施本身阻断下载。
                job = None
            with self._lock:
                self._proc = proc
                self._url = url
                self._capture = capture
                self._job = job

            attempts_left = 4 - attempt
            attempt_budget = max(0.3, remaining / attempts_left)
            attempt_deadline = min(total_deadline, time.monotonic() + attempt_budget)
            failure_message = f"第 {attempt}/3 次服务启动超时"
            while time.monotonic() < attempt_deadline:
                if cancel_event is not None and cancel_event.is_set():
                    self.stop()
                    return False
                with self._lock:
                    if self._proc is not proc:
                        return False
                if proc.poll() is not None:
                    failure_message = f"第 {attempt}/3 次服务意外退出(returncode={proc.returncode})"
                    break
                if info := self._ping(url):
                    version = info.get("version", "未知")
                    if str(version) != PROVIDER_VERSION:
                        failure_message = (
                            f"第 {attempt}/3 次服务版本不匹配，"
                            f"期望 {PROVIDER_VERSION}，实际 {version}"
                        )
                        break
                    progress(f"[PO Token] 本地生成服务已启动 v{version} ({url})")
                    return True
                time.sleep(0.2)

            progress(f"[PO Token] {failure_message}")
            self.stop()
            self._report_capture(progress, capture)

        progress("[PO Token] 三次启动均失败，继续尝试普通格式（4K 可能不可用）")
        return False

    def apply_to_opts(self, opts: dict) -> None:
        """Configure yt-dlp to use this exact local provider instance."""
        if not self._url:
            return
        extractor_args = {
            name: {key: list(values) for key, values in arguments.items()}
            for name, arguments in opts.get("extractor_args", {}).items()
        }
        provider_args = extractor_args.setdefault("youtubepot-bgutilhttp", {})
        provider_args["base_url"] = [self._url]
        youtube_args = extractor_args.setdefault("youtube", {})
        youtube_args["fetch_pot"] = ["always"]
        opts["extractor_args"] = extractor_args

    def stop(self) -> None:
        """Stop only the process owned by this manager and release its Job."""
        with self._lock:
            proc, self._proc = self._proc, None
            capture, self._capture = self._capture, None
            job, self._job = self._job, None
            self._url = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                if job is not None:
                    job.close()
                    job = None
                elif os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False, timeout=10,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                else:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        if job is not None:
            job.close()
        if capture is not None:
            capture.finish()
