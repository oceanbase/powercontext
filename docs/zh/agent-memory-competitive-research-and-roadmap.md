# AI Agent 记忆与上下文运行时竞品调研：PowerContext 差异化与近期路线

日期：2026-08-25

## 执行摘要

AI Agent Memory 已经从向量检索和对话摘要中分化为独立基础设施赛道。当前开源项目覆盖通用 Memory SDK、Context
Database、团队 Memory Hub、时序知识图谱、Coding Agent 项目记忆和 Stateful Agent Runtime 等多种形态，尚未形成统一
产品范式。

PowerContext 不适合把自己重新定义为更大的 Memory Database，也不应近期追随完整知识图谱、内建 Agent Runtime、全流量
LLM Proxy 或团队 ACL 平台。它已经形成一条更清晰、也更少见的产品主线：

> 把人和 Agent 共同推进的工作变成有证据、可交接、可接收、可记录结果、可审核复用的上下文。

PowerContext 当前最有差异化的不是单项 Handoff API，而是以下组合：

1. Source 与 exact citation 保留原始依据；
2. Memory 和 Artifact 使用保留历史的 Revision；
3. PreparedContext 在请求时按 Scope、相关性和字节预算有界组装；
4. Handoff 绑定 exact Revision 和 exact evidence；
5. PowerContext 重新解析 exact Evidence；
6. 宿主记录接收方对 Workspace、能力和授权状态的非可信自我观察，并对 exact Revision 留下 Acknowledge；
7. Task Outcome 区分成功、部分完成、阻塞、失败、取消和未知；
8. Outcome 只能生成 Candidate，必须经过 Review 才能成为长期 Experience 或 Skill；
9. 批准、发布、安装和执行保持独立权限边界。

CommonGround 和 Statewave 等项目已经提供 Handoff 或 Handoff Pack，因此不应宣传“PowerContext 是唯一有 Handoff 的产品”。
PowerContext 更准确的差异化表述是：

> Evidence-grounded、exact-revision、acknowledgement-aware、outcome-covered 的工作连续性运行层。

未来 8–12 周的推荐路线不是继续横向扩展概念，而是依次完成：

1. 关闭 Memory、Runtime、网络和 Schema 主路径上的已知可靠性问题；
2. 让 Server 能够常驻运行，并通过 Setup 和 Doctor 清楚说明每个 Agent Host 是否真正接入；
3. 把 Work Contract、Handoff、Acknowledge、Task Outcome 和 Review 做成跨 Host 一致的产品闭环；
4. 增加可选 Context Receipt 和 Context Inspector，解释每次 PreparedContext 为什么选择或省略某项内容；
5. 完成一个 Host 历史导入切片和一个外部可变 Source 的精确 Observation Pilot；
6. 使用 LongMemEval-V2 与 PowerContext 原生 Work Continuity Scenario Pack 公开验证准确率、延迟、上下文预算和交接可靠性。

## 1. 调研范围与证据边界

### 1.1 调研问题

本调研回答四个问题：

1. PowerContext 实际处在哪一条竞争赛道；
2. 哪些项目是产品中心相近的直接竞品，哪些只是算法或架构参照；
3. 哪些竞品特性能够在不破坏 PowerContext 治理内核的前提下复用；
4. 未来 8–12 周应优先展开哪些可验证工作。

### 1.2 主要证据

PowerContext 事实以当前仓库、公开 RFC、OpenAPI、测试、Issue 和 PR 为准：

