# Electron 采集工具 · 第一阶段

日期：2026-10-24。界面暂名“拾集”，当前交付为可在本机运行的源码版。

## 启动与范围

双击项目根目录的 `start-collector.cmd`。旧版 Python 界面仍由 `start.cmd` 启动。

1. 左侧打开 YouTube 视频、B站视频或小红书笔记详情页；右侧自动解析，也可手动点击解析。
2. 视频选择实际可用画质，或选择 MP3 音频、中英文字幕；B站分P在右侧切换。
3. 小红书图文勾选所需图片，支持保留原格式或转为 JPG / PNG / WebP，沿用原图优先与完整性验证。
4. 加入队列后可继续浏览。下载任务支持进度、日志、取消、当次会话重试与打开保存目录。
5. 设置中选择保存目录、代理与自动解析。代理留空时，任务复用浏览器对目标网址解析出的系统代理路线。

可在左侧网页自行登录。YouTube 内嵌登录受 Google 限制时，使用“Edge 备用登录”；
也可按站点手动导入旧版登录文件。导入不会修改旧文件，采集启动时不自动导入账号。
登录状态提示代表检测到了该站点凭据，凭据是否仍有效以实际网页及解析结果为准。

第一阶段未迁移旧版媒体批处理界面，仍可从旧启动器使用。MissAV 和独立图片重命名交付目录未改动。
Behance、淘宝、京东等适配，以及安装包/便携 EXE，留待后续阶段。

## 本机运行环境

- Electron：45.0.0，锁定于 `package-lock.json`，项目内 `node_modules` 已安装。
- Node.js：开发启动需要 22.12 或更新版本。
- Python：优先 `YT_DOWNLOADER_PYTHON`，本机为 `D:\python-envs\yt-downloader\Scripts\python.exe`。
- Pillow 与其他 Python 依赖沿用 D 盘隔离环境；Node、ffmpeg 与 PO Token provider 沿用现有项目工具。
- 健康检查只检查并复用工具；Electron 启动过程中不会自动安装 Python 依赖。

新电脑需准备 Python 依赖、Node、ffmpeg 和 provider，再运行 `npm.cmd ci`。
Git 不包含这些运行环境、媒体、登录凭据和缓存。

## 架构与状态边界

`electron/main.cjs` 管理窗口、站点会话、任务队列和配置。
本地界面由 BrowserWindow 渲染，远端网页在独立 WebContentsView 中正常浏览。
网页开启 sandbox 与 contextIsolation，关闭 nodeIntegration；远端网页没有本机 IPC 桥。
本地面板通过受限 preload 调用固定操作，主进程校验请求来源。

`collector_worker.py` 每个任务运行一个 Python 子进程，通过标准输入输出传递 JSON 行。
采集桥不开放 HTTP 端口。YouTube 沿用原有 PO Token provider，该组件按原设计使用本机服务。
`collector_core.py` 提供无 Tkinter 依赖的站点适配；`download_core.py` 复用旧界面的下载参数，
避免新旧界面各维护一套格式、重试和完整性规则。

- YouTube：继续使用 yt-dlp、JS challenge 和已有 PO Token provider。
- B站：优先读取左侧播放器实际取得的元数据与播放信息，再由 yt-dlp 处理格式、下载和合并；
  这条路径已避开实测中独立 HTTP 请求拿不到播放器数据的问题。
- 小红书：优先使用当前笔记的已加载数据，沿用图片下载模块；视频沿用 yt-dlp 解析路径。
- 切页、切视频和切笔记会作废旧结果；晚返回的旧解析不能覆盖当前页面。
- B站分P切换重新加载对应页面，避免静态播放器数据仍指向上一段。
- 已加入队列的下载保留入队时的内容快照，切页不会取消它。
- 每个任务仅导出对应站点 Cookie，临时文件完成后删除；历史不保存 Cookie 或完整任务参数。
- 重启恢复登录、设置及历史；上次未完成任务标为中断，需重新解析下载，不自动恢复旧签名地址。

默认配置目录：`%USERPROFILE%\.media-collector`。可设置 `YT_COLLECTOR_HOME` 更换位置。
该目录包含浏览器登录状态，请勿提交 Git 或分享。

## 验证记录

本机 Windows、D 盘 Python 环境，2026-10-24：

| 检查 | 结果与范围 |
| --- | --- |
| Python 回归 | 124 项通过，覆盖旧界面、图文、批处理和新增采集核心 |
| JavaScript 逻辑 | 8 项通过，覆盖安全导航、Cookie 站点隔离、队列取消及重试 |
| Electron 实机受控页面 | 27 项通过，覆盖左右视图、真实 Python 解析、B站分P、小红书切页防串、慢资源不阻塞解析、选图入队、清理与历史 |
| Electron 实际重启 | 浏览器测试 Cookie、设置与完成任务历史均保留 |
| YouTube 公开样例 | `jNQXAC9IVRw`，浏览、解析通过；下载验证时 22 个格式，低清样本 449,867 字节，FFmpeg 完整解码通过 |
| B站公开样例 | `BV12iFezdEZS`，浏览、解析 9 个格式；下载样本 3,009,768 字节，FFmpeg 完整解码通过 |
| 小红书 | 公开首页浏览通过；受控笔记通过真实 Electron → Python 的图文解析、顺序和选择传递；真实账号笔记下载待验收 |

格式数量会随站点、地区与登录状态变化。公开样本只证明这两条内容的本机流程，
不代表所有视频、会员画质、付费内容、长视频或三站账号登录均已验收。
小红书真实图文下载与视频下载，以及用户常用链接，仍需用实际页面验收。

常规验证：

```powershell
& 'D:\python-envs\yt-downloader\Scripts\python.exe' -B -m unittest test_yt_dlp_gui test_xhs_image_downloader test_media_batch test_collector_core
npm.cmd run check
npm.cmd test
npm.cmd run test:electron
```

桌面测试使用 `electron-artifacts/` 中的隔离配置、受控页面及虚构登录数据，不读取真实账号。
Windows 当前自动运行环境中原生子视图截图返回 UnknownVizError，窗口合成截图也不可用；
因此功能断言不依赖自动截图，未据此宣称已完成完整视觉验收。
可显式设置 `YT_COLLECTOR_CAPTURE=1` 尝试捕获受控测试窗口，失败原因单列在报告 `warnings` 中。

`npm.cmd run test:live` 另外访问公开站点，默认仅获取元数据，不登录、不下载。
只有显式设置 `YT_COLLECTOR_TEST_DOWNLOAD=1` 时才会下载一个 360p 或以下样本并完整解码。
可用 `YT_COLLECTOR_TEST_SITE=youtube` 或 `bilibili` 限定站点。
公开网页检查使用全新隔离配置，不导入旧版登录。报告位于 `electron-artifacts/live-*-result.json`，不提交 Git。
样本下载验证另外保留 `live-*-download-result.json`，避免被后续仅元数据检查覆盖。

## Git 检查点

已有仓库继续使用，未重新初始化或推送远端。

- 工作分支：`codex/electron-collector-phase1`。
- 改造前、109 项测试通过的基线：`a990ea3`。
- 基线标签：`baseline-before-electron-20261024`。
- 第一阶段完成标签：`electron-phase1-20261024`。

日常查看可使用 `git log --oneline --decorate` 与 `git diff`。
如需回看旧源码，先保存当前更改，再从基线标签创建新的查看分支；不要直接覆盖正在修改的工作区。
新界面与旧启动入口并存，试用旧版不需要回滚源码。
