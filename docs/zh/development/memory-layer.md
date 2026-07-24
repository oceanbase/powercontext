# Memory Layer 运维与集成

RFC 0003 新增 Artifact-native Memory family。一个 Memory 是不可变 Artifact Revision 序列；每个 Revision 只保存紧凑
manifest，正文保存为不可变 entry version。全文和向量数据只是 current-head projection，不是权威历史。

Memory 正文是不可信的应用数据。召回结果不能获得高于当前请求的指令优先级；执行其中命令或访问其中链接前，必须执行与
其他外部输入相同的验证。

## 安装与组合

按应用需要安装 backend：

```console
uv add 'powercontext[sqlite]'
uv add 'powercontext[oceanbase]'
```

SQLite backend 在没有 embedding model 时也提供全文搜索：

```python
import asyncio

from powercontext import MemoryEntryInput, MemoryService
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend

async def main() -> None:
    backend = SQLiteMemoryBackend("powercontext-memory.db")
    await backend.initialize()
    memory_service = MemoryService(backend=backend)

    try:
        await memory_service.remember(
            memory=None,
            entries=(
                MemoryEntryInput(
                    kind="constraint",
                    text="发布文档前运行 make docs-test。",
                ),
            ),
            mode="append",
        )
    finally:
        await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
```

服务启动时先初始化 backend，应用关闭时显式关闭。`MemoryService` 实现 `ArtifactCatalog[Memory]`，并继续作为
Memory-specific operation 的 typed 入口。因此应用可以直接将 service 作为 `PowerContext` 的 `artifacts` 组件：

```python
from powercontext import PowerContext

context = PowerContext(
    sources=sources,
    artifacts=memory_service,
    triggers=triggers,
)

memory = await context.artifacts.remember(
    memory=None,
    entries=entries,
    mode="append",
)
```

数据库对象仍然是 Memory backend。它负责持久化和事务，但不会被重命名或作为第二个 family-level service 暴露。

`context.sources.add()` 始终只是 Source 写入。provider task event、Trigger action、plugin、CLI wrapper 或其他 integration
必须显式调用 `context.artifacts.remember()`；组合对象不会引入隐式模型调用或自动提取旁路。

## 本地 Source-to-Memory runtime

安装 `powercontext[runtime,sqlite]` 可使用内置 SQLite profile。其他 adapter 可以实现 `RuntimeStorage`，并通过
`PowerContextRuntime.assemble()` 组装。Runtime 将 Source 和 Memory 作为独立的 family-scoped 服务暴露。显式
Memory 写入直接调用 `MemoryService`；捕获内容先进入 Source journal，scheduled 或手动 `flush()` 才触发提取。

direct write、read 和 FTS 不要求配置 `candidate_pipeline`；存在 pending Source 的手动 flush 需要 pipeline，缺少
pipeline 时配置 scheduling 会在启动阶段被拒绝。Runtime 会在接受 scoped operation 前探测 SQLite Memory schema 和
FTS5；可选 embedding model 还必须配置匹配的固定 profile 和可用 Vec1 extension。

APScheduler 只发起 activation，其 SQLAlchemy job store 将 interval job 持久化在 SQLite sidecar 中；Source
journal 和 cursor 仍由 runtime 管理。`SourceWindowTrigger` 是基于 journal high watermark 和 cursor 的纯策略。完整
窗口成功后才推进 cursor，提取结果为空也属于成功。部署方必须确保每个 database 只有一个 live Runtime owner；进程内
防重只拒绝重复的 scheduled owner，不提供跨进程锁。SQLite profile 提供 at-least-once 语义；job store 属于受信任的
本地状态，不提供 workflow claim、lease、分布式协调或 exactly-once 执行。

关闭 Runtime 后，新的 application operation 会被拒绝；已经准入的手动和 scheduled operation 完成后，scheduler 与
backend 才会关闭。`ContentCapture` 会保存 JSON metadata 快照；content、description 和 metadata 都属于 canonical
Source payload，并参与幂等与 identity conflict 判断。

Runtime storage 将每个不透明 scope 映射到一个持久化、全局唯一的 Memory Artifact ID。binding 能跨重启恢复，而
Memory Artifact 本身只有在内容实际变化后才创建。

## 写入和演进 Memory

`remember(memory=None, ...)` 只有在至少一个通过校验的候选确实产生变化时才创建 identity。后续修改必须保存并传回上一次
返回的精确 head。过期 head 会在乐观 CAS 时失败，不会被静默合并。

