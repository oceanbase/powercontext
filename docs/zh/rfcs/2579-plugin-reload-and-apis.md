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
  插件代码只在检查子进程、宿主进程和 Worker 中导入，更新插件就是换掉这些子进程，不在已运行的 Python 进程中替换模块。
- **插件自定义接口**：插件可以声明自己的 HTTP 路由，core 把它们挂载到 `/v1/plugins/{name}/api/scopes/{scope_id}/...`，
  先完成认证和 Scope 授权，再把请求交给插件的宿主进程处理。
- **插件页面**：插件可以随代码提供静态页面，在 Dashboard 中以沙箱 iframe 展示；页面只能通过宿主页面转发调用本插件的接口，
  拿不到用户的会话。

部署者仍然只需要把插件目录放到 `POWERCONTEXT_SERVER_PLUGINS_DIR` 下；core 不保存插件状态，也不分发插件代码。

本文描述目标设计，不代表主线或发布版本已经可用。接口名称和代码示例用于说明契约，最终语法以实现为准。

# 动机

RFC #2549 定义了插件接口，但有两处限制：

- **变更需要重启**：新增、更新或移除插件都要重启 Server，期间所有接口短暂不可用。插件迭代频繁时，这对共享的中心记忆
  服务影响很大。
- **只能通过现有接口使用插件**：调用方只能使用 core 已有的 Source 和 Artifact 接口。很多插件需要领域专用的操作，例如：
  - 文档知识库需要上传文件、按 revision 分页读取分块、读取原始文件，以及跨多个知识库的混合检索；
  - 插件需要给用户展示自己的处理进度、配置或检索结果页面。

  这些操作放进 core 的通用接口并不合适，插件又没有别的入口。

RFC #2549 已经为热加载和自定义接口铺好了基础：

- Server 进程不导入插件代码，同步调用在 core 管理的宿主进程中执行，后台调用每次启动新的 Worker；
- 每个 Server 进程在内存中维护已激活插件的记录，不持久化插件状态。

旧版 Artifact 插件框架（RFC #2175）的热加载依赖持久化的期望状态、管理阶段和注册代次。本提案不重新引入这些机制：
插件集合由目录内容决定，切换只发生在子进程层面。

目标：

1. 部署者把插件目录放入、替换或移出，Server 在一个扫描周期内生效，不需要重启，也不需要调用管理接口。
2. 更新期间已经开始的调用要么按旧代码完成，要么失败重试，不会出现用新声明提交旧代码结果的情况。
3. 插件可以声明自己的 HTTP 接口；core 负责路由、认证、授权、请求大小和时限，插件只写处理函数。
4. 插件接口对核心数据的写入与 RFC #2549 的用户修改遵循同样的权限和溯源规则。
5. 插件页面与 Dashboard 隔离：页面代码不能读取用户会话，也不能调用本插件接口以外的 Server 接口。

# 指南级说明

## 1. 热加载

部署者直接操作插件目录：

| 操作 | 效果 |
| --- | --- |
| 放入新的插件目录 | 下一次扫描时检查并加载；检查通过后，Source 类型、family、接口和页面开始可用 |
| 替换已有插件目录的内容 | 下一次扫描时重新检查；通过后，新的同步调用和后台调用使用新代码 |
| 移出插件目录 | 下一次扫描时卸载；Source 类型、family、接口和页面不再可用，已保存的数据保留 |

- **生效时间**：每个 Server 进程每隔 `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS`（默认 30 秒）扫描一次。设为 0 时关闭
  热加载，行为与 RFC #2549 相同。
- **替换要整体完成**：同一个插件目录在两次连续扫描中内容不变，才会被检查。部署者应先把新版本写到临时目录，再整体改名
  替换，避免扫描到写了一半的文件。
- **更新失败不影响已有数据**：新版本检查失败时，该插件进入 `degraded`：读取和检索照常（它们不运行插件代码），同步调用和
  自定义接口返回 503，后台调用暂停，直到下一个版本检查通过。
