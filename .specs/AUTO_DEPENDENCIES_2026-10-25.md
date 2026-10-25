# Windows 启动入口自动补全依赖

入口：`start-collector.cmd` / `start.cmd` → `distribution/ensure-runtime.ps1` → `distribution/runtime_setup.py`。
直接双击 `yt_dlp_gui.py` 也先转交专用环境再导入 GUI/业务模块，避免误用系统 Python；不改变冻结产物及带参数自检入口。
整理包“启动程序”的三个 CMD 也转到这两个源码入口，避免检查逻辑分散及循环调用。

## 旧版经验及本轮处理

- Node 必须与 npm 配对；复用完整工具目录，安装时保留 npm CLI，满足 Electron 的 Node 22.12+ 要求。
- 复用 `bootstrap.py` 的连续无数据超时、断点 Range 长度验证、同源重试及镜像切换；更换内容源时清除旧断点。
- Node 使用既有固定 SHA-256，FFmpeg 继续要求上游资产 digest；Electron 的 npm 锁文件及自身 checksums.json 保持启用。
- 本地 `yt-dlp-ejs` 及固定源码/本机监听的 PO Token provider 保留，provider 缺失时复用或按旧构建流程补齐。
- 不 blanket upgrade：只重新安装缺失、不能导入或不满足最低版本的 Python 包；损坏包保留已安装版本号。
- 新依赖安装到临时目录，验证成功再发布，替换失败恢复原目录；安装过程加锁，应用运行前释放锁。
- 解压/清理目标受目录边界约束，拒绝越界压缩路径；系统 PATH 及其他项目运行环境不修改。

## Python 启动前提

缺少 Python 时不能先执行 Python 修复器，因此第一层使用 Windows PowerShell。源码模式优先复用明确配置的
隔离 Python；拒绝直接往外部基础 Python（例如 ComfyUI）安装依赖。新环境使用固定 Python 3.13.11 Windows x64
独立发行版（Astral python-build-standalone 20260114 install_only，含 Tcl/Tk、pip），解压前校验固定 SHA-256。
整理包额外保留 `依赖工具/repair-cache/python.zip` 及摘要，Python 本身丢失时可先恢复，不要求预装系统 Python。
PowerShell 的文件摘要使用 .NET 内置接口，不依赖父进程的 PSModulePath。

## 范围

缺少网络时，可复用已存在的可运行工具和 Python 修复缓存；需要下载而连接失败时给出原因并停止，可重新启动重试。
不保证在完全断网且工具及缓存同时丢失时能修复。不更改用户 Cookies、登录验证、下载参数、WebRTC 或并发设置。
`--check` 会执行补全而不打开应用；旧版启动不为自身额外安装 Electron。

## 验证

- 204 项 Python 检查（含 11 项本轮补全/回滚/路径安全与直接 Python 入口检查）通过。
- 从“代码”内 CMD 入口通过 203 项 Electron 隔离检查及单独进程的重启恢复检查。
- 独立空目录中从本地修复包恢复 Python，通过 Tcl/Tk、pip 检查。
- 另一独立目录无 Python、无本地修复缓存，实际下载固定上游归档并通过摘要与运行验证；Python 3.13.11、Tcl 8.6.12、pip 25.3。
- 该次 44.6 MB 下载约 305 秒，持续有数据即继续等待，未因总时长而重下。
- 仅在独立测试副本删除 dhash.py 后实际补回；删除 EJS 的 core.min.js 后亦能补回，其他包版本保持不变。
- 验证残缺的 Node/npm、FFmpeg/FFprobe、Electron 及 provider 可复用本机有效工具并经临时目录验证后发布。
- 验证补全失败不替换原组件、发布失败可回滚、恶意越界 ZIP 被拒绝、包能导入但关键 JS 数据丢失仍能被发现。
- 本轮未新增真实网站下载验收，未改动真实账号凭据；仍是同一台 Windows 机器上的独立目录测试。
