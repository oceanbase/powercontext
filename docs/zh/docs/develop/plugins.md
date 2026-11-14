---
title: 编写插件
description: 用一个目录和 plugin.py 扩展 PowerContext：生成、展示和检索自己的 Artifact，定义 Source 类型，提供接口和 Dashboard 页面。
---

# 编写插件

插件是放在 Server 插件目录下的一个文件夹，入口是其中的 `plugin.py`。它可以：

| 能力 | 说明 |
| --- | --- |
| 生成自己的 Artifact | 声明一个 Artifact family，读取 Source，写出这个 family 的 Artifact；结果通过现有的 Artifact 接口读取、打标签 |
| 展示和检索 | 给出每个版本的默认展示；按需实现检索，让通用检索接口和 Prepared Context 能用上插件的数据 |
| 支持用户修改 | 实现新建、修改、合并、遗忘、恢复方法，让通用 Artifact 接口作用于插件的 family |
| 定义 Source 类型 | 声明插件自己的 Source 类型和 Projection，记录用户要求做的事，例如一次上传 |
| 提供接口 | 声明 HTTP 路由，挂在 `/v1/plugins/<插件名>/api/` 下，由 core 认证并转发给插件 |
| 提供 Dashboard 页面 | 随插件提供静态页面，在 Dashboard 中以沙箱 iframe 展示 |
| 使用专属存储和模型 | 在 core 分配的独立存储中保存自己的数据，调用 Server 配置的 embedding 和生成模型 |

插件代码在 Server 的 Python 环境中运行，被当作可信代码，权限与部署者相同。只加载你信任的插件。

## 启用插件目录

设置插件目录，然后重启 Server：

```bash
export POWERCONTEXT_SERVER_PLUGINS_DIR=/srv/powercontext/plugins
powercontext server run
```

- 目录下每个子目录是一个插件，子目录名就是插件名（小写字母开头，只含小写字母、数字和 `-`）。
- 插件依赖的第三方库需要装进 Server 的 Python 环境，例如 `uv pip install pypdf`。插件目录中的 `requirements.txt` 只是给
  部署者看的说明，Server 不会自动安装。
- 修改插件后需要重启 Server。Server 在加载时记录插件目录的内容指纹，运行中发现插件文件变了会拒绝执行，避免新旧代码混用。

用管理员凭据查看加载结果：

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:8000/v1/plugins
```

每个插件的 `status` 为 `loaded` 或 `failed`；失败时 `error` 给出原因，例如声明字段不合法、导入失败。

## 第一个插件：把文本整理成笔记

在插件目录下新建 `notes/plugin.py`：

```python
from powercontext.plugins import (
    ArtifactPlugin, ArtifactWriter, GenerateRequest, Inputs, Rendering, RenderRequest, SearchHit, SearchRequest,
    SearchResult,
)


class Notes:
    def __init__(self, ctx):
        self.ctx = ctx

    async def generate(self, request: GenerateRequest, writer: ArtifactWriter) -> None:
        async with writer.transaction() as tx:
            for source in request.sources:
                text = source.projection["content"]
                await tx.put(writer.stable_id(source.ref.source_id), content={"text": text}, source_refs=[source.ref])

    async def render_artifact(self, request: RenderRequest) -> Rendering:
        return Rendering(text=request.artifact.content["text"])

    async def search_artifacts(self, request: SearchRequest) -> SearchResult:
        query = request.search["query"]
        page = await self.ctx.reads(request.scope_id).list(limit=200)
        hits = [
            SearchHit(ref=item.ref, text=item.content["text"])
            for item in page.items
            if query in item.content["text"]
        ]
        return SearchResult(hits=hits[: request.search.get("limit", 10)])


async def setup(ctx):
    return Notes(ctx)


