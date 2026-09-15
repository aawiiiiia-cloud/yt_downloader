# 打包与分发笔记

## 一、两个版本

| 版本 | 运行方式 | 适用人群 |
|---|---|---|
| 源码版 | `python yt_dlp_gui.py` | 开发者 / 公司电脑(需装 Python,首次启动自动装 node/ffmpeg/yt-dlp) |
| 打包版 | 双击 `yt_dlp_gui.exe` | 众包采集同事(零安装,文件夹内含一切) |

## 二、源码版说明

- 启动时 `main()` 会调用 `bootstrap()` 自动检测并安装缺失环境:
  - **yt-dlp + yt-dlp-ejs + PO token 插件**:pip 安装,失败回退清华镜像
  - **Node.js 22.14.0**:固定版本并核对官方 SHA-256,墙内可由 npmmirror 传输,装到 `~/.yt_dlp_tools/node/`
  - **ffmpeg**:从 BtbN 发布资产下载,可走 GitHub 代理,但必须与 GitHub 官方接口返回的 SHA-256 一致
- **PO Token provider**:官方 1.3.2 源码固定到提交号,首次用 npm 锁定依赖编译；之后复用本机成品
- npm 首次编译优先 `registry.npmmirror.com`，复用 `provider_cache/npm-cache`；
  连续 180 秒无输出或超过 20 分钟会中止当前源并切换 `registry.npmjs.org`。
- 全程无需管理员权限,工具装到用户目录,只对当前进程 PATH 生效。
- 源码版会先复用项目 `build_cache/`，其次复用旧 `dist/yt_dlp_gui/tools/`；
  只有两处都没有可用的 ffmpeg/ffprobe 或 Node 时才下载。
- 公司电脑拦截 `deno.exe` 不影响:Node 优先,且不再依赖 Deno。

## 三、打包版构建

```bash
python build.py
```

正式构建环境固定为 **64 位 Python 3.13**；解释器版本或架构不符会在开始时明确停止。

产出:
- `dist/yt_dlp_gui/` — 完整文件夹(node/ffmpeg/provider 在 `tools/`,运行时自动使用)
- `dist/yt_dlp_gui.zip` — 压缩包,用于分发
- `dist/yt_dlp_gui.zip.sha256` — 压缩包完整性摘要

构建过程会下载 node.exe + ffmpeg.exe,国内网络会自动走镜像。PyInstaller 先输出到
`dist/.yt_dlp_gui_stage/`,关键模块、工具及 PO Token provider 实际启动自检通过后才事务替换正式文件夹与 ZIP;
构建失败或发布中断会保留/恢复上一版产物。
成功后会保留已校验的 `build_cache/`，避免下次重新下载完整 Node/npm、ffmpeg 和 provider 源码；该目录已被 Git 忽略。
**如果网络下载失败**,可手动下载后放进 `build_cache/`(build.py 检测到存在就跳过下载):
- `build_cache/node.exe` — Node.js 22+ LTS win-x64 的 node.exe
- `build_cache/ffmpeg.exe` + `build_cache/ffprobe.exe`
- `build_cache/bgutil-provider/` — 已编译 provider；正常情况下也会从旧 dist 自动复用

## 四、关键打包参数(不要轻易删)

```bash
pyinstaller --onedir --windowed --name yt_dlp_gui \
  --collect-data yt_dlp_ejs \                 # ★ EJS 脚本打进包,墙内离线可用
  --hidden-import yt_dlp_ejs \
  --hidden-import yt_dlp_plugins.extractor.getpot_bgutil \  # PO token 插件
  --hidden-import edge_login \
  --hidden-import xhs_image_downloader \       # 小红书图文按需导入
  --hidden-import media_batch \
  --hidden-import media_batch_ui \
  --collect-submodules PIL \
  --collect-submodules yt_dlp \               # 所有 extractor
  --collect-submodules yt_dlp_plugins \
  --noupx yt_dlp_gui.py
```

- `--collect-data yt_dlp_ejs`:yt-dlp 自带的 pyinstaller hook 也会收集,双保险
- 若去掉 EJS 数据,打包版会去 GitHub 拉脚本,墙内用户会失败
- `--noupx`:避免压缩被杀软误报

## 五、分发给同事时的注意事项

1. 必须整体解压,`tools/` 文件夹不能缺,exe 不能单独拿走。
2. 每个同事通过【内置登录...】使用自己的 YouTube/Google 账号；不要共享登录凭据。
3. 登录态没有固定过期时间；界面 7 天后主动提醒更新，检测到失效会停用并要求重新登录。
4. 若同事的电脑无法直连 YouTube,需填代理(不提供科学上网,只转发已有代理)。

## 六、验证清单(改代码后)

- [ ] 源码版: `python yt_dlp_gui.py` 正常启动,日志无红色错误
- [ ] 打包版: 在一台没装 Python 的机器上双击 exe,能下 YouTube
- [ ] 打包版断网验证: 下载时日志出现 `Solved n-challenge`,无 GitHub 请求
- [ ] YouTube/Bilibili/小红书内置登录、临时写入和原子替换都正常
- [ ] 小红书图文分享文案可识别，图片齐全且不残留 `.part` 文件
- [ ] 小红书日志优先显示“原始素材 CDN”；强制模拟全部原始 CDN 失败时能回退到“网页展示图兜底（可能带水印）”
- [ ] YouTube 下载出现 `[PO Token] 本地生成服务已启动`，结束后 node provider 进程已清理
- [ ] 4K 样例日志出现 `[格式] ... 3840x2160`，而非只看“最佳”下拉框
- [ ] 代理填写后下载生效;音频模式码率选项生效
- [ ] 设置重启后仍在(`~/.yt_dlp_gui.json`)
- [ ] 【媒体批处理】可扫描图片/视频，Pillow/dhash 在打包版中可导入
- [ ] 疑似文件移动不覆盖同名文件；取消后无残留 `.part` 文件

## 七、安全提示

- **不要**让同事用 "Get cookies.txt" 等浏览器插件导出 cookies——yt-dlp 官方已警告
  该类插件存在恶意版本。统一使用本工具的【内置登录...】。
- provider 固定上游 commit 和源码包 SHA-256；运行目录使用逐文件清单检查，任何关键文件变化都会拒绝启动。
- provider 的 GPL 对应源码和本地监听地址修改说明随包放在 `tools/bgutil-provider/corresponding-source/`。
- npm 只审计最终生产依赖；发现 high/critical 时停止发布，不自动运行会改写官方锁文件的 `npm audit fix`。
