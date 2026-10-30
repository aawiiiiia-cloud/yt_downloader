# PO Token 启动优化与分段诊断（2026-10-30）

## 测试结论

- 先做编译缓存交错对照：原版服务启动中位数 1.1473 秒，缓存复用 1.1499 秒，无显著收益；首次写缓存额外约 1.1 秒并产生 2041 个小文件。因此应用不默认启用编译缓存。
- 使用固定 esbuild 0.28.2，在构建阶段合并 2190 个输入文件，保留原生 canvas、资源、原始模块和完整对应源码。运行阶段不安装工具、不执行构建。
- 最终可移动候选使用相同管理器、相同校验和相同 Node 运行时，6 次交错启动：原版中位数 1.0673 秒，合并入口 0.5618 秒，减少 47.4%。实际 worker 约 0.6846 秒。
- 未清空系统缓存，以上是本机当前缓存环境，不代表公司 42 秒冷启动的改善比例。安全扫描与冷磁盘读取的影响仍需公司机器复测。
- 把候选移至含中文、空格及括号的目录后，真实 worker 和 Token 生成均通过。首个测试绑定生成 4.6717 秒，另一个绑定使用原有内存生成器约 0.0015 秒，返回不同 Token；未新增跨会话 Token 共用策略，也没有把网络耗时改善归功于打包。

## 实现与回退

- `tools/build-provider-bundle.cjs` 为构建工具。通过解析器转换模块资源路径、`import.meta.url` 和 `require.resolve`；运行路径相对于 provider 目录，未硬编码本机目录。
- 额外入口为 `server/build/media-dl-provider.mjs`。原 `main.js`、依赖树、原生组件和资源保留。
- `media-dl-bundle.json` 记录版本、来源提交、构建工具版本、全部输入及输出的 SHA-256；完整 provider 清单同时覆盖新产物、许可证及重建脚本。
- 日常只核验原有关键文件及优化产物，比较构建输入与已记录的完整清单。优化产物缺失、损坏或与依赖版本不匹配时，使用原入口。优化入口启动失败也会关闭该自有进程并切换原版重试；原版首次尝试可使用 60 秒预算，全部尝试仍受总预算约束。
- 保留 Python 80 秒 / Electron 90 秒、应用后台预热、回环地址及版本核验、单个操作取消隔离、借用服务不被关闭和应用退出清理。
- 防止重复预热创建多个服务，关闭后禁止直接重新启动。用户在预热中开始解析，可以收到此前的启动进度。
- 不修改 YouTube 账号、代理、DNS、路由、防病毒或公司安全策略。

## 诊断

- `provider_runtime.cjs` 是仅记录时长的 Node 预加载脚本；旧 GUI 构建也携带此资源。记录 Node 模块准备、Token 请求总耗时和进程 CPU 用时，不记录请求、响应内容或绑定值。
- Python 分开记录 worker 导入及准备、组件核验和服务总准备时间。Electron 再次筛选合法阶段及数值，仅保存最近 30 条至自己的 `provider-timings.json`，默认资料目录为 `~/.media-collector/`；不能写入时不阻止下载。
- CPU 时间是进程各线程 CPU 用时的合计，可能大于经过时间，不能直接当作 CPU 利用率或安全软件扫描时间。
- 真实 Token 验收仅保存字段名、长度、耗时和 HTTP 状态；未保存 Token、Cookie 或实际账号信息。

## 重建

使用独立的构建目录准备 esbuild 0.28.2，然后执行：

```text
node tools/build-provider-bundle.cjs PROVIDER_ROOT ESBUILD_MODULE_PATH
python -c "from pathlib import Path; from pot_provider import write_provider_manifest; write_provider_manifest(Path('PROVIDER_ROOT'))"
```

重建后应做完整清单核验、移动目录、真实 Token 生成和退出测试再分发。工具自动修复仅取得上游原包时，应用可正常退回原入口；优化包应在发布前预构建。esbuild 不加入应用运行依赖。

本次证据在 `electron-artifacts/pot-optimization-20261030/`；完整包同步后的结果在 `依赖工具/验证结果/PO-Token优化-2026-10-30/`。性能和测试结果仅对应各自记录中的环境与版本。