plugin = ArtifactPlugin(
    family="notes",
    inputs=Inputs(source_types=("content",), projection="powercontext.text-evidence@1"),
    on_sources=True,
    setup=setup,
)
```

重启 Server 后写入一条文本 Source，几秒后就能读到插件生成的 Artifact：

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/sources -d '{"content": "发布先进入华东区域"}'

curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/notes
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/notes/$ARTIFACT_ID/rendering
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/notes/search -d '{"query": "华东"}'
```

这个插件包含了插件的基本部分：

- **声明** `plugin = ArtifactPlugin(...)`：告诉 core 插件拥有哪个 family、消费哪些 Source、什么时候运行。
- **工厂** `setup(ctx)`：返回插件对象。从环境变量读取插件自己的配置、打开需要的资源，都放在这里。
- **生成** `generate(request, writer)`：收到一批新 Source，写出 Artifact。
- **展示** `render_artifact(request)`：给出一个版本的默认展示，必须实现。
- **检索** `search_artifacts(request)`：用插件自己的方式检索，可选。这个例子只做了子串匹配；正式的插件会用自己的全文或
  向量索引。

## 声明

| 字段 | 说明 |
| --- | --- |
| `family` | 插件拥有的 Artifact family，不能与内置 family 或其他插件重复 |
| `inputs` | `Inputs(source_types=..., projection=..., projections={...}, max_sources=50)`：消费哪些 Source 类型、每种类型以哪个 Projection 交付、一次最多交付多少条。`projection` 适用于所有类型，`projections` 按类型单独指定 |
| `on_sources` | 有新 Source 时运行 |
| `schedule` | `Interval(seconds=...)` 或 `Cron(expression=..., timezone=...)`，定时运行；`timezone` 不填时使用 Server 所在机器的时区。`schedule` 和 `on_sources` 都不填时插件没有后台生成，不需要实现 `generate`，Artifact 只由用户修改和插件接口写入 |
| `scopes` | 只服务这些 Scope；配置了 `schedule` 但不消费 Source 的插件必须填写 |
| `timeout_seconds`、`transaction_timeout_seconds` | 一次生成和其中写事务的时限 |
| `change_timeout_seconds` | 一次用户修改、展示或检索的时限 |
| `host_processes` | 每个 Server 进程中本插件宿主进程数的上限，默认 1；部署者可以覆盖 |
| `definitions` | 插件自己的 Source 定义，见"定义 Source 类型" |
| `api` | 插件的接口，见"提供接口" |
| `pages` | Dashboard 页面，见"Dashboard 页面" |
| `setup` | 工厂函数，必须是 async 函数 |

Server 在一个独立的子进程中导入 `plugin.py` 并检查声明，导入失败或声明不合法时插件为 `failed`，不影响 Server 和其他插件。
宿主进程调用 `setup` 之后检查必须实现的方法：缺少 `render_artifact`，或者声明了 `schedule`、`on_sources` 却缺少
`generate`，插件同样为 `failed`。

插件对象上的方法：

| 方法 | 必须实现 | 何时调用 |
| --- | --- | --- |
| `render_artifact` | 是 | 展示接口 |
| `generate` | 声明了 `schedule` 或 `on_sources` 时 | 定时，或有新 Source |
| `search_artifacts` | 否 | 通用检索接口；Prepared Context 中列出该 family |
| `create_artifact`、`update_artifact`、`merge_artifacts`、`forget_artifact`、`restore_artifact` | 否 | 对应的通用修改接口 |
| 路由的处理函数 | 声明了路由时 | 插件自己的接口 |

列出、读取版本和历史、治理状态和标签不调用插件方法，由 core 直接提供。

## 生成 Artifact

`generate` 在 Server 启动的独立 Worker 进程中运行，每个 Scope 一次调用：

- `request.scope_id` 是本次调用的 Scope；`request.sources` 是这次交付的 Source，每条带 `ref`（来源引用）和 `projection`
  （按声明的 Projection 转换后的内容）。
