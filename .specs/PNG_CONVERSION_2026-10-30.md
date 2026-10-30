# PNG 转换性能优化（2026-10-30）

## 原因

1. 原实现使用 Pillow `optimize=True` 保存 PNG。该选项会执行代价很高的全图优化压缩，并实际偏向最高压缩强度；图片越大，CPU 耗时越明显。
2. 下载完成后先完整解码一次做校验，真正转换时又完整解码一次。HEIC 解码较重，因此重复解码尤其明显。
3. 写盘和转换后完整解码校验不是主要瓶颈，继续保留，以保证损坏文件不会覆盖已完成图片。

## 修改

- PNG 改为 `optimize=False, compress_level=3`。
- PNG 仍然是无损格式，不降低分辨率、不改变像素，不使用有损质量参数。
- 需要格式转换时，文件头校验后直接由转换步骤完成唯一一次完整源图解码。
- 不转换、保留原始编码时，仍执行一次完整解码后才发布文件。
- 转换完成日志新增本张图片的转换用时。

## 实测

使用一张 2736×3648、7.06 MB 的实际 JPG，并由同图生成 HEIC 测试：

- 修改前 JPG→PNG：约 13.9 秒；同进程保守对比的快速策略约 2.5 秒。
- 修改前 HEIC→PNG：约 24.2 秒；同进程保守对比的快速策略约 3.4 秒。
- 最终代码复测（缓存较热）：JPG 1.09 秒、HEIC 1.73 秒。
- 保守按同一轮多方案测试计算，预期约快 **5–7 倍**；缓存较热时本机测得更高。
- 快速 PNG 与旧 PNG 的解码后像素完全一致。
- 文件大小通常增加约 7%–12%，换取显著更低的 CPU 等待时间。

## 验证

- 下载与图片相关 Python：141 项通过。
- 本机真实 HTTP 传输：13 项通过。
- 修改文件语法检查通过。

## 文件

- 修改：`D:\media-dl\media-dl\代码\xhs_image_downloader.py`
- 测试：`D:\media-dl\media-dl\代码\test_xhs_image_downloader.py`
- 修改前备份：`D:\media-dl\media-dl\代码\electron-artifacts\png-conversion-2026-10-30\before`
- 性能数据：`D:\media-dl\media-dl\代码\electron-artifacts\png-conversion-2026-10-30\after-profile.json`
- 最终报告：`D:\media-dl\media-dl\代码\electron-artifacts\png-conversion-2026-10-30\final-report.json`
