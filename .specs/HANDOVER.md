# yt-dlp GUI 项目 · 交接文档

> 新 agent 会话开始时,把这份文档丢给 AI 先读一遍,能省 1-2 小时的重新对齐。

## 一、项目产出

Python Tkinter 媒体下载 GUI，基于 yt-dlp 下载视频，并内置小红书图文下载模块。
**两条交付线**:源码版(公司电脑,自动装环境)+ 打包版(exe,分发给众包同事)。

**文件清单:**

| 文件 | 说明 |
| --- | --- |
| `yt_dlp_gui.py` | 主脚本。GUI + 站点路由 + 设置持久化 + 双版本检测 |
| `bootstrap.py` | 源码版环境自检 + 自动安装(node/ffmpeg/yt-dlp),GUI 启动前跑 |
| `edge_login.py` | Edge + CDP 三站点内置登录，临时写入后原子更新凭据 |
| `xhs_image_downloader.py` | 小红书图文解析、完整性检查和图片原子下载 |
| `media_batch.py` / `media_batch_ui.py` | 图片/视频检测、查重、安全移动、图片重命名/裁剪及独立窗口 |
| `pot_provider.py` | 自动启动/健康检查/关闭本地 PO Token provider |
| `provider_setup.py` | 固定版本下载并编译官方 provider，供源码版和 build.py 复用 |
| `build.py` | 一键打包脚本(pyinstaller onedir + 工具下载),产出 dist/yt_dlp_gui/ |
| `requirements.txt` | yt-dlp, yt-dlp-ejs, PO Token provider, websocket-client, Pillow, dhash |
| `使用说明.txt` | 给众包同事的说明(build.py 会把它拷进 dist) |
| `test_yt_dlp_gui.py` / `test_xhs_image_downloader.py` / `test_media_batch.py` | GUI、图文和批处理回归测试，运行 `python -m unittest discover -v` |
| `.specs/HANDOVER.md` | 本文档 |
| `.specs/BUILD_NOTES.md` | 打包与分发笔记 |

## 二、当前功能

- **URL 输入**:多行文本框,每行一个,支持批量 / 播放列表；小红书可粘贴整段分享文案
- **保存目录**:文件对话框选择,默认 `~/Downloads`,支持"打开目录"按钮
- **分辨率**:最佳 / 4K / 1440p / 1080p / 720p / 480p / 360p / 仅音频
- **输出格式**:视频 mp4/webm/mkv;仅音频自动切换为 mp3/m4a/wav/flac/opus
- **音频码率**:仅音频模式下可选 128/192/256/320 kbps
- **账号登录**:一个入口分别登录 YouTube/Bilibili/小红书；按 URL 自动匹配凭据。新凭据临时写入，成功后原子替换，失败保留旧凭据
- **小红书图文**:优先使用完整 `fileId`/网页资源 ID 从 `sns-na-i*`、`sns-img-*` 原始素材 CDN 下载；`sns-webpic-*` 仅作可能带水印的最终兜底，视频帖子自动交还 yt-dlp 流程
- **provider 首次安装**:已加入 npm 持久缓存、npmmirror 优先、官方源回退和无输出停滞检测；打包版/旧 dist 成品仍优先复用
- **provider 日常启动**:不要遍历并哈希整个 `node_modules`（约数千小文件，企业杀毒会放大到数分钟）；安装/构建走严格校验，普通启动走版本、清单和关键入口快速校验
- **代理**:可选输入框,存到 `opts["proxy"]`。不提供科学上网,只转发已有代理
- **字幕**:可选下载中英双语自动+人工字幕(srt 格式)
- **进度反馈**:进度条 + 状态栏(文件名/百分比/速度/ETA)+ 深色日志窗口
- **中断下载**:任意时刻点"停止",通过 progress_hook 抛异常干净退出
- **环境自检**:启动时探测 yt-dlp / ffmpeg / JS runtime / PO token 插件
- **设置持久化**:`~/.yt_dlp_gui.json` 保存 save_dir/分辨率/格式/cookies/代理/码率,重启不丢
- **PO Token 完整链路**:插件与官方 Node provider 均为 1.3.2；YouTube 任务按需启动随机 localhost 端口，健康检查成功后传给 yt-dlp，任务结束/退出自动关闭
- **真实格式日志**:`[格式]` 显示实际下载流的 ID、分辨率、帧率、编码
- **媒体批处理**:图片/视频分辨率筛选、视频低码率筛选、SHA-256+dHash 图片查重、视频固定黑边建议；疑似文件安全移动到指定目录
- **图片批处理**:模板批量重命名及常用比例居中裁剪副本；当前不自动按 cropdetect 结果裁剪视频