- 写入放在 `async with writer.transaction() as tx:` 里。块内的写操作在一个事务中提交，并与"这批 Source 已处理"的记录一起
  提交；生成失败或超时，这批 Source 会再次交付。
- 调用模型、解析文件等慢操作放在事务之外，事务里只做写入。
- `tx.put(artifact_id, content=..., source_refs=[...])` 写一个新版本，`source_refs` 记录出处。内容与当前版本相同时不产生新版本，
  所以重复交付不会写出重复的版本。`writer.stable_id(...)` 按输入生成稳定的 ID，便于同一条 Source 总是写到同一个 Artifact。
- 还可以用 `tx.set_lifecycle`、`tx.merge`、`tx.revert`、`tx.set_tags` 等方法，以及 `writer.get`、`writer.list`、
  `writer.by_source` 读取当前状态。
- core 在每个版本上记录写入它的插件名和插件代码的内容摘要，多个 Server 加载了不同版本的插件时可以据此追溯。

生成的 Artifact 与内置 Artifact 一样，可以通过 `/v1/scopes/{scope_id}/artifacts/{family}` 系列接口读取、打标签。

## 展示和检索

core 不理解插件 Artifact 的内容结构，也不为插件建立索引；展示和检索由插件自己完成。

**展示**：`render_artifact(request)` 收到一个确切版本（`request.artifact`），返回 `Rendering(format="markdown", text=...)`，
`format` 也可以是 `"text"`。调用方通过下面的接口获取，不带 `revision` 时取当前版本：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/v1/scopes/$SCOPE_ID/artifacts/notes/$ARTIFACT_ID/rendering?revision=2"
```

同一个版本的展示应该稳定，调用方可以按 family、ID、版本和插件版本缓存。

**检索**：`search_artifacts(request)` 收到通用检索接口的完整请求（`request.search`，包含 `query`、`limit`、`mode`、
`filters`、`fusion` 等调用方发送的全部字段；`include_scores` 由 core 处理，不转交），用插件自己的索引检索，返回 `SearchResult(hits=[SearchHit(ref=..., text=...,
score=...)])`：

- 不支持的字段请抛出 `InvalidContent`，接口返回 422，而不是静默忽略。
- core 只保留调用者有权读取、仍是当前版本且处于 active 状态的命中，保持插件给出的顺序和分数。
- `text` 是放进智能体上下文的片段，可以是内容的一部分，也可以是展示文本。
- 插件没有实现 `search_artifacts` 时，通用检索接口和 Prepared Context 对该 family 返回 422。

**Prepared Context**：`POST /v1/context/prepare` 的 `assembly.sections` 可以列出插件 family。core 用请求的 `query` 和该项的
`limit` 调用 `search_artifacts`，`request.max_bytes` 是本次请求的字节预算；命中按 section 顺序装进上下文，并引用确切版本。
该项可以带 `search`，其中的字段原样交给插件：

```json
{
  "scope_id": "team-a",
  "query": "发布区域",
  "assembly": {"sections": [{"family": "memory", "limit": 4}, {"family": "notes", "limit": 3, "search": {"mode": "hybrid"}}]}
}
```

## 支持用户修改

插件对象上实现下列方法后，通用 Artifact 接口就对插件的 family 生效；没有实现的方法，对应接口返回 422：

| 方法 | 对应接口 |
| --- | --- |
| `create_artifact(request, writer)` | `POST /v1/scopes/{scope_id}/artifacts`，请求体 `{"family": "<family>", "content": {...}}` |
| `update_artifact(request, writer)` | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}`，带 `If-Match` |
| `merge_artifacts(request, writer)` | `POST /v1/scopes/{scope_id}/artifacts/{family}/merges` |
| `forget_artifact(request, writer)` | `POST .../{artifact_id}/forget` |
| `restore_artifact(request, writer)` | `POST .../{artifact_id}/restorations` |

