# PO Token 冷启动超时修复（2026-10-30）

## 原因

内置 provider 本身没有损坏，Node 版本、provider 版本和文件校验均正常。实测：

- Windows 冷缓存首次启动约 **42.35 秒**。
- 模块进入系统缓存后，热启动约 **3.29 秒**。
- 旧 Electron 等待上限为 25 秒，Python provider worker 为 20 秒。
- Python 还会把 20 秒平均拆给 3 个全新进程，每次只有约 6–7 秒。

因此冷启动进程会在完成前被终止；后续重试重新启动另一个冷进程，连续显示 1/3、2/3、3/3 超时。这不是 provider 文件缺失，也不是 token 生成接口失效。

## 修改

1. provider 总启动预算调整为 80 秒。
2. 第一次尝试获得 75%（运行时 60 秒），不再平均切成三个过短时间片。
3. 后两次仍保留，用于真实的端口占用、进程退出等情况。
4. Electron 等待上限调整为 90 秒，避免先于 Python worker 取消。
5. 应用打开后在后台预热 provider，不阻塞主界面；通常用户开始解析前已经完成加载。
6. 首次等待超过 8 秒时显示“首次冷启动正在加载模块，可能受系统安全扫描影响，继续等待…”，避免误认为程序卡死。
7. CLI/构建验证使用相同的启动预算。
8. 没有修改第三方 provider bundle、账号 Cookie、系统代理或安全设置。

## 验证

- provider Python 单元测试：9 项通过，包括模拟 42 秒冷启动只使用一个进程成功。
- Electron provider 测试：8 项通过。
- Python 相关回归：83 项通过。
- JavaScript/Node：210 项通过。
- JavaScript 语法检查通过。
- 真实 `collector_worker` 链路：provider-ready 成功，当前热缓存启动 9.463 秒。
- 真实 token 生成：成功，耗时 12.826 秒；返回 `poToken/contentBinding/expiresAt`，报告只保存字段和长度，没有保存 token 内容。

## 文件

- 核心启动管理：`D:\media-dl\media-dl\代码\pot_provider.py`
- 应用 provider worker：`D:\media-dl\media-dl\代码\collector_worker.py`
- Electron 生命周期：`D:\media-dl\media-dl\代码\electron\youtube-provider.cjs`、`D:\media-dl\media-dl\代码\electron\main.cjs`
- 修改前备份：`D:\media-dl\media-dl\代码\electron-artifacts\pot-startup-2026-10-30\before`
- 最终报告：`D:\media-dl\media-dl\代码\electron-artifacts\pot-startup-2026-10-30\final-report.json`
