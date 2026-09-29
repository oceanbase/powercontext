---
title: 常见问题
description: 关于 Memory、Handoff、Experience、Skill 与知识生命周期的常见问题。
---

# 常见问题

本页回答学习 PowerContext 时经常遇到的问题。更系统的概念介绍见[核心概念](./core-concepts.md)；具体操作步骤请
参阅相应的工作流指南。

## 四类 Artifact 速览

**问：我经常看到 Memory、Handoff、Experience 和 Skill，应该分别在什么时候用？**

这四个 family 在同一个工作循环里扮演不同角色。想象一个 Agent 在修复 `amount.py` 中的 CSV 解析 bug：

| Family | 在修复 bug 过程中扮演的角色 | 适用场景 |
| --- | --- | --- |
| **Memory** | 保存长期项目约定："金额以整数分存储；超过两位小数的输入必须拒绝。" | 跨越多个会话与 Agent 都应成立的事实、决策或规则。 |
| **Handoff** | 记录"我已确认 bug 在 `cents()` 函数里；明天由下一个 Agent 跑失败的测试并打补丁。" | 把未完成工作的当前状态交接给另一个 Agent 或会话。 |
| **Experience** | 记录已经验证的教训："直接调用 `int(Decimal(text) * 100)` 把 `1.999` 静默截断成了 `199`；现在我们在转换前先校验精度。" | 保留基于证据的情境教训。 |
| **Skill** | 沉淀可复用流程："如何验证金额转换修复——运行测试夹具、检查边界、记录结果。" | 供其他 Agent 加载并遵循的、已验证的可复用配方。 |

一次 bug 修复通常会同时产生这四类内容：要遵守的约定（Memory）、移交的进展（Handoff）、学到的教训
（Experience）、可复用的做法（Skill）。

详见 [Memory 与 Handoff](../workflows/memory-and-handoff.md) 以及
[Experience 与 Skill 的生命周期](../workflows/experience-and-skill-lifecycle.md)。

## Source、Candidate 与知识流水线

**问：Source、Candidate 和已批准的 Artifact 是一条强制流水线吗？**

不是。它们是可以组合的独立概念，但捕获证据本身不会产生已批准的知识：

```text
Source（证据）──┐
                ├──→ Candidate（提案）──→ Review ──→ 已批准 Artifact
已有 Artifact ──┘
```

- **Source** 是原始证据——一轮对话、一次工具结果、一份文档。
- **Candidate** 是从证据或已有 Artifact 派生的提案。在审核前处于 `pending` 状态。
- **Artifact** 是审核批准后得到的产物——带有稳定引用的不可变 Revision。

也可以通过 `remember_memory` 直接写入 Memory，跳过 Source 提取和 Candidate 审核。

**问：pending 状态的 Candidate 会被 `PreparedContext` 召回吗？**

不会。pending Candidate 不参与召回。只有已批准的当前 Revision 才能进入 `PreparedContext`。这样可以防止
未经审核的模型输出悄悄进入 Agent 的工作上下文。

**问：我批准了一个 Skill Candidate，为什么 Agent 还用不了？**

批准、安装和执行是三个独立步骤：

1. **批准**在 PowerContext 中创建一个不可变的 Skill Revision。
2. **导出 / 安装**把指定 Revision 复制到 Agent 宿主（例如通过 Remote Skill 分发，或手动调用
   `download_skill_package`）。
3. **执行**发生在 Agent 宿主发现并加载已安装的 `SKILL.md` 之后。

批准 Skill 不等于安装它；安装它也不等于执行它。每一步都是显式的，以便你掌控"什么内容在什么地方运行"。

## Memory

**问：Source 和 Memory 有什么区别？**

- **Source** 是原始证据：一条聊天消息、一份文件、一段 HTTP 调用记录。按原样保存。
- **Memory** 是经过提炼的知识：一个决策、一条约束、一项事实。它是被刻意写入的（由应用显式写入，或由经过
  批准的提取流程产生）。

Source 是输入；Memory 是策展后的结果。

**问：我通过通用 Artifact API 创建了 Memory，但 `search_memory` 找不到，为什么？**

两条写入路径的效果不同：

| 路径 | 结果 |
| --- | --- |
| `POST /v1/scopes/{id}/artifacts`，`family=memory` | 创建独立的 Memory Artifact；**不参与**日常召回。 |
| `POST /v1/memory/remember` | 写入 Scope 的默认 Memory；**可以被** `search_memory` 检索，并被 `prepare_context` 使用。 |

如果你希望 Agent 真正能召回这条事实，请使用 `/v1/memory/remember`。

**问：修订 Memory 时旧版本会被删除吗？**

不会。Revision 是不可变的。修订 Memory 会创建一个新 Revision，并把旧 Revision 从活跃召回中退役；但旧
Revision 仍可通过 `GET .../revisions/{n}` 读取。这样既能保留审计轨迹，也能在需要时恢复早期措辞。

## Handoff

**问：Handoff 是手动创建的还是 Agent 自动创建的？**