```python
from powercontext.plugins import InvalidContent


class Notes:
    async def update_artifact(self, request, writer) -> None:
        if not request.content.get("text"):
            raise InvalidContent("text is required", field="text")
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content, expected_revision=request.expected_revision)

    async def forget_artifact(self, request, writer) -> None:
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")
```

- core 先检查调用者的权限和版本，再调用方法；提交写入时按调用者给出的版本再校验一次，期间被其他写入改变则返回 409
  （修改内容时为 412），插件不必自己重复检查。方法只负责校验内容并完成约定的那一次写入。
- 接口返回本次写入提交的版本，即使之后已有其他写入。
- 抛出 `InvalidContent` 时接口返回 422，错误信息展示给调用者。
- 用户修改会以修改人的身份记录出处，与内置 Artifact 的修改相同。

## 定义 Source 类型

Source 记录"发生了什么、用户要求做什么"。插件可以定义自己的 Source 类型，例如"上传了一个文件"或"编辑了一段内容"，
并在生成时消费它们：

```python
from pydantic import BaseModel
from powercontext.sources import Source, SourceMaterialization


class TicketInput(BaseModel):            # 提交时的输入
    key: str
    title: str


class TicketSource(Source):              # 保存在 core 中的 Source
    title: str


class TicketProjection:                  # 交付给 generate 的内容
    name = "tickets.ticket"
    version = "1"
    source_class = TicketSource
    output_class = TicketInput

    def project(self, source, /):
        return TicketInput(key=source.name, title=source.title)


class TicketDefinition:
    name = "ticket"                      # Source 类型名
    version = "1"
    input_class = TicketInput
    source_class = TicketSource
    projections = (TicketProjection(),)

    async def resolve(self, value, /):
        return TicketSource(
            name=value.key, definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED, description="A ticket", title=value.title,
        )

    async def read(self, source, /):
        return TicketProjection().project(source)


plugin = ArtifactPlugin(
    family="tickets",
    inputs=Inputs(source_types=("ticket",), projection="tickets.ticket@1"),
    on_sources=True,
    definitions=(TicketDefinition(),),
    setup=setup,
)
```

- 插件加载时，core 登记这些 Source 类型。之后调用方可以用通用 Source 接口写入：
  `POST /v1/scopes/{scope_id}/sources`，请求体 `{"source_type": "ticket", "content": {"key": "OPS-7", "title": "..."}}`。
  插件校验输入并解析成 Source；输入不合法返回 422。
- 插件自己的接口中用 `ctx.sources.submit(scope_id, "ticket", TicketInput(...))` 提交。
- Source 的 `name` 是它在 Scope 中的身份，同一个 `name` 重复提交得到同一条 Source。
- 修改了 Source 或 Projection 的结构时，提高定义的 `version`，并在 `resolve` 中把 `definition_version` 设为新版本。core 按
  名称和版本登记，新旧版本并存；已经保存的旧 Source 仍按旧版本解读。
- 一个插件消费多种 Source 类型、各自需要不同的 Projection 时，用 `projections` 分别指定：

  ```python
  Inputs(source_types=("upload", "edit"), projections={"upload": "kb.upload@1", "edit": "kb.edit@1"})
  ```

  `generate` 中按 `source.ref.source_type` 区分。

## 提供接口

插件可以声明自己的 HTTP 路由，处理函数是插件对象上的 async 方法：

```python
from pydantic import BaseModel
from powercontext.plugins import ApiError, ApiRequest, PluginApi, Route


class NewTicket(BaseModel):
    scope_id: str
    key: str
    title: str


class Tickets:
    def __init__(self, ctx):
        self.ctx = ctx

    async def open_ticket(self, request: ApiRequest) -> dict:
        body: NewTicket = request.body
        await self.ctx.require("scope.contribute", body.scope_id)
        ref = await self.ctx.sources.submit(body.scope_id, "ticket", TicketInput(key=body.key, title=body.title))
        return {"source_id": ref.source_id}


plugin = ArtifactPlugin(
    ...,
    api=PluginApi(routes=(
        Route(method="POST", path="/tickets", handler="open_ticket", access="authenticated",
              body="json", request_model=NewTicket),
    )),
)
```

