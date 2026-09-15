# yt-dlp GUI 下载工具

一个基于 Tkinter + yt-dlp 的多网站媒体下载工具,支持视频和小红书图文,同时提供源码版与打包版两条交付线。

## 功能特性

- **多网站下载**:支持 yt-dlp 兼容站点,包括 YouTube、Bilibili 等,支持单视频/列表
- **小红书图文**:可粘贴帖子网址或 App 整段分享文案,优先使用 `fileId`/网页资源 ID 从原始素材 CDN 下载并按顺序保存；原始源全部失效时才回退到可能带水印的网页展示图
- **三站点内置登录**:一个入口管理 YouTube、Bilibili 和小红书登录,免装 Cookie 插件
- **自动匹配凭据**:按输入网址自动使用对应站点 Cookies,混合批量任务也不会串用
- **登录状态提醒**:显示最近登录时间,超过 7 天主动建议更新;检测到失效会立即停用
- **安全更新登录**:新凭据先写临时文件并验证,成功后才原子替换;失败保留旧登录
- **简洁登录窗口**:Edge 应用模式隐藏地址栏/标签栏,保留验证码、二维码和两步验证能力
- **PO Token 完整集成**:Python 插件 + 官方 Node provider 配套打包,下载 YouTube 时自动启动、健康检查并在结束后关闭
- **实际格式日志**:下载时明确显示格式 ID、真实分辨率、帧率和编码；“最佳”不再只是抽象标签
- **JS challenge 解算**:Node.js 运行时,自动解 YouTube n-challenge 签名混淆
- **代理支持**:可选填 HTTP / SOCKS5 代理,缓解墙内直连问题
- **完整性优先**:HLS/DASH 任一分片失败即判定任务失败,不会静默生成卡顿成品
- **设置持久化**:保存目录、分辨率、Cookies、代理等重启不丢
- **环境自检自动安装**(源码版):缺 yt-dlp / Node / ffmpeg 时自动下载安装,无需管理员权限
- **PO Token 安装容错**:优先复用已编译成品；首次编译使用 npm 持久缓存和国内镜像，停滞时自动切换官方源
- **媒体批处理**:按已启用项目自动跳过整类媒体；通用查重覆盖图片和视频，图片近似强度可从“完全一致”滑到“宽松近似”
- **安全归档问题文件**:红色明确问题可统一移至指定目录，黄色相似图/黑边项需明确勾选；同名不覆盖，跨盘复制校验成功后才删除源文件
- **图片工具**:普通文字作为前缀自动编号，也兼容高级模板批量重命名；支持按常用比例居中裁剪副本（不改原图）

## 文件结构

```
source-code/
├── yt_dlp_gui.py          # 主 GUI(源码版/打包版共用)
├── bootstrap.py           # 源码版环境自检 + 自动安装(Node/ffmpeg/yt-dlp)
├── edge_login.py          # 内置登录(Edge + CDP 抓 cookie)
├── xhs_image_downloader.py # 小红书图文解析与原子下载
├── media_batch.py         # 媒体检测、查重和安全文件操作核心
├── media_batch_ui.py      # 图片/视频批处理窗口
├── pot_provider.py        # PO Token 本地服务运行/健康检查/进程清理
├── provider_setup.py      # 官方 provider 下载、编译和缓存
├── build.py               # 一键打包脚本 → dist/yt_dlp_gui/
├── requirements.txt       # Python 依赖
├── requirements-build.txt # 打包环境精确锁定版本
├── THIRD_PARTY_NOTICES.txt # 第三方组件版本、许可证与源码说明
├── test_yt_dlp_gui.py     # 回归测试(unittest)
├── test_xhs_image_downloader.py # 小红书图文测试
├── test_media_batch.py    # 媒体批处理核心测试
└── .specs/                # 交接文档 / 打包说明
```

## 使用方法

### 源码版(Python 3.10+；正式打包固定 Python 3.13 x64)

```bash
pip install -r requirements.txt
python yt_dlp_gui.py
```

首次启动会先复用项目 `build_cache/` 或旧 `dist/yt_dlp_gui/tools/` 中已有的
Node.js / ffmpeg，再自动安装确实缺失的环境。

主界面点击【媒体批处理】可扫描已有图片/视频。黑边检测只报告裁剪建议，
不会自动裁剪视频；“移动疑似文件”会在目标目录下按“图片/视频”分类保存。

### 打包版(免安装)

使用 64 位 Python 3.13 运行 `python build.py`,产出 `dist/yt_dlp_gui/` 文件夹(含便携版 node/ffmpeg/PO Token provider),整体分发即可,目标机器无需安装 Python / Node / ffmpeg。构建先在暂存目录完成并自检,失败不会删除或覆盖上一版交付物。构建会核对 Node、FFmpeg 和 provider 下载摘要，真实启动 provider 做健康检查，并生成 `dist/yt_dlp_gui.zip.sha256`。

## 测试

```bash
python -m unittest discover -v
```

## 说明

- YouTube、Bilibili、小红书凭据分别保存在用户目录下的 `.yt_dlp_gui*_cookies.txt`
- 这些文件都等同账号登录状态,仅限本机使用,不得分享或随程序分发
- 新登录凭据会在验证后原子替换，并尽量把 Windows 文件权限限制到当前用户
- YouTube 网页显示 4K 不代表旧版 yt-dlp 一定能取得 4K URL；本项目锁定 2026.08.19，并在日志以 `[格式]` 显示实际下载分辨率
- 本工具仅供学习与个人合理使用,请遵守目标平台的服务条款与当地法律法规
