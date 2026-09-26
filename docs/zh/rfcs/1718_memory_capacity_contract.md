- 提案名称：`memory_capacity_contract`
- 起始日期：2026-09-23
- RFC PR：[oceanbase/powercontext#1718](https://github.com/oceanbase/powercontext/pull/1718)
- 跟踪 Issue：[oceanbase/powercontext#1718](https://github.com/oceanbase/powercontext/issues/1718)
- 相关 RFC：[RFC 0014](/zh/rfcs/0014_memory_layer_design) 和 [RFC 1652](/zh/rfcs/1652_memory_quality_and_lifecycle)
- 相关工作：[#1321](https://github.com/oceanbase/powercontext/issues/1321)、
  [#1709](https://github.com/oceanbase/powercontext/pull/1709)、
  [#1656](https://github.com/oceanbase/powercontext/issues/1656)、
  [#1657](https://github.com/oceanbase/powercontext/issues/1657) 和
  [#1425](https://github.com/oceanbase/powercontext/issues/1425)

# 摘要

本 RFC 定义长期存续的 Memory Artifact 的容量契约：度量什么、上限在哪里、触及上限时发生什么，以及运维如何重新
获得余量。

Memory 获得三个可度量维度（活跃 entry 数、manifest entry 数、manifest 字节数）、一份覆盖这些维度的可配置预算，
以及一个在写入将越过预算时的确定性拒绝。恢复途径是显式的：`forget()` 把 entry 移出活跃检索面，新增的可选
`compact()` 操作从**当前** manifest 中丢弃符合条件的 inactive 墓碑，同时不删除任何 entry 正文、任何历史
Revision 和任何精确引用。

自动 Memory 拆分与路由**不在**本 RFC 范围内。它们需要 RFC 0014 列为后续可能性的 routing manifest，以及 RFC 0014
明确列为非目标的第二层身份。本 RFC 转而定义触及上限时稳定、可观测的失败行为，并说明后续路由设计可以接入的接缝。

# 动机

PR #1709 消除了 #1321 报告的投影写放大：一次 append 现在只重写它实际改变的那个 entry 的行。该 PR 刻意没有定义容量
边界，#1321 在这个缺口仍然存在的情况下被关闭。

遗留的问题是 Memory 没有上限。每个 Revision 都保存完整的 `flat-v1` manifest，因此追加第 *N* 条 entry 会写入包含
*N* 项的 manifest；inactive entry 作为墓碑永久留在该 manifest 中；并且公开 API 无法告诉调用方一个 Memory 距离实际
限制有多近，也不会在越过限制时拒绝。RFC 0014 把前半部分记录为缺点：

> `flat-v1` 会复制目录并无限累积 inactive 墓碑，因此 manifest 成本随 entry 数量线性增长。

并把后半部分推迟给实测工作：

> Memory 拆分、inactive 墓碑压缩和公开 routing manifest 的阈值将由实测结果决定。

RFC 1652 随后提供了**逻辑**生命周期（重要性、保留档位、可恢复的自动停用），并明确把物理部分排除在外，指向后续
设计：

> 物理墓碑/manifest 压缩、法律保留、外部擦除和跨 Artifact 清理不在范围内。

本 RFC 就是那份设计，并收窄到一个问题：单个 Memory Artifact 的容量边界。它正是 RFC 1652 所预期的"仅当盘点与评测
证明存在存储问题时才提出独立 RFC"这一步——#1321 就是该证明。

我们期望的结果：运维可以通过公开 API 读取一个 Memory 的容量；失控的写入方会以具体、可操作的错误失败，而不是静默
退化；并且运维可以通过受支持的生命周期操作和策略配置恢复容量，同时不丢失任何引用。

## 非目标

- **不做自动拆分或路由。** Memory 身份就是 Artifact ID（RFC 0014），一个 Scope 恰好解析到一个 Memory Artifact ID。
  路由到第二个 Memory 需要 RFC 0014 排除的 routing manifest 和映射身份。本 RFC 规定拒绝行为，以及后续路由设计需要
  满足的接口。
- **不删除 Revision 或 entry 正文。** 旧 Revision 和旧 entry 版本既不被重写**也不被删除**。物理擦除、法律保留和
  跨 Artifact 清理属于 #1425。
- **不改变 `flat-v1` 的存储增长。** 每个 Revision 复制 manifest 是该格式固有的特性。本 RFC 约束并观测这种增长；
  增量 manifest 格式在后续可能性中给出草图。
- **不引入游标分页。** #1656 负责有界 entry 列举，#1657 负责历史分页。本 RFC 约束这些 issue 依赖的**内部**读放大，
  自身不定义任何游标。
- **不做质量或重要性打分。** 哪条 entry 值得留下是 RFC 1652 的问题。本 RFC 只做计数。

# 使用说明

## Memory 现在具有可读的容量

每个 Memory 都会报告自己的位置：

```python
capacity = await memory_service.capacity(memory)

capacity.active_entry_count      # 412  活跃检索面上的 entry 数
capacity.manifest_entry_count    # 468  当前 manifest 中 active + inactive 的项数
capacity.manifest_bytes          # 104_568  本 Revision 提交的规范字节数
capacity.compactable_entry_count # 31   当前符合 compact() 条件的墓碑数
capacity.budget                  # 已配置的上限
capacity.exceeded                # () —— 没有维度超出预算
```

`manifest_bytes` 不是估算值，而是 `len(memory_content_bytes(content))`，即 Revision content hash 本来就承诺的那串
精确字节。因此运维读到的数字，就是存储层写入的数字。

## 越过上限是具体、可操作的失败

会把 Memory 推过预算的写入，在任何内容被持久化之前就被拒绝：

```
409 Conflict
{
  "code": "memory_capacity_exceeded",
  "message": "The Memory has reached its capacity budget.",
  "details": {"dimension": "manifest_bytes", "limit": 4194304, "observed": 4194527}
}
```

拒绝会指明触发的维度、已配置的上限，以及被拒写入本会产生的值。原样重试会以完全相同的方式失败——这是状态冲突，
不是临时性错误。

## 触及预算后的容量恢复

一个会阻断自身补救手段的容量上限会让 Memory 变成砖头。因此补救操作始终可执行，即使已超出预算：

- `forget()` 把 entry 移出活跃检索面。始终允许。
- `organize(mode="dedupe")` 停用完全重复的 entry。始终允许。
- `compact()` 从当前 manifest 丢弃符合条件的墓碑。不受容量预算阻断，但仍需显式启用并满足资格条件。

只有会**增长**当前超限维度的操作才被拒绝：追加或修订 entry，以及活跃检索面已满时的 reactivate。

如果写满的 Memory 只有过新的墓碑，运维可将最小年龄设为零后预览压缩，无需人为制造 Revision。
带标签条目仍受保护：若需保留这些标签，可能需要提高预算。容量契约不会覆盖保留决策。

## 压缩移除墓碑，而不是历史

`forget()` 会在 manifest 中保留一个 inactive 项，以便该 entry 可以被 reactivate，并保持其历史可读。这是正确的
默认行为，同时也正是 manifest 只增不减的原因。`compact()` 是回收这部分空间的显式手段：

```python
plan = await memory_service.compact(memory, dry_run=True)
plan.entry_ids        # 符合条件的墓碑
plan.reclaimed_bytes  # 完整规范内容的有符号字节减少量

result = await memory_service.compact(memory)  # 需要先启用压缩
memory = result.memory
```

压缩**不触碰**的，正是让 Memory 可被引用的那部分。entry 正文保留在 `pc_memory_entry_versions` 中。每个历史
Revision 保留各自的 manifest。Handoff 引用按 `ArtifactRef + entry_id + entry_version_id` 解析到**它所指名的那个
精确 Revision**，因此压缩之前写下的引用在压缩之后仍然逐字节验证通过。

它确实改变的、运维在启用前必须接受的是：被压缩的 entry 已从**当前** manifest 中消失，因此 `reactivate()` 无法再
恢复它，`list(include_inactive=True)` 不再展示它。带标签条目被排除，其标签继续正常解析。所以压缩是
**默认关闭、先 dry-run、且对活跃检索面不可逆**的。默认保留窗口为停用后推进 10 个 Revision，给误操作留下恢复
时间；显式将年龄设为零可立即压缩。压缩关闭时仍可预览。

## 默认值

默认预算为 5,000 条活跃 entry、10,000 个 manifest 项和 4 MiB 完整规范内容。这些上限约束每个 Revision 的增长，
不保证延迟，也不限制数据库总大小。压缩默认关闭，因为从当前清单移除条目后无法重新激活。历史读取默认上限为
100 个 Revision，超限时明确报错，不会静默截断。

# 参考级说明

## 设计不变量

1. **权威内容永不被销毁。** 不删除任何 entry 正文行，不删除或重写任何历史 Revision，不改变任何 content hash。
   压缩只决定**下一个** manifest 携带哪些项。
2. **引用保持稳定。** 本 RFC 中的任何操作都不影响精确 Revision 引用的验证，因为它按所指名的 Revision 解析，而不是
   按当前 head。
3. **预算不阻断补救。** 停用、去重和已启用的压缩无论预算状态如何都可执行。
4. **拒绝无副作用且确定。** 预算在完整准备好的下一个 manifest 上、在事务开启之前完成评估。相同输入产生相同决策。
5. **#1709 的保证不变。** 强制检查不给 append 路径增加任何按 entry 的 I/O，压缩完全不写投影行。
6. **后端中立语义。** 计数、字节数、决策和错误在 SQLite 与 OceanBase 上完全一致，只有物理空间回收不同。

## 可度量维度

三者都从一个精确 Revision 的 manifest 派生。均不冗余持久化，符合 RFC 0014 关于 entry 计数从 manifest 派生的规定。

| 维度 | 定义 | 增长于 | 缩减于 |
| --- | --- | --- | --- |
| `active_entry_count` | `state == "active"` 的 manifest 项 | add、reactivate | deactivate |
| `manifest_entry_count` | 全部 manifest 项 | add | compact |
| `manifest_bytes` | `len(memory_content_bytes(content))` | 取决于内容，包括审计变更 | 取决于内容 |

`manifest_bytes` 是最关键的维度：它是每个 Revision 实际写入的量，也是唯一计入标识符和 hash 宽度、而非假定固定
单条成本的维度。两个计数维度存在的理由是：它们才是运维实际推理的对象，并且 entry 数上限比字节上限更早捕获病态
写入方。

`deactivate` 降低活跃条目数，但不改变清单条目数。每次操作都会替换该 Revision 的审计变更和原因，因此完整规范
字节数的增减不完全由这两个计数决定。压缩缩小目录，但新审计记录可能使该 Revision 更大；后续 Revision 不再重复
这些记录。因此 `reclaimed_bytes` 必须保留符号，而释放清单项容量仍需 `compact()`。

## 预算值

新增于 `src/powercontext/builtin/artifacts/memory/models.py`：

```python
class MemoryCapacityBudget(BaseModel):
    """The capacity ceiling applied to one Memory Artifact."""

    max_active_entries: int = Field(default=5_000, ge=1)
    max_manifest_entries: int = Field(default=10_000, ge=1)
    max_manifest_bytes: int = Field(default=4_194_304, ge=1_024)

    @model_validator(mode="after")
    def validate_entry_ceiling_order(self):
        if self.max_active_entries > self.max_manifest_entries:
            raise ValueError("max_active_entries cannot exceed max_manifest_entries")
        return self


class MemoryCapacity(BaseModel):
    """Observed capacity of one exact Memory Revision against its budget."""

    memory_ref: ArtifactRef
    active_entry_count: int = Field(ge=0)
    manifest_entry_count: int = Field(ge=0)
    manifest_bytes: int = Field(ge=0)
    compactable_entry_count: int = Field(ge=0)
    budget: MemoryCapacityBudget
    exceeded: tuple[MemoryCapacityDimension, ...] = ()
```

其中 `MemoryCapacityDimension: TypeAlias = Literal["active_entries", "manifest_entries", "manifest_bytes"]`。

### 默认预算与校准

默认值是增长上限。具体部署的延迟目标需要对应后端的代表性测量来校准。

按当前 service 生成的标识符宽度，一个规范 manifest 项实测为 **223 字节**：`mem_ent_` 加 32 字符十六进制 UUID、
同样宽度的 `mem_ver_`、一个 64 字符十六进制 content hash、一个 state 和 JSON 框架。以规范编码器实测，5,000 项为
1,115,092 字节，10,000 项为 2,230,092 字节。

因此字节上限取 4 MiB，刻意高于两个 entry 上限自身的占用。若取 1 MiB，两个 entry 上限都将永不可达——字节上限
总会在约 4,700 项处先行触发——从而留下两个永不生效的已文档化维度。在 4 MiB 和当前标识符宽度下，
`manifest_entries` 对应的目录本身约为 2.13 MiB。`manifest_bytes` 还计入审计记录和原因，因此即使使用默认
标识符宽度，大批量写入也可能先触发字节上限。

保留可配置的 5,000 / 10,000 / 4 MiB 增长上限。部署预算应通过对应后端上隔离、具有代表性的测量校准，并计入保留
历史的成本。基准方法与测量摘要集中在 `benchmark/memory_capacity/README.md`，原始运行结果随验收证据保存。

## 配置

`src/powercontext/builtin/runtime/config.py` 的 `RuntimeConfig` 中，紧邻现有 memory 配置项：

```python
memory_max_active_entries: int = Field(default=5_000, ge=1, le=100_000)
memory_max_manifest_entries: int = Field(default=10_000, ge=1, le=200_000)
memory_max_manifest_bytes: int = Field(default=4_194_304, ge=1_024, le=67_108_864)
memory_compaction_enabled: bool = False
memory_compaction_min_tombstone_revisions: int = Field(default=10, ge=0)
memory_max_history_revisions: int = Field(default=100, ge=1)
```

它们按 `memory_rerank_candidate_limit` 今天的完全相同方式向下传递：在 `builtin/runtime/composition.py` 中读取，
作为字段挂在 `builtin/runtime/relational.py` 的关系型 runtime 上，再传入 `MemoryService` 构造函数。
`MemoryService.__init__` 新增 `capacity_budget: MemoryCapacityBudget | None` 和
`compaction: MemoryCompactionPolicy | None`，以及 `max_history_revisions: int = 100`；`None` 表示默认预算且
压缩关闭。`family_management.py` 的通用 Artifact 写入同样接收已配置的容量预算，不能绕过部署上限。

## 错误

新增于 `src/powercontext/builtin/artifacts/memory/errors.py`：

```python
class MemoryCapacityExceededError(MemoryLayerError, RuntimeError):
    def __init__(self, dimension: str, limit: int, observed: int) -> None:
        self.dimension = dimension
        self.limit = limit
        self.observed = observed
        super().__init__(f"memory capacity budget is exceeded: {dimension} {observed} > {limit}")
```

它继承 `MemoryLayerError`。通用 Artifact 写入保留这一特定异常，不将其转换为 `InvalidBaseAccessRequestError`，
从而返回相同的容量冲突。从 `src/powercontext/builtin/artifacts/memory/__init__.py` 导出。

`src/powercontext/server/app.py` 的 `_map_domain_error` 在宽泛的 `InvalidMemoryCandidateError` 分支之前映射它：

```python
if isinstance(error, MemoryCapacityExceededError):
    return (
        status.HTTP_409_CONFLICT,
        "memory_capacity_exceeded",
        "The Memory has reached its capacity budget.",
        {"dimension": error.dimension, "limit": error.limit, "observed": error.observed},
    )
```

选 409 而非 422 或 503：请求本身格式正确、Server 也健康，但目标资源的状态拒绝它，并且原样重试会同样失败——这与
现有把 `RevisionConflictError` 和 `MemoryEntryInactiveError` 映射为 409 的理由一致。

## 强制检查点

强制检查在准备好的 manifest 上执行一次，位于 `src/powercontext/builtin/artifacts/memory/service.py`：

- `_prepare_commit` 在构造 `Memory` 之前生成 `sorted_manifest` 和 `content`。检查紧接在 `content` 构造之后、
  返回 `MemoryCommit` 之前。
- `_commit_existing_transition` 为 `forget`、`reactivate`、`organize` 和 `compact` 做同样的事。

两条路径调用同一个辅助函数：

```python
def _require_capacity(
    self, base: Memory | None, content: MemoryContent, *, growth: frozenset[str], content_bytes: bytes
) -> None:
    """Refuse a prepared Revision that grows a dimension past its budget."""
```

`growth` 指明本操作可能增长的维度；不在 `growth` 中的维度永不被检查，这使不变量 3 由结构而非约定来保证：

| 操作 | `growth` |
| --- | --- |
| append / revise（`_prepare_commit`） | `active_entries`、`manifest_entries`、`manifest_bytes` |
| `reactivate` | `active_entries` |
| `forget`、`organize`、`compact` | `frozenset()` |

只有当准备值既超过上限**又**超过基准 Revision 在该维度上的值时，该维度才被拒绝。因此一个已经超出预算的
Memory——例如在配置下调上限之后——仍然接受不会让超限变得更糟的写入，也仍然接受所有补救操作。维度按固定顺序
`manifest_bytes`、`manifest_entries`、`active_entries` 求值，因此多个维度同时触发时上报的维度是确定的。

由于 `MemoryCreateContent` 和 `MemoryReplaceContent` 都经由 `builtin/persistence/family_management.py` 中的
`plan_remember`，通用 Artifact 写入 API 共用强制检查、已配置预算和容量专用错误。head 的比较交换拒绝 base
已经移动的预备 plan。

检查对已加载的清单计数，并复用计算 content hash 的规范字节串。不增加 entry 正文读取或投影写入，因此 #1709 的
append 保证不受影响。

## `compact()`

`MemoryService` 新增：

```python
async def compact(
    self,
    memory: Memory,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    reason: str | None = None,
) -> MemoryCompactionResult:
    """Drop qualifying inactive tombstones from the current manifest."""
```

`MemoryCompactionResult` 携带 `memory`（新 Revision；dry-run 或无变化时为原基准）、`entry_ids`、
`reclaimed_bytes` 和 `dry_run`。

`reclaimed_bytes` 是 `len(base_content_bytes) - len(next_content_bytes)`，计入每条审计变更及原因。即使移除了
墓碑也可能为负；无变化时为零；它不代表实际释放的数据库字节。dry-run 不写入任何内容，压缩关闭时也可使用。
真实提交要求 `enabled=True`，且 head 未发生变化。

### 资格条件

一个 manifest 项必须同时满足以下全部条件才符合条件：

1. 在基准 manifest 中 `state == "inactive"`。
2. 停用后已推进至少 `min_tombstone_revisions` 个 Revision。默认年龄为 10 时，版本 2 停用的条目从版本 12 起
   符合条件。仅读取保留窗口内的变更，不加载 entry 正文。重新激活并再次停用会重置窗口。零年龄只跳过年龄检查。
3. 没有任何 Artifact tag 绑定它。tag 目标按包含 inactive 项的最新 manifest 解析
   （`builtin/persistence/tags.py:302`），因此压缩带 tag 的 entry 会破坏 tag 解析。新增后端方法
   `any_tagged_entry_ids(memory)` 提供排除集合。现有 `tagged_entry_ids` 本身已覆盖整个 manifest 而非仅活跃项，
   但它要求传入 `TagFilter`，回答的是"哪些 entry 匹配该过滤条件"；资格判定需要的是"哪些 entry 带有任意 tag"，
   因此按现状不可直接复用。
4. 未被同一次操作 reactivate、revise 或以其他方式指名。

`limit` 限制单个 Revision 丢弃的墓碑数量，因此墓碑积压很多的 Memory 可以分有界的多步压缩，而不是产生一个超大
Revision。

提交前会在持有所属 Artifact 的 head 锁时重新检查标签。若候选条目刚被绑定标签，整个事务回滚并抛出
`CapabilityNotSupportedError("compaction-tag-conflict")`；调用方可针对未变化的 head 重新预览。

### 它写入的 Revision

压缩通过 `_commit_existing_transition` 产生一个普通 Revision：manifest 省略被压缩的项，每次丢弃记录一条变更。
让这一点安全的两个不变量都是结构性的，而非口头承诺：

- **无投影工作。** inactive entry 在 `pc_memory_entry_heads` 和搜索索引中没有行——`_commit` 仅从
  `state == "active"` 的项派生活跃 head 差异，因此被压缩的项既不在 `previous_active` 也不在 `current_active`，
  既不出现在删除集合也不出现在 upsert 集合。无论丢弃多少墓碑，压缩写入零投影行。
- **不删除正文。** `pc_memory_entry_versions` 的行保留。`pc_memory_entry_heads` 上的外键是
  `ondelete="RESTRICT"`，且历史 Revision 仍然引用这些版本，因此保留它们是必需的，而不仅是一种选择。

### 变更操作

压缩记录 `op="compact"`，`from_entry_version_id` 为被丢弃的版本，`to_entry_version_id=None`。一个静默减少项数却
不留变更记录的 manifest 会破坏 RFC 0014 关于 `changes()` 是 Revision 紧凑增量的规定，并且会让 Memory 中唯一一个
从当前目录移除内容的操作失去审计轨迹。

这会扩展公开的 `MemoryChangeOp`：

- `src/powercontext/builtin/artifacts/memory/models.py` —— 向 `MemoryChangeOp` 别名添加 `"compact"`。
- `openapi/powercontext.yaml` —— 向 `EntryChangeOperation` 添加 `compact`。
- 用 `make api-generate` 重新生成，并用 `make contract-test` 验证；生成的模型使用 `extra="forbid"` 加枚举校验，
  因此这是带版本的增量变更，而不是静默变更。

除写入上限与历史读取边界外，枚举扩展也影响兼容性。穷举 `EntryChangeOperation` 的客户端必须在读取历史中含压缩记录的 Memory
之前更新。由于压缩默认关闭，在运维主动启用之前没有任何现有部署会产生这个新值。

## 公开读取 API

`POST /v1/memory/capacity` 返回当前 head 的 `MemoryCapacity`，复用现有 `/v1/memory/entries/list` 处理器的 Scope
授权与 Memory 身份校验。这是一个增量 OpenAPI 操作：在 `openapi/powercontext.yaml` 中定义、重新生成、并添加契约
测试。SDK 调用方直接使用 service 层的 `capacity()`。该路由要求 `scope.read` 权限；Scope 尚无 Memory 时返回 404，
不会创建 Memory 或虚构引用来返回零值。Python HTTP 客户端使用 `PowerContextClient.get_memory_capacity()`。

压缩刻意**不**在本 RFC 中通过 HTTP 暴露。它是一个维护操作，其授权模型属于 #1425 的更广保留策略；在那份设计之前
就把它作为默认无认证的 Server 路由暴露出去顺序是错的。SDK 与进程内 runtime 调用方现在即可调用它。

## Revision 历史约束

本 RFC 不删除也不限制已存储的 Revision。删除历史会破坏血缘、精确引用和 Handoff 验证，而物理擦除是 #1425 的边界。

它确实约束了一处无界**读取**。`MemoryService.revisions()` 在循环中从 1 加载到 head 的每个 Revision，每次一个后端
`get()`，因此一个有 1,000 个 Revision 的 Memory 单次调用会发出 1,000 次加载。本 RFC 用 `max_history_revisions`
为该读放大设上限，默认 100 个 Revision；超限时在展开历史前抛出 `CapabilityNotSupportedError("history-window")`，
现有映射将其转成指明该 capability 的 422。基于游标的替代方案是 #1657 的交付物，而这个上限正是 #1656 的验收标准所要求的、让调用方事先
约定而非自行摸索的显式边界。

结果不会静默截断。每个 Revision 为 4 MiB 时，1,000 个快照接近 4 GiB，100 个也接近 400 MiB，尚未计入 Python
对象开销。这是读取展开次数上限，不是进程内存上限：补救操作和调低预算可能使版本超过字节预算。只有调用方能够
承担完整快照开销时才应显式提高上限。达到上限后，精确 Revision 读取和已存储历史仍然可用。

`entries()` 在此仍不分页，由 #1656 负责约束。压缩会作为副作用降低它的成本，因为被压缩的墓碑不再是 `entries()`
需要加载的版本。

## 后端行为

逻辑是后端中立的：它位于 service 层、作用于 manifest，并且只通过现有 `MemoryBackend` 方法加一个新的 tag 查询
访问存储。相同参数化测试覆盖两种后端的计数、字节数、决策和错误。OceanBase 验证需要可清理的
`POWERCONTEXT_TEST_OCEANBASE_URL` 数据库；跳过执行不能证明后端一致性。

差异在于物理回收，并且必须分别报告，因为两者行为不同：

- **SQLite。** 压缩降低未来目录大小，但保留所有历史清单。checkpoint 和 `VACUUM` 无法回收历史仍占用的页面。
  测量必须区分 checkpoint 后与 `VACUUM` 后的字节数；当前清单更小不代表数据库文件更小。
- **OceanBase。** 物理回收是异步的，且不能回收保留的历史。报告数据库字节数时说明观察延迟，不承诺逻辑压缩后
  数据库占用一定下降。

## 验证

### 行为测试

`tests/builtin/artifacts/memory/test_capacity.py` 在 SQLite 及已配置时的 OceanBase 上验证可观察的容量契约：

- 精确规范字节数、各维度的确定性拒绝，以及拒绝后存储不变。
- runtime 预算传递、下调预算、仅检查活跃数的重新激活，以及超预算去重。
- 压缩预览、过期 head 冲突、审计变更、正文和引用保留，以及零投影写入。
- 墓碑年龄、重新激活后的窗口重置、标签保护，以及提交前新加标签时事务回滚。
- 满容量 Memory 通过显式零年龄立即恢复，同时保护活跃条目与带标签墓碑。
- 审计原因超过被移除指针大小时 `reclaimed_bytes` 为负，无变化压缩返回零。
- 历史读取在 100 个 Revision 时成功，在 101 个时于展开前拒绝，并支持显式配置覆盖。

`tests/e2e/test_memory_capacity.py` 覆盖 HTTP 与客户端契约，包括 Scope 尚无 Memory 时的 404、显式及通用写入的
409 和读取权限。`tests/test_api_contract.py` 验证新增操作和 `compact` 枚举值。

### 回归防护

PR #1709 的 `test_memory_append_projection_writes_do_not_grow_with_entry_history` 和
`test_memory_append_leaves_untouched_projection_rows_identical` 必须在不修改的前提下保持通过。强制检查不增加任何
投影工作，因此它们的语句计数一旦变化，就说明实现把检查放错了位置。

### 规模基准

新增 `benchmark/memory_capacity/` 模块，与现有 `locomo` 基准并列、并按 `benchmark/README.md` 给出的理由置于
`tests/` 之外，在 entry 数 200、1,000、5,000 以及一个完整压缩周期上记录：

entry 数、manifest 字节数、数据库字节数、平均 append 延迟、末窗平均 append 延迟、每次 append 的投影行写入数，
以及压缩前后的搜索召回行为。

这是 #1718 所要求的完整边界，取代而非重复 #1709 的投影语句计数测量。SQLite 与 OceanBase 结果分别报告，各自说明
其回收流程。

## 实施顺序

每一步都可独立评审，并让代码树保持通过。

1. **只度量，不强制。** `MemoryCapacityBudget`、`MemoryCapacity`、`MemoryCapacityDimension`、
   `MemoryService.capacity()` 和导出。测试上报字节数等于规范字节数。
2. **强制检查。** `MemoryCapacityExceededError`、`_require_capacity`、两个调用点、`growth` 表、HTTP 映射。
   补齐拒绝、无持久化、以及超预算时补救可执行的测试。
3. **配置。** 六个 `RuntimeConfig` 字段与构造函数传递。测试配置的上限能到达 service。
4. **压缩。** `compact()`、含新 tag 查询的资格判定、`compact` 变更 op、OpenAPI 枚举新增、`make api-generate`、
   `make contract-test`。
5. **读取约束。** `revisions()` 的上限及其 capability 错误。
6. **公开读取端点。** `POST /v1/memory/capacity`、OpenAPI、契约测试。
7. **基准。** `benchmark/memory_capacity/` 以及记录的 SQLite 与 OceanBase 结果。

仅第 1 至 3 步就能闭合 #1718 中"没有可观测上限"的那一半，值得在压缩之前先合入。

整体变更的验证命令：`make check`、`make test`，第 4 或 6 步之后的 `make contract-test`，以及针对本文档及其中文
翻译的 `make docs-test`。

## 验收标准

- Memory 的活跃 entry 数、manifest entry 数和 manifest 字节数可通过公开 API 读取，且上报的字节数等于该 Revision
  所承诺的规范字节数。
- 会越过某个预算维度的写入抛出 `MemoryCapacityExceededError`，映射为指明维度、上限和观测值的 409，并且不持久化
  任何内容。
- 拒绝是确定的：针对同一 head 的同一写入以相同方式失败，多个维度同时触发时上报的维度固定。
- 容量预算不阻断 `forget()`、`organize()` 和已启用的 `compact()`。存在符合条件墓碑的满容量 Memory 能通过
  生命周期操作恢复，无需直接访问存储；零年龄允许立即恢复，但标签始终受保护。
- `compact()` 不删除任何 entry 正文行、任何历史 Revision 和任何 content hash，且写入零投影行。
- 压缩之前创建的引用在压缩之后仍能针对其指名的 Revision 验证通过。
- 压缩跳过带 tag 的墓碑和低于配置最小年龄的墓碑，并支持 dry-run。
- `changes()` 为每个被丢弃的 entry 上报 `compact` 操作。
- #1709 的增量投影写入保证保持不变，由其现有测试验证。
- 规模结果记录 entry 数、manifest 字节数、数据库字节数、append 延迟、末窗 append 延迟、投影行写入数和压缩后的
  搜索行为，SQLite 与 OceanBase 分别报告。
- 压缩默认关闭，保留窗口默认 10 个 Revision。预算默认为 5,000 / 10,000 / 4 MiB，历史读取默认 100 个 Revision；
  调用方可显式配置这些限制。

# 缺点

- **上限可能拒绝合理的写入。** 确实需要在单个 Memory 中放超过 5,000 条活跃 entry 的部署，现在会失败，而此前只是
  退化。这是有意的取舍——静默的超线性退化比一个具名的限制更糟——但它确实是行为变更，部署可能需要按负载调整默认值。
- **压缩对活跃检索面不可逆。** 被压缩的 entry 无法 reactivate。dry-run、最小年龄、tag 排除和默认关闭共同降低
  风险。显式零年龄以放弃恢复窗口换取立即释放容量。
- **一个公开枚举被扩展。** `EntryChangeOperation` 新增 `compact` 会要求严格校验的客户端更新。
- **总存储仍然无界。** `flat-v1` 仍然按 Revision 复制目录并保留历史。预算约束每个 Revision 的增长，补救操作豁免；
  压缩和历史读取上限均不限制数据库累计大小。
- **三个维度比一个多。** 两个计数加字节数的契约面比单一 entry 上限更大。计数是运维推理的对象，字节是存储支付的
  代价，合并两者必然丢掉其中一方。

# 设计理由与替代方案

**为什么拒绝而不是拆分？** 拆分需要把一个逻辑 Memory 映射到多个 Artifact 的第二层身份。RFC 0014 把它列为非目标，
并把 routing manifest 列为后续可能性。在这里临时发明的拆分方案必须回答：一次搜索覆盖哪些 Memory、一个 Scope
解析到哪个 Memory、以及引用如何跨拆分存续——这本身就是一份独立 RFC。拒绝是诚实的中间状态：确定、可观测、可测试，
并且向前兼容，因为后续路由设计只需在唯一一个调用点把 `_require_capacity` 的抛出替换为路由决策。

**为什么不删除旧 Revision？** 那是可获得的最大存储收益，也是这里绝不能做的事。它会破坏血缘、精确引用和 Handoff
验证，而且属于物理擦除，由 #1425 负责。

**为什么不在上限处汇总或合并 entry？** RFC 1652 拒绝正文压缩的理由值得重述：entry 正文是自包含、承载引用的记录，
重写它们有丢失姓名、日期和数量的风险。容量压力不能成为改写内容的许可。

**为什么用 `manifest_bytes` 而不是数据库字节数？** 数据库字节数才是运维真正关心的量，但它依赖具体后端、滞后于
写入，并且在 SQLite 上需要 `VACUUM` 才有意义。manifest 字节数精确、后端中立、已在写入路径中被计算，并与本 RFC
所约束的成本直接成正比。基准测试会报告数据库字节数，使二者关系是被测量的而非被假定的。

**为什么不用专门的容量表？** RFC 0014 要求 entry 计数从 manifest 派生而非冗余持久化。计数表会引入可能与 manifest
不一致的第二个事实来源，而它并不能服务任何我们无法从已加载的 manifest 直接得出的读取。

**为什么在 service 而不是后端强制？** service 是组装下一个 manifest 的层，能在持久化写入之前拒绝。
两个 SQL 适配器由此继承完全一致的语义，通用 Artifact 写入路径也一并继承。

**为什么预算默认启用？** 默认关闭的容量契约约束不了任何东西，而 #1321 报告的正是一个没有任何边界的系统。
启用预算让增长越界显式可见；确需更高上限的部署可以配置预算。压缩仍需单独启用。

**不做此事的影响。** #1321 的超线性增长仍然无界且不可观测。长期存续的 Memory 会持续退化，没有信号、没有上限，
也没有受支持的墓碑空间回收手段。

# 先例

不可变日志系统以同样方式区分逻辑删除与物理回收：墓碑标记删除，随后的压缩过程回收空间，而不重写读者可能仍持有的
历史。LSM-tree 压缩和 Git 基于可达性的垃圾回收都把回收步骤做成显式且异步的，而不是隐含在删除动作里。

在 PowerContext 内部，`organize()` 已经确立了维护操作是带变更记录的普通 Revision、而非带外改动这一范式；
`src/powercontext/builtin/persistence/topic_memory_budget.py` 中的 Topic Memory 工作预算则确立了在昂贵工作之前
强制服务端上限、并给出稳定耗尽原因的范式。本 RFC 同时遵循两者：压缩是普通 Revision，预算是带具名原因、在持久化前检查的
上限。

RFC 1652 的保留档位与可恢复自动停用是本物理契约的逻辑对应面：那份 RFC 决定哪些 entry 应当离开活跃检索面，本
RFC 决定 manifest 何时可以不再携带它们。

# 未解决问题

- **部署校准。** 默认值保留 5,000 / 10,000 / 4 MiB。仍需隔离的延迟测量，才能针对具体负载推荐更严格的预算。
- **压缩是否应当自动化？** 本 RFC 让它显式、由运维驱动。低于余量阈值时执行定时压缩是否安全，取决于 #1425 的
  授权与 dry-run 模型。
- **被压缩 entry 的恢复路径是什么？** 目前通过 `reactivate()` 没有恢复路径。是否值得定义一个把保留的正文作为新
  entry 重新加入的 `restore` 操作、以及它采用何种身份，刻意留待后续。
- **按 Scope 的预算？** 这里的预算按部署生效。按 Scope 覆盖需要 #1219 和 #1345 负责的 Scope 级配置能力。
- **游标与一致性的共享决策。** `revisions()` 的上限必须与 #1656、#1657 最终确定的游标和固定 Revision 语义一致。
  若它们先落地，本上限应变成它们的默认页大小，而不是一个独立限制。

# 后续可能性

增量 manifest 格式——`manifest-v2`，保存一个基准引用加自该基准以来的变更，并周期性写入完整快照——才是
`flat-v1` 按 Revision 复制问题的真正解法，它会把本 RFC 所约束的存储增长从与 entry 数成正比变为与变更数成正比。
这是需要迁移和重建路径的持久化格式变更，因此需要独立 RFC；本 RFC 的可度量维度正是证明其必要性并验证其结果的
依据。

再往后：接入 `_require_capacity` 单一决策点的 routing manifest 与自动拆分；在 #1425 授权模型下的定时压缩；把
容量信号接入 RFC 1652 的清理提案，使压力去选择低重要性的 entry 而不仅仅是拒绝写入；以及在 Scope 级配置就绪后
按 Scope 覆盖预算。
