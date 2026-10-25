# 淘宝图片采集调研（2026-10-25）

后续用户已授权测试与集成，首版实现及验收边界见 [集成记录](TAOBAO_2026-10-25.md)。本文保留调研阶段的结论和来源。

## 范围与结论

用户已明确：电商平台只采集图片，不需要视频。本次只调研，不改应用代码。

目标是淘宝、天猫、天猫超市单个商品页的主图、SKU 规格图、图文详情图。
本轮找到可参考的公开源码，但未找到已验证能完整、稳定覆盖上述三个入口的成熟现成依赖。
建议在现有 Electron 中增加独立的淘宝图片适配层，沿用已有预览、单张下载、批量勾选、图片校验和任务队列。

## 当图：已核实的官方信息

- [Chrome 商店](https://chromewebstore.google.com/detail/downpictures-universal-im/gleocnabcllkbfobbdkibkkghpichebp?hl=zh-CN)
- [官网](https://downpictures.com/)

商店页面当前版本为 2.0.131.30001，标注更新日期为 2026-10-01。
官方描述包含主图、SKU 图、详情图、高清图、图片去重、预览、像素尺寸、文件大小、批量下载与下载管理。
官方还描述浏览商品时同步结果。本应用继续保留用户选定的手动解析与自动解析关闭时保留上次结果的行为。
本次没有安装扩展，也没有实际运行当图下载；以上是官方功能声明，并非本机下载验收结果。
本轮未找到可确认属于该扩展官方的公开解析源码仓库。

## GitHub 仓库核对

下表更新时间为本次 GitHub API 的 `pushed_at`，不等于真实页面适配成功或持续维护保证。
公开源码仅下载为文本并静态阅读，没有安装、执行第三方项目。

| 项目 | 最近推送 | 实际源码与局限 | 采用意见 |
| --- | --- | --- | --- |
| [WenLiux/shangtu-picun](https://github.com/WenLiux/shangtu-picun) | 2026-08-11 | 详情容器定向采集、SKU 图与规格名、格式识别，有模拟页面测试；没有主图采集流程；主机匹配未包含天猫超市 | 分类策略最贴近需求，参考思路；许可证 CC BY-NC-SA 4.0，不能当作 MIT 组件直接并入 |
| [henglyrepo/Taobao-Image-Downloader](https://github.com/henglyrepo/Taobao-Image-Downloader) | 2026-02-25 | DOM、懒加载属性、srcset、背景图、内联脚本图片扫描；泛域名包含天猫子域；没有可靠主图/SKU/详情区分；后台将不同页面图片累加到同一存储 | 有 MIT LICENSE，可研究通用图片提取；需重新实现商品隔离、分类和原图候选校验 |
| [PaidaxingW/fatkun-pdx](https://github.com/PaidaxingW/fatkun-pdx) | 2026-05-06 | 声明多平台分类，实际主体是全页扫描、关键词/比例猜分类；750 像素下限会漏掉缩略展示的 SKU 和窄长详情图；定义的站点选择器未用于分区收集 | 可参考界面，不适合当主解析器；README 写 MIT，但仓库没有独立许可证文件 |
| [KhangLama/safari-web-image-downloader](https://github.com/KhangLama/safari-web-image-downloader) | 2026-06-29 | 通用图片扫描与阿里 CDN 缩放后缀处理，不是淘宝商品分类适配 | 不作为主要依赖；README 称 MIT，未见独立 LICENSE |
| [nufeichuiyun/taobao-sku-share-extension](https://github.com/nufeichuiyun/taobao-sku-share-extension) | 2026-08-18 | 明确列出淘宝、天猫、天猫超市三个域名，处理商品 id、skuId 与页面变化；用途是分享链接，并不是图片下载 | 可参考 URL 与规格状态的边界，没有 LICENSE，不直接复制源码 |
| [dkbonjour/GoTaoBao](https://github.com/dkbonjour/GoTaoBao) | 2017-08-27 | 很早的商品信息工具 | 过旧，不作为当前实现基础 |

### 重点源码证据

1. 商图批存：
   - [源码](https://github.com/WenLiux/shangtu-picun/blob/main/shangtu-picun.user.js)
   - 第 13–14 行、38–42 行：只匹配 `detail.tmall.com`、`item.taobao.com`，没有 `chaoshi.detail.tmall.com`。
   - 第 456 行起：详情图从 `#imageTextInfo-content` 等具体容器读取，能避免全页扫描混入评价、推荐等图片。
   - 第 514 行起：以“颜色分类”和 `[data-vid]` 等节点找 SKU，过滤禁用项和无图项。因此它展示的“全部 SKU”并不代表覆盖所有规格模板或下架规格。
   - 第 564 行起：SKU 未加载时会滚动规格节点；直接移植会影响左侧页面位置，需要另作处理。
   - [LICENSE](https://github.com/WenLiux/shangtu-picun/blob/main/LICENSE) 与脚本头均为 CC BY-NC-SA 4.0，GitHub API 的 NOASSERTION 不是没有许可证。
2. Taobao-Image-Downloader：
   - [content.js](https://github.com/henglyrepo/Taobao-Image-Downloader/blob/main/content.js)：`scanDOM` 扫全页，而非当前商品各区。
   - [background.js](https://github.com/henglyrepo/Taobao-Image-Downloader/blob/main/background.js)：`capturedImages` 合并到全局存储，不能直接采用其缓存方式。
   - `getHighResUrl` 用字符串正则替换缩放后缀，不能将改写后的 URL 一概视为有效原图。
3. Fatkun PDX：
   - [content.js](https://github.com/PaidaxingW/fatkun-pdx/blob/main/content.js)：`MIN_SIZE = 750`；`collectImages` 对全页 `img` 收集后按尺寸过滤，站点的 main/detail 选择器未实际参与收集。

## 建议的接入方式（尚未实现）

### 页面与素材识别

- 一个“淘宝”站点分组，覆盖淘宝、天猫、天猫超市；保持原始主机，不能把天猫超市改写为普通天猫地址。
- 商品按 `id` 绑定；保留 `skuId` 作为当前规格上下文。切规格不能混入另一商品的结果。
- 优先读取左侧当前商品已经加载的媒体数据，再从主图、规格、详情三个限定区域读取 DOM。
- 只有需要补充时才观察当前页面自己的媒体响应；按商品 ID、页面代次绑定，避免同页推荐商品污染。
- 详情图需要处理懒加载与不同页面模板。若需展开或加载，限定在商品详情区域并恢复浏览位置；不能拿“当前已出现几张图”冒充详情已完整。
- 无图规格正常跳过；保留有图规格的名称和图片对应关系。去重时保留规格关联，不能只按 URL 去重后丢失名称。
- 第一阶段针对单个商品，不包含搜索列表、整店、评论晒图；后续可在确认需求后独立扩展。

### 原图与保存

- 保留网页预览地址与下载候选地址；只对可信阿里图片 CDN 的已知缩放规则生成原图候选。
- 原图候选失败后回退页面提供的真实地址；不盲目删除任意查询参数，不去掉可能必要的签名。
- 下载复用现有图片解码校验与原子保存流程；依据实际图片格式处理扩展名，避免 HTML 错误页误存为图片。
- 像素尺寸与 MB 文件大小分开取得。预览图的尺寸不能冒充原图尺寸；不完整时仍显示“尺寸未知”。
- 图片卡片沿用单张下载；分类内支持多选和全选；自动解析关闭时保持上次解析结果。
- 登录沿用当前浏览器会话：页面要求登录或验证时明确提示。是否需要登录须通过真实样本确认，不能把浏览器访问失败当作账号凭证失效。

## 用户样例与验收状态

以下只在文档中去除追踪参数，保留商品 id、skuId 与原始域名；不代表已经打开或验收成功。

| 类型 | 样例 | 当前状态 |
| --- | --- | --- |
| 天猫 | https://detail.tmall.com/item.htm?id=1093500428611&skuId=6312145842345 | 尚未真实验收 |
| 天猫超市 | https://chaoshi.detail.tmall.com/item.htm?id=1070500473161&skuId=6120992941508 | 尚未真实验收 |
| 淘宝 | https://item.taobao.com/item.htm?id=784122440183&skuId=6040731631249 | 尚未真实验收 |

公开网页读取工具未能返回这三条商品页内容。随后尝试在浏览器打开淘宝样例时，被浏览器安全策略明确拦截；该拦截没有经过用户权限提示或自动审批。
按工具要求停止商品页浏览器操作，没有尝试通过其他浏览器、CDP、脚本网络访问或间接地址绕过。
因此没有本次真实图片数量、图片尺寸、完整性或下载成功率数据；不能将静态源码核对写成三条链接已通过。

未来真实验收至少包括：三个入口分别主图/规格图/详情图数量与页面对照；排除推荐/评价图；原图失败回退；一张与多张保存；格式解码；SKU 关联；切商品与切规格隔离；懒加载完整性；手动解析结果保留。
本阶段不包含任何电商视频解析或下载。

## 本地证据

本次公开源码文本与 API 元数据保存在被忽略的 `electron-artifacts/taobao-research-20261025/`。
未修改应用源码、配置、依赖、登录状态或下载任务。