- **多实例**：每个 Server 实例各自扫描自己机器上的插件目录。代码由部署者分发到各实例；在各实例都完成扫描之前，不同实例
  可能短暂使用不同版本。

## 2. 插件自定义接口

插件在声明中加上 `api`，列出自己的路由；处理函数是 `create(ctx)` 返回对象上的方法：

```python
from pydantic import BaseModel

from powercontext.plugins import ApiRequest, BinaryResponse, PluginApi, Route, SourcePlugin


class UploadResult(BaseModel):
    source_id: str


class Documents:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def upload(self, request: ApiRequest) -> UploadResult:
        # 原始字节存进插件专属存储，Source 只记录位置和摘要
        location, digest = await save_original(self.ctx.storage, request.body, request.query["name"][0])
        ref = await self.ctx.sources.submit("document-input", DocumentInput(location=location, sha256=digest))
        return UploadResult(source_id=ref.source_id)

    async def original(self, request: ApiRequest) -> BinaryResponse:
        content, media_type = await load_original(self.ctx.storage, request.path_params["source_id"])
        return BinaryResponse(content=content, content_type=media_type)


async def create(ctx) -> Documents:
    return Documents(ctx)


plugin = SourcePlugin(
    definitions=(DocumentInputDefinition(),),
    api=PluginApi(routes=(
        Route(method="POST", path="/documents", handler="upload", access="scope.contribute",
              body="binary", response_model=UploadResult),
        Route(method="GET", path="/documents/{source_id}/original", handler="original", access="scope.read"),
    )),
    create=create,
)
```

调用方这样使用：

```text
POST /v1/plugins/documents/api/scopes/team-a/documents?name=sop.md     （请求体为文件字节）
GET  /v1/plugins/documents/api/scopes/team-a/documents/{source_id}/original
GET  /v1/plugins/documents/openapi.json                                （该插件接口的 OpenAPI 描述）
```

- 所有路由都挂在某个 Scope 下。core 先认证调用者，再检查调用者对路径中 Scope 的权限（路由声明的 `access`），然后把请求交给
  该插件的宿主进程。
- 处理函数可以读写核心数据，规则与 RFC #2549 的用户修改相同：写入以调用者身份进行，core 检查权限并记录溯源。
- 处理函数需要涉及其他 Scope 时（例如跨多个知识库检索），用 `ctx.require(...)` 逐个检查调用者的权限。
- 插件接口不属于 `openapi/powercontext.yaml`，生成的客户端和 MCP 不包含它们；每个插件的接口描述由 core 根据声明生成。

## 3. 插件页面

插件在声明中加上 `pages`，指向插件目录中的静态文件：

```python
from powercontext.plugins import PluginPages

plugin = SourcePlugin(
    ...,
    pages=PluginPages(directory="ui", entry="index.html", title={"zh": "文档", "en": "Documents"}),
)
```

- Dashboard 导航中出现该插件的页面。打开后，Dashboard 在当前 Scope 下以沙箱 iframe 加载插件页面。
- 插件页面运行在不透明来源中，读不到 Dashboard 的 cookie，也不能直接请求 Server。它通过 `postMessage` 请 Dashboard 转发请求，
  Dashboard 只转发到本插件的自定义接口，并以当前登录用户的身份调用。
- 页面只能是静态文件（HTML、JavaScript、CSS、图片、字体），由 core 直接从插件目录提供，不运行插件代码。

页面中的调用：

```javascript
const client = powercontextPluginClient();            // 由 core 提供的小脚本，封装 postMessage
const { scopeId } = await client.context();
const result = await client.request("GET", `/documents/${sourceId}/original`);
```

# 参考级说明

## 1. 热加载

### 1.1 扫描与检查

```text
每个 Server 进程，每隔 rescan 秒（以及启动时）：
  -> 列出插件目录的直接子目录，为每个目录计算指纹：所有文件的相对路径、大小、修改时间（不含 __pycache__）
  -> 指纹与上次扫描相同，且与已激活版本相同：跳过
  -> 指纹与上次扫描不同：记为待稳定，本轮不检查
  -> 指纹连续两次扫描相同，且与已激活版本不同：在有时限的检查子进程中检查（与 RFC #2549 的启动检查相同）
  -> 根据检查结果执行新增、更新、降级或卸载
```

