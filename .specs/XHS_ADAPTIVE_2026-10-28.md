# 小红书图片自适应下载策略（2026-10-28）

## 结论

已先完成本地代理检查，再修改下载策略。当前快照中，小红书网页和已检查的原图域名均由 WLAN 直连；Electron 的 `resolveProxy` 返回 `DIRECT`，Python 未继承 HTTP/HTTPS/ALL_PROXY，目标地址也没有命中 AliWireGuard 路由。未发现“Electron 被误判为外网进程，导致图片走代理变慢”的证据。

该结论只代表 2026-10-28 的当前网络快照和同配置探测，不等同于历史抓包，也不能排除上游透明转发或某次过去的网络状态不同。本次没有关闭 VPN，也没有修改代理、DNS、路由、Cookie、账号资料或用户已有下载文件。

代理检查详情：`D:\media-dl(2)\media-dl\代码\electron-artifacts\proxy-check-2026-10-28\diagnosis.md`

## 已修改的下载行为

1. **最近速度而非全程平均速度**：下载卡片和日志使用最近约 3 秒的速率，网络恢复后不会一直被前面的慢速拖低。
2. **小红书原图低速切换**：启动宽限 3 秒后，连续两个独立的 4 秒窗口低于 32 KiB/s，才判为持续低速；典型十多 KiB/s 会在约 11 秒后切换。
3. **只切同质量原图**：仅在小红书已知原图 CDN、相同资源路径、无查询参数和无缩放变换的地址之间切换。不会因为低速直接降级到网页预览图。
4. **有限探测，避免反复切换**：最多探测 3 个慢原图节点；如果备用节点也没有更快完成，就回到已测较快的原图并继续，不无限循环。
5. **失败顺序优化**：某个原图连接或接收失败后，优先换另一个原图，所有原图确实失败后才尝试展示图兜底。
6. **原图空闲超时缩短为 15 秒**：包括等待响应和接收中长时间不再有数据；这不是整张图片的总时限，持续正常传输不会被强制中断。
7. **更及时的进度**：读取块由 64 KiB 调整为 16 KiB；每约 5 秒记录已接收大小、总大小、最近速度和耗时。
8. **完整性保护不变**：切换节点时丢弃该次临时片段，不拼接不同节点数据；校验成功后才原子替换目标文件。取消任务优先于节点切换。
9. **本任务内节点记忆**：同一次帖子下载中，后续图片优先尝试前面表现较好的原图节点；不会跨任务保存网络历史。

淘宝、站酷、Behance 等共用下载器的站点获得了更及时的 16 KiB 进度采样、最近速度和接收日志，但没有启用小红书专属的“等质量原图节点切换”，以免把不同清晰度或不同语义的备用地址误当成等质量原图。

## 验证结果

- 下载相关 Python：**138 项通过**。
- Electron/JavaScript：**182 项通过**，语法检查通过。
- 真实本机 HTTP 传输：**13 项通过**。覆盖持续低速、未知大小、响应头超时、已接收部分数据后停滞、取消、不覆盖旧文件、3 个慢节点后回到最快原图等情形。
- 小红书线上有限验证：**3/3 次完整原图匹配**。图片 10 分别从 i11、i10 起步，图片 7 从 i11 起步；下载结果的 SHA-256 与修改前已确认的原图一致。本次线上速度约 154–1191 KiB/s，未复现十多 KiB/s，所以“十多 KiB/s 自动切换”由真实 socket 慢流测试验证，而不是声称本次线上恰好复现。
- 全项目 Python：共 **290 项，288 项通过、2 项失败**。失败仍是修改前已有的受限 PowerShell 运行环境测试：`test_both_python_associations_relay_to_bundle_before_legacy_imports`、`test_python_recovery_rejects_archive_path_traversal`，与本次下载代码无关，不能表述为全项目全绿。

## 文件

- 修改：`D:\media-dl(2)\media-dl\代码\xhs_image_downloader.py`
- 新增策略模块：`D:\media-dl(2)\media-dl\代码\image_transfer.py`
- 单元测试：`D:\media-dl(2)\media-dl\代码\test_image_transfer.py`、`D:\media-dl(2)\media-dl\代码\test_xhs_adaptive.py`
- 真实网络回归：`D:\media-dl(2)\media-dl\代码\test_xhs_download_network.py`
- 修改前备份：`D:\media-dl(2)\media-dl\代码\electron-artifacts\xhs-adaptive-2026-10-28\before`
- 变更补丁：`D:\media-dl(2)\media-dl\代码\electron-artifacts\xhs-adaptive-2026-10-28\changes.patch`
- 最终报告：`D:\media-dl(2)\media-dl\代码\electron-artifacts\xhs-adaptive-2026-10-28\final-report.json`
- 线上验证：`D:\media-dl(2)\media-dl\代码\electron-artifacts\xhs-adaptive-2026-10-28\live-validation.json`