## 三、已解决的 5 个非显性问题(重要,别踩重复的坑)

### 1. YouTube 反爬:必须传 Cookies

匿名请求会被 YouTube 判定为 bot,报 `Sign in to confirm you're not a bot`。

**解决方案**:GUI 里的 Cookies 下拉,两种模式:
- **浏览器直读**:`opts["cookiesfrombrowser"] = (browser_name,)`
- **文件读取**(推荐):`opts["cookiefile"] = "/path/to/cookies.txt"`

**踩坑点**:
- Chrome 127+ 引入 **App-Bound Encryption**,`cookiesfrombrowser` 常报"找不到 cookies database",就算路径对也读不到
- 用 "Get cookies.txt" 类扩展导出时,**必须停留在 YouTube 标签页**点导出,否则拿到的是当前标签页所在域的 Cookies,YouTube 认不出
- ★ 2026 新警告:yt-dlp 官方 FAQ 明确该类扩展有被证实为恶意软件的版本(窃取登录态),**别再用扩展导 cookies**。改用 Firefox 直读或命令行 `yt-dlp --cookies-from-browser firefox --cookies out.txt`

### 2. YouTube 视频 URL 混淆:必须 JS runtime

YouTube 视频 URL 里有个 `n` 参数是 JavaScript 动态算出来的。没有 JS runtime 解算,拿到的下载链接是错的,报 `The page needs to be reloaded`。

**解决方案**:代码启动时探测 PATH,优先 Node → Deno,通过 `js_runtimes` 参数告诉 yt-dlp。

**踩坑点**:
- yt-dlp **默认只会自动找 Deno**,即使 PATH 里有 Node 也不会自动用,必须显式指定
- **优先级选 Node 而不是 Deno**:企业环境 Deno 常被 EDR/杀软拦截,Node.js 更常见,IT 也更容易放行
- yt-dlp 2026 起,`js_runtimes` 参数格式从老的 list 改成了 **dict**:`{runtime_name: {}}`,老写法会报 `Invalid js_runtimes format`
- Node 必须是 **22.0.0+**(yt-dlp 2026 的 EJS 要求)

### 3. yt-dlp 2026:EJS 远程组件默认不启用

yt-dlp 2026 版本起,把 n-challenge 的 solver 脚本从内置改成**按需从 GitHub 下载**(EJS = External JavaScript)。出于安全考虑默认关闭,日志会警告 `Remote component challenge solver script was skipped`,导致 `n challenge solving failed`。

**解决方案**:显式传 `remote_components=["ejs:github"]`。

**副作用与限制**:
- ★ 2026 新方案:源码版 bootstrap 会 `pip install yt-dlp-ejs`;打包版通过 `--collect-data yt_dlp_ejs` 把脚本打进包。**只要 yt-dlp-ejs 本地存在,就优先用本地脚本,不触发 GitHub 请求**(已实测日志 `source: python package`)。墙内离线可用。`remote_components` 保留为兜底,不删。

### 4. Cookies 有效期短

cookies.txt 里 session token 通常几小时到几周,失败次数多了 YouTube 会主动失效当前 session。

**症状**:导过一次 cookies,首次能用,一段时间后又开始报 `Sign in to confirm`。

**解决方案**:重新登录 Firefox 后点"一键获取"刷新。给众包同事的说明里也写了。

### 5. Windows 用户名含中文的路径处理

用户名是 `夏伟瑞`,几次日志里出现 `C:\Users\夏伟瑞\...`。yt-dlp 和 Python `os.path` 都能正确处理 Unicode,但**部分外部工具(某些老版本 ffmpeg / Node)对含中文的临时路径可能报错**。目前没踩到,如果后续遇到诡异错误优先怀疑这点。代码统一用 `pathlib.Path`,禁字符串拼接。

### 6. 读 Firefox cookies 的列名坑(2026-08 实踩)