- 路由挂在 `/v1/plugins/<插件名>/api<path>`，例如 `POST /v1/plugins/tickets/api/tickets`。调用方使用与其他接口相同的凭据。
- `access` 决定 core 在调用前做的检查：
  - `authenticated`：只要求已登录；
  - `scope.read` / `scope.contribute`：要求调用者对 `scope_param` 指定的路径参数中的 Scope 具备该权限。
- 处理函数里用 `ctx.require(action, scope_id)` 检查其他权限，不具备时接口返回 403；`ctx.allowed(...)` 只返回真假。
- 读写核心数据：`ctx.reads(scope_id)` 按调用者权限读取；`ctx.writer(scope_id)` 和 `ctx.sources.submit(...)` 只能用于本次
  请求中已经确认 `scope.contribute` 的 Scope（路由声明了该权限，或处理函数先调用了 `ctx.require`）。修改已有 Artifact
  （包括标签、handle 和生命周期）还需要调用者对该 Artifact 有 `artifact.write` 权限，与通用接口相同；没有权限时接口返回 403。
- 接口中的写入不会自动记录"谁做了什么"。需要这类出处时，像上面的例子一样把操作作为 Source 提交，由 `generate` 写入新版本并
  引用它。
- 需要创建 Scope 时用 `ctx.scopes.create(title=..., summary=..., idempotency_key=...)`，要求调用者具备 `server.admin`。
- `request.body` 按 `body` 声明是校验后的 `request_model` 实例、`bytes` 或 `None`；`request.path_params`、`request.query`、
  `request.caller` 分别是路径参数、查询参数和调用者。
- 返回值：Pydantic 模型或 dict 返回 JSON；`BinaryResponse(content=..., content_type=..., filename=...)` 返回文件；
  `None` 返回 204。抛出 `ApiError(status, code, message)` 返回对应的 4xx 错误。
- 请求体上限：JSON 默认 1 MiB，二进制默认 32 MiB，可以用 `max_body_bytes` 为单个路由声明，但不会超过部署者设置的
  `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES`。超出时 core 返回 413，不调用插件。
- 每次调用受 `timeout_seconds`（默认 30 秒）约束，超时返回 504。

接口、展示、检索和用户修改方法在宿主进程中执行。每个插件有自己的一组宿主进程，不与其他插件共用：插件加载后启动第一个并
一直保留；所有进程都忙、且有请求排队时再启动一个，直到 `host_processes`；多出的进程空闲 5 分钟后停止。工厂 `setup(ctx)`
在每个宿主进程中执行一次，插件对象服务该进程收到的所有请求，所以不要假设所有请求都在同一个进程中。

- 一个宿主进程同时处理多个请求，都在同一个事件循环上。调用超过时限时，core 只取消这个请求（写事务随之回滚）并返回 504，
  同一进程中的其他请求不受影响。
- 阻塞事件循环会让取消无法生效，宿主进程的心跳也随之中断。心跳中断 5 秒后 core 终止该进程：超时的请求返回 504，进程中的
  其他请求返回 503，进程退避后重启。CPU 密集或同步阻塞的代码放进 `await asyncio.to_thread(...)`；需要几分钟的工作交给
  后台的 `generate`。
- Prepared Context 中的插件 section 在插件不可用（超时、宿主进程不可用或检索失败）时省略，回执中记录该 family 的检索
  失败，其他 section 照常返回。

## 专属存储和模型

```python
from sqlalchemy import text


async def setup(ctx):
    engine = ctx.storage.engine()          # 只指向本插件的存储
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE IF NOT EXISTS notes_index (id TEXT PRIMARY KEY, body TEXT)"))
    return Notes(ctx)
```