- 指纹只用文件元数据，不读文件内容，扫描成本与插件目录的文件数成正比。插件依赖安装在 Server 的 Python 环境中，不在插件
  目录里，不参与扫描。
- 声明摘要由检查子进程对可序列化的声明部分计算，记录在已激活插件记录中，用于判断声明是否变化。
- 启动时的第一次扫描不需要等待稳定，与 RFC #2549 的启动加载相同。

### 1.2 切换

| 情况 | 处理 |
| --- | --- |
| 新增，检查通过 | 注册 Source 类型、family（读取、检索、合并注册表）、自定义接口和页面；本进程是 Supervisor 领导者时注册 binding |
| 更新，检查通过，声明不变 | 替换宿主进程（见 1.3）；后台调用从下一次派发起使用新代码 |
| 更新，检查通过，声明改变 | 按新声明重新注册：新增的 Source 类型、路由、页面生效，移除的不再可用；binding 的调度参数按新声明更新；替换宿主进程 |
| 更新，检查失败 | 进入 `degraded`：保留 Source 类型和 family 的读取注册，停止宿主进程、暂停该插件的 binding，同步调用和自定义接口返回 503 |
| 移除 | 注销 Source 类型、family、接口、页面和 binding；等待进行中的同步调用结束或超时后停止宿主进程 |

- family 改名等同于移除旧 family、新增新 family。旧 family 的 Artifact 保留在 core 中，处理方式与 RFC #2549 中停用插件相同。
- 移除的 Source 类型不再接受写入，返回 422；已保存的 Source 保留。
- 移除插件后遗留的调用意图不再被处理，规则与 RFC #2549 的执行约束相同；同名插件重新放入后从原游标和 checkpoint 继续。
- Supervisor 的 binding 由领导者进程注册。领导权转移时，新领导者按自己的已激活插件记录注册 binding。

### 1.3 宿主进程的替换

```text
新版本检查通过
  -> 启动新的宿主进程，导入新代码并核对声明
  -> 新进程就绪后，新的同步调用和自定义接口请求发往新进程
  -> 旧进程不再接受请求，等待进行中的请求结束；超过该调用的时限后终止
```

新进程启动失败时，按检查失败处理（`degraded`），旧进程同样停止，因为旧代码已经不在磁盘上，无法保证它后续导入的模块与
已加载的部分一致。

### 1.4 后台调用的屏蔽

Worker 每次调用都重新导入插件。为了不把旧代码的结果按新声明提交，执行器在派发时把插件目录的指纹交给 Worker：

- Worker 导入插件前，重新计算指纹，与派发时的指纹不一致则直接失败，不调用插件；
- 写事务块提交前再计算一次指纹，不一致则回滚，本次调用失败，由 Supervisor 退避后重新派发。

Worker 与插件目录在同一台机器上，指纹比对不需要持久化状态，也不需要注册代次。现有的 Supervisor 租约 fence 继续负责屏蔽
领导权变更后的旧提交。提交前的最后一次检查与提交之间仍有一个很小的窗口：这时提交的是旧代码按旧声明算出的结果，相当于
"更新前刚好完成"。

### 1.5 进程间的差异

已激活插件记录只在本进程内存中，不跨进程同步：

- 在所有实例都完成扫描之前，同一个请求在不同实例上可能得到不同结果（例如一个实例已加载新插件、另一个还没有）。
- `GET /v1/plugins` 和 `GET /v1/plugins/{name}` 返回处理该请求的实例的视图，响应中增加 `declaration_digest`、`state`
  （`loaded`、`degraded`）和 `activated_at`，便于部署者比对各实例是否一致。
- 后台调用只在领导者的 Worker 中执行，屏蔽规则见 1.4；同步调用在接收请求的实例上执行，使用该实例当前的版本。

