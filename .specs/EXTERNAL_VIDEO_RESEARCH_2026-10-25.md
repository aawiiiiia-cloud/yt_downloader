# 第三方托管视频解析与下载调研

2026-10-25。当前检查点 `fd24cb4`。本次仅检查现有代码、已保存的公开页面媒体快照与上游资料，没有安装依赖、修改产品逻辑，或重新播放/下载样本。Behance 默认跳过外部视频的策略保持不变。

## 结论

可在现有架构上扩展，推荐“识别实际托管平台 → 平台专用解析 → 必要时读取浏览器已加载的播放器请求 → 现有下载队列”。gallery-dl 继续识别作品模块；yt-dlp 继续处理画质、音视频下载与合并。浏览器媒体请求观察作为补充，不能仅凭发现一个视频链接就认定可以完整下载。

不建议另换一套 Behance 下载器解决全部外部视频：gallery-dl 的 Behance EmbedModule 分支本身只是取 iframe 地址并交给 ytdl，实际限制仍由 YouTube、Vimeo 等托管平台决定。[上游源码](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/behance.py)

## 本地已确认的事实

- D 盘独立环境当前为 yt-dlp `2026.8.19`、gallery-dl `1.32.17`；没有安装 `curl_cffi`，yt-dlp 返回的可用 impersonation targets 为空。
- 用户的 EQUIPT 作品保存快照中有 1 个 YouTube 和 6 个 Vimeo 嵌入视频。6 个 Vimeo 原始 iframe 地址均未携带 `h` 参数；这不证明它们需要 `h`，也不能据此认定存在丢失参数的故障。
- 此前联网记录是 Vimeo 播放器页面请求返回 401，还没有取得画质；没有证据证明它们是登录过期，也没有确认这 6 个播放器在同一网络与浏览器会话中是否能够播放。
- 旧实现已经把 Behance 作品地址作为 Referer 传入 yt-dlp；已安装 Vimeo 提取器也确实读取该设置。不能把故障简单归结为“没有设置来源地址”。
- 当前 `vimeo_extractor.py` 仅并行处理取得播放器配置后的 HLS/DASH 画质分支；播放器网页请求沿用上游逻辑，401 发生在它之前。
- 现有 Vimeo 地址整理函数保留 `h`，但会将常见地址统一到 `player.vimeo.com`。后续诊断应分别记录原始嵌入入口和普通 Vimeo 入口的结果，不能默认两者访问行为相同。
- 浏览器登录保存在同一个应用浏览会话中；任务 Cookies 按站点导出。旧 Behance 外部解析拿到的是 Behance 快照，不会自动获得 Vimeo 登录凭据。它是后续需要完善的账号路由，不足以单独解释所有公开视频 401。
- 项目已有抖音页面响应观察与有界缓存，可借鉴导航取消、视频 ID 匹配、内存上限及关闭清理；不能直接套用其仅限抖音主页面响应的过滤规则。

样本：YouTube `SmQd5BgLmd0`；Vimeo `1169840122`、`1173551482`、`1162767792`、`1166604123`、`1162769396`、`1162769973`。

## Vimeo 401 的研究结果

| 候选原因 | 支持依据 | 当前判断 |
| --- | --- | --- |
| 请求被浏览器指纹检查或挑战拦截 | yt-dlp 有同类 embed-only 401 问题，部分报告者补齐 curl_cffi 后恢复；本地恰好缺少该能力 | 优先做对照试验；不是已确认根因，也不是安装后必然恢复 |
| 嵌入来源与允许域名不一致 | Vimeo 官方说明域名隐私依赖实际请求的 Referer；多层 iframe 的直接父页面可能不同于最外层作品页 | 读取真实请求核对，不能随意伪造一个允许域名 |
| Unlisted 视频缺少原有访问参数 | Vimeo 官方要求此类嵌入链接带 `h` | 保存并传递已有参数；本样本没有证据证明需要这个参数 |
| 需要 Vimeo 登录、视频密码或特定访问权限 | Vimeo 提取器分别处理这些状态；Behance 登录不能代替 Vimeo 登录 | 依据实际响应提示处理，不能仅凭 401 把账号标为过期 |

