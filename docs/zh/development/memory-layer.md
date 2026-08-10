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

## 启用 SQLite Vec1

只有同时提供 0.7 或更高版本的 Vec1 loadable extension 和 embedding model，SQLite 才会启用向量检索。
PowerContext 不负责安装或构建这个 native extension；请提供适用于目标操作系统和架构的 library：

```python
from pathlib import Path

config = BuiltinConfig(
    database=SQLiteConfig(
        url="sqlite+aiosqlite:///powercontext.db",
        vec1_extension=Path("/opt/sqlite-extensions/vec1"),
    )
)
async with open_builtin_runtime(
    config,
    embedding_model=embedding_model,
) as runtime:
    ...
```

SQLite profile 会组合 FTS5 和 Vec1 strategy，并通过 Memory capabilities 报告 `fts`、`vector` 和 `hybrid`。持久化
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
服务于同一组 Runtime 和 Server search 调用，Vec1 与 HNSW 也通过同一接口提供向量检索。

## 运行检查

对外提供服务前，应确认：

- 所选 profile 能够成功打开并完成初始化；
- 每个 tenant 或 project 映射到预期的 scope ID；
- 定时 extraction 已经配置 candidate pipeline；
- Vec1 配置包含匹配的 embedding model；
- OceanBase vector search 配置了匹配的 embedding model；
- capability response 与实际初始化的 index 一致；
- database 和 scheduler 资源会随进程生命周期关闭。
