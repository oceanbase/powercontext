# Core Protocol 集成导读

这份导读写给实现 PowerContext 组件的开发者。它用一个逐步扩展的 context system 说明 Core Protocol
如何组合，也标出哪些工作应留在集成层。

文中的类型和函数是设计示例，不是待发布的 builtin API。示例省略数据库表、codec、worker 和模型客户端的实现，
因为这些细节会遮住协议本身。实现者需要关注的是对象如何流动，以及每一层负责什么。

`tests/e2e/test_context_system.py` 是本文的可执行配套测试。它验证按 session 分区的 Trigger State、按 query 和
owner scope 检索 Memory、异构 Source、一次 Action 写入零到多个 Memory，以及定时生成 Handoff。测试以 SQLite
作为本地数据库的例子，以 APScheduler 作为调度器的例子。修改本文的对象流、协议边界或推荐调用顺序时，应检查该
测试，并在预期行为发生变化时一并更新。测试中的具体 codec 和 worker 只是夹具，不是拟议中的 Core API。

## Core 定义的边界

Core 提供三组领域契约和一个组合对象：

| 概念 | Core 类型 | 用途 |
| --- | --- | --- |
| Source | `Source`, `SourceAdapter`, `SourceCatalog`, `SourceStore` | 接入外部工作材料，保留可读取的证据对象 |
| Artifact | `ArtifactDraft`, `Artifact`, `ArtifactCatalog`, `ArtifactStore` | 写入和读取带 Revision 与 lineage 的上下文产物 |
| Trigger | `Trigger`, `PolicyTransition` | 将 Signal 和 State 映射为新的 State 与零个或多个 Action |
| Composition | `Sources`, `Artifacts`, `PowerContext` | 绑定应用选择的具体组件 |

Memory 生成、Handoff 查询、Trigger State 持久化、Action 执行和任务调度不属于 Core。集成实现可以提供这些能力，
但不应让 Core 依赖某一种数据库、scheduler 或模型 SDK。

## 阶段一：从 Agent turn 生成 Memory

Agent 完成一次模型调用后，集成层将这一轮对话保存为 Source。Source 是生成 Memory 时使用的证据，不是 Memory
本身。

### 定义 Agent Source

具体实现显式继承对应协议。这样可以在类定义处看出输入、Source 和读取结果的类型关系。

```python
from dataclasses import dataclass

from powercontext import Source, SourceAdapter, SourceMaterialization


@dataclass(frozen=True, slots=True)
class AgentTurnInput:
    session_id: str
    round_number: int
    user: str
    assistant: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTurnSource(Source):
    session_id: str
    round_number: int
    user: str
    assistant: str


class AgentTurnAdapter(
    SourceAdapter[AgentTurnInput, AgentTurnSource, AgentTurnInput]
):
    input_class = AgentTurnInput
    name = "agent-turn"
    source_class = AgentTurnSource

    async def resolve(self, value: AgentTurnInput, /) -> AgentTurnSource:
        return AgentTurnSource(
            name=f"{value.session_id}/round-{value.round_number}",
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            round_number=value.round_number,
            user=value.user,
            assistant=value.assistant,
        )

    async def read(self, source: AgentTurnSource, /) -> AgentTurnInput:
        return AgentTurnInput(
            session_id=source.session_id,
            round_number=source.round_number,
            user=source.user,
            assistant=source.assistant,
        )
```

`SourceAdapter.name` 标识一次 Adapter 注册，`Source.name` 标识具体 Source class 内的一个值。Core 按
Adapter 声明的精确 `source_class` 路由读取；Adapter 名称不会复制到 Source 值上，也不作为它的持久化
discriminator。

这个 Adapter 选择 captured materialization。Source 中的数据是该轮完成时的快照。需要读取外部 trace store
时，也可以改为 referenced materialization，并让 `read()` 临时物化内容。选择哪一种方式由 Adapter 决定。

### 定义 Memory Artifact

Memory 是一种 Artifact family。具体 family 定义自己的 content，不向通用 Artifact 增加检索字段或业务日期。

```python
from dataclasses import dataclass
from typing import ClassVar

from powercontext import Artifact, ArtifactDraft


@dataclass(frozen=True, slots=True)
class MemoryContent:
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = "memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class Memory(Artifact[MemoryContent]):
    family: ClassVar[str] = "memory"
```

`MemoryDraft.sources` 保存本次生成实际使用的 Agent Source。Store 提交 Draft 后返回带有 identity、Revision 和
lineage 的 `Memory`。

### 定义按轮次触发的策略

Trigger 不读数据库，也不调用模型。它只计算 transition。