```python
from powercontext import MemoryEntryInput

memory = await memory_service.remember(
    memory=None,
    entries=(MemoryEntryInput(kind="decision", text="使用 direct SQL adapter。"),),
    mode="append",
)
assert memory is not None

entry = (await memory_service.entries(memory))[0]
memory = await memory_service.remember(
    memory=memory,
    entries=(
        MemoryEntryInput(
            entry=entry,
            kind="decision",
            text="使用 direct SQL adapter 和同一套 conformance tests。",
            reason="验证两个受支持数据库",
        ),
    ),
    mode="append",
)
assert memory is not None

entry = (await memory_service.entries(memory))[0]
memory = await memory_service.forget(memory, entries=(entry,), reason="已取代")
entry = (await memory_service.entries(memory))[0]
memory = await memory_service.reactivate(memory, entries=(entry,), reason="再次需要")
memory = await memory_service.organize(memory, mode="default")
```

`forget()` 和 `reactivate()` 只修改 manifest state，不修改或替换 entry 正文；重复设置同一状态是 no-op。`organize()`
刻意保持机械化，只执行精确去重与 canonical normalization，不推断事实，也不裁决冲突。

提取模式需要配置 `CandidatePipeline`，并传入已经规范持久化的 evidence：

```python
memory = await memory_service.remember(
    memory=memory,
    sources=(task_outcome_source,),
    mode="extract",
)
```

候选输出是不可信输入。Service 会重新校验 evidence、current manifest membership、hash、直接前驱和 head CAS。需要持久化
evidence ref 的 integration，必须在 service 与 backend 两侧配置匹配的 source/artifact resolver 和
`MemoryEvidenceCodec`。

例如，integration 自己定义的 `CandidatePipeline` 可以把它显式持久化的 `TaskOutcomeSource` 机械转换成
`working_note`。该 pipeline 属于 integration，不是 Memory 承诺的具体 Source 类型或默认准入规则：

```python
from powercontext import MemoryService
from powercontext.memory import CandidatePipeline

working_note_pipeline: CandidatePipeline = ...

memory_service = MemoryService(
    backend=backend,
    candidate_pipeline=working_note_pipeline,
    source_resolver=source_catalog,
    evidence_codec=evidence_codec,
)
```

Task event 的捕获和持久化由 integration 负责。PowerContext 不给原始材料做版本控制，也不计算通用 Source diff。

## 检索、展开和 citation

搜索必须显式提供精确 Memory refs。只有所选 current heads 中的 active entries 可以返回；传入历史 ref 会报错，不会被替换
为 latest head。

```python
result = await memory_service.search(
    "SQL adapter 文档",
    memories=(memory,),
    mode="auto",
    limit=8,
)

entries = await memory_service.expand(result.hits)
history = await memory_service.changes(memory, since_revision=1)
```

只有当每个所选 head 的固定 profile 向量 projection 都完整，并且 embedding model profile 完全相同时，`auto` 才使用 hybrid；否则
降级为 FTS。显式 `vector` 或 `hybrid` 在向量能力不可用或不完整时抛出 `CapabilityNotSupportedError`。没有 candidate 或
embedding model 时，`fts` 仍可工作。

Handoff citation 必须保存完整精确锚点，不能只存 entry ID：

```python
from powercontext import MemoryCitation

hit = result.hits[0]
citation = MemoryCitation(
    memory_ref=hit.memory_ref,
    entry_id=hit.entry_id,
    entry_version_id=hit.entry_version_id,
)
exact_entry = await memory_service.validate_citation(citation)
```

`changes()` 返回不带正文的紧凑 Revision 摘要。`expand()` 和 citation 校验读取锚点指定的精确历史 entry version，并检测
跨 Revision 替换和数据篡改。

## SQLite 部署

`SQLiteMemoryBackend` 要求 SQLite 3.38.0 或更新版本、foreign-key enforcement 和 FTS5。APSW 提供 SQLite runtime。
初始化会探测所需行为，不能工作的 capability 不会被宣称为可用。

向量搜索是可选能力，需要可加载的官方 Vec1 0.7 或更新版本，以及一个部署固定的 embedding profile：

