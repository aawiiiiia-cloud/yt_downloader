"""JSON-lines process bridge. One task per process, cancellation on stdin."""
from __future__ import annotations

import contextlib
import json
import sys
import threading

from collector_core import Cancelled, Collector, prepare_runtime, runtime_health


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
        threading.Thread(target=read_control, daemon=True).start()
        # Third-party print() must not corrupt the structured stdout protocol.
        with contextlib.redirect_stdout(sys.stderr):
            prepare_runtime()
            command = request.get("command")
            if command == "health":
                result = runtime_health()
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
        emit("cancelled" if cancel.is_set() or isinstance(exc, Cancelled) else "error", str(exc))
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