```python
from dataclasses import dataclass

from powercontext import Trigger
from powercontext.triggers import PolicyTransition


@dataclass(frozen=True, slots=True)
class SourceStored:
    source: AgentTurnSource


@dataclass(frozen=True, slots=True)
class PendingTurns:
    sources: tuple[AgentTurnSource, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerateMemory:
    sources: tuple[AgentTurnSource, ...]


class EveryAgentRounds(
    Trigger[SourceStored, PendingTurns, GenerateMemory]
):
    def __init__(self, rounds: int) -> None:
        if rounds < 1:
            raise ValueError("rounds must be positive")
        self._rounds = rounds

    def initial_state(self) -> PendingTurns:
        return PendingTurns()

    def activate(
        self,
        signal: SourceStored,
        state: PendingTurns,
        /,
    ) -> PolicyTransition[PendingTurns, GenerateMemory]:
        sources = (*state.sources, signal.source)
        if len(sources) < self._rounds:
            return PolicyTransition(state=PendingTurns(sources))
        return PolicyTransition(
            state=PendingTurns(),
            actions=(GenerateMemory(sources),),
        )
```

`PolicyTransition.actions` 是 tuple。策略可以不产生 Action，也可以一次产生多个 Action。Action 数量与 Artifact
数量是两件事：一个 `GenerateMemory` Action 可以不生成 Memory，也可以生成一个或多个 Memory。

### 组合组件

具体 repository 也应显式继承 `SourceCatalogBackend`、`SourceStore`、`ArtifactCatalog` 或 `ArtifactStore`。
这里假定应用已经提供 `source_repository` 和 `artifact_repository`。

```python
from dataclasses import dataclass

from powercontext import Artifacts, PowerContext, SourceCatalog, Sources, Trigger


@dataclass(frozen=True, slots=True)
class ContextTriggers:
    agent_memory: Trigger[SourceStored, PendingTurns, GenerateMemory]


context = PowerContext(
    sources=Sources(
        catalog=SourceCatalog(
            backend=source_repository,
            adapters=(AgentTurnAdapter(),),
        ),
        store=source_repository,
    ),
    artifacts=Artifacts(
        catalog=artifact_repository,
        store=artifact_repository,
    ),
    triggers=ContextTriggers(
        agent_memory=EveryAgentRounds(10),
    ),
)
```

`PowerContext` 不启动 worker，也不接管 repository。它只是显式配置的组合结果。

### 在集成层执行 transition

集成层在 Source 写入成功后发布 Signal，加载对应 State，再调用 Trigger。Action handler 读取 Source、调用模型，
最后写入 Artifact。

```python
from collections.abc import Awaitable, Callable


async def record_agent_turn(
    context: PowerContext[ContextTriggers],
    state: PendingTurns,
    value: AgentTurnInput,
    extract_memories: Callable[
        [tuple[AgentTurnInput, ...]],
        Awaitable[tuple[MemoryContent, ...]],
    ],
) -> PendingTurns:
    resolved = await context.sources.resolve(value)
    source = await context.sources.add(resolved)
    if not isinstance(source, AgentTurnSource):
        raise TypeError

    transition = context.triggers.agent_memory.activate(
        SourceStored(source),
        state,
    )
    for action in transition.actions:
        turns: list[AgentTurnInput] = []
        for item in action.sources:
            turn = await context.sources.read(item)
            if not isinstance(turn, AgentTurnInput):
                raise TypeError
            turns.append(turn)

        contents = await extract_memories(tuple(turns))
        for content in contents:
            await context.artifacts.add(
                MemoryDraft(
                    content=content,
                    sources=action.sources,
                )
            )
    return transition.state
```

这个函数只是说明控制流。正式实现通常先把 Signal 写入 durable queue，由 worker 处理 transition 和 Action。
`Sources.add()` 本身仍是单纯的 Source 写入，不暗示模型调用或 Artifact 生成。

示例直接传入 `state` 只是为了缩短代码。runtime 必须按照应用定义的 partition 持久化 Trigger State，例如 session、
owner 或 project scope。Core 不定义这个 partition key。每个 Trigger 只保存一份全局 State 会让无关会话的 Source
混在一起。

### Memory 在模型调用前注入

已提交的 Memory 在构造模型请求时读取。本轮 trace 要等模型返回后才能写入，因此本轮产生的 Memory 只影响后续请求。

```python
memories = await memory_queries.search(
    query=user_message,
    scope=context_scope,
)
request = build_agent_request(
    user_message=user_message,
    memories=memories,
)
assistant = await model(request)

state = await record_agent_turn(
    context,
    state,
    AgentTurnInput(
        session_id=session_id,
        round_number=round_number,
        user=user_message,
        assistant=assistant,
    ),
    extract_memories,
)
```

`memory_queries` 是 Memory family 的查询组件。通用 `ArtifactCatalog` 不提供 search，因为不同 family 在召回、排序、
过滤和 scope 上有各自语义。`context_scope` 是应用定义的对象，Core 不要求每种 Artifact 都包含 owner 或 session 字段。

## 阶段二：增加 Git Source

Source 扩展不需要修改 `Sources` 或 `PowerContext`。实现新的 Source、输入和 Adapter，并在应用启动时加入
`SourceCatalog` 即可。