Firefox 的 `moz_cookies` 表用 **`host`** 列存域名,Chrome 才用 **`host_key`**。`_firefox_has_youtube_login` 里如果写成 `host_key`,sqlite 会抛 `no such column`,被 except 静默 catch 成 False → "检测到 Firefox 但没找到登录"。**只认 Firefox 登录 cookie 名**:`SID` / `LOGIN_INFO` / `__Secure-3PAPISID` / `__Secure-1PSID`(实测都存在)。改回 `host` 即恢复。

## 四、双版本架构

```
yt_dlp_gui.py
  main()
   ├─ _is_bundled()          # sys.frozen 检测:打包版 vs 源码版
   ├─ _setup_env()
   │    ├─ 打包版: 把 <exe所在目录>/tools/ 加进 PATH(工具内嵌)
   │    └─ 源码版: from bootstrap import bootstrap → 自动装 node/ffmpeg/yt-dlp
   ├─ 重新 import yt_dlp      # bootstrap 可能刚装好,顶层还是 None
   ├─ root = tk.Tk()
   └─ DownloaderGUI(root)     # __init__ 里 Settings() + 恢复设置

DownloaderGUI
   ├─ __init__        状态初始化 + Settings
   ├─ _build_ui       控件树(新增:码率下拉/代理行/"一键获取"/"从文件")
   ├─ _restore_ui_from_settings  重启恢复设置
   ├─ _check_env      yt-dlp/ffmpeg/node/PO token 探测
   ├─ _start_download 参数校验 → 起后台线程
   ├─ _run_download   线程内跑 yt_dlp.YoutubeDL().download()
   ├─ _progress_hook  yt-dlp 进度回调 → queue
   ├─ _build_ydl_opts ★ 组装 yt-dlp options(踩坑集中地)
   ├─ _poll_queue     主线程 120ms 轮询
   ├─ _request_stop   threading.Event → hook 抛 _UserCancelled
   ├─ _on_cookies_change / _one_click_cookies / _choose_cookies_file
   ├─ _detect_browser_with_youtube / _firefox_has_youtube_login
   └─ _on_close       窗口关闭保存设置
```

### bootstrap.py(源码版自动安装)

```
bootstrap() → {"ytdlp","node","ffmpeg"}
   ├─ _ensure_ytdlp   import 测试;缺则 pip install(失败回退清华镜像)
   ├─ _ensure_node    PATH → ~/.yt_dlp_tools/node/ → 下载 22+ LTS 便携版
   │                    (nodejs.org → npmmirror 兜底,取最新 LTS≥22)
   ├─ _ensure_ffmpeg  PATH → ~/.yt_dlp_tools/ffmpeg/ → 多源下载
   │                    (gyan.dev → BtbN → gh-proxy.com/ghfast.top/ghproxy.net)
   └─ 装完把目录加进当前进程 PATH,无需重启
```
下载到 `~/.yt_dlp_tools/`,无需管理员权限。打包版不 import 本模块。

### 线程模型

- **主线程 = UI 线程**,绝对不能阻塞(不然 GUI 会卡死)
- 下载在 `threading.Thread(daemon=True)` 后台线程里跑
- 跨线程通信通过 `queue.Queue`,主线程 `root.after(120ms)` 定时轮询
- **取消机制**:主线程置 `threading.Event`,下载线程的 `progress_hook` 里检测,`raise _UserCancelled` 冒泡出 yt-dlp

## 五、打包(build.py)

```bash
python build.py
```
产出 `dist/yt_dlp_gui/` 文件夹 + zip。关键参数:
- `--onedir`(非 onefile,避免 160MB 工具塞进 exe 启动慢)
- `--collect-data yt_dlp_ejs`(EJS 脚本本地化,墙内离线)
- `--hidden-import yt_dlp_plugins.extractor.getpot_bgutil{,_http,_script}`(命名空间包要逐个点名)
- `--collect-submodules yt_dlp`(动态 import 的 extractor)
- `--noupx`(避免杀软误报)

**已验证**:exe 能启动、EJS/插件打进包、frozen 环境真下载 YouTube 成功。
**待用户机器做**:ffmpeg 二进制下载(本机墙内 GitHub/gyan 限速,多源兜底已写在代码里,或手动把 ffmpeg.exe/ffprobe.exe 放 `build_cache/`)。详见 `.specs/BUILD_NOTES.md`。

## 六、环境依赖

