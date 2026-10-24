# Electron 采集工具 · 第一阶段

日期：2026-10-24。界面暂名“拾集”，当前交付为可在本机运行的源码版。

## 启动与范围

双击项目根目录的 `start-collector.cmd`。旧版 Python 界面仍由 `start.cmd` 启动。

1. 左侧打开 YouTube 视频、B站视频或小红书笔记详情页；右侧自动解析，也可手动点击解析。
2. 视频选择实际可用画质，或选择 MP3 音频、中英文字幕；B站分P在右侧切换。
3. 小红书图文勾选所需图片，支持保留原格式或转为 JPG / PNG / WebP，沿用原图优先与完整性验证。
4. 加入队列后可继续浏览。下载任务支持进度、日志、取消、当次会话重试与打开保存目录。
5. 设置中选择保存目录、代理与自动解析。代理留空时，任务复用浏览器对目标网址解析出的系统代理路线。
6. 站点按钮切换分组；组内支持多个标签，视频或笔记详情链接另开标签，`＋` 新建首页，`×` 关闭标签。

YouTube 登录入口使用 Edge，并在验证后才能下载；B站和小红书可在左侧网页自行登录。
也可点击“导入Cookies”，在文件选择窗口中选择本站点的 Netscape 格式 Cookies 文本文件。
YouTube 仅显示 Edge 登录和导入 Cookies；其他两站额外显示网页登录。
导入不会修改源文件，取消选择不改变当前登录；采集启动时不自动导入账号。
YouTube 的“已登录并验证”需要服务端登录标志确认，缓存最多 5 分钟；仅检测到 Cookie 时显示“登录待验证”。
B站和小红书登录状态提示仍代表检测到了该站点凭据，是否有效以实际网页及解析结果为准。

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
本地界面由 BrowserWindow 渲染，每个已加载标签有独立 WebContentsView，切换时保留原页面。
`electron/site-tabs.cjs` 管理分组、标签选择、详情去重与仅含导航信息的恢复记录。
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
- 同一标签导航时作废旧结果；切换标签保留各自结果，后台解析只更新自己的标签，晚返回的旧解析不能覆盖新页面。
- 每个标签保留本次运行中的页面位置和采集选项；后台视图隐藏、静音并暂停媒体，关闭标签释放视图。
- B站分P切换重新加载对应页面，避免静态播放器数据仍指向上一段。
- B站 BV 页面读取快照时最多等待 8 秒，避免播放器数据晚于 DOM 就绪而误走不兼容的独立页面解析；取消或跳转会停止等待。
- 已加入队列的下载保留入队时的内容快照，切页不会取消它。
- 每个任务仅导出对应站点 Cookie，临时文件完成后删除；历史不保存 Cookie 或完整任务参数。
- 重启恢复登录、设置、标签网址及历史；只加载当前标签，其他标签选中后再加载。解析结果和勾选不跨重启保存。
- 中间分隔条可拖动、键盘微调、双击复位，宽度保存在设置中并在重启后恢复。原生网页收到拖动事件时由主进程转回本地界面，不隐藏或重新加载网页。
- 上次未完成任务标为中断，需重新解析下载，不自动恢复旧签名地址。

默认配置目录：`%USERPROFILE%\.media-collector`。可设置 `YT_COLLECTOR_HOME` 更换位置。
该目录包含浏览器登录状态，请勿提交 Git 或分享。

## 验证记录

本机 Windows、D 盘 Python 环境，2026-10-24：

| 检查 | 结果与范围 |
| --- | --- |
| Python 回归 | 130 项通过，覆盖旧界面、图文、批处理、采集核心及 YouTube 下载认证检查 |
| JavaScript 逻辑 | 25 项通过，覆盖分组和标签、导航、凭据隔离、队列、预览安全、大小时长和登录验证 |
| Electron 实机受控页面 | 74 项通过，新增文件选择与取消、无效文件及跨站过滤、导入后认证验证、跨原生网页拖动、尺寸限制、复位及键盘操作；保留多标签和下载回归 |
| Electron 实际重启 | 浏览器测试 Cookie、设置、分隔宽度、完成任务历史和标签组恢复；其他标签延迟加载 |
| 三站真实多标签 | YouTube 公开样例及用户提供的 B站、小红书页面，浏览、解析、封面、分组往返、解析结果保留和后台媒体暂停通过 |
| YouTube 公开样例 | `jNQXAC9IVRw`，浏览、解析通过；此前检查点的未登录下载样本 449,867 字节已完整解码。当前策略已改为必须登录后下载，该历史结果不代表当前允许匿名下载 |
| B站公开样例 | `BV12iFezdEZS`，浏览、解析 9 个格式；下载样本 3,009,768 字节，FFmpeg 完整解码通过 |
| 用户提供的 B站链接 | `BV1ALeX6kEEi`，解析 9 个格式，360p 视频和 AAC 音频下载共 13,886,619 字节，完整解码通过 |
| 用户提供的小红书链接 | `6ad40df8000000002803a450`《打工心得④》，9 张图全部下载并逐张完整解码，合计 6,815,666 字节，顺序 01–09 正确 |

