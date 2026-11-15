---
title: 插件热加载与自定义接口
description: 在插件接口 RFC #2549 的宿主进程之上，支持运行中增减和更新插件，并允许插件声明自己的 HTTP 接口和 Dashboard 页面，由 core 统一挂载、鉴权和隔离。
---

- Proposal Name: `plugin_reload_and_apis`
- Start Date: 2026-11-10
- RFC PR: [oceanbase/powercontext#2579](https://github.com/oceanbase/powercontext/pull/2579)

# 概要

本提案在 [插件接口 RFC #2549](https://github.com/oceanbase/powercontext/pull/2549) 之上增加三项能力：

- **热加载**：每个 Server 进程定期扫描插件目录，发现新增、更新或移除的插件后在运行中生效，不需要重启 Server。
  检查通过的每个版本都会被复制成一份按内容摘要命名的只读快照，宿主进程和 Worker 只从快照导入。更新插件就是让新的
  子进程使用新快照，不在已运行的 Python 进程中替换模块，正在执行的调用也不会读到新代码。
- **插件自定义接口**：插件可以声明自己的 HTTP 路由，core 把它们挂载到独立的分组 `/v1/plugins/{name}/api/...` 下，
  完成认证和所需的授权检查后，把请求交给插件的宿主进程处理。插件可以选择把路由同时作为 MCP 工具提供给 Agent。插件的
  Artifact 仍然可以通过通用 Artifact 接口读取和检索。
- **插件页面**：插件可以随代码提供静态页面，在 Dashboard 中以沙箱 iframe 展示；页面通过宿主页面转发调用本插件的接口，
  拿不到用户的会话。

部署者仍然只需要把插件目录放到 `POWERCONTEXT_SERVER_PLUGINS_DIR` 下；core 不在数据库中保存插件状态，也不分发插件代码。

本文描述目标设计，不代表主线或发布版本已经可用。接口名称和代码示例用于说明契约，最终语法以实现为准。

# 动机

RFC #2549 定义了插件接口，但有两处限制：

- **变更需要重启**：新增、更新或移除插件都要重启 Server，期间所有接口短暂不可用。插件迭代频繁时，这对共享的中心记忆
  服务影响很大。
- **只能通过现有接口使用插件**：调用方只能使用 core 已有的 Source 和 Artifact 接口。很多插件需要领域专用的操作，例如：
  - 文档知识库需要创建知识库、上传文件、读取原始文件，以及跨多个知识库的混合检索；
  - 插件需要给用户展示自己的处理进度、配置或检索结果页面。

  这些操作放进 core 的通用接口并不合适，插件又没有别的入口。

RFC #2549 已经为热加载和自定义接口铺好了基础：

- Server 进程不导入插件代码，同步调用在 core 管理的宿主进程中执行，后台调用每次启动新的 Worker；
- 每个 Server 进程在内存中维护已激活插件的记录，不持久化插件状态。

旧版 Artifact 插件框架（RFC #2175）的热加载依赖持久化的期望状态、管理阶段和注册代次。本提案不重新引入这些机制：
插件集合由目录内容决定，切换只发生在子进程层面。

目标：

1. 部署者把插件目录放入、替换或移出，Server 在两个扫描周期内生效（内容需要连续两次扫描相同才检查），不需要重启，也不需要调用管理接口。
2. 每次调用从开始到结束只使用一个版本的插件代码，不会出现新旧代码混用，也不会出现用新声明提交旧代码结果的情况。
3. 插件可以声明自己的 HTTP 接口；core 负责路由、认证、授权、请求大小和时限，插件只写处理函数。
4. 插件接口对核心数据的写入与 RFC #2549 的用户修改遵循同样的权限和溯源规则。
5. 插件页面拿不到用户的 PowerContext 会话，也不能借用户会话调用本插件接口之外的 Server 接口。

# 指南级说明

## 1. 热加载

部署者直接操作插件目录：

| 操作 | 效果 |
| --- | --- |
| 放入新的插件目录 | 下一次扫描时检查并加载；检查通过后，Source 类型、family、接口和页面开始可用 |
| 替换已有插件目录的内容 | 下一次扫描时重新检查；通过后，新的同步调用和后台调用使用新版本，进行中的调用按旧版本完成 |
| 移出插件目录 | 下一次扫描时卸载；Source 类型、family、接口和页面不再可用，已保存的数据保留 |

- **生效时间**：每个 Server 进程每隔 `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS`（默认 60 秒）扫描一次。插件数量多、
  目录大时可以调大；设为 0 时关闭热加载，行为与 RFC #2549 相同。
- **替换要整体完成**：同一个插件目录在两次连续扫描中内容不变，才会被检查。部署者应先把新版本写到临时目录，再整体改名
  替换，避免扫描到写了一半的文件。
- **更新失败时继续使用旧版本**：新版本检查失败时，该插件继续以当前版本运行，同时记录检查失败的事件；修复后的版本被扫描到
  时再切换。新放入的插件检查失败时不加载。
- **依赖要先装好**：插件依赖安装在 Server 的 Python 环境中，不在插件目录里，扫描检测不到依赖的变化。新版本需要新的依赖时，
  部署者应先安装依赖，再替换插件目录。
- **多实例**：每个 Server 实例各自扫描自己机器上的插件目录。代码由部署者分发到各实例；在各实例都完成扫描之前，不同实例
  可能短暂使用不同版本。
- **数据升级由插件负责**：插件可以在声明中提供 `upgrade(ctx)`，core 在每次加载一个插件版本时调用一次，由插件自己判断
  是否需要升级、怎样升级。插件数据存在哪里、是什么格式，core 不知道，也不检查兼容性；新版本读不了或消费不了旧数据，由
  插件负责。插件可以在代码中兼容旧格式，也可以在 `upgrade` 中统一改写，或者两者结合。

## 2. 插件自定义接口

插件在声明中加上 `api`，列出自己的路由；处理函数是 `setup(ctx)` 返回对象上的方法。下面的文档插件把每个知识库对应为
一个 Scope：创建知识库就是创建一个 Scope，上传的文件存进插件存储，Source 只记录位置和摘要：

```python
from pydantic import BaseModel

from powercontext.plugins import ApiError, ApiRequest, ArtifactPlugin, BinaryResponse, PluginApi, Rendering, Route


class NewKnowledgeBase(BaseModel):
    title: str
    summary: str
    idempotency_key: str


class KnowledgeBase(BaseModel):
    scope_id: str


class UploadResult(BaseModel):
    source_id: str


class Documents:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def render_artifact(self, request) -> Rendering:
        return Rendering(text="# " + request.artifact.content["name"])

    async def create_knowledge_base(self, request: ApiRequest) -> KnowledgeBase:
        body: NewKnowledgeBase = request.body
        # 以调用者身份创建 Scope，权限与 POST /v1/scopes 相同（server.admin）
        scope = await self.ctx.scopes.create(title=body.title, summary=body.summary,
                                             idempotency_key=body.idempotency_key)
        return KnowledgeBase(scope_id=scope.scope_id)

    async def upload(self, request: ApiRequest) -> UploadResult:
        # 原始字节按内容摘要存进插件存储，Source 只记录位置和摘要
        location, digest = await save_original(self.ctx.storage, request.scope_id, request.body)
        ref = await self.ctx.sources.submit(
            request.scope_id, "document-input",
            DocumentInput(location=location, sha256=digest, name=request.query["name"][0]),
        )
        return UploadResult(source_id=ref.source_id)

    async def original(self, request: ApiRequest) -> BinaryResponse:
        # 专属存储不按 Scope 隔离：按 (scope_id, source_id) 查找，不属于该知识库的文件视为不存在
        found = await load_original(self.ctx.storage, request.scope_id, request.path_params["source_id"])
        if found is None:
            raise ApiError(404, "document_not_found", "document not found in this knowledge base")
        return BinaryResponse(content=found.content, content_type=found.media_type)


async def setup(ctx) -> Documents:
    return Documents(ctx)


plugin = ArtifactPlugin(
    family="kb-document",
    definitions=(DocumentInputDefinition(),),
    api=PluginApi(routes=(
        Route(method="POST", path="/knowledge-bases", handler="create_knowledge_base", access="authenticated",
              body="json", request_model=NewKnowledgeBase, response_model=KnowledgeBase),
        Route(method="POST", path="/knowledge-bases/{kb_id}/documents", handler="upload",
              access="scope.contribute", scope_param="kb_id", body="binary", response_model=UploadResult),
        Route(method="GET", path="/knowledge-bases/{kb_id}/documents/{source_id}/original", handler="original",
              access="scope.read", scope_param="kb_id"),
    )),
    setup=setup,
)
```

调用方这样使用：

```text
POST /v1/plugins/documents/api/knowledge-bases                                    （JSON 请求体）
POST /v1/plugins/documents/api/knowledge-bases/{kb_id}/documents?name=sop.md      （请求体为文件字节）
GET  /v1/plugins/documents/api/knowledge-bases/{kb_id}/documents/{source_id}/original
GET  /v1/plugins/documents/openapi.json                                            （该插件接口的 OpenAPI 描述）
```

- 插件路由是独立的一组，挂在 `/v1/plugins/{name}/api/` 下，路径由插件决定，不与 core 的路由混在一起。
- core 先认证调用者，再按路由声明的 `access` 检查权限：
  - `scope.read`、`scope.contribute`：`scope_param` 指定哪个路径参数是 Scope ID，core 检查调用者对它的权限后才调用插件；
  - `authenticated`：只要求调用者已登录，处理函数需要的其他权限用 `ctx.require(...)` 检查，例如跨多个知识库检索时逐个
    检查各 Scope。
- 处理函数可以读写核心数据，规则与 RFC #2549 的用户修改相同：写入以调用者身份进行，core 检查权限并记录溯源。
- 插件可以以调用者身份创建 Scope，权限与 `POST /v1/scopes` 相同，需要 `server.admin`。
- 插件 family 的 Artifact 仍然通过通用 Artifact 接口浏览、读取、检索和修改；插件接口只补充领域专用的操作。
- 插件接口不属于 `openapi/powercontext.yaml`，生成的客户端不包含它们；每个插件的接口描述由 core 根据声明生成。
- 路由声明 `mcp=True` 时，core 同时把它作为 MCP 工具提供，Agent 可以像调用内置工具一样调用，例如跨知识库检索。

## 3. 插件页面

插件在声明中加上 `pages`，指向插件目录中的静态文件：

```python
from powercontext.plugins import PluginPages

plugin = ArtifactPlugin(
    ...,
    pages=PluginPages(directory="ui", entry="index.html", title={"zh": "文档", "en": "Documents"}),
)
```

- Dashboard 导航中出现该插件的页面。打开后，Dashboard 在当前 Scope 下以沙箱 iframe 加载插件页面。
- 插件页面运行在不透明来源中，读不到 Dashboard 的 cookie，直接请求 Server 时也不会带上用户会话。调用本插件的接口时，
  页面通过 `postMessage` 请 Dashboard 转发，Dashboard 以当前登录用户的身份调用。
- 页面可以链接、跳转或在新窗口中打开外部站点，例如把检索结果交给外部的 RAG 服务页面展示。插件是可信代码，页面访问外部
  站点不受限制；core 只保证页面借不到用户的 PowerContext 会话。
- 页面只能是静态文件（HTML、JavaScript、CSS、图片、字体），由 core 直接从插件的当前版本快照提供，不运行插件代码。

页面中的调用：

```javascript
const client = powercontextPluginClient();            // 由 core 提供的小脚本，封装 postMessage
const { scopeId } = await client.context();
const result = await client.request("GET", `/knowledge-bases/${scopeId}/documents/${sourceId}/original`);
```

# 参考级说明

## 1. 热加载

### 1.1 扫描与检查

- 每个 Server 进程启动时和每隔扫描间隔，为插件目录的每个直接子目录计算内容摘要。
- 摘要与当前版本相同则跳过；与上一次扫描不同时先等待，连续两次扫描相同、且与当前版本不同时才检查，避免检查写了一半的
  目录。
- 检查与 RFC #2549 的启动检查相同，在版本快照上执行（见 1.3）；根据结果新增、更新或卸载插件。

- 摘要按文件内容计算，与 RFC #2549 的代码指纹相同。只看文件大小和修改时间不够：用时间戳归一化的归档解压出的新版本，
  大小和修改时间可能与旧版本完全相同。
- 扫描成本与插件目录的总字节数成正比。在 2 核开发机上，对 14 MB、676 个文件计算一次摘要约需 230 ms；插件目录不包含依赖，
  通常远小于此。每次扫描的耗时记录在指标中，部署者可以据此调整扫描间隔。
- 声明摘要由检查子进程对可序列化的声明部分计算，记录在已激活插件记录中，用于判断声明是否变化。
- 启动时的第一次扫描不需要等待稳定，与 RFC #2549 的启动加载相同。

### 1.2 切换

| 情况 | 处理 |
| --- | --- |
| 新增，检查通过 | 注册 Source 类型、family（读取、检索、合并注册表）、自定义接口和页面；本进程是 Supervisor 领导者时注册 binding |
| 新增，检查失败 | 不加载，记录检查失败；`GET /v1/plugins` 中该插件为 `failed` |
| 更新，检查通过，声明不变 | 切换到新快照：替换宿主进程（见 1.4）；后台调用从下一次派发起使用新快照 |
| 更新，检查通过，声明改变 | 按新声明重新注册：新增的 Source 类型、路由、页面生效，移除的不再可用；binding 的调度参数按新声明更新；切换到新快照 |
| 更新，检查失败 | 继续使用当前快照，记录检查失败；`GET /v1/plugins` 中给出最近一次检查失败的分类 |
| 移除 | 注销 Source 类型、family、接口、页面和 binding；等待进行中的同步调用结束或超时后停止宿主进程 |

- family 改名等同于移除旧 family、新增新 family。旧 family 的 Artifact 保留在 core 中，处理方式与 RFC #2549 中停用插件相同。
- 移除的 Source 类型不再接受写入，返回 422；已保存的 Source 保留。
- 移除插件后遗留的调用意图不再被处理，规则与 RFC #2549 的执行约束相同；同名插件重新放入后从原游标和 checkpoint 继续。
- Supervisor 的 binding 由领导者进程注册。领导权转移时，新领导者按自己的已激活插件记录注册 binding。

### 1.3 版本快照

插件目录由部署者随时修改，不能直接从中导入：替换目录后，已经运行的进程中尚未执行的延迟导入会读到新文件，一次调用里就会
混用新旧代码。因此插件代码只从版本快照导入：

- **生成**：摘要稳定后，core 把插件目录复制成数据目录下的只读快照，以内容摘要命名，在快照上执行检查。依赖不在插件目录中，
  快照通常很小。
- **使用**：检查子进程、宿主进程和 Worker 都把快照目录加入导入路径，`ctx.plugin_dir` 指向快照；页面的静态文件也从当前
  快照提供。插件目录本身只用于扫描。
- **取代代码指纹核对**：RFC #2549 中 Worker 和宿主进程在导入前核对代码指纹。有了快照，派发时直接把快照路径交给 Worker，
  快照不会被修改，不再需要核对；热加载关闭时，仍按 RFC #2549 的规则核对。
- **清理**：每个 Server 进程只删除自己生成、且既不是当前版本、也没有本进程的宿主进程或 Worker 在使用的快照，在下一次
  扫描中删除。Server 启动时只删除早于本进程启动时间、且不是任何当前版本的快照。同一台机器上的多个 Server 进程（例如
  `api` 与 `background` 角色）共用数据目录时，一个进程启动不会删掉另一个进程正在使用的快照。快照是本机的派生文件，删除后
  可以从插件目录重新生成，不属于需要备份的数据。

### 1.4 宿主进程的替换与后台调用

- 新版本检查通过后，core 从新快照启动新的宿主进程；新进程就绪后，新的同步调用和自定义接口请求发往新进程。
- 旧进程不再接受请求，继续用旧快照完成进行中的请求，超过该调用的时限后终止。

- 新宿主进程启动失败时，按检查失败处理：继续使用旧进程和旧快照。
- Worker 派发时携带快照路径。已派发的 Worker 用旧快照完成，结果按旧版本的声明提交，相当于"更新前刚好完成"；新派发的
  Worker 使用新快照。现有的 Supervisor 租约 fence 继续负责屏蔽领导权变更后的旧提交。
- 一次调用只在一个快照中执行，因此同步调用、自定义接口和后台调用都不会混用新旧代码，不需要持久化的注册代次。

### 1.5 进程间的差异

已激活插件记录只在本进程内存中，不跨进程同步：

- 在所有实例都完成扫描之前，同一个请求在不同实例上可能得到不同结果（例如一个实例已加载新插件、另一个还没有）。
- `GET /v1/plugins` 和 `GET /v1/plugins/{name}` 返回处理该请求的实例的视图，响应中增加 `version_digest`（当前快照的内容
  摘要）、`declaration_digest`、`state`（`loaded`、`failed`）、`activated_at` 和最近一次检查失败的分类，便于部署者比对
  各实例是否一致。
- 后台调用只在领导者的 Worker 中执行，使用领导者本机的快照；同步调用在接收请求的实例上执行，使用该实例当前的快照。

### 1.6 插件数据升级

```python
async def upgrade(ctx: PluginContext) -> None:
    """每次加载本版本时调用；自行判断已有数据是否需要升级。"""

plugin = ArtifactPlugin(..., setup=setup, upgrade=upgrade, upgrade_timeout_seconds=600)
```

- **调用时机**：每次加载一个插件版本都会调用，包括 Server 启动时加载和热加载切换到新版本，不只在首次安装时调用。插件
  自己记录数据的版本（例如写在专属存储中，或写进 Artifact 内容），与代码中的版本比较后决定做什么。
- **在哪里执行**：每个加载新版本的 Server 实例都执行，不论角色。接收请求的实例由新宿主进程在 `setup(ctx)` 之前执行，
  完成后才报告就绪，新版本就绪之前 core 不向它转发同步调用和接口请求。`background` 角色的实例不运行宿主进程，由一个只
  执行 `upgrade` 的短时子进程执行，完成后才用新快照派发后台调用。这样新版本的 `generate` 只会在本实例的 `upgrade` 完成后
  运行。
- **只做快速、可重复的变更**：`upgrade` 用于插件存储的 schema 变更等很快完成的工作，不用于大规模改写数据。大量数据的
  格式变化，在读取时惰性迁移（新代码兼容读取旧格式，写回时使用新格式），或由插件提供独立的迁移脚本，由部署者在升级
  前执行；迁移脚本使用插件自己的存储和 core 的公开接口，core 不为它提供额外机制。
- **失败**：`upgrade` 抛出异常或超过 `upgrade_timeout_seconds`（默认 600 秒）时，按新版本检查失败处理：热加载时继续使用
  旧版本；Server 启动时插件为 `failed`。
- **上下文**：专属存储、模型和日志；`ctx.reads(scope_id)` 与 `ctx.writer(scope_id)` 以插件自身的身份执行，与 Worker
  相同，不检查调用者权限，用于改写少量旧格式的 Artifact；写事务规则与 Worker 相同，一次 `upgrade` 最多开启一个写事务块。core 不提供"列出插件数据所在的 Scope"，插件按自己的记录
  遍历。Artifact 的历史版本不可修改，读取历史版本的代码仍需兼容旧格式。
- **重复与并发**：每个 Server 实例、每次加载都会调用，core 不在实例之间协调。`upgrade` 必须可以重复执行，也可以在多个
  实例上同时执行；需要互斥时，由插件在专属存储中自己加锁。
- **新旧版本并存**：多实例逐个完成扫描、热加载切换的过程中，旧版本可能仍在读写同一份数据。插件应先做新旧版本都能使用的
  变更，等旧版本不再运行后，在后续版本中再清理。
- **不支持降级**：core 不记录插件的数据版本，也不阻止加载更旧的代码；旧代码读不了新数据，由插件负责。
- **Source 定义**：core 按名称和版本登记 Source 定义，新旧版本可以并存。新版本插件仍需读取旧版本的 Source 时，应继续
  声明旧版本的定义。

## 2. 插件自定义接口

### 2.1 声明

```python
class Route(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                                   # 相对路径，以 / 开头，可含 {参数}
    handler: str                                # setup(ctx) 返回对象上的方法名
    access: Literal["authenticated", "scope.read", "scope.contribute"]
    scope_param: str | None = None              # access 为 Scope 权限时必填：作为 Scope ID 的路径参数
    body: Literal["none", "json", "binary"] = "none"
    max_body_bytes: int | None = None           # 本路由的请求体上限；JSON 默认 1 MiB，二进制默认取部署上限
    request_model: type[BaseModel] | None = None    # body 为 json 时用于校验和生成接口描述
    response_model: type[BaseModel] | None = None
    timeout_seconds: float = 30
    description: str | None = None              # 接口描述与 MCP 工具描述
    mcp: bool = False                           # 同时作为 MCP 工具提供

class PluginApi(BaseModel):
    routes: Sequence[Route]
```

- `ArtifactPlugin` 和 RFC #2617 的 `SourcePlugin` 都可以声明 `api`。声明 `api` 的插件需要宿主进程。
- 路由挂载在 `/v1/plugins/{name}/api{path}`。同一插件内 `(method, path)` 不能重复；`handler` 必须存在且是 async 方法，
  在检查子进程中校验。
- `access` 为 `scope.read` 或 `scope.contribute` 时，`scope_param` 必须是 `path` 中的参数；core 检查调用者对该 Scope 的
  权限，通过后把它作为 `ApiRequest.scope_id`。`access` 为 `authenticated` 时不能设置 `scope_param`，`scope_id` 为空。
- 写核心数据之前，必须确认调用者对目标 Scope 的 `scope.contribute`：路由声明 `access="scope.contribute"`，或在处理函数中
  调用 `ctx.require("scope.contribute", scope_id)`。后一种适合路径中没有 Scope ID 的路由，例如知识库这类把 Scope 隐藏在
  插件自己 ID 之后的接口。`ctx.writer` 和 `ctx.sources.submit` 只对本次请求中已经确认过的 Scope 可用。
- 路由本身不能声明 `server.admin`；需要管理员权限的操作在处理函数中用 `ctx.require("server.admin")` 检查，或由 core 在
  `ctx.scopes` 中检查。

### 2.2 请求与响应

```python
class Caller(BaseModel):
    principal_id: str                           # 调用者身份；不含凭据

class ApiRequest(BaseModel):
    scope_id: str | None                        # scope_param 对应的 Scope；authenticated 路由为 None
    route: str                                  # 声明中的 path 模板
    path_params: dict[str, str]
    query: dict[str, list[str]]
    body: BaseModel | bytes | None              # 按 body 声明：request_model 实例、字节或 None
    caller: Caller

class BinaryResponse(BaseModel):
    content: bytes
    content_type: str
    filename: str | None = None

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None: ...   # status 必须是 4xx
```

- 处理函数返回 `response_model` 的实例（序列化为 JSON，状态码 200）、`BinaryResponse`，或 `None`（204）。
- 抛出 `ApiError` 时按其状态码返回，状态码必须在 400–499 之间，且不能是 401 或 403（认证和授权由 core 决定）；
  抛出其他异常返回 500，只记录异常类型。
- 请求和响应大小有上限：路由可以用 `max_body_bytes` 声明自己的请求体上限，不声明时 JSON 为 1 MiB、二进制为部署上限。
  部署上限 `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES`（默认 32 MiB）同时限制所有插件路由的请求体和响应，路由声明
  的值超过它时按部署上限执行。插件路由不受 Server 通用请求体上限 `POWERCONTEXT_SERVER_HTTP_MAX_REQUEST_BODY_BYTES` 约束，
  只受部署上限约束，这样部署者为插件上传放大上限时，不必放大所有接口的上限。超出时 core 直接返回 413，不调用插件。
- 超过 `timeout_seconds` 返回 504；宿主进程不可用返回 503；插件或路由不存在返回 404。
- core 不为插件接口提供幂等键或请求记录，重试语义由插件定义；需要幂等时，插件在请求中要求调用方提供幂等键。

### 2.3 处理函数能做什么

处理函数在宿主进程中执行，`setup(ctx)` 在每个宿主进程中只执行一次，Scope 随每个请求传入（与 RFC #2549 相同）。除了
RFC #2549 的上下文（专属存储、模型、日志），自定义接口调用还提供：

| 能力 | 说明 |
| --- | --- |
| `ctx.require(action, scope_id=None)` | 检查调用者是否具备 `scope.read`、`scope.contribute`（需要 `scope_id`）或 `server.admin`，不具备时抛出异常，接口返回 403。用于 `authenticated` 路由和跨 Scope 的操作 |
| `ctx.reads(scope_id)` | 指定 Scope 的 Artifact 只读视图，读取前按调用者权限检查；可用 `reads(...).reads(family)` 读取其他 family |
| `ctx.writer(scope_id)` | Artifact 插件的写入器，范围为指定 Scope 和本插件的 family；只能用于本次请求中已确认 `scope.contribute` 的 Scope |
| `ctx.sources.submit(scope_id, definition, value)` | 提交一条本插件定义的 Source（Source 插件，或声明了 `definitions` 的 Artifact 插件），执行与 Source 写入接口相同的校验、`resolve` 和 Projection，返回 `SourceRef`；只能用于本次请求中已确认 `scope.contribute` 的 Scope |
| `ctx.scopes.create(*, title, summary, idempotency_key, parent_scope_id=None)` | 以调用者身份创建 Scope，复用 `POST /v1/scopes` 的实现：需要调用者具备 `server.admin`，调用者成为新 Scope 的管理员，相同幂等键返回同一个 Scope |
| `ctx.scopes.update(scope_id, *, expected_version, title=None, summary=None)` | 以调用者身份修改 Scope 的标题和摘要，权限与 `PUT /v1/scopes/{scope_id}` 相同；只能修改本插件创建的 Scope |
| `ctx.scopes.list()` | 列出本插件创建、调用者可见的 Scope |

写入规则：

- 自定义接口中的写入以调用者身份进行，权限与通用接口相同：新建需要 Scope 的贡献权限，结果归属于调用者；修改已有 Artifact
  需要调用者对它的写权限（`artifact.write`）。core 在每个写操作执行时检查，不满足即抛出异常，接口返回 403。
- 自定义接口中的写入不是 RFC #2549 的用户修改：core 不自动记录 `lineage_only` Content Source，写入器也只能引用本次调用
  窗口中的 Source，而接口调用没有窗口，所以新版本不带出处。需要记录"谁做了什么"时，插件把这次操作作为本插件定义的 Source
  提交，由 `generate` 写入新版本并引用它；修改基于的版本在 `generate` 中核对，已变化时放弃这次修改。接口返回时修改尚未
  生效，插件应提供查询修改状态（待处理、已应用、已放弃）的方式，让调用方知道修改是否被放弃。知识库示例的切块编辑
  就是这样实现的。
- 一次请求最多开启一个写事务块，块内写操作在一个事务中提交，与 RFC #2549 的写事务规则相同。
- 处理函数不能推进游标或 checkpoint，也不能写其他插件的 family。
- 专属存储不按 Scope 隔离（见 RFC #2549 参考级说明 1.2）。处理函数按 `request.scope_id` 读写自己的数据，不能只凭路径中的
  其他 ID 返回数据，否则对一个 Scope 有权限的调用者能读到其他 Scope 的数据。core 中单个 Artifact 的授权（例如 Artifact
  查看者、所有者）只在 Scope 权限之外追加访问，不收窄 Scope 成员的读取范围，所以按 Scope 检查后从自有存储返回正文不会
  越权；core 以后若增加收窄单个 Artifact 读取的规则，插件接口需要随之按 Artifact 检查。

插件创建的 Scope：

- core 在创建时给 Scope 加上外部引用 `{"kind": "powercontext.plugin", "value": "<插件名>"}`，`ctx.scopes.list()` 和
  `ctx.scopes.update()` 据此判断归属，部署者也能看出 Scope 由哪个插件创建。
- 创建 Scope 的权限要求不因插件而放宽。允许非管理员创建 Scope（例如在父 Scope 下创建子 Scope）需要修改 core 的授权
  模型，不在本提案范围内。
- 插件的业务配置（例如知识库的分块策略）不写入 Scope，由插件以 `scope_id` 为键存进自己的存储。先创建 Scope，再写配置；
  写配置失败时，调用方以相同的幂等键重试，得到同一个 Scope 后重新写入。

### 2.4 MCP 工具

声明 `mcp=True` 的路由由 core 生成为 MCP 工具，与 Server 现有的 MCP 工具使用同一个 MCP 端点：

- 只有 `body` 为 `none` 或 `json` 的路由可以开启；二进制路由不能开启，检查时报错。
- 工具名为 `<插件名>__<handler>`，插件名原样保留（可以含 `-`），与 handler 之间用双下划线分隔。插件名不含下划线，第一个
  双下划线之前就是插件名，因此不同插件的工具名不会相撞。工具名不超过 64 个字符，超出或与内置工具重名时插件检查失败。
  描述取自 `description`，开启时必须填写。工具参数由路径参数、查询参数和
  `request_model` 合成，`scope_param` 对应的参数就是 Scope ID。
- 调用工具等同于以 MCP 会话的身份调用该路由，认证、`access` 授权、时限和错误处理与 HTTP 调用相同。
- `GET` 且 `access` 为 `scope.read` 或 `authenticated` 的路由标为只读工具，其他标为写入工具，宿主据此决定是否需要用户确认。
- 热加载新增、更新或移除插件时，工具随之增删；MCP 客户端下一次列出工具时看到变化，core 能通知的会话会收到工具列表
  变化的通知。
- 只有插件显式开启的路由会成为工具，避免工具数量过多占用 Agent 的上下文。

### 2.5 接口描述

`GET /v1/plugins/{name}/openapi.json`（需要认证）返回 core 根据路由声明生成的 OpenAPI 文档：路径、方法、权限要求、
`request_model` 和 `response_model` 的 JSON Schema，以及统一的错误响应。插件接口的兼容性由插件自己负责，需要不兼容的
变更时，插件可以在路径中自带版本（例如 `/v2/knowledge-bases`）。

## 3. 插件页面

### 3.1 声明与静态文件

```python
class PluginPages(BaseModel):
    directory: str = "ui"                       # 插件目录内的相对路径
    entry: str = "index.html"
    title: dict[str, str]                       # 按语言给出的导航标题，至少包含 en
```

- 静态文件路径为 `/dashboard/plugins/{name}/ui/{path}`，只提供当前快照中 `directory` 下的文件，拒绝 `..` 和指向目录外的
  符号链接。
- 只允许以下类型：`.html`、`.js`、`.css`、`.json`、`.svg`、`.png`、`.jpg`、`.webp`、`.woff2`；单个文件不超过 5 MiB。
- 静态文件只包含插件代码和资源，不含用户数据，因此不要求登录。
- 响应头中的 CSP：
  - 以 `sandbox` 指令让页面即使被直接打开，也运行在不透明来源中，读不到 Dashboard 的 cookie。它直接请求 Server 时属于跨站
    请求，浏览器不会带上 `SameSite=Strict` 的会话 cookie，页面也没有 API 凭据。
  - 脚本只能来自插件快照，页面运行的代码与插件版本一致。
  - 链接、表单、弹窗和对外部 HTTPS 站点的请求都允许：插件是可信代码，可以把用户带到外部服务，或调用外部服务的公开接口。
    这些请求都不带用户的 PowerContext 会话。
  - 只允许 Dashboard 嵌入。

### 3.2 宿主页面与转发

- **宿主页面**：`/dashboard/plugins/{name}` 是 core 的 Dashboard 页面，要求登录。它以沙箱 iframe 加载插件页面，iframe 第一次
  加载后把当前 Scope、语言和主题发给页面；之后只接受来自该 iframe 的请求消息，路径必须匹配本插件声明的路由，再以同源
  请求经服务端转发，结果用消息传回（二进制响应以 Blob 传回）。
- **服务端转发**：`/dashboard/plugins/{name}/api/...` 是 Dashboard 的服务端路由，要求 Dashboard 会话，并要求请求的
  `Sec-Fetch-Site` 为 `same-origin`（缺失时校验 `Origin` 与 Server 一致）。它沿用 Dashboard 现有的做法，以会话对应的身份
  调用 `/v1/plugins/{name}/api/...`。
- **会话隔离**：Dashboard 的会话 cookie 是 `HttpOnly`、`SameSite=Strict`，路径为 `/dashboard`。插件页面处于不透明来源，
  脚本读不到 cookie；Server 也不接受 `Origin: null` 的转发请求。
- **跳转后停止转发**：沙箱 iframe 可以把自己导航到外部站点，CSP 无法阻止。外部页面同样能向宿主页面发送 `postMessage`，
  并且通过 `event.source` 校验。宿主页面因此在 iframe 第二次加载后停止转发，外部页面借不到用户会话。插件页面应实现为
  单页应用；在插件页面之间跳转同样会停止转发。
- **范围**：宿主页面只转发到本插件已声明的路由；转发后的请求仍由 core 按调用者权限检查，插件页面不能借此访问调用者无权
  访问的 Scope。
- **页面客户端脚本**：core 在 `/dashboard/plugins/_client.js` 提供一个小脚本，封装上述消息协议，插件页面可以直接引用。

## 4. 执行约束

- 自定义接口在宿主进程中执行，与 RFC #2549 的同步调用共用本插件的宿主进程组，遵循同样的并发上限、超时取消、失去响应
  时终止和重启退避规则：超过 `timeout_seconds` 的请求只取消它自己并返回 504，不影响同一进程中的其他请求。接口请求较重的
  插件可以调大 `host_processes`，以分钟计的工作应转入后台的 `generate`（见 RFC #2549 参考级说明第 6 节）。
- 自定义接口的请求体在 Server 进程中读取并校验大小后，以字节或已校验的模型传给宿主进程；`request_model` 的校验在宿主进程
  中执行，失败返回 422。
- 静态页面文件由 Server 进程直接从当前快照读取，不进入宿主进程。切换版本后，新请求立即读到新快照的文件。

## 5. 持久化

除 RFC #2549 已有的 Source 定义清单外，不新增数据库数据。热加载新版本时，新增或变化的 Source 定义按名称和版本登记到
`pc_source_definition_manifests`；已有 Source 引用这些定义，所以插件版本卸载时不删除它们。插件集合、版本、路由表和页面
注册都只存在于各 Server 进程的内存中，Server 重启后按插件目录重建。版本
快照是数据目录下的本机文件，可以随时从插件目录重新生成。插件接口写入的核心数据与 RFC #2549 相同；插件创建的 Scope 是
普通的 Scope；插件自己的数据存放在插件存储中。

## 6. 接口变更

| 变更 | 内容 |
| --- | --- |
| 新增 | `/v1/plugins/{name}/api/...`：插件声明的路由 |
| 新增 | `GET /v1/plugins/{name}/openapi.json` |
| 新增 | `/dashboard/plugins/{name}`、`/dashboard/plugins/{name}/ui/...`、`/dashboard/plugins/{name}/api/...`、`/dashboard/plugins/_client.js` |
| 新增 | 插件声明 `mcp=True` 的路由作为 MCP 工具出现在 Server 的 MCP 端点 |
| 新增 | `powercontext.plugins`：`PluginApi`、`Route`、`ApiRequest`、`BinaryResponse`、`ApiError`、`Caller`、`PluginPages`；上下文的 `require`、`reads`、`writer`、`sources`、`scopes` |
| 调整 | `SourcePlugin`、`ArtifactPlugin` 增加 `api`、`pages`、`upgrade` 和 `upgrade_timeout_seconds` 字段 |
| 调整 | `GET /v1/plugins`、`GET /v1/plugins/{name}` 的响应增加 `version_digest`、`declaration_digest`、`state`、`activated_at`、最近一次检查失败的分类、路由列表和页面信息 |
| 新增 | 环境变量 `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS`（默认 60，0 关闭热加载）、`POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES`（默认 32 MiB） |

`openapi/powercontext.yaml` 只描述 `/v1/plugins/{name}/openapi.json` 和 `/v1/plugins` 的变化，不包含插件自己的路由。

## 7. 可观测性

| 字段 | 含义 |
| --- | --- |
| `plugin_name` | 插件名 |
| `plugin_event` | `loaded`、`updated`、`removed`、`check_failed`、`upgrade_failed` |
| `version_digest` | 本次生效的快照内容摘要 |
| `declaration_digest` | 本次生效的声明摘要 |
| `plugin_route` | 自定义接口的路由模板（例如 `GET /knowledge-bases/{kb_id}/documents/{source_id}/original`），不含实际参数值 |
| `plugin_method` | 沿用 RFC #2549，新增 `api` |
| `scan_ms` | 一次扫描从开始到完成的耗时，毫秒 |

- 每次加载、更新、移除和检查失败记录一条事件；检查失败只记录已知的失败分类，不记录异常消息。
- 指标标签可以使用 `plugin_name`、`plugin_route` 和响应状态类别（2xx、4xx、5xx），不包含 Scope ID、路径参数或查询参数。
- 日志不包含请求体、响应体和文件内容。新增字段按开发指南登记到允许列表，并在中英文可观测性文档中说明。

# 缺点

- **多实例存在不一致窗口**：各实例独立扫描，插件版本在一个扫描周期内可能不一致；调用方可能在不同实例上看到不同的结果。
- **扫描有持续开销**：每个 Server 进程定期读取插件目录的全部内容计算摘要，开销随插件目录总字节数增加。
- **快照占用磁盘**：每个版本在数据目录下保存一份插件目录的副本，旧版本在不再使用后清理。
- **依赖变化不会被检测**：插件依赖在共用的 Python 环境中，更新依赖需要部署者先安装；依赖与插件代码不兼容时，检查会失败。
- **插件接口在主契约之外**：插件路由不进入 `openapi/powercontext.yaml`，没有生成的客户端；作为 MCP 工具提供的路由，其名称
  和参数也随插件变化。兼容性由插件自己保证。
- **插件页面要做成单页应用**：页面跳转后宿主页面停止转发，多页面的插件页面需要重新打开。
- **处理函数是可信代码**：宿主进程隔离普通故障，不是沙箱。插件接口以调用者身份写入核心数据，插件代码中的缺陷可能以用户
  名义写入错误数据，这一点与 RFC #2549 的用户修改相同。

# 理由与备选

| 方案 | 取舍 |
| --- | --- |
| 文件系统监听（inotify 等） | 响应更快，但各平台行为不同，容器和网络文件系统上不可靠，还会收到写了一半的事件；定期扫描加稳定判断更简单 |
| 按文件大小和修改时间判断变化 | 不需要读文件内容，但时间戳归一化的归档解压后，不同内容可能有相同的大小和修改时间，更新会被永久忽略 |
| 管理接口触发重新加载 | 只能作用于收到请求的实例，多实例时需要再做分发；定期扫描自然覆盖所有实例。是否增加"立即扫描本实例"的接口见未决问题 |
| 持久化插件集合与注册代次（旧 Artifact 插件框架） | 可以让各实例严格一致，但需要重新引入期望状态、管理阶段和代次屏蔽，这是 RFC #2549 刚删掉的机制 |
| 在 Server 进程内重新导入插件模块 | Python 无法可靠地替换已加载模块：插件的同级模块以顶层名字进入 `sys.modules`，旧对象仍被引用。替换子进程没有这个问题 |
| 子进程直接从插件目录导入，用指纹屏蔽旧代码 | 不需要复制，但目录替换后，已运行进程中的延迟导入会读到新文件，同一次调用混用新旧代码；提交前核对指纹也挡不住同步调用 |
| 按版本快照导入（本提案） | 每次调用固定在一个不可变的版本上，更新失败时可以继续使用旧版本；代价是复制和清理快照 |
| 更新失败时停用插件 | 行为简单，但一次错误的部署会让插件的同步调用和接口全部不可用；有快照后保留旧版本没有额外成本 |
| 插件接口在 Server 进程中执行 | 延迟最低，但插件阻塞或崩溃会影响整个 Server，也会重新把插件代码导入 Server 进程 |
| 插件接口每次请求派发 Worker | 复用现有机制，但每次都要启动新的 Python 进程，延迟以秒计 |
| 插件路由一律挂在 Scope 下 | 授权规则统一，但创建知识库这类不属于任何 Scope 的操作无处安放；改为插件自定路径，Scope 授权由 `scope_param` 声明 |
| 插件声明 `server.admin` 路由 | 插件可以提供管理功能，但会在路由层引入第二套管理员授权；第一版只在处理函数中用 `ctx.require` 检查 |
| 插件创建 Scope 时放宽权限 | 普通用户也能创建知识库，但这改变了"Scope 由管理员创建"的 core 授权规则，应另行提案 |
| 插件页面与 Dashboard 同源 | 实现最简单，但页面脚本可以带着用户的会话调用任何 Dashboard 和 Server 接口，等于冒充用户 |
| 插件页面禁止访问外部站点 | 需要同时限制请求、表单、弹窗和导航，CSP 无法完整做到；插件本身是可信代码，服务端也能访问外部，禁止页面访问外部没有实际意义 |
| 插件页面使用独立端口或域名 | 也能隔离来源，但部署者要额外配置端口、证书和反向代理；沙箱 iframe 不需要额外配置 |
| 插件页面由插件代码在服务端渲染 | 页面能直接拿到数据，但每次页面请求都要进入宿主进程，并且要定义模板和转义规则；静态页面加接口调用更简单 |
| 所有插件路由自动成为 MCP 工具 | 不需要额外声明，但工具数量随插件增长，占用 Agent 的上下文，二进制路由也不适合作为工具；改为按路由开启，只开放 JSON 路由 |
| core 记录插件的数据版本，调用时传入旧版本号（Moodle、Home Assistant） | 插件不必自己记录版本，但那些项目中被迁移的数据由宿主保管；本提案中插件专属存储的位置和格式只有插件知道，Artifact 内容的格式也由插件定义，core 记录的版本号无法反映这些数据的实际状态 |
| core 按阶段执行插件提供的迁移脚本（Kong 的 up / teardown） | 能处理多实例滚动升级，但要求 core 理解插件的存储并提供迁移运行器和命令；本提案只调用一个钩子，把"先做兼容变更、旧版本停止后再清理"作为对插件的要求 |
| 插件数据升级由插件在钩子中自行判断（本提案，与 Backstage、WordPress 相同） | core 只约定调用时机和失败处理，不感知插件数据；代价是兼容性完全由插件保证，core 不兜底 |
| 插件接口不提供 MCP 工具 | 实现最少，但 Agent 是 PowerContext 的主要使用方，插件的领域操作（例如跨知识库检索）对 Agent 不可用。现有 MCP 工具本来就由 HTTP 接口生成，插件路由可以复用同一机制 |

# 先例

- **Dify 插件守护进程**：每个插件包按 `<作者-名字-版本>@<校验和>` 解压到独立的工作目录，并从该目录启动运行时；新版本
  使用新目录，与旧版本互不影响。本提案的版本快照采用相同的做法，但依赖仍共用 Server 的 Python 环境。
- **旧 Artifact 插件框架**：保存插件目录的内容指纹，Worker 加载前重新核验。本提案沿用按内容计算的摘要。
- **Chrome 扩展**：扩展页面运行在扩展自己的来源中，通过声明的权限和消息传递与网页及浏览器交互，不能直接使用网页的会话。
  本提案的插件页面采用相同的隔离思路：不透明来源，加上只能调用本插件接口的消息转发。
- **VS Code Webview**：扩展的界面运行在 iframe 中，通过 `postMessage` 与扩展通信，并要求设置 CSP。插件页面的消息协议与之类似。
- **Dify 插件的 Endpoint 扩展**：插件可以声明 HTTP 端点，由 Dify 挂载并转发请求。本提案的自定义接口同样由宿主统一挂载，
  并由 core 完成认证和授权。
- **插件数据升级**：Backstage 的后端插件在初始化时自行执行数据库迁移，框架明确不负责插件的结构迁移；WordPress 的激活钩子
  在插件更新时不一定触发，插件的通行做法是自己保存数据版本、每次加载时比较。本提案的 `upgrade` 采用同样的分工，并在每次
  加载时都调用。Kong 把插件迁移分成不破坏旧数据的 up 和清理用的 teardown，以支持新旧节点并存，本提案把这一点作为对插件
  的要求。Moodle 和 Home Assistant 由宿主记录版本并传入旧版本号，适用于数据由宿主保管的场景。
- **Gunicorn 等进程管理器的平滑重载**：先启动新的工作进程，再让旧进程处理完已有请求后退出。宿主进程的替换采用同样的顺序。
- **PowerContext 现有基础**：RFC #2549 的宿主进程、已激活插件记录、用户修改规则和插件专属存储；Supervisor 的 binding、
  租约 fence 和退避；Scope 的创建接口与幂等键；Dashboard 的会话与服务端调用方式。

# 未决问题

| 问题 | 说明 |
| --- | --- |
| 立即扫描 | 是否提供 `POST /v1/plugins/rescan`（`server.admin`）让部署者立即触发本实例扫描；多实例时只作用于收到请求的实例 |
| 扫描间隔默认值 | 60 秒是否合适；是否需要在大插件目录下限制每次扫描的字节数 |
| 接口限流 | 是否为插件接口提供按路由或按调用者的限流，还是沿用 Server 现有的入口限制 |
| Desktop 中的插件页面 | Desktop 应用是否嵌入插件页面，以及其 WebView 中的沙箱和 CSP 行为 |
| 浏览器行为验证 | CSP `sandbox` 指令下 `'self'` 对子资源的匹配，以及不透明来源请求的 `SameSite` 处理，需要在主流浏览器上验证后确定最终响应头 |
| 二进制请求的流式处理 | 第一版在 Server 进程中完整读入请求体再转给宿主进程；大文件是否需要流式传输 |

# 后续可能

- 插件页面在 Desktop 应用中展示。
- 插件之间通过声明的接口互相调用。
- 为插件接口提供生成的客户端或 SDK 辅助。