## 2. 插件自定义接口

### 2.1 声明

```python
class Route(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                                   # 相对路径，以 / 开头，可含 {参数}
    handler: str                                # create(ctx) 返回对象上的方法名
    access: Literal["scope.read", "scope.contribute"]
    body: Literal["none", "json", "binary"] = "none"
    request_model: type[BaseModel] | None = None    # body 为 json 时用于校验和生成接口描述
    response_model: type[BaseModel] | None = None
    timeout_seconds: float = 30

class PluginApi(BaseModel):
    routes: Sequence[Route]
```

- `SourcePlugin` 和 `ArtifactPlugin` 都可以声明 `api`。声明 `api` 的插件需要宿主进程。
- 路由挂载在 `/v1/plugins/{name}/api/scopes/{scope_id}{path}`。同一插件内 `(method, path)` 不能重复；`handler` 必须存在且是
  async 方法，在检查子进程中校验。
- `access` 是调用者对路径中 Scope 必须具备的权限。需要写核心数据的路由必须声明 `scope.contribute`。
- 插件不能声明 Scope 之外的全局路由，也不能声明需要 `server.admin` 的路由。

### 2.2 请求与响应

```python
class Caller(BaseModel):
    principal_id: str                           # 调用者身份；不含凭据

class ApiRequest(BaseModel):
    scope_id: str
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
- 请求和响应大小有上限：JSON 请求体 1 MiB，二进制请求体和响应由 `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES` 决定
  （默认 32 MiB）。超出时 core 直接返回 413，不调用插件。
- 超过 `timeout_seconds` 返回 504；宿主进程不可用或插件处于 `degraded` 返回 503；插件或路由不存在返回 404。
- core 不为插件接口提供幂等键或请求记录，重试语义由插件定义。

### 2.3 处理函数能做什么

处理函数在宿主进程中执行，`create(ctx)` 每个 Scope 执行一次后复用（与 RFC #2549 相同）。除了 RFC #2549 的上下文（专属存储、
模型、日志），自定义接口调用还提供：

| 能力 | 说明 |
| --- | --- |
| `ctx.require(action, scope_id)` | 检查调用者在指定 Scope 上是否具备 `scope.read` 或 `scope.contribute`，不具备时抛出异常，接口返回 403。用于跨 Scope 的操作 |
| `ctx.reads(scope_id=None)` | Artifact 只读视图，范围为指定 Scope（默认为路径中的 Scope），读取前按调用者权限检查；可用 `reads(...).reads(family)` 读取其他 family |
| `ctx.writer` | Artifact 插件的写入器，只在 `scope.contribute` 路由中提供，范围为路径中的 Scope 和本插件的 family |
| `ctx.sources.submit(definition, value)` | Source 插件提交一条本插件定义的 Source，执行与 Source 写入接口相同的校验、`resolve` 和 Projection，返回 `SourceRef`；只在 `scope.contribute` 路由中提供 |

写入规则：

- 自定义接口中的写入以调用者身份进行，权限与通用接口相同：新建需要 Scope 的贡献权限，结果归属于调用者；修改已有 Artifact
  需要调用者对它的写权限（`artifact.write`）。core 在每个写操作执行时检查，不满足即抛出异常，接口返回 403。
- 每个新建或改变内容的写操作，core 都按 RFC #2549 的用户修改规则记录 `lineage_only` Content Source。
- 一次请求最多开启一个写事务块，块内写操作在一个事务中提交，与 RFC #2549 的写事务规则相同。
- 处理函数不能推进游标或 checkpoint，也不能写其他插件的 family。

### 2.4 接口描述

`GET /v1/plugins/{name}/openapi.json`（需要认证，任何 Scope 权限均可读取）返回 core 根据路由声明生成的 OpenAPI 文档：路径、
方法、`request_model` 和 `response_model` 的 JSON Schema，以及统一的错误响应。插件接口的兼容性由插件自己负责，需要不兼容的
变更时，插件可以在路径中自带版本（例如 `/v2/documents`）。

## 3. 插件页面

### 3.1 声明与静态文件

```python
class PluginPages(BaseModel):
    directory: str = "ui"                       # 插件目录内的相对路径
    entry: str = "index.html"
    title: dict[str, str]                       # 按语言给出的导航标题，至少包含 en
