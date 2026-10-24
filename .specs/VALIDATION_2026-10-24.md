# 主项目修复与验证记录 · 2026-10-24

## D 盘环境

- 独立环境：`D:\python-envs\yt-downloader`。
- Python：3.13.11 x64，基于现有 `D:\comfy\ComfyUI-aki-v3\python` 创建虚拟环境；未修改该基础环境的依赖。
- 已按项目约束安装 yt-dlp 2026.8.19、yt-dlp-ejs 0.8.0、bgutil-ytdlp-pot-provider 1.3.2、websocket-client 1.9.2、Pillow 11.3.0、pillow-heif 1.7.0、dhash 1.4。
- Pillow 实际位置：`D:\python-envs\yt-downloader\Lib\site-packages\PIL`。
- 用户 PATH 增加 `D:\python-envs\yt-downloader\Scripts`，原有条目全部保留；已验证按系统和用户 PATH 启动的 `python` 指向 D 盘新环境。
- 用户环境变量 `YT_DOWNLOADER_PYTHON` 指向该环境的 `Scripts\python.exe`。
- 环境变量修改前的备份：项目 `build_cache/environment-before-20261024.json`。
- `start.cmd` 提供双击启动入口；`start.cmd --check-env` 已验证可加载 D 盘的 Pillow、HEIF、dhash 和 Tkinter。

## 修复内容

1. 下载图片仅凭 MIME 和文件长度可能错误接受 HTML 或截断 JPEG，甚至覆盖之前的正常文件。现在在发布文件前验证图片结构并完整解码；失败会保留旧文件、清理临时文件。
2. CMYK JPEG 转 PNG 会报 `cannot write mode CMYK as PNG`。现在先转换为 RGB，并移除不再适用的 CMYK 色彩配置。
3. 图片 SHA-256 扫描中取消操作会被通用异常处理吞掉，导致正常图片被标记为无法读取。现在正确向上抛出取消信号。
4. 环境工具路径使用子串判断，且未统一 Windows 长短路径和大小写。现在使用解析后的路径、完整 PATH 条目和大小写无关比较。
5. 更新小红书测试中的旧预期，保留原始素材 CDN 优先、网页展示图最后回退的现有行为。

## 验证结果

```text
python -m unittest test_yt_dlp_gui test_xhs_image_downloader test_media_batch -v
Ran 109 tests
OK
```

- 原有主项目 97 项测试，新增 12 项回归与真实媒体测试，全部通过，无跳过。
- 图片使用临时生成的真实 JPEG、PNG、WebP、HEIF 和动画 GIF；覆盖原始字节保留、透明背景、EXIF 方向、HEIF 转换、CMYK 转换、损坏图片拒绝、裁剪失败清理和取消操作。
- 使用本机 ffmpeg/ffprobe 生成临时 MP4，实际验证宽高、帧率、时长、SHA-256 和黑边建议，确认源视频字节未改变。
- GUI 测试使用隐藏窗口和临时设置，登录与网络调用由测试替身隔离。
- `pip check`：`No broken requirements found.`
- 测试完整日志：项目 `build_cache/main-tests-20261024.log`。

本次仅处理主项目，未修改或测试 MissAV。未进行真实网站下载或账号登录，也未重新构建 EXE；这些行为不在上述测试结果的覆盖范围内。
