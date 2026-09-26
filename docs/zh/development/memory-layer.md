# 使用 Builtin Memory layer

Builtin Memory family 将可复用 entry 保存为不可变 Artifact revision。`builtin` extra 包含完整 runtime 和两种受支持的
database integration。远程应用应采用[远程访问文档](remote-access-implementation.md)说明的 Server API。

## 选择 database

安装内置实现：

```bash
uv add "powercontext[builtin]"
```

SQLite 是默认选择。`open_builtin_runtime()` 持有所选 database profile，并为两种 database 返回同一个
`BuiltinRuntime` interface：

```python
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    open_builtin_runtime,
)


async def save_note() -> None:
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db")
    )
    async with open_builtin_runtime(config) as runtime:
        result = await runtime.memory.for_scope("project-alpha").remember(
            RememberMemoryRequest(
                entries=(
                    MemoryEntryInput(
                        kind="decision",
                        text="Use one composition root for the process.",
                    ),
                )
            )
        )
        assert result.memory_ref.revision == 1
```

scope ID 在数据库中选择相互隔离的 Source journal、Memory lifecycle 和 Trigger cursor。

## 写入和演进 entry

`ScopedMemoryApplication.remember()` 接受显式的 `MemoryEntryInput`。基于 Source 的 extraction 使用另一条路径：
先 capture Source，再通过已经配置 candidate pipeline 的 Runtime flush 待处理 Source window。

result 包含新的不可变 Memory reference 和发生变化的 entry。后续 mutation 直接使用它的 citation：

```python
from powercontext.builtin.runtime import ReviseMemoryEntryRequest

memory = runtime.memory.for_scope("project-alpha")
entries = await memory.list()
current = entries.entries[0]
revised = await memory.revise(
    ReviseMemoryEntryRequest(
        citation=current.citation,
        kind=current.entry.kind,
        text="Use PowerContext as the only composition root.",
        reason="Clarify ownership.",
    )
)
```

`retire()` 将 entry 标记为 inactive，但不删除不可变 content。`changes()` 返回紧凑的 revision change。
expected revision 和 citation 保留 optimistic concurrency，调用方无需重新构造 reference。

## 容量与墓碑压缩

`await runtime.memory.for_scope(scope_id).capacity()` 返回当前版本的活跃条目数、清单条目总数、精确的规范化内容
字节数、可压缩墓碑数、预算和超限维度。直接调用 `await service.capacity(memory)` 则测量传入的精确版本。
远程调用使用 `POST /v1/memory/capacity`，请求体为 `{"scope_id": "project-alpha"}`；Python 客户端提供
`PowerContextClient.get_memory_capacity(GetMemoryCapacityRequest(scope_id="project-alpha"))`。
Scope 尚无 Memory 时返回 404，查询不会创建 Memory。

`RuntimeConfig` 提供以下部署级默认值：

| 配置项 | 默认值 |
| --- | --- |
| `memory_max_active_entries` | 5,000 |
| `memory_max_manifest_entries` | 10,000 |
| `memory_max_manifest_bytes` | 4,194,304 |
| `memory_compaction_enabled` | `False` |
| `memory_compaction_min_tombstone_revisions` | 10 |
| `memory_max_history_revisions` | 100 |

容量默认值约束每个版本的增长，不保证追加延迟，也不限制数据库总大小；保留的历史清单仍会持续累积。
部署时应结合对应后端的代表性测量调整预算。

活跃条目上限不得大于清单条目上限。显式写入、提取和通用 Artifact 管理共用预算。只有某维度既超过上限、又比
基础版本更大时才拒绝写入；错误维度按字节数、清单条目数、活跃条目数的固定顺序选择。HTTP 返回
`409 memory_capacity_exceeded`，详情包含 `dimension`、`limit` 和 `observed`，拒绝后不持久化内容。
`manifest_bytes` 计入完整规范化版本内容，包括变更记录及其原因。

超限时仍可执行 `forget()` 和 `organize()`；`reactivate()` 仅检查活跃条目数增长。压缩从当前清单移除达到保留
年龄且未绑定标签的非活跃条目。通过 `RuntimeConfig` 显式启用，或使用 `MemoryCompactionPolicy(enabled=True)`
构造 `MemoryService`，执行前先预览：