```

- 静态文件路径为 `/dashboard/plugins/{name}/ui/{path}`，只提供 `directory` 下的文件，拒绝 `..` 和指向目录外的符号链接。
- 只允许以下类型：`.html`、`.js`、`.css`、`.json`、`.svg`、`.png`、`.jpg`、`.webp`、`.woff2`；单个文件不超过 5 MiB。
- 静态文件只包含插件代码和资源，不含用户数据，因此不要求登录。
- 响应头：

  ```text
  Content-Security-Policy: sandbox allow-scripts allow-forms allow-downloads;
                           default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
                           img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-ancestors 'self'
  X-Content-Type-Options: nosniff
  ```

  `sandbox` 指令让页面即使被直接打开，也运行在不透明来源中；`connect-src 'none'` 禁止页面直接发起网络请求；
  `frame-ancestors 'self'` 只允许 Dashboard 嵌入。

### 3.2 宿主页面与转发

```text
/dashboard/plugins/{name}?scope=...            （core 的 Dashboard 页面，要求登录）
  -> 渲染 <iframe sandbox="allow-scripts allow-forms allow-downloads" src="/dashboard/plugins/{name}/ui/{entry}">
  -> iframe 加载后，发送 {type: "powercontext.context", scopeId, locale, theme}
  -> 收到 iframe 的 {type: "powercontext.request", id, method, path, query, body}
     -> 只接受来自该 iframe 的消息（event.source 校验）
     -> path 必须匹配本插件声明的某个路由
     -> fetch /dashboard/plugins/{name}/api/scopes/{scopeId}{path}（同源，带会话 cookie）
     -> 回复 {type: "powercontext.response", id, status, body}（二进制响应以 Blob 传回）