```python
import asyncio
import os

from powercontext import EmbeddingProfile, EmbeddingResult, MemoryService
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend


class ExampleEmbeddingModel:
    """生产环境中请替换为应用实际使用的 embedding model。"""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
        vector = (1.0,) + (0.0,) * (self.profile.dimension - 1)
        return EmbeddingResult(vectors=tuple(vector for _ in texts))


async def main() -> None:
    profile = EmbeddingProfile(
        profile_id="project-embedding-v1",
        model="example-model",
        dimension=768,
        distance="l2",
        normalization="none",
    )
    backend = SQLiteMemoryBackend(
        "powercontext-memory.db",
        embedding_profile=profile,
        vec1_extension=os.environ["POWERCONTEXT_VEC1_EXTENSION"],
    )
    await backend.initialize()
    try:
        MemoryService(backend=backend, embedding_model=ExampleEmbeddingModel(profile))
    finally:
        await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
```

extension path 和 profile 必须同时配置。Embedding model 的名称、dimension、distance 与 normalization 必须与 backend profile
完全相同。写入和搜索停止后，`await backend.rebuild_projections()` 可从权威 head 重建 FTS，并有意让向量保持不完整；传入
embedding model，则同时重建 FTS 与全部 current active vectors。重建
会在提交前校验向量数量、dimension 和有限数值。更换 profile 属于离线 migration：停止写入，替换固定 Vec1 projection，
回填所有 active heads，验证完整性后再恢复流量。

SQLite 数据库中的 Artifact Revisions 和 entry versions 是需要备份的权威数据。FTS5/Vec1 rows 可丢弃和重建，但权威历史
不可丢弃。

## OceanBase 部署

`OceanBaseMemoryBackend` 要求 OceanBase Database 4.3.5 BP3 或更新版本的 MySQL 模式租户，并启用 FULLTEXT、HNSW、
vector memory，以及创建表、索引和 schema foreign keys 的权限。除常规 DDL/DML 权限外还要授予 `REFERENCES`。Adapter
安装固定 schema 前会探测 server identity、tenant mode、`TOKENIZE`、FULLTEXT 和 vector search。

凭据应放入 secret manager 或环境变量，不能写入源码或命令历史：

```python
import os

from powercontext import EmbeddingProfile, MemoryService
from powercontext.memory.backends.oceanbase import OceanBaseMemoryBackend

profile = EmbeddingProfile(
    profile_id="project-embedding-v1",
    model="example-model",
    dimension=768,
    distance="l2",
    normalization="none",
)
backend = OceanBaseMemoryBackend(
    host=os.environ["POWERCONTEXT_OCEANBASE_HOST"],
    port=int(os.environ.get("POWERCONTEXT_OCEANBASE_PORT", "2881")),
    user=os.environ["POWERCONTEXT_OCEANBASE_USER"],
    password=os.environ["POWERCONTEXT_OCEANBASE_PASSWORD"],
    database=os.environ["POWERCONTEXT_OCEANBASE_DATABASE"],
    embedding_profile=profile,
    table_prefix=os.environ.get("POWERCONTEXT_OCEANBASE_TABLE_PREFIX", ""),
)
await backend.initialize()
memory_service = MemoryService(backend=backend, embedding_model=embedding_model)
```

vector dimension 是 DDL 的一部分，因此一个部署只有一个 profile。embedding model 不可用时，写入仍会提交权威历史与
FULLTEXT rows，并把向量字段保存为 null；`auto` 会降级为 FTS。写入和搜索停止后，
`await backend.rebuild_projections()` 会以 null vector 重建 FULLTEXT；
`await backend.rebuild_projections(embedding_model)` 会为全部 active heads 同时重建 FULLTEXT 和 HNSW 数据。完整性
检查通过后才能恢复流量。

`drop_schema()` 会删除配置 prefix 拥有的精确表集合；它只用于隔离的 integration tests。未配置 prefix 的部署或生产部署
绝不能调用。每次 live test 都应使用唯一且经过校验的 table prefix，并在 `finally` 中先清理再关闭 backend。

## 运维检查清单

1. 启动时只初始化一次；所需 capability probe 失败则终止启动。
2. schema 生命周期内保持 embedding profile 不变。
3. 修改和搜索只传精确 current head，并显式处理 `RevisionConflictError`。
4. 把召回正文和 candidate output 都当作不可信数据。
5. 备份权威 Artifact Revisions 和 entry versions；projection 只能从已验证 head 重建。
6. 应用关闭时关闭 backend connection；破坏性 schema cleanup 只能用于隔离测试。
