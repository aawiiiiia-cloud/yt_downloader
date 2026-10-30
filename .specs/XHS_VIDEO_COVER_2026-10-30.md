# 小红书视频封面马赛克修复（2026-10-30）

## 结论

画面不是视频首帧解码损坏，也不是 Electron 图片缩放导致。小红书为同一帧返回两个 1080×1440 的 JPEG：

- `nd_prv`：21.5 KiB，经过激进量化，呈现明显色块和马赛克。
- `nd_dft`：233.5 KiB，正常显示质量。

两者的像素尺寸相同，因此只比较宽高无法发现问题。截图与 `nd_prv` 候选一致。

## 原因

视频信息标准化原先优先信任 yt-dlp 的单个 `thumbnail` 字段。小红书返回候选顺序会在请求之间变化；某些页面/会话路径会把 `nd_prv` 放入该字段，界面随后原样显示这个低质量封面。

## 修改

- 仅对小红书视频重新评估全部封面候选。
- 避开 `nd_prv`，优先无变换原图，其次选择 `nd_dft` 正常显示封面。
- 不依赖候选返回顺序。
- 其他视频网站仍保留原有明确主封面选择逻辑。
- 如果小红书确实只返回一个 `nd_prv`，仍保留它作为最后可用封面，而不是让预览完全空白。

## 验证

- 该真实帖子强制把低清 `nd_prv` 设为主缩略图后，新逻辑成功改选 `nd_dft`。
- 下载与解析相关 Python：144 项通过。
- 小红书真实本机 HTTP 传输：13 项通过。
- 修改文件语法检查通过。

## 文件

- 修改：`D:\media-dl\media-dl\代码\collector_core.py`
- 测试：`D:\media-dl\media-dl\代码\test_collector_core.py`
- 修改前备份：`D:\media-dl\media-dl\代码\electron-artifacts\xhs-video-cover-2026-10-30\before`
- 封面对比：`D:\media-dl\media-dl\代码\electron-artifacts\xhs-video-cover-2026-10-30\cover-comparison.png`
- 最终报告：`D:\media-dl\media-dl\代码\electron-artifacts\xhs-video-cover-2026-10-30\final-report.json`