资料：[yt-dlp 同类问题 #10645](https://github.com/yt-dlp/yt-dlp/issues/10645)、[官方请求模拟依赖说明](https://github.com/yt-dlp/yt-dlp#impersonation)、[Vimeo 来源策略说明](https://help.vimeo.com/hc/en-us/articles/35817429341457-Troubleshooting-video-playback-errors-due-to-referrer-policy-conflicts)、[Vimeo Unlisted 参数说明](https://help.vimeo.com/hc/en-us/articles/12426470858001-Embedded-player-displays-This-video-does-not-exist-message)。

## 建议的解析路径

### YouTube

1. 从作品模块取出实际视频 ID，识别为 YouTube 托管。
2. 在后台复用现有 YouTube 账号状态、下载前验证和共享 PO Token 服务。账号缺失时引导至现有登录区域；无需要求用户每次手动复制链接或切换标签解析。
3. 继续由 yt-dlp 获取封面、时长、画质并下载，避免自行拼接短时效媒体地址。

这里“必须登录”继续遵守本项目的产品要求。上游资料用于说明 Cookies 与 PO Token 的处理方式，不用来削弱现有登录限制。[yt-dlp 官方 YouTube 文档](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)

### Vimeo

1. 保留完整原始 iframe URL、作品 ID、模块 ID、原始来源页面，以及已有 `h` 等访问参数。
2. 先用 Vimeo 专用提取器，传递真实来源、该平台独立 Cookies、相同代理和 User-Agent；在隔离环境对照验证 curl_cffi 的作用。
3. 如果左侧播放器能加载而常规提取失败，观察该播放器实际取得的配置、MP4、HLS 或 DASH 入口，再交给 yt-dlp 解析与下载。播放器配置可以提供标题、封面、时长及多个画质；只抓到单条分片不能视为取得整个视频。
4. 未加载的懒加载播放器可能需要滚动到对应位置，或用户先点击播放。没有有效请求时显示等待加载，不能保证打开作品后全部立即可采集。
5. 如果播放器本身不能播放，先展示实际失败原因，不承诺一定能够下载。

yt-dlp 的 Vimeo 提取器已支持从播放器配置读取 progressive、HLS、DASH、封面与时长，可复用该能力。[上游解析器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/vimeo.py)

### 其他托管平台

已获 yt-dlp 支持的平台优先复用其专用提取器；其余普通 MP4/HLS/DASH 可作为后续通用媒体观察能力扩展。浏览器资源筛选可参考[猫抓项目](https://github.com/xifangczy/cat-catch)，本阶段无需安装整个扩展或替换现有下载链路。

## Electron 接入要点

- `session.webRequest` 能读取请求 URL、请求头、响应状态、所属 webContents 和 frame，可用于关联作品与播放器。该接口不直接返回响应正文；如需播放器配置正文，应使用受控的调试协议读取，并验证独立 iframe 会话。[WebRequest 文档](https://www.electronjs.org/docs/latest/api/web-request)、[Debugger 文档](https://www.electronjs.org/docs/latest/api/debugger)
- 每类 WebRequest 事件仅最后注册的监听器生效；采用会话级统一分发，再按 webContents、frame、作品 ID、模块 ID 关联，避免不同标签或未来站点互相覆盖。
- 仅保留已识别播放器所需的媒体信息，设置域名、响应大小、数量与缓存时限；导航、关闭或取消时清理。Cookies 按实际目标平台提供，不把 Behance 与所有平台账号一起导出。
- 临时媒体签名只保存在主进程私有缓存中。下载开始时必要地重新获取；历史、界面和日志继续隐藏认证字段与签名。
- 外部视频授权应绑定用户明确选择、作品快照和模块标识。保留当前入队、执行、Python 三处默认拒绝路径，仅为经过校验的显式操作增加入口。
- 解析结果仍按作品分组；关闭自动解析时，媒体观察只更新私有缓存，不刷新右侧已完成的资源列表。
- 401、403、限流、网络故障、源文件签名过期需区分；只有明确拒绝账号凭据的证据才更新账号失效状态。

## 推荐实施与验收顺序

1. 先做独立诊断，保持产品默认跳过：用用户这 6 个 Vimeo 逐个对照原始 iframe 的浏览器播放、播放器响应、常规提取与补齐请求模拟后的提取；记录失败发生阶段，而非只记一个状态码。
2. 先接 YouTube 与 Vimeo 的显式解析操作，再接通用媒体观察。资源卡片标注真实托管平台，外部视频默认不勾选；此 UI 为建议，尚未实施。
3. 每类至少选择一个短小视频完成真实下载、音视频轨道和全片解码验收，再验证取消、签名过期重取、多标签隔离及账号错误归属。取不到确切文件大小时显示未知或明确估算，不能伪装成精确值。

本次结论是架构可行、已有明确的诊断优先级；尚未证明用户页面中的 7 个外部视频全部可下载。

## 后续实测（同日）

用户批准测试后，已通过独立脚本验证浏览器媒体配置路径：6 个 Vimeo 跨轮次取得 5 份有效配置，全部解析成功；一个 240p 短片实际下载及全片解码通过。单独补齐 curl_cffi 未解决直接提取失败，原有环境读取浏览器配置后也能下载。404 存在不稳定性，不能直接认定视频删除。见[实测报告](EXTERNAL_VIDEO_TESTS_2026-10-25.md)。产品默认跳过策略保持不变。
