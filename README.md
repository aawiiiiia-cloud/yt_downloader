# yt-dlp GUI 下载工具

一个基于 Tkinter 的 YouTube 视频下载工具,内建登录、代理、码率选择等功能,同时提供源码版与打包版两条交付线。

## 功能特性

- **YouTube 视频下载**:支持单视频/列表,可选分辨率(含仅音频),音频码率 128/192/320 kbps
- **内置登录(Edge + CDP)**:自动启动 Edge 抓取 YouTube 登录态 cookie(含 HttpOnly),免装任何浏览器插件
- **Cookies 多来源**:Firefox 浏览器直读 / cookies.txt 文件 / 内置登录,三态可切换
- **PO Token 插件**:集成 `bgutil-ytdlp-pot-provider`,自动生成 Proof of Origin token,减轻 YouTube 的 bot 检测
- **JS challenge 解算**:Node.js 运行时,自动解 YouTube n-challenge 签名混淆
- **代理支持**:可选填 HTTP / SOCKS5 代理,缓解墙内直连问题
- **设置持久化**:保存目录、分辨率、Cookies、代理等重启不丢
- **环境自检自动安装**(源码版):缺 yt-dlp / Node / ffmpeg 时自动下载安装,无需管理员权限

## 文件结构

```
source-code/
├── yt_dlp_gui.py          # 主 GUI(源码版/打包版共用)
├── bootstrap.py           # 源码版环境自检 + 自动安装(Node/ffmpeg/yt-dlp)
├── edge_login.py          # 内置登录(Edge + CDP 抓 cookie)
├── build.py               # 一键打包脚本 → dist/yt_dlp_gui/
├── requirements.txt       # Python 依赖
├── test_yt_dlp_gui.py     # 回归测试(unittest)
└── .specs/                # 交接文档 / 打包说明
```

## 使用方法

### 源码版(需 Python 3.10+)

```bash
pip install -r requirements.txt
python yt_dlp_gui.py
```

首次启动会自动检测并安装缺失的 Node.js / ffmpeg / yt-dlp。

### 打包版(免安装)

运行 `python build.py`,产出 `dist/yt_dlp_gui/` 文件夹(含便携版 node/ffmpeg),整体分发即可,目标机器无需安装 Python / Node / ffmpeg。

## 测试

```bash
python -m unittest test_yt_dlp_gui -v
```

## 说明

- 内置登录凭据保存在 `~/.yt_dlp_gui_cookies.txt`,仅本机使用,请勿外泄
- 本工具仅供学习与个人合理使用,请遵守目标平台的服务条款与当地法律法规
