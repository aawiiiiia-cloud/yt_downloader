# 淘宝首页窄窗口横向滚动修复（2026-10-28）

## 问题与范围

用户截图中，淘宝首页在窄浏览区只显示部分商品列，底部没有横向滚动条。
本次仅处理 HTTPS 的 `www.taobao.com` / `taobao.com` 首页：`/`、`/index.html`、`/index.htm`。
查询参数保留；登录、验证、商品详情、搜索结果以及其他网站不使用此兼容样式。
不更改缩放比例、浏览区域边界、右侧面板、账号凭据和下载逻辑。

## 实现

- `electron/taobao-scroll.cjs`：首页正文至少 1200px 宽，根页面允许横向和纵向滚动，恢复浏览区底部的 Chromium 原生滚动条。较宽视口没有横向溢出时，不额外显示横向条。
- 所有兼容样式均受根元素的 `data-media-dl-taobao-home-scroll` 属性约束。进入首页时启用，SPA 离开首页时移除属性；整个文档替换后重新加载样式。
- 使用 user-origin 的 important 样式覆盖网站隐藏滚动条的规则，包括 inline important。实测随包 Electron 的 user-origin 样式不能可靠地用 `removeInsertedCSS` 清除，因此不依赖该接口，改为让样式在非首页完全失效。
- 主进程和页面执行时分别校验首页地址，防止异步加载期间跳转后误启用。插入和启停串行化，关闭标签清理事件并最终停用；没有向网页开放 Node 或本机调用入口。
- `electron/main.cjs`：仅在淘宝标签原生视图创建时装载兼容器，在标签关闭时释放。
- `electron/taobao-scroll-smoke.cjs`：受控五列商品页面，复现隐藏滚动条、inline important、长页面与右侧商品截断，并验证滚动和生命周期。
- `electron/tests/taobao-scroll.test.cjs`：14 项单元测试，覆盖范围、样式隔离、重复事件、文档切换、SPA、iframe、异步竞争、失败重试与关闭清理。
- `electron/smoke.cjs` 接入受控页面与检查；`package.json` 增加新增文件语法检查。

## 备份与证据

修改前备份：`electron-artifacts/taobao-horizontal-2026-10-28/before/`，包含主进程、桌面测试、package.json、README 和清单。
测试证据保存在同级目录：`unit-tests.log`、`syntax-result.json`、`scroll-result.json`、`targeted-result.json` 与启动日志。
`run-targeted.cjs` 是此次保留的隔离测试入口：临时接管标准 smoke 的执行函数，不更改生产配置；仍通过标准 smoke 模式创建新的测试资料目录。

## 验证及限制

- 全部 JavaScript 单元测试：147 项通过（原有 133 项，加本次 14 项）。
- 全部 61 个 JavaScript 文件语法检查通过；淘宝受控检查 11 项通过。窄视口中底部横向条为 15px，页面向右移动 607px 后最后一列完整可见；上下滚动、标签位置保留、刷新、SPA 离开与返回首页及右侧面板隔离均通过。原生鼠标拖动和较宽窗口行为未完成验收。
- 本次页面使用受控数据，不是联网淘宝首页；未使用用户登录、Cookies 或个人配置。
- 当前桌面测试环境不能可靠捕获测试窗口或驱动原生滚动条。报告单独记录 `captureWarnings`、`nativeDrag` 与 `nativeInputWarning`，不将页面脚本滚动冒充鼠标拖动验收。
- 若当前桌面无法完成较宽窗口下的原生视口更新，报告记录 `wideResizeWarning`，不将宽屏与窗口缩放标记为通过。
- 完整桌面回归在此前已有的 B站封面失败重试用例中超时（`cover failure was not exposed`），尚未完成全套和重启持久化检查；本次未为绕过该失败而修改 B站或其他功能。
- 最终受控检查的通过项与数据，以 `targeted-result.json` 和 `scroll-result.json` 为准。

## 用户验收

完全退出 media-dl，再使用原启动入口重开；不要只刷新旧进程网页。
打开淘宝首页，缩小窗口：浏览区底部应出现横向条；向右拖动可查看原先截断的商品。
上下滚动时横向条仍位于浏览区底部；切换标签后位置应保留；增宽窗口至内容能完整容纳时横向条应消失。
切到商品、登录或验证页不应保留首页最小宽度和兼容样式。
无需重新登录、迁移配置或重新安装依赖。