都可以。当人类判断需要移交工作时，应用可以直接调用 `handoff_current_work`；Agent 也可以在其工具流程中
提出 Handoff。两种情况下的流程相同：先返回一份临时预览，只有 `commit_handoff` 才会写入持久的 Revision。

**问：接收方 Agent 怎么知道要做什么？**

Handoff 自身带有结构化字段：目标、带证据引用的当前状态、处置方式（`continuable` / `complete`）、下一步
动作以及已声明的遗漏。接收方 Agent 阅读的是这份内容，而不是完整的先前会话。

## Experience 与 Skill

**问：Experience 和 Skill 有什么区别？**

- **Experience** 是一段经过验证的叙事：情境 → 行动 → 结果 → 教训。它告诉后来的读者*当时发生了什么、学到
  了什么*。
- **Skill** 是一个可复用流程：名称、描述、指令、验证步骤。它告诉后来的 Agent *该怎么做*。

常见模式是：先有一条已批准的 Experience，再基于它生成 Skill Candidate，审核通过后才成为可安装的
Skill。

**问：PowerContext 会自动安装 Skill 吗？**

不会。Skill 作为 Artifact 存储在 PowerContext 中。要让 Agent 能使用它，应用（或 Remote Skill Receiver）必
须显式下载并安装到 Agent 的工作目录。

## Middleware、工具与 MCP

**问：教程里经常看到“Middleware”，它是什么？**

Middleware 在 Agent 的模型或工具调用前后增加行为。PowerContext 的 LangChain 适配器
`PowerContextMiddleware` 使用 LangChain 的公开中间件 API，在调用模型前根据最近一条非空用户消息准备有界上下文。
召回返回内容时，它会把标记为不可信历史的上下文块加入当次模型请求，而不替换 Agent 的核心循环。

**问：什么时候用 Middleware，什么时候用 MCP 工具？**

先区分自动召回与显式工具调用，再选择工具的接入方式：

| 接入方式 | 行为与连接方式 |
| --- | --- |
| **LangChain Middleware** | 在符合条件的模型调用前通过 HTTP 请求上下文，不必等待模型主动请求搜索。 |
| **LangGraph 工具** | `powercontext_tools()` 提供原生 LangChain 工具，通过 Python HTTP Client 显式操作 Memory，不涉及 MCP 连接。 |
| **MCP 工具** | 配置好的 MCP 客户端发现并调用 Server 暴露的工具；宿主应用可以直接调用，也可以将其提供给模型。 |

例如，Middleware 可以提供 CSV 项目的金额约束，显式工具则保存用户新确认的规则。通过工具访问 Memory 不一定需要
MCP；MCP 是协议，不决定由谁触发操作。安装和可用操作详见 [LangChain](../integrations/langchain.md)、
[LangGraph](../integrations/langgraph.md) 和[选择接口](../develop/interfaces.md)。

**问：接入 PowerContext 需要重写我的 Agent 吗？**

使用现有的 LangChain 集成不需要重写。保留模型与应用工具，安装适配器，并配置它与运行中的 PowerContext Server 的连接。
下面的接线示例假设 `model` 和 `application_tools` 已初始化，`server_url` 是 Server 的 HTTP 基础地址，
`scope_id` 是你有权访问的既有 Scope，`token` 是裸 Bearer token（无需认证时为 `None`），`question` 是当前用户输入。
两段示例均要求远程 Server 使用 HTTPS；明文 HTTP 仅用于本机回环地址。启用访问控制时，调用者还需要解析 Scope 和执行
所请求的 Memory 操作的权限，详见 [Scope 与访问控制](../workflows/scopes-and-access.md)。Agent 调用使用异步 API：

```python
from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware, PowerContextScope

agent = create_agent(
    model,
    tools=application_tools,
    middleware=[PowerContextMiddleware()],
    context_schema=PowerContextScope,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": question}]},
    context=PowerContextScope(scope_id=scope_id, base_url=server_url, token=token),
)
```

这个 Scope 对象配置的是 LangChain 中间件，不是独立的 LangGraph 工具。值为 `None` 的字段会回退到中间件的配置；
使用无需认证的 Server 时，应同时省略其中的 token 配置。

**问：Middleware 注入的内容会污染会话历史吗？**

注入的上下文块只修改当次模型请求，中间件不会将它追加到 Agent 状态或 checkpointer 中。后续模型调用可以根据最近的用户
消息再次请求上下文。普通对话和工具消息仍由应用的历史策略管理；如果助手在回答中复述了某条记忆，这段回答仍可能保留在
历史中。可选的完整回合 Source 采集是另一项默认关闭的功能，详见 [LangChain 召回与采集生命周期](../integrations/langchain.md)。

**问：MCP 是什么？它解决了什么问题？**

MCP（Model Context Protocol，模型上下文协议）统一了兼容客户端发现和调用 Server 工具的方式。当宿主支持 MCP，且你希望
使用该工具接口时，可以连接 PowerContext 的 MCP 端点。宿主仍需为相关操作配置连接、认证和 Scope。
`powercontext_tools()` 不是 MCP 客户端：其中的 Memory 工具调用 `/v1/memory/remember` 等 HTTP 端点，关闭 MCP 后
也能工作。MCP 工具目录是单独精选的接口，并不等于全部 HTTP API，详见[选择接口](../develop/interfaces.md)。