```

- **服务端转发**：`/dashboard/plugins/{name}/api/...` 是 Dashboard 的服务端路由，要求 Dashboard 会话，并要求请求的
  `Sec-Fetch-Site` 为 `same-origin`（缺失时校验 `Origin` 与 Server 一致）。它沿用 Dashboard 现有的做法，以会话对应的身份
  调用 `/v1/plugins/{name}/api/...`。
- **会话隔离**：Dashboard 的会话 cookie 是 `HttpOnly`、`SameSite=Strict`，路径为 `/dashboard`。插件页面处于不透明来源，
  脚本读不到 cookie；它直接请求 Server 时属于跨站请求，浏览器不会带上会话，Server 也不接受 `Origin: null` 的转发请求。
- **范围**：宿主页面只转发到本插件已声明的路由，Scope 固定为当前 Dashboard 选择的 Scope；插件页面不能切换到用户无权访问的
  Scope，因为转发后的请求仍由 core 按调用者权限检查。
- **页面客户端脚本**：core 在 `/dashboard/plugins/_client.js` 提供一个小脚本，封装上述消息协议，插件页面可以直接引用。

## 4. 执行约束

- 自定义接口在宿主进程中执行，与 RFC #2549 的同步调用共享并发上限、超时终止和重启退避规则。
- 自定义接口的请求体在 Server 进程中读取并校验大小后，以字节或已校验的模型传给宿主进程；`request_model` 的校验在宿主进程
  中执行，失败返回 422。
- 静态页面文件由 Server 进程直接读取，不进入宿主进程。热加载替换目录后，新请求立即读到新文件。

## 5. 持久化

不新增持久化数据。插件集合、版本、路由表和页面注册都只存在于各 Server 进程的内存中，Server 重启后按插件目录重建。插件
接口写入的核心数据与 RFC #2549 相同；插件自己的数据存放在插件专属存储中。

## 6. 接口变更

| 变更 | 内容 |
| --- | --- |
| 新增 | `/v1/plugins/{name}/api/scopes/{scope_id}/...`：插件声明的路由 |
| 新增 | `GET /v1/plugins/{name}/openapi.json` |
| 新增 | `/dashboard/plugins/{name}`、`/dashboard/plugins/{name}/ui/...`、`/dashboard/plugins/{name}/api/...`、`/dashboard/plugins/_client.js` |
| 新增 | `powercontext.plugins`：`PluginApi`、`Route`、`ApiRequest`、`BinaryResponse`、`ApiError`、`Caller`、`PluginPages` |
| 调整 | `SourcePlugin`、`ArtifactPlugin` 增加 `api` 和 `pages` 字段 |
| 调整 | `GET /v1/plugins`、`GET /v1/plugins/{name}` 的响应增加 `declaration_digest`、`state`、`activated_at`、路由列表和页面信息 |
| 新增 | 环境变量 `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS`（默认 30，0 关闭热加载）、`POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES`（默认 32 MiB） |

`openapi/powercontext.yaml` 只描述 `/v1/plugins/{name}/openapi.json` 和 `/v1/plugins` 的变化，不包含插件自己的路由。

## 7. 可观测性

| 字段 | 含义 |
| --- | --- |
| `plugin_name` | 插件名 |
| `plugin_event` | `loaded`、`updated`、`degraded`、`removed`、`check_failed` |
| `declaration_digest` | 本次生效的声明摘要 |
| `plugin_route` | 自定义接口的路由模板（例如 `GET /documents/{source_id}/original`），不含实际参数值 |
| `plugin_method` | 沿用 RFC #2549，新增 `api` |

- 每次加载、更新、降级和移除记录一条事件；检查失败只记录已知的失败分类，不记录异常消息。
- 指标标签可以使用 `plugin_name`、`plugin_route` 和响应状态类别（2xx、4xx、5xx），不包含 Scope ID、路径参数或查询参数。
- 日志不包含请求体、响应体和文件内容。新增字段按开发指南登记到允许列表，并在中英文可观测性文档中说明。

# 缺点

- **多实例存在不一致窗口**：各实例独立扫描，插件版本在一个扫描周期内可能不一致；调用方可能在不同实例上看到不同的结果。
- **扫描有持续开销**：每个 Server 进程定期列出插件目录的所有文件。插件目录很大（例如把依赖放进目录）时开销随之增加。
- **插件接口在主契约之外**：插件路由不进入 `openapi/powercontext.yaml`，没有生成的客户端，也不在 MCP 中出现；兼容性由插件
  自己保证。
- **更新失败会让插件暂时不可用**：检查失败时插件进入 `degraded`，同步调用和自定义接口返回 503，直到修复后的版本被扫描到。
  旧版本不会保留，因为磁盘上已经没有旧代码。
- **插件页面受限**：页面不能直接发起网络请求，只能通过宿主页面调用本插件接口；需要外部资源的页面要由插件接口代理。
- **处理函数是可信代码**：宿主进程隔离普通故障，不是沙箱。插件接口以调用者身份写入核心数据，插件代码中的缺陷可能以用户
  名义写入错误数据，这一点与 RFC #2549 的用户修改相同。

# 理由与备选

| 方案 | 取舍 |
| --- | --- |
| 文件系统监听（inotify 等） | 响应更快，但各平台行为不同，容器和网络文件系统上不可靠，还会收到写了一半的事件；定期扫描加稳定判断更简单 |
| 管理接口触发重新加载 | 只能作用于收到请求的实例，多实例时需要再做分发；定期扫描自然覆盖所有实例。是否增加"立即扫描本实例"的接口见未决问题 |
| 持久化插件集合与注册代次（旧 Artifact 插件框架） | 可以让各实例严格一致，但需要重新引入期望状态、管理阶段和代次屏蔽，这是 RFC #2549 刚删掉的机制 |
| 在 Server 进程内重新导入插件模块 | Python 无法可靠地替换已加载模块：插件的同级模块以顶层名字进入 `sys.modules`，旧对象仍被引用。替换子进程没有这个问题 |
| 用持久化代次屏蔽旧 Worker | Worker 与插件目录在同一台机器上，比对目录指纹即可，不需要数据库状态 |
| 更新失败时保留旧版本 | Worker 每次调用都从磁盘导入，旧代码已被替换，无法只为宿主进程保留旧版本；降级并明确返回 503 更可预测 |
| 插件接口在 Server 进程中执行 | 延迟最低，但插件阻塞或崩溃会影响整个 Server，也会重新把插件代码导入 Server 进程 |
| 插件接口每次请求派发 Worker | 复用现有机制，但每次都要启动新的 Python 进程，延迟以秒计 |
| 插件声明全局或 `server.admin` 路由 | 插件可以提供管理功能，但会绕开 Scope 授权边界；第一版只允许 Scope 内的路由 |
| 插件页面与 Dashboard 同源 | 实现最简单，但页面脚本可以带着用户的会话调用任何 Dashboard 和 Server 接口，等于冒充用户 |
| 插件页面使用独立端口或域名 | 也能隔离来源，但部署者要额外配置端口、证书和反向代理；沙箱 iframe 不需要额外配置 |
| 插件页面由插件代码在服务端渲染 | 页面能直接拿到数据，但每次页面请求都要进入宿主进程，并且要定义模板和转义规则；静态页面加接口调用更简单 |
| 自动把插件接口暴露为 MCP 工具 | 方便 Agent 使用，但需要定义工具描述、参数映射和预算约束，另行讨论 |

# 先例

- **Chrome 扩展**：扩展页面运行在扩展自己的来源中，通过声明的权限和消息传递与网页及浏览器交互，不能直接使用网页的会话。
  本提案的插件页面采用相同的隔离思路：不透明来源，加上只能调用本插件接口的消息转发。
- **VS Code Webview**：扩展的界面运行在 iframe 中，通过 `postMessage` 与扩展通信，并要求设置 CSP。插件页面的消息协议与之类似。
- **Dify 插件的 Endpoint 扩展**：插件可以声明 HTTP 端点，由 Dify 挂载并转发请求。本提案的自定义接口同样由宿主统一挂载，
  并额外要求 Scope 授权。
- **Gunicorn 等进程管理器的平滑重载**：先启动新的工作进程，再让旧进程处理完已有请求后退出。宿主进程的替换采用同样的顺序。
- **PowerContext 现有基础**：RFC #2549 的宿主进程、已激活插件记录、用户修改规则和插件专属存储；Supervisor 的 binding、
  租约 fence 和退避；Dashboard 的会话与服务端调用方式。

# 未决问题

| 问题 | 说明 |
| --- | --- |
| 立即扫描 | 是否提供 `POST /v1/plugins/rescan`（`server.admin`）让部署者立即触发本实例扫描；多实例时只作用于收到请求的实例 |
| 扫描间隔默认值 | 30 秒是否合适；是否需要在大插件目录下限制每次扫描的文件数 |
| 接口限流 | 是否为插件接口提供按路由或按调用者的限流，还是沿用 Server 现有的入口限制 |
| MCP 暴露 | 插件接口是否以及如何作为 MCP 工具提供给 Agent |
| Desktop 中的插件页面 | Desktop 应用是否嵌入插件页面，以及其 WebView 中的沙箱和 CSP 行为 |
| 浏览器行为验证 | CSP `sandbox` 指令下 `'self'` 对子资源的匹配，以及不透明来源请求的 `SameSite` 处理，需要在主流浏览器上验证后确定最终响应头 |
| 二进制请求的流式处理 | 第一版在 Server 进程中完整读入请求体再转给宿主进程；大文件是否需要流式传输 |

# 后续可能

- 插件接口作为 MCP 工具，供 Agent 直接调用。
- 插件页面在 Desktop 应用中展示。
- 插件之间通过声明的接口互相调用。
- 为插件接口提供生成的客户端或 SDK 辅助。
