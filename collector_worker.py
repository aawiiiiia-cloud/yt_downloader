"""JSON-lines process bridge. One task per process, cancellation on stdin."""
from __future__ import annotations

import contextlib
import json
import sys
import threading

from collector_core import Cancelled, Collector, prepare_runtime, runtime_health
from collector_errors import error_payload


def run_provider(emit, cancel):
    """Keep one owned provider alive until Electron closes its control pipe."""
    from pot_provider import PotProviderManager
    manager = PotProviderManager()
    try:
        if not manager.ensure_started(lambda text: emit('log', text), cancel, timeout=20):
            emit('provider-ready', {'url': None})
            return {'stopped': True}
        emit('provider-ready', {'url': manager.url})
        while not cancel.wait(0.5):
            if not manager.is_running:
                raise RuntimeError('YouTube 辅助服务已停止，下次解析会重新启动')
        return {'stopped': True}
    finally:
        manager.stop()


def main() -> int:
    output = sys.stdout
    lock = threading.Lock()

    def emit(kind, data):
        with lock:
            output.write(json.dumps({"type": kind, "data": data}, ensure_ascii=False, default=str) + "\n")
            output.flush()

    cancel = threading.Event()
    service = None
    try:
        request = json.loads(sys.stdin.readline(2_000_001))

        def read_control():
            for line in sys.stdin:
                try:
                    if json.loads(line).get("command") == "cancel":
                        cancel.set()
                except (ValueError, AttributeError):
                    pass
            if request.get('command') == 'provider':
                cancel.set()
        threading.Thread(target=read_control, daemon=True).start()
        # Third-party print() must not corrupt the structured stdout protocol.
        with contextlib.redirect_stdout(sys.stderr):
            if request.get("command") == "download":
                emit("log", "下载器已启动，正在准备运行环境…")
            prepare_runtime()
            if request.get("command") == "download":
                emit("log", "运行环境已就绪，开始处理下载任务")
            command = request.get("command")
            if command == "health":
                result = runtime_health()
            elif command == 'provider':
                result = run_provider(emit, cancel)
            elif command == "login":
                from edge_login import run_login, SITE_CONFIGS
                from pathlib import Path
                site = request["site"]
                if site not in SITE_CONFIGS:
                    raise ValueError("不支持该站点登录")
                result = run_login(
                    Path(request["cookieFile"]), site=site, progress=lambda text: emit("log", text),
                    cancel_event=cancel, proxy=request.get("proxy") or "", verify=(site == "youtube"),
                )
                result = {"site": site, "cookieFile": str(result)}
            else:
                service = Collector(request, emit, cancel)
                if command not in {"inspect", "download"}:
                    raise ValueError("未知采集任务")
                result = service.inspect() if command == "inspect" else service.download()
            if cancel.is_set():
                raise Cancelled("操作已取消")
            emit("result", result)
        return 0
    except Exception as exc:
        cancelled = cancel.is_set() or isinstance(exc, Cancelled)
        emit('cancelled' if cancelled else 'error', str(exc) if cancelled else error_payload(exc))
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
