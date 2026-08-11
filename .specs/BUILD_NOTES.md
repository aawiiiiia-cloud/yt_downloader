# 打包与分发笔记

## 一、两个版本

| 版本 | 运行方式 | 适用人群 |
|---|---|---|
| 源码版 | `python yt_dlp_gui.py` | 开发者 / 公司电脑(需装 Python,首次启动自动装 node/ffmpeg/yt-dlp) |
| 打包版 | 双击 `yt_dlp_gui.exe` | 众包采集同事(零安装,文件夹内含一切) |

## 二、源码版说明

- 启动时 `main()` 会调用 `bootstrap()` 自动检测并安装缺失环境:
  - **yt-dlp + yt-dlp-ejs + PO token 插件**:pip 安装,失败回退清华镜像
  - **Node.js 22+ LTS**:nodejs.org 下载便携版,墙内回退 npmmirror,装到 `~/.yt_dlp_tools/node/`
  - **ffmpeg**:gyan.dev 优先 → BtbN 直连 → GitHub 代理镜像,装到 `~/.yt_dlp_tools/ffmpeg/`
- 全程无需管理员权限,工具装到用户目录,只对当前进程 PATH 生效。
- 公司电脑拦截 `deno.exe` 不影响:Node 优先,且不再依赖 Deno。

## 三、打包版构建

```bash
python build.py
```

产出:
- `dist/yt_dlp_gui/` — 完整文件夹(node/ffmpeg 在 `tools/`,运行时自动加进 PATH)
- `dist/yt_dlp_gui.zip` — 压缩包,用于分发

构建过程会下载 node.exe + ffmpeg.exe,国内网络会自动走镜像。
**如果网络下载失败**,可手动下载后放进 `build_cache/`(build.py 检测到存在就跳过下载):
- `build_cache/node.exe` — Node.js 22+ LTS win-x64 的 node.exe
- `build_cache/ffmpeg.exe` + `build_cache/ffprobe.exe`

## 四、关键打包参数(不要轻易删)

```bash
pyinstaller --onedir --windowed --name yt_dlp_gui \
  --collect-data yt_dlp_ejs \                 # ★ EJS 脚本打进包,墙内离线可用
  --hidden-import yt_dlp_ejs \
  --hidden-import yt_dlp_plugins.extractor.getpot_bgutil \  # PO token 插件
  --collect-submodules yt_dlp \               # 所有 extractor
  --collect-submodules yt_dlp_plugins \
  --noupx yt_dlp_gui.py
```

- `--collect-data yt_dlp_ejs`:yt-dlp 自带的 pyinstaller hook 也会收集,双保险
- 若去掉 EJS 数据,打包版会去 GitHub 拉脚本,墙内用户会失败
- `--noupx`:避免压缩被杀软误报

## 五、分发给同事时的注意事项

1. 必须整体解压,`tools/` 文件夹不能缺,exe 不能单独拿走。
2. 每个同事需要自己的 YouTube 登录(Firefox 登录一次 → 点"一键获取")。
3. Cookies 大约两周过期,过期重新登录 Firefox 再点"一键获取"。
4. 若同事的电脑无法直连 YouTube,需填代理(不提供科学上网,只转发已有代理)。

## 六、验证清单(改代码后)

- [ ] 源码版: `python yt_dlp_gui.py` 正常启动,日志无红色错误
- [ ] 打包版: 在一台没装 Python 的机器上双击 exe,能下 YouTube
- [ ] 打包版断网验证: 下载时日志出现 `Solved n-challenge`,无 GitHub 请求
- [ ] Cookies 一键获取(Firefox)、文件导入、ABE 警告都正常
- [ ] 代理填写后下载生效;音频模式码率选项生效
- [ ] 设置重启后仍在(`~/.yt_dlp_gui.json`)

## 七、安全提示

- **不要**让同事用 "Get cookies.txt" 等浏览器插件导出 cookies——yt-dlp 官方已警告
  该类插件存在恶意版本。用 Firefox 一键获取,或命令行
  `yt-dlp --cookies-from-browser firefox --cookies cookies.txt` 生成。