```python
@dataclass(frozen=True, slots=True)
class GitCommitInput:
    repository: str
    commit: str
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GitCommitSource(Source):
    repository: str
    commit: str
    summary: str


class GitCommitAdapter(
    SourceAdapter[GitCommitInput, GitCommitSource, GitCommitInput]
):
    input_class = GitCommitInput
    name = "git-commit"
    source_class = GitCommitSource

    async def resolve(self, value: GitCommitInput, /) -> GitCommitSource: ...

    async def read(self, source: GitCommitSource, /) -> GitCommitInput: ...
```

应用组合时同时注册两个 Adapter：

```python
source_catalog = SourceCatalog(
    backend=source_repository,
    adapters=(AgentTurnAdapter(), GitCommitAdapter()),
)
```

Git commit 可以进入独立的 Trigger，也可以和 Agent turn 一起生成 Memory。哪个 Source 参与哪种 Artifact，属于集成策略。
Core 只要求 Artifact lineage 保存实际使用的 Source 对象。

## 阶段三：替换 Trigger

应用通过 typed bundle 选择 Trigger。若替换实现保持相同的 Signal、State 和 Action 契约，只需要替换 bundle 中的对象。

```python
triggers = ContextTriggers(
    agent_memory=EveryAgentRounds(20),
)

context = PowerContext(
    sources=sources,
    artifacts=artifacts,
    triggers=triggers,
)
```

也可以按 session completion、人工确认或 eval result 编写另一个
`Trigger[SourceStored, PendingTurns, GenerateMemory]`。runtime 无需变化。

如果新的 Trigger 改变 State 或 Action 类型，替换范围会更大。对应的 state codec、Action handler 和 owning runtime
需要一起替换。Core 不承诺这些内部零件可以任意组合。

## 阶段四：接入本地数据库和调度器

本节以 SQLite 和 APScheduler 分别作为本地数据库和调度器的具体例子。Core Protocol 不要求使用其中任何一种。

```text
Agent hook or Git hook
    -> write Source
    -> append Signal

APScheduler job
    -> append timer Signal

worker
    -> load Trigger State
    -> Trigger.activate(Signal, State)
    -> commit next State and pending Actions
    -> execute Action
    -> add or revise Artifact
```

本地数据库实现通常包含以下数据。配套 E2E 以 SQLite 实现这一部分。

| 数据 | 写入者 | 读取者 |
| --- | --- | --- |
| Source | Agent hook、Git hook 或其他 adapter integration | Action handler、SourceCatalog |
| Artifact Revision | Memory 或 Handoff handler | family query、ArtifactCatalog |
| pending Signal | hook、外部事件或 scheduler callback | trigger worker |
| 按应用 partition 保存的 Trigger State | trigger worker | trigger worker |
| pending Action | trigger worker | Action handler |

runtime 在 activation 前需要为 Signal 选择对应的 State partition。处理 Signal 时，应在同一事务中保存 next State
和 pending Actions。Action 的 claim、ack、retry 和幂等策略由 runtime 决定。

APScheduler 只负责时间。Cron job 应写入类似 `DailyReviewDue(scope, day)` 的 Signal，再由当前 runtime 使用当前注入
的 Trigger 计算 transition。job 不应自行重建默认 Trigger，否则应用提供的定制策略不会生效。

每日 Handoff handler 可以读取指定 scope 的当日 Memory，构造 `HandoffDraft(artifacts=memories)`，然后写入新的
Artifact 或修订已有 Handoff。Memory 和 Handoff 共享 `Artifacts`，family query 则保持独立。

## 完整调用顺序

一个完整 Agent turn 的顺序如下：

```text
read relevant committed Memory for the current scope
    -> build model request
    -> call model
    -> resolve and add AgentTurnSource
    -> append SourceStored Signal
    -> evaluate Trigger with persisted partitioned State
    -> execute GenerateMemory Action
    -> add zero or more Memory Artifacts with Source lineage
```

每日 Handoff 使用另一条 Signal 和 Action 链路，但继续复用同一个 Source catalog 和 Artifact lifecycle。

## 评审时需要确认的边界

Reviewer 应从下面几个问题检查后续实现：

1. `PowerContext` 是否只组合显式配置的 `Sources`、`Artifacts` 和 typed Trigger bundle。
2. 公开接口是否传递 Source 和 Artifact 对象，而不是 backend id 或 row reference。
3. `Sources.add()` 和 `Artifacts.add()` 是否保持明确的持久化语义，没有隐含模型调用。
4. Trigger 是否保持 sans-I/O，并允许 transition 返回零个或多个 Action。
5. runtime 是否对 Trigger State 分区，同时没有把 partition key 提升为 Core 概念。
6. family query 是否应用具体的 scope，而 worker、scheduler、codec 和事务仍留在具体集成中。

如果实现需要改动这些边界，应先回到 Core Protocol 讨论，而不是在 runtime 中添加隐式约定。
