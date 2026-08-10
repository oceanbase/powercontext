# Core 协议与组合

本文面向扩展 PowerContext 或将其集成进其他应用的开发者，说明当前稳定的 Core 边界。需要直接使用存储和远程服务时，
应采用其他开发文档介绍的 Builtin profile 和 Server。

## 组合根

`PowerContext` 是公开且唯一的组合根，负责绑定选定的 Source、Artifact 和 Trigger 服务：

```python
from powercontext import PowerContext

context = PowerContext(
    sources=source_services,
    artifacts=artifact_services,
    triggers=trigger_services,
)
```

这个对象不发现实现，不打开数据库，不读取环境变量，也不启动 scheduler。这些生命周期决策属于应用入口或 Builtin
profile。Core 只负责组合，因此同一组契约可以用于本地进程、Server 进程，或拥有自身资源生命周期的应用。

## 领域角色

### Source

`Source` 描述 adapter 可以解析或读取的 evidence。Source 子类型使用 Pydantic model，校验和序列化由模型本身负责：

```python
from typing import Literal

from powercontext import Source, SourceMaterialization


class IssueSource(Source):
    provider: Literal["github"]
    repository: str
    number: int


issue = IssueSource(
    name="oceanbase/powercontext#42",
    materialization=SourceMaterialization.REFERENCED,
    provider="github",
    repository="oceanbase/powercontext",
    number=42,
)
```

adapter 持有原生输入、Source 子类型和 `read()` 返回值之间的映射。将 adapter 注册到 `SourceCatalog` 后，调用方不应
再复制 adapter 的身份规则。

Source catalog 会派生 `SourceRef`：

```python
source_ref = source_catalog.as_ref(issue)
```

调用方无需反复填写 `source_type` 和 `source_id`。

### Artifact

`Artifact` 是可复用输出的一个不可变 revision。Artifact family 通过类值声明 family name，结构化内容使用
`BaseModel`：

```python
from typing import ClassVar

from pydantic import BaseModel

from powercontext import Artifact


class NoteContent(BaseModel):
    text: str


class Note(Artifact[NoteContent]):
    family: ClassVar[str] = "note"
```

持久化后的 Artifact 已经包含 identity 和 revision。其他值需要精确引用时，使用 `artifact.as_ref()`：

```python
note_ref = note.as_ref()
```

lineage 保存生成当前 revision 所使用的 Source 和 Artifact 引用。store 负责 revision conflict 和持久化，family
service 负责领域行为。

### Trigger

`Trigger` 是基于 signal 和先前 state 的策略。它返回 `PolicyTransition`，其中包含下一状态和零个或多个 action。
Trigger 不应打开存储、调度自身或执行它选择的 action。

APScheduler 属于 Builtin runtime 的生命周期，负责决定何时评估策略。Trigger 只解释当前 signal 的含义。

## 职责边界

| 关注点 | 所有者 |
| --- | --- |
| 领域模型、引用、协议与组合 | Core |
| Builtin Memory、关系型持久化、索引与 runtime 策略 | `powercontext.builtin` |
| 基于环境变量的进程配置 | `powercontext.client.settings`、`powercontext.server.settings` |
| HTTP 生命周期与可选 MCP transport | `powercontext.server` |
| Provider 相关的生成和 embedding | 推理集成 |
| 数据库和 scheduler 资源生命周期 | 应用入口或 Builtin runtime instance |

Core model 使用 Pydantic `BaseModel`。只有存在真实领域约束时才增加 validator。对于普通存储字段，不需要包装
property；模型和协议已经能表达边界时，也不需要自定义 JSON value 层级或第二套 definition 对象。

## 选择集成路径

应选择能够直接持有所需行为的最小公开层：

- 新增 Source adapter、Artifact family、Trigger 策略或持久化 adapter 时，使用 Core protocol。
- 使用标准 Source 和 Memory 服务时，通过 `open_builtin_runtime()` 选择任一受支持 database。
- 运行带可选 MCP 的标准 HTTP 服务时，采用 `create_server_app()`。

[Memory 文档](memory-layer.md)说明 Builtin Artifact family，
[远程访问文档](remote-access-implementation.md)说明进程配置和 transport。

## 扩展评审

新增抽象前，应确认它至少表示以下一种边界：

- 带真实校验的稳定领域值；
- 存在多个实用实现的行为；
- 资源或生命周期边界；
- 用户可以感知的能力。

只代理单个实现的别名、重复的配置模型，或只返回已有字段的 property，都不构成有用的边界。