- `ctx.storage.engine()` 返回一个 SQLAlchemy 异步引擎，指向数据目录下的 `plugins/<插件名>.sqlite3`。表结构和迁移由插件自己
  管理，core 不读取、不备份这块存储。目前只支持 SQLite 部署；使用 OceanBase 或 seekdb 的 Server 上，访问 `ctx.storage`
  会报错。
- 这块存储不在 core 的事务里。大块内容（文件、向量）建议按内容摘要保存，再在 Artifact 中引用摘要：写入重试或事务回滚只会
  留下没人引用的记录，不会让 Artifact 指向错误的数据。
- 存储属于整个插件，不按 Scope 隔离。按 `scope_id` 划分数据，并在读取时确认数据属于本次请求的 Scope。
- `ctx.models` 是 Server 配置的模型，两种都没配置时为 `None`：
  - `await ctx.models.embed(["..."])` 返回向量，`ctx.models.embedding_dimension` 是维度；
  - `await ctx.models.generate([ChatMessage(role="user", content="...")], max_tokens=500)` 返回生成的文本；
  - 用 `embedding_available`、`generation_available` 判断是否配置；调用未配置的模型时抛出 `ModelUnavailable`。

## Dashboard 页面

声明了接口的插件可以提供页面：

```python
plugin = ArtifactPlugin(..., pages=PluginPages(directory="ui", entry="index.html", title={"zh": "工单", "en": "Tickets"}))
```

`ui/index.html` 中引用 core 提供的客户端脚本，通过它调用本插件的接口：

```html
<script src="/dashboard/plugins/_client.js"></script>
<script>
  const client = powercontextPluginClient();
  const { language, theme, scopeId } = await client.context();
  const response = await client.request("POST", "/tickets", { json: { scope_id: scopeId, key: "OPS-7", title: "磁盘满" } });
  if (response.ok) console.log(response.json);
</script>
```

- 页面出现在 Dashboard 左侧导航中，地址是 `/dashboard/plugins/<插件名>`。
- 页面运行在沙箱 iframe 中，拿不到用户的登录会话；请求由 Dashboard 以当前用户的身份转发，只能调用本插件声明的路由。
- `client.request(method, path, options)` 的 `options` 可以有 `json`、`body`（例如文件的 `ArrayBuffer`）、`contentType` 和
  `query`；返回 `status`、`ok`、`json`、`blob`、`filename`。
- 页面要做成单页应用：页面跳转后 Dashboard 会停止转发，提示用户重新打开。沙箱中不能使用 `alert`、`confirm` 等浏览器对话框。
- 只提供静态文件（HTML、JavaScript、CSS、图片、字体），单个文件不超过 5 MiB；脚本只能来自插件自己的目录和
  `/dashboard/plugins/_client.js`。

## 排错

| 现象 | 处理 |
| --- | --- |
| `/v1/plugins` 中插件为 `failed` | 查看 `error`：导入失败时检查依赖是否装进了 Server 环境；缺少必须实现的方法时补上方法 |
| 接口返回 503 | 宿主进程正在启动或重启，或者因事件循环被阻塞而被终止；查看 Server 日志中的插件错误 |
| 接口返回 504 `plugin_timeout` | 调用超过了时限；耗时的工作放进 `asyncio.to_thread` 或交给 `generate`，或者调大时限 |
| 接口返回 500 `plugin_error` | 处理函数抛出了未处理的异常；Server 日志记录了异常类型 |
| 修改插件后 Worker 不再执行 | 插件文件在运行中被修改，重启 Server |
| 生成的 Artifact 没有出现 | 用 `GET /v1/plugins/<插件名>/scopes` 查看各 Scope 是否还有待处理的 Source |

## 当前限制

- 插件更新需要重启 Server，还不支持热加载。
- 插件专属存储目前只支持 SQLite 部署。
- 插件依赖由部署者手动安装到 Server 环境。
- 插件代码与 Server 有相同的权限，宿主进程和 Worker 隔离的是故障，不是不可信代码。