```python
preview = await service.compact(memory, dry_run=True, limit=100)
result = await service.compact(memory, limit=100)
memory = result.memory
```

压缩关闭时仍可预览，预览不写入版本。年龄按已推进的版本数计算：默认保留 10 个版本时，在版本 2 停用的条目
从版本 12 起可压缩。重新激活并再次停用会重置保留窗口。资格检查只读取这一近期窗口。无变化的维护操作不会
推进版本；如果所有墓碑都过新，可显式配置 `memory_compaction_min_tombstone_revisions=0`，或使用
`MemoryCompactionPolicy(enabled=True, min_tombstone_revisions=0)`，先预览再立即压缩。零年龄只跳过保留窗口，
活跃条目和带标签墓碑仍受保护。除非需要立即恢复容量，否则建议保留默认窗口，因为压缩后无法重新激活条目。
标签会保护非活跃条目；压缩期间新增标签会使整个事务回滚并抛出
`CapabilityNotSupportedError("compaction-tag-conflict")`，调用方可重新预览。

压缩保留所有条目正文、历史版本和精确引用。被移除的条目无法重新激活，也不会出现在当前清单中。
每次移除都记录新增的 `compact` 变更类型；启用前应更新穷举变更类型的消费者。压缩仅提供进程内接口。
`reclaimed_bytes` 是完整规范化内容的有符号字节差；审计记录或较长原因可能抵消小规模清单缩减，因此该值可能
为负。后续版本不再携带本次压缩的变更记录。

`MemoryService.revisions()` 在历史超过 `memory_max_history_revisions` 时，在展开历史前抛出
`CapabilityNotSupportedError("history-window")`，不会静默截断结果；历史内容和精确版本读取仍然保留。
在接近 4 MiB 字节预算时，默认 100 个版本已可能包含约 400 MiB 规范内容，尚未计入对象开销。这是读取展开次数
上限，不是内存硬上限；调低预算或执行补救操作后，版本也可能超过字节预算。只有调用方能承担完整快照开销时
才应提高历史读取上限。这一上限不为 `entries()` 或 `changes()` 提供分页。

## 检索、展开与引用

SQLite 和 OceanBase 都会初始化全文索引，因此不配置 embedding model 也可以检索：

```python
from powercontext.builtin.runtime import SearchMemoryRequest

result = await runtime.memory.for_scope("project-alpha").search(
    SearchMemoryRequest(query="composition root", mode="fts")
)
```

每个 hit 都包含参与排序的精确 Memory revision、entry identity 和 entry version。Runtime 的 list 和 exact-read
operation 返回相同的 citation 字段。

`mode="auto"` 会选择当前可用的最强模式，并可在 query embedding 暂时不可用时回退到 FTS。显式请求 `vector`
或 `hybrid` 时，如果 profile 没有提供相应能力，操作会失败。

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

SQLite profile 会组合 FTS5 和 sqlite-vec strategy，并通过 Memory capabilities 报告 `fts`、`vector` 和 `hybrid`。持久化
projection 与 query vector 必须使用同一个 `EmbeddingProfile`，包括 model name、dimension、distance 和
normalization。更换 profile 后，应先重建 projection，再恢复 vector search。

调用 `MemoryService.rebuild_projections()` 可以从权威 Memory revision 重建派生检索数据。revision 和 entry 表
始终是事实来源。

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
    memory = runtime.memory.for_scope("project-alpha")
```

OceanBase profile 与 SQLite 使用相同的 index 组合方式。全文 strategy 始终可用；提供 embedding model 后，会增加
`VECTOR` projection 和 HNSW strategy，并启用 `vector` 与 `hybrid` mode。SQLite FTS5 与 OceanBase FULLTEXT
服务于同一组 Runtime 和 Server search 调用，sqlite-vec 与 HNSW 也通过同一接口提供向量检索。

## 运行检查

对外提供服务前，应确认：

- 所选 profile 能够成功打开并完成初始化；
- 每个 tenant 或 project 映射到预期的 scope ID；
- 定时 extraction 已经配置 candidate pipeline；
- SQLite vector search 配置了匹配的 embedding model；
- OceanBase vector search 配置了匹配的 embedding model；
- capability response 与实际初始化的 index 一致；
- database 和 scheduler 资源会随进程生命周期关闭。
