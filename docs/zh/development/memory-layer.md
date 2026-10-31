# 使用 Builtin Memory layer

Builtin Memory 把每条可长期保留的事实、偏好或决策保存为一个独立的 `atomic-memory` Artifact。每条记忆都有自己的
identity、revision、状态、tag 和访问关系。`builtin` extra 包含完整 runtime 和两种受支持的 database integration。
远程应用应采用[远程访问文档](remote-access-implementation.md)说明的 Server API。

## 选择 database

安装内置实现：

```bash
uv add "powercontext[builtin]"
```

仅安装此 extra 即可在进程内初始化 Runtime、写入和读取数据。使用 Server 的可选 Casbin 鉴权 provider 时，
请安装 `server` extra。

SQLite 是默认选择。`open_builtin_runtime()` 持有所选 database profile，并为两种 database 返回同一个
`BuiltinRuntime` interface：

```python
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime


async def save_note() -> None:
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
    )
    async with open_builtin_runtime(config) as runtime:
        assert runtime.atomic_memory is not None
        result = await runtime.atomic_memory.for_scope("project-alpha").create(
            (AtomicMemoryContent(kind="decision", text="Use one composition root for the process."),)
        )
        assert result.primary.ref.revision == 1
```

scope ID 在数据库中选择相互隔离的 Source journal、记忆集合和 Trigger cursor。

## 写入、提取和变更记忆

`ScopedAtomicMemoryApplication.create()` 以全有或全无的方式批量写入显式记忆，每个输入对应返回一条记录。基于 Source
的提取走另一条路径：先 capture Source，再调用 `flush()`，由已配置的提取 pipeline 处理待处理的 Source window。
`cursor()` 返回已处理到的 Source 位置。
使用内置提取与 reconcile Prompt 时，没有相关记忆、也没有同窗口先前候选的候选按提取结果保留，不发起 reconcile
请求；使用自定义 Prompt 或注入的 pipeline 时，每个候选都会经过 reconcile。是否保留一条事实由提取决定。内置
reconciler 的无目标 `noop` 只在与相关项比较时丢弃候选，因此这类候选不会经过这第二次判断。

在 SQLite、seekdb 和 OceanBase 上，`flush()` 使用相同的检索与比较规则，让新候选与相关历史记忆比较。
Source 和 Prompt 进入处理窗口时鉴权，召回的记忆进入比较集合时鉴权。窗口处理期间权限发生变化会阻止发布；
已准入窗口的输入仍可完成当前比较，重试必须重新鉴权。游标推进前也会复核实际依赖的 Source 和 Artifact，
避免把已变化的依据当作当前事实提交。

生成正文必须保留明确的时间依据。对于包含 `statement`、可选 `subject`，以及 ISO 格式的 `effective_at`、
`event_at` 或 `recorded_at` 的单陈述结构化 Source，提炼会检查所选事实是否保留生效或事件时间；两者都未提供时，
则保留 Source 自身的记录时间。原生 JSON 文本载体也接受相同检查。任意嵌套元数据或含其他事实字段的对象不能据此
建立单陈述的时间关联。调和只检查明确选入 `evidence_ids` 的依据：保留这些 Source 时间限定，以及所选 Artifact
事实中的 ISO 日期和时间字面值。检查不会把每个日期都解释为生效时间，也不选取最大日期。完整的新更正可以只引用
新 Source；被消费的前驱仍保留在精确 Artifact 来源链中，不必把全部历史日期复制进每次修订的正文。

缺失时间限定的输出会在配置的请求预算内反馈给模型纠正；若仍失败，则既不发布，也不推进游标。等价的 ISO 时刻可被
接受，UTC 零点可使用对应的 ISO 日期，其他时间精度必须保留。精度高于微秒或超出受支持 UTC 年份范围的值，必须
保留原始字面表示。检查只使用当前比较输入，不展开历史 Source 链，也不静默补写已有无日期记忆。时间含义、其他
日期格式以及哪些事实仍然适用，仍需模型判断。

发布成功后，新记忆及其直接 Source 来源链可以一起读回。Artifact ID 冲突或依据变化时，窗口不会
留下部分发布结果，也不会推进游标。多条创建的返回值包含各条记忆的当前正文、状态和来源链；
读取每条记忆仍须分别获得授权。

自定义 Atomic Memory 和 Topic Memory 索引必须支持一次发布完整的已准备投影集合。缺少此能力时，系统会在发布写入前拒绝操作。