- [PowerContext README](https://github.com/oceanbase/powercontext#readme)
- [产品定义与愿景 RFC](rfcs/0001_product_definition_and_vision.md)
- [Context Pack RFC](rfcs/0028_context_pack.md)
- [Handoff Artifact RFC](rfcs/0048_handoff_artifact.md)
- [Experience 和 Skill RFC](rfcs/0051_experience_skill_artifact_families.md)
- [Handoff Report RFC](rfcs/0082_handoff_report.md)
- [工作连续性 RFC](rfcs/1223_human_agent_work_continuity.md)
- [统一 Workload 与长周期评测 RFC](rfcs/1229_unified_workloads_and_long_horizon_memory_evaluation.md)
- [本地 Server 常驻服务 RFC](rfcs/1299_local_server_availability_and_service_installation.md)

竞品事实优先使用官方仓库、官方文档、官方论文和可查看的实现目录。Awesome-Agent-Memory 用于发现候选，不作为产品质量
排名依据：

- [Awesome Agent Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory)

### 1.3 置信度说明

以下结论置信度较高：

- 项目公开定位；
- 官方 README 明确列出的能力；
- 仓库中可见的模块、接口、集成和部署方式；
- PowerContext 当前代码、RFC、Issue 和 PR 状态。

以下结论仅作为方向信号：

- 项目自行公布的 Benchmark 成绩；
- Star 增长、融资和社区规模；
- 尚未发布的 Roadmap；
- “生产级”“行业第一”“显著领先”等营销表述。

本调研没有复现竞品 Benchmark，不使用竞品自报分数决定 PowerContext 的工程优先级。

## 2. PowerContext 当前定位

[PowerContext README](https://github.com/oceanbase/powercontext#readme) 将项目定义为面向人机协作的 Context Runtime，而不是单一 Memory SDK。当前公开能力
包括：

- Memory 抽取、显式写入、修订和停用；
- 有 Scope、有 Citation、有字节预算的 PreparedContext；
- Source 与 Evidence Lineage；
- Prepared、Committed 和 Continue Handoff；
- Work Contract、Current Work Handoff、Handoff Receipt 和 Task Outcome；
- Candidate、Review、不可变 Artifact Revision；
- Experience 和 Skill 的生成、审核与显式导出；
- SQLite 本地部署、OceanBase 团队部署、HTTP/OpenAPI、MCP、认证和 OpenTelemetry；
- Codex、Claude Code、DeepSeek Harness、Hermes、Pi、OpenClaw 等 Agent Host 接入，以及仍在审查中的其他 Host 扩展。

产品对象不是孤立的 Memory、Trace、Artifact 或 Workflow，而是人和 Agent 共同推进的工作。最小产品闭环可以表达为：

    Source
      -> Memory / PreparedContext
      -> Work Contract
      -> Handoff
      -> Acknowledge
      -> Task Outcome
      -> Experience Candidate
      -> Human Review
      -> Approved Experience / Skill
      -> Later PreparedContext

这里有四个不能混淆的状态边界：

- Handoff accepted 不等于任务完成；
- Outcome covered 不等于 Outcome succeeded；
- Task Outcome 不等于可复用 Experience；
- Skill approved 不等于已发布、已安装或可执行。

这些边界是 PowerContext 相比“自动摘要并回注”的 Memory 产品更重要的设计资产。

## 3. 赛道全景

### 3.1 通用 Memory SDK 和服务

代表项目：

- [Mem0](https://github.com/mem0ai/mem0)
- [Supermemory](https://github.com/supermemoryai/supermemory)
- [Memobase](https://github.com/memodb-io/memobase)

产品中心是简单 Add/Search API、用户画像、会话状态、个性化和托管服务。优势是 SDK 易用、接入成本低和生态广。弱点是通常
不处理完整工作状态、接收确认、Outcome 和治理闭环。

### 3.2 Context Database 和 Memory OS

代表项目：

- [OpenViking](https://github.com/volcengine/OpenViking)
- [MemOS](https://github.com/MemTensor/MemOS)
- [Statewave](https://github.com/smaramwbc/statewave)

产品中心是统一管理多种 Context、分层加载、混合检索、上下文组装和长期演进。优势是能力完整、适合构建上层 Agent。风险是
容易把产品中心变成“存储更多上下文”，而不是“工作如何被接手”。

### 3.3 团队 Memory Hub

代表项目：

- [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)
- [MemClaw](https://github.com/caura-ai/caura-memclaw)

产品中心是 Team、User、Agent、Memory Asset、ACL、Loadout 和控制面。优势是资产可见性和团队装配，风险是身份、授权、
多租户和运维复杂度较高。

### 3.4 时序 Context Graph

代表项目：

- [Graphiti](https://github.com/getzep/graphiti)
- [Cognee](https://github.com/topoteretes/cognee)

产品中心是实体、关系、事实有效时间、来源 Episode 和图检索。优势是表达动态事实和关系，风险是图构建成本、模型依赖和运维
复杂度。

### 3.5 Coding Agent 项目记忆

代表项目：

- [Claude-Mem](https://github.com/thedotmack/claude-mem)
- [agentmemory](https://github.com/rohitg00/agentmemory)
- [Memorix](https://github.com/AVIDS2/memorix)
- [projectmem](https://github.com/riponcm/projectmem)
- [ByteRover](https://github.com/campfirein/byterover-cli)

产品中心是自动采集 Coding Agent Session、项目范围检索、Git/代码知识、失败经验和跨工具记忆。它们与 PowerContext 争夺
同一批早期用户，特别值得关注安装、诊断、Viewer、Hook、MCP 工具数量和跨 Agent 支持。

### 3.6 工作连续性和共享工作记录

代表项目：

- [PowerContext](https://github.com/oceanbase/powercontext)
- [CommonGround](https://github.com/Intelligent-Internet/CommonGround)
- [MyContext](https://github.com/openTrinity/mycontext)

产品中心是工作上下文、公共工作记录、Evidence、Handoff、参与者接续和人类控制。这是 PowerContext 最应占据的赛道。

## 4. 重点竞品

### 4.1 OpenViking

[OpenViking](https://github.com/volcengine/OpenViking/blob/main/README_CN.md) 将 Memory、资源和 Skill 统一放入 viking://
虚拟文件系统。Agent 通过 ls、tree、find 等确定性操作浏览 Context。内容被处理为 L0 摘要、L1 概览和 L2 详情，检索可以
先定位目录再向下探索，并保存浏览轨迹。

值得借鉴：

- 摘要、概览、详情的渐进式披露；
- 检索路径可查看；
- 统一 Context 浏览器；
- Setup Helper、Agent 检测和运行诊断；
- 索引重建、快照和导入导出等运维面；
- Studio 中可直接观察 Session、Memory 和检索结果。

不应照搬：

- 不把 Source、Memory、Handoff、Experience 和 Skill 全部退化为文件系统对象；
- 不让 viking:// 一类路径成为新的 PowerContext 身份体系；
- 不把自动自演进置于 Candidate 和 Review 之前；
- 不直接复制 AGPL-3.0 实现。

OpenViking 的 Benchmark 成绩为官方自报，本调研不视为已复现证据。

### 4.2 TencentDB Agent Memory

[TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory#readme) 将 Chat Memory、Skill、Wiki 和
CodeGraph 统一为 Memory Asset，并提供 Team、Agent、Task、Owner、版本、状态、可见性、ACL 和 Agent Loadout。

值得借鉴：

- 资产不是匿名搜索结果，而是有 Owner、版本和状态的对象；
- 冷启动入口清楚：导入代码、文档和历史 Session；
- Human Control Panel 能审核、分享、装配和撤销；
- Agent 只获得与当前角色和任务相关的资产；
- Skill 的版本、资源、触发条件、执行步骤和验证规则在 UI 中可见。

近期不应照搬：

- Team/User/Role/Agent 四级 ACL；
- 用 LLM Proxy 作为 PowerContext 主要接入路径；
- 内建 Wiki 和 CodeGraph；
- 自动给 Agent 装配未审核资产。

PowerContext 当前 Scope 不是 ACL，Project/Workstream 语义仍在讨论。正式访问控制需要独立的 Authentication/Authorization
设计，不能通过给 Artifact 添加 visibility 字段完成。

### 4.3 MyContext

[MyContext](https://github.com/openTrinity/mycontext#readme) 是本地优先的个人工作 Context Layer。它连接即时通讯、文档、
会议和其他工作来源，形成个人 Context Graph，并提供搜索问答和数字分身。Agent 不可用时，搜索会降级为本地排序结果，
而不是生成无依据答案；发送等有后果的操作需要明确授权。

值得借鉴：

- Source Connector 与 Context 消费解耦；
- 人物、项目、事件、会话和 Evidence 的可浏览关系；
- 首次启动时选择数据源、范围和隐私；
- 对失败和降级行为给用户明确反馈；
- AI 是 Context 使用者，不是 Context 所有者；
- 有实际后果的操作始终需要显式确认。

不应照搬数字分身和通信代理路线。它会把 PowerContext 从协作基础设施推向个人助手产品。MyContext 当前处于 Developer
Preview，并使用 Elastic License 2.0。

### 4.4 Claude-Mem

[Claude-Mem](https://github.com/thedotmack/claude-mem#readme) 使用 Agent 生命周期 Hook 自动采集工具调用和 Session
Observation，生成摘要，再注入后续 Session。它提供三段式检索：

1. Search 返回紧凑索引和 ID；
2. Timeline 返回命中点周围的时间上下文；
3. Get Observations 只读取选中 ID 的完整内容。

值得借鉴：

- 索引、时间线、详情的渐进式查看；
- 实时 Web Viewer；
- 一条命令完成安装、Hook 和 Worker；
- Privacy Tag 和不保存控制；
- 用户可以看到系统保存了什么、何时保存、如何被召回。

PowerContext 的证据、Revision 和 Review 更严格，但目前的产品反馈不如 Claude-Mem 直接。

### 4.5 agentmemory

[agentmemory](https://github.com/rohitg00/agentmemory#readme) 是当前最需要跟踪的 Coding Agent Memory 竞品之一。它提供
交互式安装、多个 Agent Adapter、Keyless BM25、可选本地 Embedding、Viewer、OpenTelemetry、跨 Agent 共享或隔离范围，
以及 Core/All 两档工具集合。

值得借鉴：

- 一条命令进入交互式 Setup；
- 没有模型密钥时仍有明确可用的 Keyword Recall；
- 自动发现和连接 Agent；
- Viewer、Log、Metric、Trace 组成完整诊断面；
- Windows、macOS 和 Linux 的数据目录、端口和卸载边界清楚；
- Agent 工具可按 Core 和 Full Profile 控制可见性；
- 同一个 Server 为多个 Agent Host 提供共享 Memory。

不应照搬：

- 默认向 Agent 暴露几十个 MCP Tool；
- 大量 REST Endpoint 和本地端口；
- 将 Auto Compression、Reflection 和 Consolidation 默认变成长期事实；
- 在缺少 PC 治理边界的情况下直接引入 Team Scope。

agentmemory 对 PowerContext 的直接压力是工程易用性和即时可见性，不是 Handoff 语义。

### 4.6 Memorix

[Memorix](https://github.com/AVIDS2/memorix#readme) 提供 Observation、Reasoning、Git、Code 和 Curated Long-term Memory，
并基于任务生成 Workset。它还覆盖 Setup、Doctor、Dashboard、Hooks、Skills、MCP 和多 Agent 协作。

值得借鉴：

- Task-lensed Workset，而不是每次返回同一类上下文；
- Workset 显示起始文件、已有知识、风险和验证建议；
- Git Memory 回答“改了什么、为什么重要”；
- Code Memory 显示 Freshness，不把陈旧索引当作当前事实；
- MCP 提供 Micro、Lite、Team、Full 等工具 Profile；
- Setup 和 Doctor 明确区分不同 Host 的真实能力。

不应跟进其 Orchestration、Worktree 管理、媒体生成和内置 Coding Agent。PowerContext 应继续作为独立 Context Runtime。

### 4.7 Statewave

[Statewave](https://github.com/smaramwbc/statewave#readme) 使用 Ingest、Compile、Retrieve、Govern 流程，把 Event 编译为
结构化 Memory，再组装成受 Token 预算约束的 Context Bundle。每个 Bundle 可以带 Provenance 和 Assembly Receipt。

这是 PowerContext 最值得借鉴的一个机制。

PowerContext 当前 PreparedContext Builder 已经知道最终选中的 exact origins，并有候选数、条数、单项字节和总字节预算，
但公开 PreparedContext 主要暴露 Status、Content 和 Content Bytes。可选 Context Receipt 可以记录：

- Context Policy Version；
- Query Digest，而不是原始 Query；
- 最大预算和实际使用量；
- 选中的 exact Memory/Experience 引用；
- 省略数量和省略原因；
- Keyword、Vector、Rerank 和 Fallback 的实际状态；
- 各阶段延迟；
- 最终注入内容 Digest；
- LLM Rerank 的模型、配置和非确定性标记。

Receipt 默认不持久化，不改变当前注入正文，不把 PowerContext 变成 Trace 存储系统。Evaluation Harness 和用户显式操作可以
保存它。

### 4.8 CommonGround

[CommonGround](https://github.com/Intelligent-Internet/CommonGround#readme) 强调 durable public work records、Turn
边界、Handoff、因果关系和 pull-first recovery。它认为真正需要共享的不是 Agent 私有内部状态，而是未来参与者能够恢复工作
的公共事实。

值得借鉴：

- Agent 私有状态和公共工作记录分离；
- Parent/Child Work、Completion、Observation 和 Absorption 的因果关系；
- 接收方主动拉取和检查；
- Kernel 不拥有所有 Workflow、Runtime 和业务决策。

CommonGround 当前仍是 Preview，并明确说明自己 memory-ready、not memory-complete。PowerContext 已经拥有更完整的
Memory、Artifact、Review 和 Handoff 生命周期，因此不需要追随其实体模型。

### 4.9 projectmem

[projectmem](https://github.com/riponcm/projectmem#readme) 使用 Append-only Event Log 保存 Issue、Attempt、Fix 和
Decision，并在修改文件或提交前提示以前失败过的方案。它还把 Plan 与 Event History 分离：

- Plan 表达准备做什么；
- Event 表达实际发生了什么。

PowerContext 已经能从失败、超时、跳过和未知的 Task Outcome 中生成 Experience Candidate，但缺少面向用户的失败经验通道。
可以借鉴：

- 对当前任务匹配到的失败 Experience 使用 Caution Lane 展示；
- 明确说明失败发生在什么条件下；
- 关联 exact Task Outcome Evidence；
- 不把失败经验和普通 Memory 混成相同权重的事实；
- 先提示，不近期引入强制 Pre-commit 阻断。

## 5. 算法与架构观察组

### 5.1 MemOS

[MemOS](https://github.com/MemTensor/MemOS#readme) 把 Memory 抽象为可组合 Memory Cube，并探索 L1 Trace、L2 Policy、
L3 World Model 和 Skill 演进。它还提供自然语言反馈纠错、混合检索、本地 Plugin 和 Viewer。

对 PowerContext 的主要启发是：

- Trace、Policy、World Model 和 Skill 应保持不同生命周期；
- Experience 可以表达失败或成功的可复用判断；
- 多个受治理 Artifact 可以组成 Task-specific Context；
- 自动演进只能生成 Candidate，不能绕过 Review；
- 用户反馈应形成有来源、有历史的修订，而不是静默覆盖。

MemOS 自报 Benchmark 未在本调研中复现。

### 5.2 Mem0

[Mem0](https://github.com/mem0ai/mem0#readme) 的优势是简单 API、多层 Scope、Hybrid Retrieval、Entity Linking、Temporal
Reasoning 和托管服务。它是 SDK 易用性和通用集成的重要参照。

PowerContext 可以借鉴：

- API 入口简洁；
- Keyword、Semantic 和 Entity Signal 融合；
- Current/Past/Future 查询的专门评测；
- 明确区分 Library、Self-hosted 和 Managed Profile。

不应把 ADD-only 作为治理语义。ADD-only 可以是某个抽取或 Ingestion 策略，但不能替代 PowerContext 的 Revise、Retire
和 exact Revision。

### 5.3 Graphiti

[Graphiti](https://github.com/getzep/graphiti#readme) 的强项是 Temporal Context Graph：

- Fact 有有效时间；
- Episode 保留原始来源；
- 新事实可以使旧事实失效，但历史仍然可查询；
- Semantic、Keyword 和 Graph Traversal 融合；
- 可以表达 Current Truth 和 Historical Truth。

PowerContext 当前有 Revision History，但尚未定义完整 Bitemporal Memory。近期应先通过 LongMemEval-V2 和专门的
Current/Past/Supersession Scenario 确认真实缺口，再决定是否设计 Temporal Memory RFC。

简单增加 current_state、past_event、future_plan 标签不足以形成可靠时间语义。完整设计至少需要区分：

- 现实世界有效时间；
- 系统观察时间；
- 修订或停用时间；
- 新事实是否否定旧事实；
- 当前状态查询与历史状态查询；
- Source 冲突和不确定性。

### 5.4 Letta、Talamus 和 Panella

[Letta](https://github.com/letta-ai/letta) 更接近拥有 Memory 的 Stateful Agent Runtime。它的 Agent 自编辑 Memory 和
Sleep-time 整理适合作为反方参照：连续性可以被内化到单一 Runtime，而不是显式 Handoff。

[Talamus](https://github.com/ampres-ai/talamus) 值得观察 Bitemporal History、As-of Query、Citation 和人工纠错。

[Panella](https://github.com/panellatech/panella) 值得观察 Propose-only Agent Write、独立 Approver Credential 和
Receipt-gated Durability。

PowerContext 不需要复制这些项目，但它们可以校验当前治理边界是否足够清晰。

## 6. 能力横向比较

下表中的“强”“中”“弱”只描述公开产品覆盖，不代表总体质量或 Benchmark 排名。

| 项目 | 产品中心 | Evidence/History | 有界 Context | Work Continuity | Human Governance | 安装与可见性 |
| --- | --- | --- | --- | --- | --- | --- |
| PowerContext | 人机工作 Context Runtime | 强 | 强，解释面不足 | 强，闭环最完整 | 强 | 中 |
| OpenViking | Context Database | 中 | 强，分层和轨迹突出 | 弱 | 中 | 强 |
| TencentDB Agent Memory | Team Memory Hub | 中到强 | 强 | 弱 | 强，偏访问控制 | 强 |
| MyContext | Personal Work Context | 强 | 中 | 中，偏个人工作流 | 强，操作授权突出 | 强，Desktop 优先 |
| Claude-Mem | Session Memory | 中 | 强，渐进查看突出 | 弱 | 弱到中 | 强 |
| agentmemory | Coding Agent Memory Runtime | 中 | 强 | 弱到中 | 中 | 强 |
| Memorix | Coding Project Memory | 中到强 | 强，Task-lensed | 中 | 中到强 | 强 |
| Statewave | Compiled Context Bundle | 强 | 强，Receipt 突出 | 中 | 强，Policy 突出 | 中 |
| CommonGround | Shared Work Records | 强 | 弱到中 | 强 | 中 | 中 |
| projectmem | Event-sourced Coding Memory | 强 | 中 | 中 | 中 | 中到强 |
| MemOS | Self-evolving Memory OS | 中 | 强 | 弱 | 中，自动演进更强 | 强 |
| Mem0 | Universal Memory API | 弱到中 | 强 | 弱 | 弱到中 | 强 |
| Graphiti | Temporal Context Graph | 强 | 强 | 弱 | 中 | 中 |

PowerContext 当前相对领先：

- exact Source Citation；
- Revision 和 Retire 保留历史；
- Handoff exact selection；
- Acknowledge 与 Receiver Checks；
- Task Outcome 的精确状态；
- Candidate、Review、Approval、Publication 和 Execution 分离；
- Recall 失败不阻断原任务。

PowerContext 当前相对落后：

- 安装后 Server 是否持续可用；
- Agent Host 能力是否一致、是否容易诊断；
- PreparedContext 选择过程的可解释性；
- Source Connector 和既有历史冷启动；
- 检索 Progressive Disclosure；
- 用户可见的失败 Experience 和风险提示；
- 当前事实、历史事实和未来计划的时间语义；
- 正式多租户和团队 ACL。

## 7. 可借鉴特性与优先级

### 7.1 近期直接实现

#### 一条 Setup 路径和持续可用的 Server

来源：Claude-Mem、agentmemory、OpenViking、Memorix。

近期动作：

- powercontext setup 检测 Agent Host；
- 用户显式选择单个或多个 Host；
- powercontext service install/status/uninstall；
- Doctor 显示 Server、Integration、Capture、Recall 和 Guidance 状态；
- 不支持的平台显示 unsupported，不伪装成功；
- 明确本地数据目录、日志目录、配置来源和卸载边界。

#### Context Receipt

来源：Statewave，以及 PowerContext 当前 PreparedContext Build 内部 origins。

近期动作：

- 可选返回本轮 Context 的 exact selection receipt；
- 默认不保存 Query 和正文；
- 显示 selected/omitted、budget、policy、fallback、latency 和 digest；
- Evaluation Harness 可以保存完整 Receipt；
- Dashboard 可以读取最近一次由用户显式保存的 Receipt。

#### Progressive Context Inspector

来源：Claude-Mem、OpenViking、MyContext。

近期动作：

- Compact Index；
- Timeline 或 Source Journal 邻近上下文；
- Exact Memory/Experience/Source Detail；
- “为什么选择”和“为什么省略”；
- Citation 跳转；
- 失败和降级状态。

#### Integration Capability Profile

来源：Memorix、agentmemory，以及 PowerContext Issue #1338。

近期动作：

- 通过 [#1357](https://github.com/oceanbase/powercontext/issues/1357) 定义版本化、机器可读的仓库契约；
- 区分 Agent Host、Framework Adapter 和 Evaluation Harness；
- 区分 Released、Master-only、Experimental、Proposed 和 Unsupported；
- Minimal：Memory Read/Write、Source Capture、Context Injection；
- Recommended：Handoff、Work Contract、Acknowledge、Task Outcome；
- Full：Experience、Skill、External Skill 和平台增强；
- MCP Tool 使用 Core、Recommended、Full Profile；
- Profile 必须由具体 Capability 推导或校验，不能只是手写标签；
- 不要求所有 Host 暴露相同数量的工具；
- 首版 Manifest 是由实现、测试、CLI 和文档共同验证的仓库契约，不立即承诺为稳定公共 HTTP API。

#### 冷启动

来源：TencentDB Agent Memory、MyContext、Statewave。

近期动作：

- 用户显式选择一个 Host 导入历史用户 Prompt；
- Dry-run、稳定 Source ID、幂等、Secret Filtering；
- 不读取完整 Assistant Transcript；
- 不扫描没有 Workspace 的 Session；
- 完成一个外部可变 Source 的 immutable observation Pilot。

### 7.2 近期实验，不先承诺 Schema

#### L0/L1/L2

OpenViking 的分层加载值得验证，但不能直接映射到 PowerContext Memory Schema。

先比较：

- 当前 Memory/Experience 粒度；
- Query-time Compact Index；
- Write-time L0/L1 Summary；
- 不同 Entry Byte Budget；
- 不同 Candidate/Entry Limit；
- Ingestion 成本、Recall 延迟、Token、准确率和 Citation Coverage。

只有数据证明持久化 L0/L1 显著优于 Query-time Index，才设计新的版本化字段。

#### Temporal Reasoning

先增加评测：

- Current State；
- Historical State；
- Future Plan；
- Superseded Decision；
- Conflicting Sources；
- Abstention。

没有评测证据前，不为 Memory 增加未经定义的时间标签。

#### Task-lensed Context

Memorix 的 Workset 值得实验。PowerContext 可以在不建立 Workflow Engine 的前提下，根据当前任务类型调整 Context Lane：

- Bugfix：复现、失败经验、相关测试；
- Release：构建、验证、兼容和发布约束；
- Review：规则、风险、历史缺陷和 exact evidence；
- Onboarding：架构入口、文档和当前状态；
- Handoff：Contract、Receipt、Outcome 和唯一 Next Action。

Task Lens 必须是 Context Selection Policy，不是新的 Task 数据库。

### 7.3 延后

#### Team ACL

依赖：

- Project/Workstream Scope 决策；
- Authentication/Authorization RFC；
- User、Agent、Team、Owner 身份；
- 跨 Scope Policy；
- OceanBase Tenant Isolation；
- Admin 和 Reviewer 权限。

近期只做 Agent Target 和 Skill Delivery Status，不表述为 ACL。

#### Context Graph

依赖真实 Temporal/Entity Retrieval 缺口和运维预算。近期不引入 Neo4j、FalkorDB 或完整 Graph Engine。

#### Connector Marketplace

先完成一个 Source Pilot，验证 Source Identity、Observation、Revision、Checkpoint、Retry 和 Citation，再决定是否扩成框架。

### 7.4 明确不做

- 不把所有 LLM 流量代理作为主要接入方式；
- 不建立完整 Agent Runtime；
- 不建立多 Agent Orchestrator；
- 不把 Session Close 当作 Task Completion；
- 不自动 Commit Handoff；
- 不自动批准或发布 Skill；
- 不把 Branch、Session 或 Agent 作为 Workstream Identity；
- 不把 Access Failure 当作可 Fail-open 的普通 Recall Failure；
- 不复制 AGPL 或 Elastic License 实现；
- 不为了追逐竞品能力数量暴露几十个默认 MCP Tool。

## 8. 差异化定位

### 8.1 推荐一句话

> PowerContext turns human-agent work into evidence-grounded, handoff-ready context.

中文：

> PowerContext 将人和 Agent 共同推进的工作沉淀为有证据、可交接、可继续的上下文。

### 8.2 不推荐的表达

- 唯一的 Agent Handoff 产品；
- 最强 Agent Memory；
- 自动自演进 Context OS；
- 团队知识图谱平台；
- Agent Workflow Engine；
- 零人工的 Skill 演进系统。

### 8.3 真正需要证明的价值

PowerContext 必须证明：

- 新 Session 不依赖旧 Session Transcript 也能继续；
- 新 Agent Host 能读取并检查同一工作状态；
- Evidence 失效或 Workspace Diverged 时不会静默执行旧 Next Action；
- 接收方缺少能力或授权时能够拒绝或请求澄清；
- 失败、部分完成、超时和未知不会被升级为成功；
- 未经 Review 的 Experience 不进入长期 Context；
- 旧判断被修订后仍能追溯历史；
- Context Budget 降低 Token 的同时不损失必要 Evidence。

## 9. 路线选择

### 路线 A：增长优先

主要工作：

- 更多 Agent Host；
- 更多 Connector；
- Desktop、Dashboard 和导入；
- 一键安装。

优点：

- 用户增长反馈快；
- 容易展示功能数量。

风险：

- 每个 Host 能力不一致；
- Server 不稳定时覆盖面越大，故障越多；
- 产品价值可能退化为“又一个跨 Agent Memory”。

### 路线 B：算法优先

主要工作：

- Context Graph；
- Temporal Memory；
- L0/L1/L2；
- Self-evolution；
- 复杂 Rerank。

优点：

- 容易参与 Memory Benchmark；
- 可能提升 Retrieval 指标。

风险：

- 赛道最拥挤；
- Inference、Schema 和运维成本高；
- 可能偏离人机协作和 Handoff。

### 路线 C：工作连续性优先

主要工作：

- 可靠性；
- Service、Setup、Doctor；
- Integration Capability Profile；
- Cross-host Work Continuity；
- Context Receipt 和 Inspector；
- Cold Start；
- LongMemEval-V2 和 Work Continuity Benchmark。

优点：

- 最大化当前代码和 RFC 资产；
- 差异化最清楚；
- 每个阶段都能形成可观察验收；
- 安装、体验、算法和评测都服务于同一产品中心。

风险：

- 需要克制横向扩功能；
- 必须用数据证明显式 Handoff 的价值。

推荐路线 C。

## 10. 未来 8–12 周工作规划

按 1–3 名核心开发者估算。允许部分工作重叠，但不能绕过前置 Gate。

本路线分为四条相互关联但独立验收的工作流：

- Product Critical Path：可靠性、常驻服务、Capability Manifest 和跨 Host Work Continuity；
- Independent Product Feature：PreparedContext Receipt 和后续 Context Inspector；
- Independent Research：Source Identity、Immutable Observation 和历史导入；
- Independent Evaluation：LongMemEval-V2 与 PowerContext 原生连续性场景。

研究和评测结果保持在路线图中可见，但不自动成为核心产品退出条件。只有发现正确性或安全阻塞，或经过明确决策升级为
Product Critical Path 时，才阻止核心产品路径完成。

### 阶段 0：第 1–2 周，可靠性与契约基线

目标：

关闭当前 Memory 和 Runtime 主路径上的已知完整性、安全、迁移、性能和资源边界问题，并统一 RFC 与代码状态。

现有工作：

- [#1297 Memory Revision 完整性](https://github.com/oceanbase/powercontext/issues/1297)
- [PR #1303 Memory Revision 修复](https://github.com/oceanbase/powercontext/pull/1303)
- [#1319 远程 HTTP Transport Policy](https://github.com/oceanbase/powercontext/issues/1319)
- [#1320 OceanBase 现有 Schema 升级](https://github.com/oceanbase/powercontext/issues/1320)
- [#1321 Memory Append 超线性增长](https://github.com/oceanbase/powercontext/issues/1321)
- [#1322 Runtime Scope Cache 无界增长](https://github.com/oceanbase/powercontext/issues/1322)，已完成
- [PR #1325 Runtime Cache 修复](https://github.com/oceanbase/powercontext/pull/1325)，已合并
- [#1219 Scope Model](https://github.com/oceanbase/powercontext/issues/1219)
- [PR #1238 Project/Workstream Scope RFC](https://github.com/oceanbase/powercontext/pull/1238)

新增决策：

- 明确 RFC 1223 是 Draft、Accepted 还是 Experimental；
- 确保 README、OpenAPI、RFC 和代码对 Work Continuity 状态一致；
- 记录 1K、10K、100K Memory History 的 Append/Search 基线；
- 已知问题未关闭时，不扩大相关公开承诺。

验收：

- Malformed Revision 不会产生 Search/Entries 分裂；
- Scope 数量增长时 Runtime Cache 保持有界；
- 现有 OceanBase Schema 有真实升级测试；
- 非安全 Remote HTTP 配置被拒绝；
- Append/Latency 不再呈现已知超线性退化；
- Scope RFC 的未决问题有明确后续 Issue；
- 当前工作区和用户已有变更不受影响。

### 阶段 1：第 2–4 周，Reliable Daily Use

目标：

让用户可以安装、启动、重启、诊断和恢复 PowerContext，而不需要理解 Server 内部结构。

现有工作：

- [#1298 Local Server Availability](https://github.com/oceanbase/powercontext/issues/1298)
- [RFC 1299](rfcs/1299_local_server_availability_and_service_installation.md)
- [#1301 Setup All Detected Agent CLIs](https://github.com/oceanbase/powercontext/issues/1301)
- [PR #1311 Setup Select](https://github.com/oceanbase/powercontext/pull/1311)
- [#1313 Doctor Integrations](https://github.com/oceanbase/powercontext/issues/1313)
- [PR #1314 Doctor Integrations](https://github.com/oceanbase/powercontext/pull/1314)
- [#1333 Full-capability Setup Guide](https://github.com/oceanbase/powercontext/issues/1333)

工作包：

1. Integration 显示有界、去重的 Server Unavailable 提示；
2. Service Status 返回 Support、Registration、Definition、Manager、Liveness、Readiness 和 Log Location；
3. 发布第一个通过 Native Lifecycle Test 的平台 Adapter；
4. 其他平台诚实报告 unsupported；
5. Setup 检测 Host，但只有用户确认后修改配置；
6. Doctor 显示 PC 管理的配置和用户自有配置；
7. 定义 Keyless/No-provider Capability；
8. 明确数据目录、日志目录、配置文件和卸载边界。

核心验收：

    Clean Machine
      -> Install PowerContext
      -> Detect Agent
      -> Install Integration
      -> Install and Start Service
      -> Doctor
      -> Store One Decision
      -> New Session Recalls It

建议产品指标：

- 从安装开始到第一次跨 Session Recall 不超过 10 分钟；
- 已宣布支持的平台在 Login/Restart 后恢复；
- Doctor JSON 不包含 Secret；
- Server Unavailable、Authentication Failure、Version Mismatch 和 Invalid Response 可区分；
- Integration 继续 Fail-open，不因 Recall 故障阻断原任务。

### 阶段 2：第 3–6 周，Cross-host Work Continuity

目标：

把 Work Contract、Handoff、Acknowledge、Task Outcome 和 Review 做成跨 Host 一致的产品能力。

依赖与已完成工作：

- [#1338 Coding Agent Capability Alignment](https://github.com/oceanbase/powercontext/issues/1338)
- [#1357 Versioned Integration Capability Manifest](https://github.com/oceanbase/powercontext/issues/1357)
- [#1358 Cross-host Work Continuity Acceptance](https://github.com/oceanbase/powercontext/issues/1358)
- [#1302 Experience and Skill Review](https://github.com/oceanbase/powercontext/issues/1302)，已完成
- [PR #1304 Review UI](https://github.com/oceanbase/powercontext/pull/1304)，已合并
- [PR #1334 Review Workflow Documentation](https://github.com/oceanbase/powercontext/pull/1334)，已合并

工作包：

1. 通过 #1357 定义 Integration Kind、Availability 和具体 Capability；
2. Minimal、Recommended、Full 必须由 Capability 推导或校验；
3. 通过 #1358 从 Manifest 选择第一个具备所需能力的 Agent Host Pair；
4. Released 和 Master-only 验收分开报告；
5. 统一 Core Guidance 和 Skill，只保留平台必要差异；
6. 默认 MCP Tool Profile 保持紧凑；
7. 使用已经合并的 Review Inbox 和 Skills Library；
8. 允许用户查看 Evidence、Revision、Delivery Status 和 Drift；
9. Candidate Approval 和 Skill Publication 保持独立。

必须通过的跨 Host Scenario：

    Host A Creates Work Contract
      -> Host A Performs Partial Work
      -> Host A Prepares and Commits Exact Handoff
      -> PowerContext Re-resolves Exact Evidence
      -> Host B Records Untrusted Receiver Self-attestations
      -> Host B Acknowledges Exact Revision
      -> Host B Records Task Outcome
      -> Handoff Report Shows Outcome Covered
      -> Experience Candidate Is Generated
      -> Human Reviews Candidate
      -> Approved Revision Is Published Separately

验收：

- 不复制 Host A 的 Session Transcript；
- Evidence 不可用时不能 Accepted；
- Workspace Diverged 时不静默执行旧 Next Action；
- Capability 和 Authorization 是宿主记录的非可信自我观察，不认证接收方、不授予权限；
- Confirmed 只表示该观察通过 Schema 校验，不表示 PC 已执行身份认证或 ACL；
- Accepted 不等于 Completed；
- Outcome 必须引用 exact accepted Receipt；
- Failed/Timed-out/Unavailable 不升级为 Passed；
- Candidate 不能自批准；
- Approval 不自动安装或执行 Skill。

### 阶段 3：第 5–8 周，Explainable PreparedContext

目标：

回答：

- 为什么召回这条；
- 为什么没有召回另一条；
- 使用了多少预算；
- 是否发生 Truncation、Dedup、Rerank 或 Fallback；
- 注入内容来自哪个 exact Revision 或 Source。

现有工作：

- [#1214 Fine-grained Tracing](https://github.com/oceanbase/powercontext/issues/1214)
- [#1356 PreparedContext Receipt RFC](https://github.com/oceanbase/powercontext/issues/1356)

独立产品功能工作：

- 先完成并接受 #1356 RFC；
- Read-only Context Inspector；
- Search/Timeline/Detail 三段式交互；
- Context Receipt；
- Recall、Rerank、Context Build 阶段 OTel Span。

建议 Receipt 字段：

- Schema Version；
- Policy Version；
- Query Digest；
- Scope 或 Workstream Reference；
- Requested Max Bytes；
- Used Bytes；
- Selected exact References；
- Omitted Count by Reason；
- Retrieval Channel Status；
- Rerank Status and Model；
- Fallback and Failure Category；
- Stage Latencies；
- Output Digest。

安全边界：

- 默认不记录原始 Query；
- 默认不记录 Memory/Experience 正文；
- 默认不记录向量；
- Trace 只记录有界计数、状态和延迟；
- Receipt 默认不持久化；
- Diagnostic Failure 不改变 Prepare 行为；
- LLM Rerank 标记为 Non-deterministic。

验收：

- 每条注入项都有 exact origin；
- 每类省略有明确原因；
- Receipt Digest 能识别本轮实际注入内容；
- 关闭 Diagnostic 后当前 API 行为兼容；
- Dashboard 可从索引展开到 exact detail；
- 不泄露 Secret、Prompt 或绝对路径。

### 阶段 4：第 7–10 周，Cold Start 和 Source Pilot

目标：

让已有项目能够复用安装 PowerContext 之前已经产生的工作信息，同时验证外部可变对象的精确 Evidence 语义。

现有工作：

- [#1240 Source Integration Shape](https://github.com/oceanbase/powercontext/issues/1240)
- [#1300 Import Pre-install User Prompts](https://github.com/oceanbase/powercontext/issues/1300)

工作包一：Codex Prompt Backfill

- 必须显式选择 Host；
- 默认只读取 User Prompt；
- 支持 Dry-run；
- 使用稳定、幂等 Source ID；
- 不读取完整 Assistant Transcript；
- 不扫描没有 Workspace 的 Session；
- 不猜测 Global Scope；
- Secret Filter；
- Server 必须已经运行；
- 没有 Inference Provider 时只创建 Source，不声称已形成 Memory。

工作包二：GitHub Issue/PR Source Pilot

该 Pilot 继续由已经分配负责人的
[#1240 Source Integration Shape](https://github.com/oceanbase/powercontext/issues/1240)
定义和验证，不另开平行实现 Issue。

- 用户显式提供对象；
- 保存 Logical Source Identity；
- 保存 Immutable Observation Identity；
- 外部对象修改后生成新 Observation；
- 旧 Artifact Citation 继续解析旧 Observation；
- 不做全量后台同步；
- 不做 Webhook；
- 不做 Connector Marketplace；
- 凭据和 Retry 只实现 Pilot 所需的最小边界。

验收：

- 重复导入幂等；
- Dry-run 不写入；
- Source Mutation 不覆盖旧 Evidence；
- 无 Workspace 的 Session 被跳过；
- Secret 不进入 Source、Log 和错误消息；
- Prompt Backfill 不改变 Live Hook；
- Pilot 证明 Capture、Ref 或 Hybrid 的 RFC 决策可行。

### 阶段 5：第 9–12 周，Benchmark 和公开证据

目标：

同时使用行业 Benchmark 和 PowerContext 原生 Scenario 证明产品价值。

现有工作：

- [#1263 Benchmark Coverage](https://github.com/oceanbase/powercontext/issues/1263)
- [PR #1328 Single-arm Baseline Comparisons](https://github.com/oceanbase/powercontext/pull/1328)
- [#1359 Bounded LongMemEval-V2 Workload](https://github.com/oceanbase/powercontext/issues/1359)
- [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)

工作包一：LongMemEval-V2

- 实现 PowerContext Memory Backend；
- 固定覆盖五类能力的小样本；
- 提供 Local Smoke；
- 提供完整运行配置；
- 保存 Dataset Revision、Harness Revision、Model、PowerContext Revision 和 Policy；
- 报告 Accuracy、Latency、Context Bytes、Token 和 Cost；
- Partial Run 明确标记为 Subset。

工作包二：Work Continuity Scenario Pack

至少覆盖：

- 新 Session 无 Transcript Continue；
- Cross-host Continue；
- Evidence Missing；
- Workspace Diverged；
- Receiver Needs Clarification；
- Receiver Declines；
- Accepted but Awaiting Outcome；
- Failed/Partial/Succeeded Outcome；
- Superseded Handoff Revision；
- Outcome to Experience Candidate；
- Human Review；
- Approved Experience Later Recall。

工作包三：检索实验 Arm

- Current Memory/Experience Granularity；
- Query-time Compact Index；
- Write-time L0/L1 Summary；
- Hybrid Retrieval；
- Temporal Filter；
- Task Lens。

比较指标：

- QA/Task Accuracy；
- Task Success；
- Recall p50/p95；
- Prepare p50/p95；
- Input Context Bytes/Tokens；
- Ingestion Token and Cost；
- Exact Citation Availability；
- Abstention Accuracy；
- Handoff Acceptance Rate；
- Handoff Outcome Coverage Rate；
- Unauthorized Automatic Action Count，目标为零。

只有实验表明 Write-time L0/L1 或 Temporal Metadata 有明确收益，才进入新的公共 Schema RFC。

## 11. 产品和工程指标

### 11.1 采用指标

- Time to First Cross-session Recall；
- Setup Completion Rate；
- Doctor Pass Rate；
- Service Availability；
- Supported Host Recommended-profile Coverage；
- Uninstall and Recovery Success。

### 11.2 Context 指标

- Recall Latency；
- Prepare Latency；
- Context Bytes/Tokens；
- Selected/Omitted Candidate Count；
- Citation Availability；
- Truncation Rate；
- Rerank Fallback Rate；
- Empty Context Rate；
- Source-to-Memory Conversion Rate。

### 11.3 Work Continuity 指标

- Work Contract Coverage；
- Handoff Evidence Availability；
- Acknowledge Rate；
- Needs Clarification Rate；
- Decline Rate；
- Accepted Handoff Outcome Coverage；
- Diverged Workspace Safe-stop Rate；
- Unauthorized Action Count。

### 11.4 Experience 和 Skill 指标

- Task Outcome to Candidate Rate；
- Candidate Approval/Reject/Revise Rate；
- Approved Experience Later Recall Rate；
- Failure Lesson Recall Rate；
- Skill Delivery Success；
- Skill Drift/Conflict Rate；
- Automatic Approval、Publication 或 Execution Count，目标为零。

## 12. 风险和停止条件

### 12.1 Scope 和 ACL 风险

在 Scope RFC 和 Authentication/Authorization 设计完成前：

- 不添加 Team ACL；
- 不把 Agent ID 当作安全身份；
- 不把 Project Membership 当作访问授权；
- 不把 Skill Target 当作 Tenant Boundary。

### 12.2 Schema 膨胀风险

在 Benchmark 证明之前：

- 不增加 L0/L1/L2 持久化字段；
- 不增加未经定义的 Temporal Tag；
- 不增加完整 Context Graph；
- 不增加新的 Task/Workflow 聚合根。

### 12.3 自动化越权风险

- Session End 不自动生成 Outcome；
- Stop 不自动 Commit Handoff；
- Model 不自动批准 Candidate；
- Approved Skill 不自动发布；
- Published Skill 不自动安装；
- Installed Skill 不自动执行。

### 12.4 接入复杂度风险

- 不以 OpenAI/Anthropic Proxy 作为默认接入；
- 不要求所有 Host 提供相同 Hook；
- 不暴露所有工具作为默认 MCP Profile；
- 新 Host 没有 Source Capture 和 Context Injection 时，不宣称完整 Memory Integration。

### 12.5 Benchmark 误导风险

- 不把竞品自报结果当作已复现事实；
- 不把 Subset 当作完整 Benchmark；
- 不用不同模型、不同 Prompt、不同数据版本做无说明比较；
- 不把 Full Context Baseline 自动视为无效；
- 同时报告 Accuracy、Latency、Token 和 Cost；
- 公开 exact Revision 和配置。

## 13. 建议的 Issue 和 Work Package

优先复用现有 Issue，不重复建立 Tracker。当前独立工作项为：

1. [#1356 PreparedContext Receipts and Progressive Disclosure](https://github.com/oceanbase/powercontext/issues/1356)
2. [#1357 Versioned Integration Capability Manifest](https://github.com/oceanbase/powercontext/issues/1357)
3. [#1358 Cross-host Work Continuity Acceptance](https://github.com/oceanbase/powercontext/issues/1358)
4. [#1359 Bounded LongMemEval-V2 Workload](https://github.com/oceanbase/powercontext/issues/1359)
5. [#1240 Source Integration Shape](https://github.com/oceanbase/powercontext/issues/1240)，继续负责受限 Source Pilot
6. [#1300 Pre-install Host Prompt Import](https://github.com/oceanbase/powercontext/issues/1300)

Context Inspector 只在 #1356 RFC 接受后建立独立实现 Issue。

建议形成三个 Release Theme：

### Reliable Daily Use

- Memory 和 Runtime 正确性；
- Service；
- Setup；
- Doctor；
- Review Inbox；
- Capability Profile。

### Explainable Continuity

- Cross-host Work Contract；
- Exact Handoff；
- Acknowledge；
- Task Outcome；
- Context Receipt；
- Context Inspector。

### Cold Start and Proof

- Prompt Backfill；
- GitHub Source Pilot；
- LongMemEval-V2；
- Work Continuity Scenario Pack；
- Retrieval Experiment Arms。

## 14. 最终判断

PowerContext 不应通过能力数量与通用 Memory 产品竞争。它最有机会建立的产品位置是：

> 当人、Session、模型或 Agent Host 发生变化时，工作仍然能够在 exact evidence、明确责任边界和实时检查下继续。

竞品已经证明用户需要：

- 一条命令安装；
- Server 持续可用；
- Keyless Baseline；
- Viewer 和 Trace；
- Progressive Disclosure；
- Cold Start；
- Hybrid Retrieval；
- Asset Control。

PowerContext 应吸收这些工程和交互能力，但让它们服务于 Work Continuity，而不是改变产品中心。

近期胜负手不是再增加一种 Memory，而是让用户在十分钟内完成一次真实闭环，并能清楚看到：

- 保存了什么；
- 为什么召回；
- 哪些内容被省略；
- 交接的是哪个 exact Revision；
- 接收方检查了什么；
- 最终结果是否被记录；
- 哪些经验经过 Review 后进入了未来 Context。

如果这条链路稳定、跨 Host、可解释、可评测，PowerContext 就不是 Awesome-Agent-Memory 中另一个 Memory Project，而是
一类更明确的工作连续性基础设施。

## 参考项目与资料

- [PowerContext](https://github.com/oceanbase/powercontext)
- [Awesome Agent Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory)
- [OpenViking](https://github.com/volcengine/OpenViking)
- [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)
- [MyContext](https://github.com/openTrinity/mycontext)
- [Claude-Mem](https://github.com/thedotmack/claude-mem)
- [agentmemory](https://github.com/rohitg00/agentmemory)
- [Memorix](https://github.com/AVIDS2/memorix)
- [Statewave](https://github.com/smaramwbc/statewave)
- [CommonGround](https://github.com/Intelligent-Internet/CommonGround)
- [projectmem](https://github.com/riponcm/projectmem)
- [MemOS](https://github.com/MemTensor/MemOS)
- [Mem0](https://github.com/mem0ai/mem0)
- [Graphiti](https://github.com/getzep/graphiti)
- [Letta](https://github.com/letta-ai/letta)
- [Talamus](https://github.com/ampres-ai/talamus)
- [Panella](https://github.com/panellatech/panella)
- [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2)
- [Agent Memory Benchmark](https://github.com/vectorize-io/agent-memory-benchmark)