| 依赖 | 源码版 | 打包版 | 备注 |
| --- | --- | --- | --- |
| Python 3.11+ | ✅ 必需(需用户装) | 内嵌 | 跟随 yt-dlp 当前推荐版本 |
| yt-dlp 2026.08.19 | 自动 pip 装 | 打包进 exe | 该版本实测目标视频可见 2160p |
| yt-dlp-ejs | 自动 pip 装 | 打包进 exe | ★ 墙内离线关键 |
| bgutil-ytdlp-pot-provider | 自动 pip 装 | 打包进 exe | PO token 插件 |
| bgutil Node provider 1.3.2 | 首次自动编译 | tools/bgutil-provider | 与 Python 插件必须同版本 |
| Node.js 22+ | 自动下载便携版 | tools/node.exe | 公司挡 deno,用 node |
| ffmpeg | 自动下载便携版 | tools/ffmpeg.exe | 合并高清/转音频必需 |
| GitHub/PyPI/镜像 可达 | 首次自动装时 | 不需要 | 见 BUILD_NOTES |

## 七、已知局限(仍未做)

- **单任务顺序执行**:多 URL 是一个一个下,没有并发和独立进度显示
- **YouTube 之外的站点未测试**:yt-dlp 支持 1000+ 站点,理论可用,但 UI 文案都是 YouTube 语境
- **无下载历史**:退出即丢,记不住上次下过啥
- **无自定义文件名模板**:硬编码 `%(title)s [%(id)s].%(ext)s`
- **YouTube SABR 持续变化**:provider 解决 token，不等于永远保证所有格式；若官方再次只返回 SABR，仍需跟进 yt-dlp。日志必须以 `[格式]` 实际分辨率为准

## 八、给下一个 agent 的操作建议

1. **先跑通再改**:装齐环境,用一个已知能下载的 YouTube URL 走一遍,确认基线正常。只有基线跑通了,才能判断后续改动有没有引入回归。
2. **改动分小步**:每次改一个功能,改完立刻本地验证一次真下载。
3. **改 `_build_ydl_opts` 时要特别小心**:这个方法是踩坑的集中地。里面的 Cookies / JS runtime / remote_components 三个 block 看起来冗余,但都是踩了坑才加上的,**不要"清理"或"简化"**。(remote_components 现在是兜底,本地 yt-dlp-ejs 优先)
4. **开发机没有独立 Python**:这台机器 python 命令是 Store 存根。测试用 `D:\comfy\ComfyUI-aki-v3\python\python.exe` 建的 venv `~/.yt_dlp_gui_venv`。
5. **墙内 GitHub 大文件下载不可靠**:ffmpeg 源已加多源兜底(gyan.dev → BtbN → gh-proxy)。如果还慢,手动放 build_cache/ 最快。
6. **YouTube API 变化频繁**:如果某天下载全部失败,先怀疑 yt-dlp 版本过旧,`pip install -U yt-dlp` 再试。
7. **别急着换 GUI 框架**:tkinter 虽然朴素但零依赖零打包成本。切换到 customtkinter/PyQt 前先想清楚打包体积和分发问题。

## 九、附:典型成功日志(基线参考)

```
[信息] yt-dlp 版本: 2026.08.19
[信息] PO Token 插件已就位 (bgutil-ytdlp-pot-provider)
[信息] PO Token 本地生成服务文件已就位（下载 YouTube 时自动启动）
[信息] ffmpeg 已就位,可正常合并/转码
[信息] 检测到 Node.js,将用于 YouTube JS challenge 解算
[开始] 共 1 个任务,输出到 C:/Users/xxx/Desktop/youtube
[Cookies] 从浏览器读取: firefox
[JS runtime] 使用: node
[远程组件] 启用: ['ejs:github'] (本地 yt-dlp-ejs 脚本优先,此仅兜底)
[PO Token] 本地生成服务已启动 v1.3.2 (http://127.0.0.1:随机端口)
[youtube] Extracting URL: https://www.youtube.com/watch?v=...
[youtube] Downloading webpage
[youtube] Downloading player ...
[js:node] Solving JS challenges using node
[debug] [youtube] [jsc:node] Using challenge solver lib script v0.8.0 (source: python package)
[info] Downloading 1 format(s): ...
[格式] id=313 · 3840x2160 · 24fps · 编码 vp9
[download]  47.3% at 2.5MB/s ETA 01:22
[完成] xxx.mp4
[成功] 所有任务已完成
```

看到 `Solved n-challenge` 和 `source: python package` 就稳了(本地 EJS,不联网)。

---

**文档生成时间:2026-08-10(双版本改造后更新)**
