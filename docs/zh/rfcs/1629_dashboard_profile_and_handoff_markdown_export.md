- Proposal Name: `dashboard_profile_and_handoff_markdown_export`
- Start Date: 2026-09-16
- RFC PR: [#1629](https://github.com/oceanbase/powercontext/pull/1629)
- Design Baseline: [master / 534460e0](https://github.com/oceanbase/powercontext/tree/534460e068ef733ec22a672b838734635fa1677e)。
- Related RFCs: [Handoff Artifact](0048_handoff_artifact.md)、[Handoff Report](0082_handoff_report.md)、[Source and Artifact REST API](1437_source_artifact_rest_api.md)、[Profile Artifact](1485_profile_artifact.md)。

# Summary

本 RFC 为 PowerContext Dashboard 增加 Profile 画像的只读界面，并在现有交接目录和详情页提供单条 Handoff 的 Markdown 下载。画像支持当前内容、精确历史版本和来源核对；交接导出固定到用户选中记录的精确 Revision，完整保留正文、已知遗漏及引用。实现复用现有 Scope、Artifact API、认证及 Tabler/Jinja2/HTMX 架构，不创建新的制品存储，不调用模型重新总结。

基线 master 已包含提示词页面 `/dashboard/prompts` 和主题记忆页面 `/dashboard/topics`。提示词可以读取当前范围的配置；主题记忆支持目录、搜索与精确版本读取。这两类页面作为已有能力保留，不属于本 RFC 的新增实现范围。

# Motivation

Dashboard 已能阅读记忆、经验、技能、交接、提示词和主题记忆，但当前 Scope 已保存的 Profile 尚无页面入口。用户需要核对画像内容、理解其来源或查看旧版本时，仍需直接调用 API。

现有交接详情能够展示工作目标、状态、下一步和遗漏，但不能直接下载 Markdown。手动复制页面容易丢失引用和精确版本，目录中的截短摘要也不能代替完整正文。

`POST /v1/handoff-reports/get` 已支持 Markdown 下载，但选择的是各 Scope 最新 committed Handoff。当前 Handoff 写入采用 Scope 内单例，详情仍可打开历史 Revision；因此该报告接口不能直接作为“导出当前条目”的实现。用户正在阅读 `handoff/handoff@3` 时，下载必须仍为该版本，即使之后产生 Revision 4。

## Goals and non-goals

- 提供当前 Scope 的 Profile 阅读入口、历史版本阅读和来源核对。
- 为交接目录条目及详情提供完整、确定性的精确版本 Markdown 下载。
- 明确空内容、读取错误、历史版本和下载失败的不同状态。
- 完成新阅读流程需要的目录返回状态传递，以及登录后的精确记录恢复。
- 保留现有提示词与主题记忆的路由、页面、搜索和分页行为，不增加重复入口，也不重构其布局。
- 不实现画像编辑、回滚、重新生成、审核和处理策略管理。
- 不增加统一所有制品页、跨 Scope 聚合、批量下载、PDF、公开分享、导入或 Agent 交接的新机器契约。

# Guide-level explanation

## Navigation and scope

导航中增加“画像”。保留现有入口的相对顺序，完整顺序为：首页、交接、记忆、经验与技能、画像、主题记忆、提示词、用量。画像在没有内容时仍可进入；生成能力未配置不影响已保存画像的阅读。

画像和交接均读取当前 Scope。父子关系或 Context Reference 不触发隐式汇总。切换 Scope 时清除上一范围的记录身份、历史选择和目录位置，返回对应阅读入口。切换语言或主题保留原范围和正在阅读的精确版本。

## Profile reading

一个 Scope 最多一个 Profile Head，身份固定为 `profile/profile`。画像页展示完整 Markdown，以及服务端保存的 Revision、生成方式、生成时间、来源窗口等实际存在的元数据。正文中的标题和结论来自已保存内容，展示层不补写画像章节。

“版本历史”打开历史 Revision 目录；选择某一版本后，页面标明“历史画像 · Revision N”，提供“返回当前画像”。读取旧版本不回滚 Head，也不重新生成内容。没有 committed Profile 时显示“此范围暂无画像”，不把没有内容解释为处理失败。

来源与引用位于正文之后。用户只能阅读已获准访问、且与当前精确版本相关联的原始材料。材料不可读时保留允许展示的引用信息，不用其他材料代替。

## Handoff Markdown download

交接目录每个可读条目提供“导出 Markdown”，详情页在标题旁提供相同操作。阅读与下载使用同一 Artifact ID 和 Revision。目录摘要即使被截短，下载仍包含完整正文。

例如 `/dashboard/handoff-detail?scope=S1&artifact=h1&revision=3` 下载 `handoff-r3.md`，正文记录完整身份。文件包含目标、状态、下一步、遗漏、逐条 citations、直接来源及上游制品引用。固定章节支持中文和英文，业务正文保持原文。

下载不创建 Revision、不更新工作状态、不触发继续交接。成功由浏览器下载结果呈现；失败返回可读错误和原记录入口，不把错误页保存为 Markdown 文件。凭据失效后，重新登录返回原精确详情，由用户再次点击下载。

## UI pre-design

以下预设计图使用示例内容说明布局与操作位置，不表示功能已经实现。继续使用现有 Tabler 的字体、留白、按钮、导航和来源弹窗；正文中的示例内容不构成新的内容 Schema。

### Profile reading page

左侧为已有范围选择与导航，右侧依次呈现页头、版本元信息、完整正文和来源。页头右侧为“版本历史”。历史目录采用服务端分页，显示 Revision 及实际可用的元信息；当前页无历史入口可用时，不显示可点击的空按钮。

选择历史条目进入固定 Revision 的阅读状态，显示“历史画像 · Revision N”；“返回当前画像”重新解析当前 Head。“返回版本历史”恢复原历史目录位置。来源弹窗关闭后保留阅读位置。

### Handoff collection with download

目录条目保留目标、状态、摘要及阅读入口，在操作区增加次要按钮“导出 Markdown”。继续进行、阻塞和完成用文字表达，不只依赖颜色。目录没有可读条目时不出现无对象的下载按钮。

每条记录的阅读与下载链接携带同一个精确引用。下载当前条目不等于生成当前范围最新报告；不在目录顶部增加含义不清的通用导出按钮。

### Handoff detail with download

页头显示目标、Artifact ID 和 Revision，主要操作为“导出 Markdown”。正文完整展示状态、下一步、遗漏及 citations，来源核对位于正文后。返回目录恢复进入详情前的分页位置。

下载使用浏览器原生附件行为，禁用 HTMX boost，不增加格式选择器，不预先显示“导出成功”。错误页提供“返回此交接”；需要认证时保留该精确详情作为登录后的返回目标。

### Mobile reading

小屏复用顶部折叠菜单。标题、版本、主要操作和正文按顺序排列；下载按钮位于正文之前，操作区可自然换行。长引用允许换行，代码块仅在自身区域滚动，不让整个页面横向滚动。画像使用相同阅读顺序，版本历史保持可达。来源弹窗沿用小屏全屏模式。

### Empty, error and version states

| 状态 | 页面表达 | 可用操作 |
| --- | --- | --- |
| 当前范围无 Profile | 保留标题和范围，显示“此范围暂无画像” | 切换范围 |
| Profile 历史版本 | 显示历史 Revision，完整呈现旧版本正文 | 返回历史目录或当前画像 |
| 历史游标失效 | 提示目录位置已失效，不混用其他页 | 重新打开历史第一页 |
| 来源读取失败 | 原记录继续可读，材料区域说明失败 | 重试材料或关闭弹窗 |
| 精确版本不存在或不可访问 | 显示实际错误，不改读 Head | 返回原目录或切换范围 |
| 无交接条目 | 保留目录结构，不展示无对象的下载按钮 | 切换范围 |
| 下载失败 | 可读错误页，不带附件头 | 返回精确详情后重试 |
| 凭据失效 | 登录页保留经过校验的记录返回目标 | 登录后回到原详情，不自动下载 |

实现验收覆盖中英文、明暗主题、键盘操作、200% 缩放、完整范围名称，以及弹窗关闭后的阅读位置。

# Reference-level explanation

## Existing capabilities and API reuse

设计基线为 `534460e0`。`prompts` 和 `topics` 已注册在 Dashboard 页面及导航中，并分别由 `load_prompts`、`load_topics` 加载。通用 Artifact 目录还已支持 Topic Memory 的 `title`、`summary`、`published_at` 和 `source_count`，本 RFC 不新增主题目录接口，也不要求额外逐条读取主题正文。

新增能力复用以下接口：

| 能力 | 接口 | 边界 |
| --- | --- | --- |
| 当前画像 | `GET /v1/scopes/{scope_id}/artifacts/profile?limit=1`，再读精确 Revision | 授权后的空目录表示无已提交画像；使用返回记录的精确 Revision |
| 画像历史目录 | `GET /v1/scopes/{scope_id}/artifacts/profile/profile/revisions` | 服务端游标分页，不推算未报告的历史 |
| 画像精确正文 | `GET /v1/scopes/{scope_id}/artifacts/profile/profile/revisions/{revision}` | 不回退到当前 Head |
| 交接精确正文 | `GET /v1/scopes/{scope_id}/artifacts/handoff/{artifact_id}/revisions/{revision}` | 完整正文、digest 和该版本引用 |
| 原始材料 | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | 先验证与当前精确记录的关联 |

页面使用 DashboardAPI 调用现有 HTTP 接口并执行鉴权，不直接访问数据库。Profile 按 `ProfileContent` 校验和呈现；Handoff 导出按 `HandoffContent` 校验，但必须同时保留精确读取响应中的身份、`content_digest`、`sources` 和 `artifacts`，不能仅使用丢弃元数据后的页面摘要对象。

## Routes and reading state

| 路由 | 状态参数 | 用途 |
| --- | --- | --- |
| `/dashboard/profile` | `scope`；可选 `revision`、`view=history`、`profile_cursor`、`profile_history`、`return_to` | 当前画像、精确历史正文或历史目录 |
| `/dashboard/handoff-download` | 必填 `scope`、`artifact`、`revision`；可选 `lang`、`return_to` | 单条交接下载 |
| 既有 `/dashboard/handoff-detail` | 保留既有参数，增加可选 `return_to` | 精确阅读和返回原目录 |

`view=history` 与 `revision` 同时出现时返回 422，不隐式决定阅读模式。`revision` 必须为正整数。历史目录从首个服务端游标开始读取，前后页历史沿用已有有界输入校验方式。Profile 初次入口可按现有规则解析默认 Scope；显式历史链接固定具体 Scope。

下载路由在通用 `/{page}` 路由前注册。只接受 Handoff，不将用户输入的 Family、路径或模板名称用于动态导入和文件读取。画像来源阅读扩展现有 evidence origin 识别与精确记录加载，不能落入经验页的默认 origin。

## Directory return context

`return_to` 只表示导航目标，不参与精确正文或下载对象选择。进入交接详情时，由服务端链接构造器把当前目录的 `scope`、`cursor`、`handoff_history`、`period` 和 `lang` 组装成 `/dashboard/handoff` 的站内相对 URL。画像历史目录到正文采用相同方式，目标固定为 `/dashboard/profile?view=history`，携带相同 Scope、`profile_cursor`、`profile_history` 和语言。

详情链接把该目标编码到 `return_to`，刷新、直接复制链接和切换语言后仍可恢复原目录。不依赖浏览器 Referer、`history.back()` 或一个跨标签页共享的“最后访问页”Cookie。

只接受上述两类返回路径及其白名单参数；拒绝 scheme、host、双斜线、反斜线、控制字符、重复参数和嵌套 `return_to`。目标 Scope 必须与当前记录相同；cursor 及历史参数按既有契约校验。返回 URL 最大 8 KiB。无效或过长的返回目标丢弃并回到当前范围的默认目录，不影响已合法指定的精确正文读取。

切换 Scope 时清除旧 `return_to`。返回链接指向的目录游标过期时展示恢复入口，由用户重新打开第一页。下载错误页始终根据请求的 `scope/artifact/revision` 重建精确详情链接，保留合法目录返回目标；不能直接把目录 URL 作为重新登录后的交接对象。

## Authentication recovery

当前 `save_session` 登录后固定跳转 `/dashboard/home`，且表单只解析 token；本 RFC 明确扩展登录恢复，不能仅声称复用现有流程即可返回记录。

对画像和交接访问发生 401 时，登录页增加隐藏字段 `next`。该目标由服务端构造，只允许本 RFC 涉及的 `/dashboard/profile` 和 `/dashboard/handoff-detail`，参数按对应路由白名单校验，必须保留精确 Scope 和已选 Revision。下载请求转换成精确详情地址，不把下载路由作为登录后的目标。允许携带一层经过上述校验的目录 `return_to`，不接受任意嵌套地址。

登录提交端允许 `token` 和 `next` 两个字段，保留现有 Origin 检查、token 校验和 Cookie 安全属性。`next` 单独限制为 16 KiB，表单总长度上限为 32 KiB；不得因为新增字段解除请求体长度限制。非法 next 忽略并回到首页。登录表单错误重试时保留合法目标；403 权限不足不自动当作凭据失效反复要求登录。

会话建立后跳转到原画像或交接详情，目标请求仍经过正常鉴权。下载不自动重放，用户再次点击导出。原记录已不存在或无权读取时，呈现该精确记录的错误，不退到另一个 Head。

## Profile rendering and evidence

Profile 正文来自 `ProfileContent.content`，元数据来自 `generation`，不存在的字段不补零或推测。Markdown 展示禁用原始 HTML 和图片请求，仅允许安全链接协议；外部链接设置安全打开属性。不得对未经处理的正文直接使用 Jinja `safe`。

沿用现有 Tabler 阅读区、版本目录分页、来源弹窗、中英文 labels 和主题切换。将画像的来源引用适配到同一材料核对流程，验证 `source_type + source_id` 与该精确 Revision 的关系，不能只依据 URL 请求某份材料。引用不授予读取材料的权限。

## Exact Handoff Markdown download

下载为同源 GET。`scope/artifact/revision` 必填，缺失或非法返回 422；不回退默认范围和当前 Head。经过 Dashboard session 认证后，经 DashboardAPI 获取指定 Handoff Revision 并校验完整内容。

成功响应使用：

```http
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename="handoff-r3.md"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

文件名由固定前缀和校验后的整数版本组成，不使用原始标题或 ID 构造响应头。完整身份记录在正文内。链接显式带上页面的 `lang`；只支持 `zh`、`en`，缺省沿用 Dashboard 语言解析规则。

下载禁用 HTMX boost。404、403、422、503 等失败保留相应状态及可读恢复页面，只有成功时添加附件头；401 按登录恢复流程处理。原文材料不可用不阻止导出已保存的交接引用，导出不额外读取 Source 正文。

## Markdown document contract

renderer 接收精确 Artifact 读取结果与 locale，使用领域模型解析正文，不读取 HTML、不调用 LLM。固定章节包括：

1. 标题及格式版本 `powercontext.handoff-markdown.v1`。
2. Scope、Family、Artifact ID、Revision 和 `content_digest`。
3. 目标与 disposition。
4. 按原顺序完整列出的 state 及每条 citations。
5. next_action 及其 citations；不存在时明确说明没有记录下一步。
6. 所有 omissions 及可选 citation。
7. Revision 的直接 SourceRef 和上游 ArtifactRef。

Source 引用保留 type 和 ID，Artifact 引用保留 Family、ID、Revision，Memory 引用保留完整精确条目版本信息。引用采用转义文本或安全代码块，不推断外部 URL，不嵌入 Source 原文，不宣称依据已重新验证。

输出为字面 UTF-8、LF 和固定章节顺序；相同输入及 locale 产生相同字节，不加入当前时间和随机值。`content_digest` 仅标识服务端制品内容，不冒充下载文件摘要，也不伪造 Handoff Report 的 selection/report digest。

业务内容按文本保真原则转义原始 HTML、链接和 Markdown 结构，不生成主动加载的图片或其他远程资源。多行文本保持可读，代码围栏长度根据内容中的最长连续反引号选择，避免正文闭合围栏。输出上限为 10 MiB UTF-8 字节，超限返回 413，不静默截断正文或引用。

单条 renderer 独立于认证路由。可共享无行为变化的转义 helper，但不通过构造假的 latest Report 实现导出；现有 Report schema、选择语义、digest 和输出契约不变。关闭 Handoff Report feature 不影响基于 Artifact 精确读取的单条下载。

## Compatibility and implementation boundary

新增路由属于 Dashboard 展示适配，不增加 `/v1` operation，不修改数据库、Artifact schema、Head/Candidate 生命周期或生成策略。既有 `prompts/topics` 页面保持路由和行为兼容。

Dashboard 继续默认关闭，遵守现有静态 Bearer 身份的启用条件，不扩大注入认证或授权 Provider 的团队部署支持范围。页面、来源、历史和下载均执行当前 Scope/Artifact 权限。

实现主要涉及 Dashboard 的 `routes.py`、`content.py`、`api.py`、`presenters.py`、`session.py`、模板及 labels，以及独立 Markdown renderer。新增返回参数不得改变既有提示词、主题记忆和其他页面参数的解析。

若实施中需要改变公开 HTTP 契约，先修改 `openapi/powercontext.yaml`，再执行 `make api-generate`、`make contract-test`，不手改生成的 Python 源码。本设计本身不要求公开 API 变更。

## Acceptance and validation

| 场景 | 验收要求 |
| --- | --- |
| 无画像、只有画像或无生成配置 | 页面可达，已有内容可读，空状态不冒充失败 |
| 当前画像与历史版本 | 正文、元数据、来源均属于指定版本 |
| 历史目录翻页、正文刷新与返回 | 恢复原目录位置；游标过期可回第一页 |
| Scope 切换和同名范围 | 清除旧记录与返回目标，不隐式汇总 |
| 交接目录摘要截短 | 导出完整正文、citations 和 omissions |
| Head 已更新或同 Scope 新建其他交接 | 下载仍为链接指定的 Artifact 和 Revision |
| 目录后续页进入详情、刷新、返回 | 恢复原游标及分页历史 |
| 下载请求凭据失效 | 登录后回到原精确详情，不自动下载 |
| 恶意 next/return_to 或过大表单 | 不跳外站，不跨 Scope 混用位置，长度限制生效 |
| 不存在、无权限或服务不可用 | 不回退其他版本，不生成错误附件，有恢复入口 |
| 同输入同 locale 重复下载 | 文件字节一致，不新增制品或改变工作状态 |
| 中文、英文、明暗主题、小屏 | 阅读、版本选择、来源核对和下载均可用 |
| HTML、链接、控制字符和围栏内容 | 页面及 Markdown 不产生结构或可执行注入 |
| 输出超限 | 明确失败，不静默截断 |
| 既有提示词与主题记忆 | 原路由、配置阅读、主题目录/搜索/精确读取行为不变 |

行为测试通过公开 API、页面导航和实际下载验证。Dashboard 测试覆盖新阅读及恢复流程；renderer focused tests 保护精确身份、正文和引用完整性；跨组件验收放在 `tests/e2e/`。不冻结私有调用顺序、模板清单或并发数量。

完成后运行 `make check`、相关 Dashboard/Server/Access/renderer 测试和 `make docs-test`。浏览器验收沿用 `scripts/dashboard_browser.cjs` 并增加真实下载、登录恢复及上述返回链路。仅公开契约变化时运行生成与 contract 检查。截图、下载文件和运行缓存放到仓库外，不提交凭据及生成网站输出。

# Drawbacks

新增画像入口增加导航占用，需要覆盖小屏及长范围名称。历史正文和目录返回状态增加 URL 参数，需要统一校验和长度控制。

登录恢复扩展了已有会话表单，必须避免开放跳转及自动重复下载。该改动仅承担恢复已选择阅读目标的责任，不引入新的权限模型。

本地 Markdown 文件不再受服务器后续权限变化控制。下载来自用户明确选择的可读交接，不附带额外 Source 原文或公开链接。

单条精确交接和最新 Handoff Report 是不同选择语义，实现必须保留其边界，并避免因共享 renderer helper 改变已有报告行为。

# Rationale and alternatives

Profile 采用独立阅读入口符合其单个完整画像的内容模型，不需要先引入通用“所有制品”浏览器。提示词和主题记忆已有对应界面，无需在本 RFC 重复设计。

精确读取后由服务端生成 Markdown，可以保留完整正文与引用。前端复制 DOM 容易受摘要裁剪、格式和页面结构影响，不能可靠代表完整制品。

直接调用最新 Report API 无法保证导出任意目录条目或历史 Revision。为报告增加任意版本 selection 会扩大公共契约及 digest 设计，因此本 RFC 使用 Dashboard 的单条下载适配。

返回目录采用白名单站内 URL，能够在刷新和复制链接后恢复位置；依赖 `history.back()` 或 Referer 无法稳定区分正常浏览、直接链接和登录跳转。登录后返回详情并由用户再次下载，可以保留明确的操作对象。

画像编辑、生成和回滚需要条件写入、审核与冲突恢复，会扩大本次工作范围；只读查看仍可与现有工具及 API 配合完成这些任务。

# Prior art

Artifact REST API 已提供当前 Head、历史目录、精确 Revision、digest 和 lineage。Profile RFC 定义画像内容、身份和生命周期；Handoff RFC 定义完整交接及精确引用；Handoff Report 提供最新范围报告的 Markdown 能力。

现有 Dashboard 已包含记忆、经验、技能、交接、提示词和主题记忆。新增阅读与下载遵循 [Dashboard 设计原则](../development/dashboard.md)的 Scope 边界、完整正文、独立错误恢复、中英文及明暗主题要求。

# Unresolved questions

首期功能边界、精确导出对象、目录状态传递和登录恢复在本文中明确。实施前需要选定安全 Markdown 渲染依赖并核对许可证，实施中通过浏览器验收确认导航空间、长正文和历史目录可用性。这些实现选择不能削弱精确读取、输出完整性及输入校验要求。

跨 Scope 聚合、画像编辑和批量报告下载不属于本 RFC，若需要应单独设计。

# Future possibilities

可后续增加画像条件编辑、历史比较，以及明确命名的“导出最新范围报告”。已有提示词和主题记忆页面的增强应依据实际差距独立提出，继续复用 Scope 权限与精确引用。