内置提炼和调和指令按每条事实所依赖的 Source 保留语言：中文事实保留中文，英文事实保留英文，同批处理不会统一改变
所有事实的语言。中英混合事实保留自然语言叙述及精确的标识符、名称和值。新输入仅换一种语言表达相同事实时，不因此
修订未改变的已发布事实。这是 Generation 的系统策略，不会重写已有记忆，也不提供跨语言语义检索。Source 内容仍是
不可信证据，不能作为修改输出策略的配置。

每条记忆独立变更。`forget()` 让一条记忆失效，`merge()` 用一条新记忆替换多条旧记忆。`forget()` 要求传入预期的内容
revision 和状态版本，`merge()` 要求传入调用方读到的精确记录，调用方持有的数据过期时操作会失败，不会覆盖并发修改。
`restore()` 使用 `preview_restoration()` 返回的 token 执行恢复：

```python
memory = runtime.atomic_memory.for_scope("project-alpha")
current = (await memory.list()).items[0]
await memory.forget(
    current.ref.artifact_id,
    expected_revision=current.ref.revision,
    expected_state_version=current.state.state_version,
)
```

`list()` 按状态分页列出记忆，`get()` 读取一条记忆，也可以指定精确 revision。

## 检索

SQLite 和 OceanBase 都会初始化全文索引，因此不配置 embedding model 也能检索：

```python
result = await runtime.atomic_memory.for_scope("project-alpha").search("composition root", mode="text")
```

每个 hit 都标明参与排序的精确记忆 revision。`mode="auto"` 会选择当前可用的最强模式，并可在 query embedding
暂时不可用时回退到文本检索。显式请求 `vector` 或 `hybrid` 时，如果 profile 没有提供相应能力，操作会失败。

## 旧 Memory HTTP 操作

Server 保留五个旧入口，每个都转换为 Atomic Memory 操作：

| 操作 | 行为 |
| --- | --- |
| `POST /v1/memory/remember` | 创建一条记忆；带 `expected_revision` 的请求会被拒绝。 |
| `POST /v1/memory/search` | 检索有效记忆；`fts` 映射为文本检索。 |
| `POST /v1/memory/entries/list` | 列出记忆；`include_inactive` 会加入已遗忘、已合并和已退役的记忆。 |
| `POST /v1/memory/entries/get` | 按旧 `target` 的集合 ID 和 entry ID 读取迁移后的记忆。 |
| `POST /v1/memory/flush` | 处理下一个 Source window。 |

集合 revision、citation、capacity、changes、revise、retire、entry tag 以及 `memory` 访问目标在 Atomic Memory 中
没有对应语义，会在执行任何操作前返回 `422 legacy_memory_operation_unsupported`。

## 启用 SQLite 向量检索

提供 embedding model 后，SQLite 会启用向量检索。`powercontext[builtin]` 已捆绑 `sqlite-vec`，无需配置 extension
路径或单独安装 native library：

```python
config = BuiltinConfig(
    database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
)
async with open_builtin_runtime(
    config,
    embedding_model=embedding_model,
) as runtime:
    ...
```

SQLite profile 会组合 FTS5 和 sqlite-vec strategy，并报告 `text`、`vector` 和 `hybrid` 检索模式。持久化 projection
与 query vector 必须使用同一个 `EmbeddingProfile`，包括 model name、dimension、distance 和 normalization。
更换 profile 后，应先重建派生检索索引，再恢复 vector search。Artifact revision 和状态始终是事实来源。

## 使用 OceanBase 持久化

使用 `OceanBaseConfig` 即可选择 OceanBase，不需要修改 Server 或 Runtime 代码：

```python
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime

config = OceanBaseConfig(
    url=SecretStr(
        "mysql+aoceanbase://user:password@127.0.0.1:2881/powercontext?charset=utf8mb4"
    )
)

async with open_builtin_runtime(
    BuiltinConfig(database=config),
    embedding_model=embedding_model,
) as runtime:
    memory = runtime.atomic_memory.for_scope("project-alpha")
```

OceanBase profile 与 SQLite 使用相同的 index 组合方式。全文 strategy 始终可用；提供 embedding model 后，会增加
`VECTOR` projection 和 HNSW strategy，并启用 `vector` 与 `hybrid` mode。SQLite FTS5 与 OceanBase FULLTEXT
服务于同一组 Runtime 和 Server search 调用，sqlite-vec 与 HNSW 也通过同一接口提供向量检索。

## 运行检查

对外提供服务前，应确认：

- 所选 profile 能够成功打开并完成初始化；
- 每个 tenant 或 project 映射到预期的 scope ID；
- 定时 extraction 已经配置提取 pipeline；
- SQLite vector search 配置了匹配的 embedding model；
- OceanBase vector search 配置了匹配的 embedding model；
- capability response 与实际初始化的 index 一致；
- database 和 scheduler 资源会随进程生命周期关闭。