格式数量会随站点、地区与登录状态变化。公开样本只证明所列内容的本机流程，
不代表所有视频、会员画质、付费内容、长视频或三站账号登录均已验收。
用户提供的两条链接均在全新隔离会话中完成验收，没有登录账号。
小红书视频、登录后专属内容和其他链接仍需分别验收。
当前已检查真实右侧面板截图；完整浏览窗口视觉验收与真实 YouTube 账号的完整登录流程仍需人工确认。
兼容修复与具体检查过程见 [ACCEPTANCE_2026-10-24.md](ACCEPTANCE_2026-10-24.md)。

常规验证：

```powershell
& 'D:\python-envs\yt-downloader\Scripts\python.exe' -B -m unittest test_yt_dlp_gui test_xhs_image_downloader test_media_batch test_collector_core
npm.cmd run check
npm.cmd test
npm.cmd run test:electron
```

桌面测试使用 `electron-artifacts/` 中的隔离配置、受控页面及虚构登录数据，不读取真实账号。
Windows 当前自动运行环境中原生子视图截图返回 UnknownVizError，窗口合成截图也不可用；
因此功能断言不依赖完整窗口截图。右侧本地面板可单独截图，已用于封面和信息显示的视觉检查。
可显式设置 `YT_COLLECTOR_CAPTURE=1` 尝试捕获受控测试窗口，失败原因单列在报告 `warnings` 中。

`npm.cmd run test:live` 另外访问公开站点，默认仅获取元数据，不登录、不下载。
只有显式设置 `YT_COLLECTOR_TEST_DOWNLOAD=1` 时才会下载一个 360p 或以下样本并完整解码。
可用 `YT_COLLECTOR_TEST_SITE=youtube` 或 `bilibili` 限定站点。
`YT_COLLECTOR_TEST_TARGETS` 可指定本机 JSON 文件，格式为 `[["站点", "完整网址"], ...]`；
支持 `youtube`、`bilibili`、`xiaohongshu`。含访问参数的链接文件应放在已忽略的 `electron-artifacts/`。
小红书图文默认只下载首图；同时设置 `YT_COLLECTOR_TEST_ALL_IMAGES=1` 可验证全部图片。
`YT_COLLECTOR_TEST_PREVIEW=1` 会验证资源预览、可见图片解码与面板上的大小时长，并尝试保存右侧截图。
`YT_COLLECTOR_TEST_TABS=1` 会往返已打开的真实站点分组，检查页面、解析结果和后台媒体状态。
`YT_COLLECTOR_TEST_LINK_TABS=1` 可另外点击页面中已有的其他详情链接，验证新标签路由，检查后关闭新标签。
公开检查保持隔离且不自动导入账号，YouTube 下载检查会按当前强制登录规则被拦截。
公开网页检查使用全新隔离配置，不导入旧版登录。报告位于 `electron-artifacts/live-*-result.json`，不提交 Git。
样本下载验证另外保留 `live-*-download-result.json`，避免被后续仅元数据检查覆盖。

## Git 检查点

已有仓库继续使用，未重新初始化或推送远端。

- 工作分支：`codex/electron-collector-phase1`。
- 改造前、109 项测试通过的基线：`a990ea3`。
- 基线标签：`baseline-before-electron-20261024`。
- 第一阶段完成标签：`electron-phase1-20261024`。
- 用户链接验收修复标签：`electron-phase1-acceptance-20261024`。
- 封面、媒体信息与登录规则标签：`electron-preview-auth-20261024`，详情见 [PREVIEW_AUTH_2026-10-24.md](PREVIEW_AUTH_2026-10-24.md)。
- 站点分组与多标签标签：`electron-site-tabs-20261024`，详情见 [SITE_TABS_2026-10-24.md](SITE_TABS_2026-10-24.md)。

日常查看可使用 `git log --oneline --decorate` 与 `git diff`。
如需回看旧源码，先保存当前更改，再从基线标签创建新的查看分支；不要直接覆盖正在修改的工作区。
新界面与旧启动入口并存，试用旧版不需要回滚源码。