**问：可以同时使用 Middleware 和显式 Memory 工具吗？**

可以，但必须对齐两者的配置。下面组合的是 **LangChain Middleware 与基于 HTTP 的 LangGraph 工具**，不是 MCP 工具。
两个包各自定义了 `PowerContextScope` 类；LangGraph 工具不识别 LangChain 的 Scope 对象，会回退到
`POWERCONTEXT_LANGGRAPH_*` 配置。因此，传入 LangChain 包的 `PowerContextScope(scope_id=...)` 并不会配置工具写入的位置。

对于只处理一个 Scope 的应用进程，在启动 Agent 前，用同一组值设置两套集成的连接、Scope 和 token。
示例沿用上文的 `model`、`server_url`、`scope_id`、`token` 和 `question`，有意不传入任何一个包的 Scope 对象：

```python
import os

from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware
from powercontext_langgraph import powercontext_tools

connection = {"BASE_URL": server_url, "SCOPE_ID": scope_id, "TOKEN": token}
for prefix in ("POWERCONTEXT_LANGCHAIN", "POWERCONTEXT_LANGGRAPH"):
    for key, value in connection.items():
        name = f"{prefix}_{key}"
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

agent = create_agent(
    model,
    tools=powercontext_tools(),
    middleware=[PowerContextMiddleware()],
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": question}]},
)
```

凭据应来自应用的密钥配置，不要硬编码 token。环境变量是进程级配置，应在启动时设置一次，不能在并发处理多个 Scope 的
应用中逐请求改写。不要假设之后覆盖 LangChain Scope 就会同时改变工具的目标。应分别核对召回的 Scope 与最终保存条目的
Scope。配置详情见 [LangChain 连接与 Scope 设置](../integrations/langchain.md) 和
[LangGraph 连接设置](../integrations/langgraph.md)。

## 导入与派生外部 Skill

**问：我已经有一个 Skill 包，应该选择 import 还是 fork？**

假设团队已有一个 CSV 金额检查包，包含 `SKILL.md`、检查脚本和参考说明。根据你想保留现有包，还是提出不同的操作指令，
选择相应模式：

| 模式 | 适用情况 | 实际行为 |
| --- | --- | --- |
| **`import`** | 希望将现有包纳入管理，而不改写其中的文件。 | Runtime 直接根据捕获并校验过的包提出候选，保留文件路径和字节内容，无需生成模型。 |
| **`fork`** | 希望基于外部包生成适合当前项目的操作指令。 | 配置好的生成器以捕获的包为依据提出新建议，不保证生成的包保留原来的脚本或资源。 |

两种模式都不会修改外部原包。返回的 Candidate 在 Review 批准前保持 pending，批准后才创建新的 managed Skill Artifact；
fork 也可能返回 `no_op`，不产生 Candidate。这里的 fork 指生成 Skill 提案，不是创建 GitHub 仓库的 Fork。
接口说明见[外部 Agent-native Skill](../develop/interfaces.md)，目标目录配置见[在 Agent 中安装 Skill](../workflows/configure-agent-skill-targets.md)。

**问：导入后再修改外部文件，managed Skill 会自动更新吗？**

不会。外部 registration 通过 fingerprint 标识扫描时的包，已批准的 managed Skill Revision 则保存导入时的快照。
即使 `SKILL.md` 没变，修改脚本或参考文件也可能改变 fingerprint。此时解析旧 fingerprint 会返回 `unavailable`，
使用它导入会被拒绝，而不是静默选用变化后的包。需要重新扫描，并明确选择新的 fingerprint 才能导入新内容；
这不会自动替换之前批准的 Skill。

外部 registration 不可用，不等于已批准的 managed Revision 被删除。你仍可以精确读取或下载那个 Revision。
例如，在外部 CSV 检查包的参考说明中补充非有限值规则，不会使这条规则自动进入已导入的副本。

**问：fork 已经返回 Candidate，它现在就能替代原来的包吗？**

不能仅凭这一点判断。从原包捕获 Source 证据，不代表生成的提案中包含了原来的脚本和资源。应检查候选包，包括指令中的命令
是否引用了包内实际存在的文件。在 CSV 示例中，增加“检查非有限值”的指令，不等于检查脚本已经被保留、更新或执行。

包校验和摘要匹配能确认结构与内容身份，不能证明它在你的输入上行为正确。在依赖它之前，应审核提案，并在已获授权的隔离
工作区中验证预期任务。批准、导出或安装、实际执行仍然是不同步骤，详见[审核 Candidate](../workflows/review-candidates.md)
和 [Experience 与 Skill 生命周期](../workflows/experience-and-skill-lifecycle.md)。

## 还有疑问？

- 概念模型请见[核心概念](./core-concepts.md)。
- 端到端流程请见[工作流](../workflows/index.md)章节。
- API 细节请见 `openapi/powercontext.yaml`。
- 如果本页内容不清楚或有误，欢迎[提出 Issue](https://github.com/oceanbase/powercontext/issues/new) 帮助
  我们改进。
