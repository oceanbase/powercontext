- Proposal Name: `product_definition_and_vision`
- Start Date: 2026-07-07
- RFC PR: [oceanbase/powercontext#0001](https://github.com/oceanbase/powercontext/pull/0001)

# Summary

PowerContext 是面向人和 Agent 共同工作的上下文运行层。它将人和 Agent 共同推进过的工作转化为可以被后续参与者理解、接手和继续的上下文。该 RFC 定义 PowerContext 的产品定位、核心概念、阶段性范围和验收口径，作为第一份产品定义与构想相关的设计记录。

PowerContext 的核心表达是：

> Work context layer for humans and agents.

更具体地说：

> PowerContext turns human-agent work into handoff-ready context.

本 RFC 只定义产品对象、设计概念和前两个阶段需求，不展开具体实现方案、代码结构、宣发材料和远期案例规划。这里记录的是产品方向，不对应某个 Core revision 已提供的 API 或功能。

# Motivation

Memory、context、AgentOps、RAG、workflow、observability 等产品正在形成接近共识的能力结构。无论入口是 memory、context、AgentOps 还是 workflow，成熟产品大体都会走向类似组合：

- 接入文档、代码、工单、对话、trace、事件和人工输入。
- 保留用户、团队、项目或 Agent 的长期信息。
- 在任务执行时召回相关材料。
- 记录 Agent、工具、模型和 workflow 的运行过程。
- 沉淀总结、规范、SOP、workflow、skill 或结构化内容。
- 收集人类反馈、任务结果、eval 分数和质量信号。
- 将稳定流程交给 Agent 或 workflow 执行。

这意味着 source、memory、retrieval、trace、artifact、feedback 等部件本身已经不足以定义差异。差异来自这些部件围绕什么产品中心组织。

当前主流导向大体有两个：

- 更强的 Agent：让 Agent 记住更多、找到更相关的上下文、从失败中改进、在长任务中保持状态，并自动完成更多步骤。
- 组织自进化系统：让上下文自动沉淀、经验自动合并和升级、反馈驱动记忆与技能演化，并让 workflow 越来越自动化。

这两个方向主要聚焦在 Agent 能力增强或自动化闭环，而不是人和 Agent 如何共同完成长期工作。人在其中容易退化成机械操作员：只负责输入目标、等待结果，却难以理解中间判断、接手后续状态或复用工作经验。

PowerContext 选择的问题是：

- 当人和 Agent 共同推进一段工作后，这段工作如何被后续的人或 Agent 理解、接手和继续？
- 当用户放任 Agent 执行一段时间后，如何保证中间的决策判断可以留存？
- 当一个新参与者接手项目时，如何自然理解它的演进，而不是只看到零散记忆、技能或看板？

# Guide-level explanation

PowerContext 的产品对象不是单独的 memory、trace、artifact 或 workflow，而是人和 Agent 共同推进的工作。

围绕这个对象，PowerContext 会处理 source、artifact、feedback、automation 等能力，但这些能力不是 PowerContext 的差异本身。PowerContext 的差异在于它们服务于同一个目标：

> 让工作在经过人和 Agent 的共同推进之后，仍然可以被接手。

## 产品选择：协同与交接

PowerContext 的切入点是 work handoff。

在真实组织里，Agent 不会孤立运行。人会提出目标、判断风险、修正方向、接手结果；Agent 会执行任务、暴露不确定性、沉淀过程、推动后续工作。

在这个切入点下，上下文不是 Agent 内部状态，也不是组织里越多越好的资料集合。上下文是协作和交接的条件。这里的 handoff 是让一段由人和 Agent 共同推进过的工作能够被理解、接手和继续。

因此，PowerContext 的核心问题不是：

- Agent 怎样变得更强。
- 上下文怎样自动演化。
- 系统怎样减少人类参与。

而是：

- 人如何看懂 Agent 已经做过什么。
- Agent 如何继承人已经判断过什么。
- 一个参与者如何接手另一个参与者留下的工作。
- 工作如何在多人、多 Agent、多工具之间保持连续。

典型场景包括：

- 人把任务交给 Agent。
- Agent 把不确定性还给人。
- 一个 Agent 接续另一个 Agent 的任务。
- 一段会话沉淀为后续 workflow。
- 一次 review、incident 或调试经验转化为团队可复用资产。
- 一个组织在不同 Agent、工具和团队之间保留工作判断。

PowerContext 要让人和 Agent 面对同一段工作状态，只是通过不同视图、不同能力和不同入口参与其中。

## 核心概念：Source、Artifact、Trigger

PowerContext 第一版只暴露三类概念：

```text
source -> artifact <- trigger
```

- Source：系统输入，即外部工作材料的引用和证据。
- Artifact：PowerContext 制作并维护的上下文产物。
- Trigger：外部对系统行为和 artifact 状态的控制信号。

其他能力尽量收敛到这三类概念下面，不把 memory、trace、workflow、skill、eval 做成并列的一级入口。

### Source

Source 表示 PowerContext 接入的外部数据。

它包括工单、OTel、代码、文档、Agent 轨迹、review、incident、人工备注等。Source 通过插件接入。PowerContext 不替代用户已有系统，只把这些外部材料用于制作上下文制品。

Source 首先是工作证据，其次才是调试材料。原始数据仍由用户系统持有，PowerContext 只组织其交接语义和运行时视图。

### Artifact

Artifact 表示 PowerContext 制作出的上下文产物。

典型 artifact 包括：

- 长短期记忆：长期判断和短期 working memory。
- 用户偏好：面向千人千面的偏好、约定和约束。
- 日常习惯：定时任务、周期性动作、特定情景动作。
- SOP：skills、workflow、runbook。
- 小工具或小程序：面向人的可操作内容。

Artifact 不是原始数据，也不是一次性的检索结果。它必须能进入后续工作，被人理解、被 Agent 使用，或被继续维护。

### Trigger

Trigger 表示外部对 PowerContext 的控制能力，用于影响 artifact 的生命周期、演进和使用方式。

它和 source 不同：

- Source 提供材料。
- Trigger 提供信号和控制。

Trigger 可以是使用反馈、人工确认、人工否定、任务成功失败、eval 分数、外部事件、定时信号，或对某类 artifact 的显式操作。

### Handoff context

Handoff context 是面向接手者的上下文视图。它不是新的原始数据集合，也不是完整运行日志，而是从 source 和 artifact 中组织出来的交接材料。

第一版 handoff context 至少应帮助接手者回答四个问题：

- 已经发生了什么。
- 做过哪些关键判断。
- 当前状态和下一步是什么。
- 这些判断依据哪些 source 或 artifact。

## 用户主路径

第一版围绕四个动作组织：

- Connect source。
- Create artifact。
- Mount artifact。
- Send trigger。

Mount artifact 表示把制品以 MCP、skill、hook 或 context provider 的形式挂载到目标 Agent。这是 PowerContext 需要帮助用户完成的关键动作。

典型路径如下：

1. 用户在个人 runtime manifest 里声明 source 和挂载项。
2. 用户接入自己的外部系统，例如 OTel、工单、代码仓库。
3. PowerContext 读取 source，但不复制原始系统。
4. 用户或 Agent 基于 source ref 创建 artifact。
5. 用户把 artifact 挂载到自己使用的 Agent 产品。
6. 人或 Agent 接手工作时获得 handoff context。
7. 反馈通过 trigger 回流，影响个人状态和团队资产。

这里和传统 memory 产品不同：用户不是从 `memory.add` 开始，而是从已有工作系统开始，把可交接部分沉淀为 artifact。

## 个人优先，团队汇总

配置应个人优先，而不是团队强统一：

- Personal state：个人 source、个人挂载、个人反馈、个人工作上下文。
- Team assets：经过确认或持续复用后汇总出的共享 artifact。
- Team policy：团队维护默认策略、共享制品和推荐挂载项。

这样产品可以自然降级到个人使用；多人使用时，结果再汇总为团队资产。

# Reference-level explanation

本 RFC 的 reference-level explanation 只定义产品语义和边界，不定义代码架构、数据表、服务拆分或具体实现方案。RFC 0002 记录对应的 SDK 产品模型和实现边界；源码和开发导读描述特定 revision 可用的实现。

## 术语契约

| 术语 | 定义 | 不是 |
| --- | --- | --- |
| Work | 人和 Agent 围绕一个目标共同推进的任务、判断和状态变化 | 单条 memory、单次 trace、单个 workflow |
| Source | 外部工作材料的引用和证据 | PowerContext 复制保存的原始系统 |
| Artifact | PowerContext 制作并维护的上下文产物 | 原始数据、一次性检索结果、不可维护摘要 |
| Trigger | 影响 artifact 生命周期、演进和使用方式的外部信号 | 普通 source 内容 |
| Handoff context | 面向接手者的上下文视图 | 完整日志、全量知识库、单纯 debug trace |
| Personal state | 个人 source、挂载、反馈和工作上下文 | 团队强统一配置 |
| Team assets | 经确认或复用后形成的共享 artifact | 所有个人状态的自动合并 |
| Team policy | 团队维护的默认策略、共享制品和推荐挂载项 | 对个人运行时的完全替代 |

## 产品主线

PowerContext 的产品主线是：

```text
Understand work -> Shape handoff context -> Support human-agent collaboration -> Collect reuse feedback -> Maintain reusable work assets
```

具体来说：

1. 理解一段人和 Agent 共同推进的工作。
2. 把其中可继承的判断、偏好、习惯和技能组织成交接上下文。
3. 在人或 Agent 接手工作时组装合适的上下文视图。
4. 在后续使用中收集反馈，判断交接是否有效。
5. 持续维护可以被组织复用的工作资产。

## 能力边界

Source、artifact、trigger、feedback、mount 等能力必须服从同一个判断标准：

> 它是否让工作更容易被下一个人或 Agent 接手。

因此，第一、二阶段的边界如下：

| 能力 | 应该支持 | 不应该变成 |
| --- | --- | --- |
| Source | 引用外部工作证据，并保留可追溯关系 | 替代用户已有数据系统 |
| Artifact | 沉淀可接手、可挂载、可维护的上下文产物 | 无生命周期的一次性总结 |
| Trigger | 让反馈、事件和确认信号影响 artifact | 另一个内容输入入口 |
| Mount | 将 artifact 投射到 Agent 可用入口 | 不受治理的隐式注入 |
| Handoff context | 帮助人或 Agent 理解已发生工作和下一步状态 | 全量日志浏览器或普通看板 |

关键取舍包括：

- 长期信息只有在服务交接时才有意义；短期 working memory 不应自动成为工作主线。
- 运行记录首先是工作证据，其次才是调试材料；PowerContext 自己的 trace 也应该纳入治理范畴。
- 偏好、习惯、技能和自动化的累积只是目标的一部分；这些内容最终要形成面向人的材料。
- PowerContext 不绕过用户已有数据系统。原始 source 仍由用户系统持有，PowerContext 只组织其交接语义和运行时视图。

## 目标用户

PowerContext 的早期入口可以是个人开发者接入自己的 Agent，在不同 Agent 或不同项目之间沉淀上下文；主要服务对象是多人、多 Agent 协作的组织。

典型用户包括：

- 使用多个 Agent 参与工程、运维、数据或知识工作的团队。
- 需要在人和 Agent 之间交接任务、判断和流程的团队。
- 需要把 Agent 工作轨迹转化为团队经验的组织。
- 希望保留人的判断和责任，而不是把工作完全隐藏在自动化内部的组织。

这些组织不只需要更强的 Agent，也需要更可交接的工作。

## 和同类产品的区别

同类产品大多围绕 memory、context provider 或 graph retrieval 组织主路径：

- Mem0 / OpenMemory：添加、搜索、共享 memory。
- LangMem / Letta / Zep / Graphiti：抽取、组织、召回 memory 或 graph context。
- Continue / Claude Code：通过 context provider、MCP、skills、hooks 把外部材料挂进 Agent。

PowerContext 的主路径是：

```text
user source system -> source ref -> handoff artifact -> MCP / skill / hook projection -> agent memory injection -> human-agent work handoff -> feedback trigger
```

PowerContext 是对 PowerMem 产品范围的扩大，而不是在 PowerMem 旁边再做一个 memory 系统。Context 是比 Memory 更大的范围。它会包含提取后的偏好、经验、长期有效的工作产物，也会包含短期 working context，最后还可能演进出 skill、routine、workflow 等可复用能力。

PowerMem 以 memory 为中心，主要解决长期记忆、混合检索、智能抽取、Agent 集成和技能沉淀问题。PowerContext 的范围更大，产品中心从 memory 转向 context。

两者关系如下：

| 项目 | 产品中心 | 技术对象 | 主要问题 |
| --- | --- | --- | --- |
| PowerMem | Memory | memory record, vector / graph search, skill store | Agent 如何记住和召回 |
| PowerContext | Context | work, source ref, context item, handoff view | 人和 Agent 如何共同接手工作 |

在 PowerContext 中，memory 是 context 的一个子集。长期偏好、项目约定、团队经验、短期 working memory、routine、skill 都属于 context 的不同形态。

## 产品定位

PowerContext 当前最重要的是形成一个清晰可用的闭环：

> 个人按需挂载，团队汇总结果；对外讲工作交接，对内保留自进化能力。

产品定位可以概括为：

- 对外主打工作交接：这是差异化空隙，避免正面进入自进化方向的红海。
- 对内保留自进化能力作为底盘：已有的记忆、经验和技能能力本身就是自进化方向的实现，不丢弃。
- 两者同属 PowerContext 一个产品：对外故事用工作交接，能力底盘用自进化，不拆成两个产品。

需求分层如下：

| 层级 | 目标 | 必须形成的内容 |
| --- | --- | --- |
| 运行层 | 可以真实试用 | source、artifact、trigger、mount 的最小闭环 |
| 自进化底盘 | 证明不是静态 memory | memory、experience、skill / routine 的生成、更新和汰换 |
| 交接体验 | 对外叙事可感知 | handoff context、交接视图、Agent 注入 |

## 阶段一：2026 年 7 月底 / 8 月初

目标：第一个可用版本，开始内部试用，验证易用性、交接质量和产品边界。

功能需求：

- Runtime manifest：支持挂载 source、artifact 到目标 Agent 或相关运行入口。
- Source provider：至少支持代码仓库、Agent 或其 OTel 接入，能够在自己的项目中开始使用基础能力。
- Artifact registry：支持 memory、experience、routine、skill 四类 artifact。这里可以涵盖以前 PowerMem 的 memory 和 experience，也可以涵盖 ContextSeek 的 skill 相关内容；routine 不一定要在第一期做完。
- Handoff context：能从 source ref 和 artifact 生成一次可交接视图。主要预期有两个：任务关键时间线和方案；在这个基础上形成偏好累积。
- Trigger / hook：支持人工反馈、任务结果反馈、Agent lifecycle hook。
- Codex dogfood：优先支持 Codex 使用场景，包括 memory 注入、skills 挂载、交接摘要回收。

试用与评估：

- 个人开发者可以接入自己的 Agent 工作流。
- 内部评估围绕易用性、交接质量、制品可维护性打分。
- 第一组 demo 应体现从一次 Agent 工作轨迹生成 handoff context，再沉淀为 memory / routine。

验收口径：

- 架构边界清晰：source 不复制原始系统，artifact 可管理，trigger 可回流。
- 内部用户可以在 Codex 场景完成一次端到端试用。
- 产品上形成 source、artifact、trigger、mount 的最小闭环。
- 交接视图能够帮助后续的人或 Agent 理解已发生的工作、关键判断和下一步状态。

## 阶段二：2026 年 9 月 1 日

目标：第一个正式版本。基于阶段一试用反馈，收敛产品路径，具备稳定的个人挂载体验，并检查从个人用户到企业多用户场景的体验链路一致性。

功能需求：

- 完善 manifest 和 CLI 的可用性。
- 完善 MCP server、skills pack、hooks adapter 三类挂载形态。可以新增主流 Agent 支持，但只做内部有比较多精力验证的内容；不怕提供得少，重点是保持可用性。
- 根据内部试用反馈收敛 artifact 生命周期，决定是否调整预期的分层和起作用的时间点，并检查产物质量。
- 检查面向多租户的企业场景设计，从交接的角度倒推逻辑，确保个人用户到企业多用户场景下的体验链路一致。
- 补齐基础 profile 和看板能力，让用户能够理解个人状态、团队资产和交接上下文之间的关系。

验收口径：

- 对外能讲清差异化：PowerContext 不是另一个 memory 系统，而是 human-agent work handoff 的上下文运行层。
- 对内能证明自进化能力没有被丢掉：memory、experience、routine、skill 可以作为 artifact 被生成、更新、汰换和复用。
- 用户能完成个人挂载场景，并在后续接手时获得 handoff context。
- 企业和团队场景下，personal state、team assets、team policy 的关系清晰，不要求团队一开始强统一。
- 新增功能必须回到工作交接，不单独讲底层能力。

## 阶段一与阶段二优先级

P0：

- Runtime manifest。
- Artifact registry。
- Handoff context。
- Codex dogfood。
- Trigger / feedback。
- MCP / skill / hook 的最小挂载。

P1：

- Source provider 扩展。
- Artifact lifecycle。
- Experience / routine / skill 的生成和汰换。
- 基础 profile 和看板。
- 内部评估链路。

# Drawbacks

选择 work handoff 作为产品中心会带来几个代价：

- 产品叙事不再只围绕 Agent 自动变强，可能弱化一部分已有 memory 或 self-evolving system 叙事的直接表达。
- Source、artifact、trigger 的抽象会把多个能力收敛到更少的一层概念下，早期需要避免用户误以为底层能力被隐藏或削弱。
- Handoff context 的质量难以只靠静态指标评估，需要真实任务和后续接手体验来验证。
- 个人优先、团队汇总的路径会带来治理复杂度，需要在个人灵活性和团队一致性之间保持边界。

# Rationale and alternatives

## 为什么选择 work handoff

PowerContext 需要一个比 memory 更大的产品中心，但不应直接进入“自动演化一切”的同质化竞争。Work handoff 能同时解释人类判断、Agent 轨迹、上下文制品和反馈闭环，也能把已有 PowerMem 能力纳入更大的产品范围。

该选择的核心收益是：

- 对外差异化清晰：PowerContext 不是另一个 memory 系统，而是 human-agent work handoff 的上下文运行层。
- 对内能力可继承：memory、experience、routine、skill 都可以作为 artifact 继续演进。
- 用户路径更自然：从已有 source system 开始，而不是强迫用户从添加 memory 开始。
- 团队场景更完整：能够表达 personal state、team assets、team policy 之间的关系。

## 备选方案

备选方案一：继续以 memory 为产品中心。

- 优点：概念成熟，用户容易理解，能继承 PowerMem 的既有表达。
- 缺点：容易进入同质化竞争，难以解释 trace、routine、workflow、handoff view 等超出 memory 的产品对象。

备选方案二：以 self-evolving organization 为产品中心。

- 优点：可以覆盖经验沉淀、skill 演化、workflow 自动化等长期能力。
- 缺点：对外解释成本更高，也容易让人的判断和责任被自动化叙事遮蔽。

备选方案三：以 AgentOps 或 observability 为产品中心。

- 优点：和 trace、eval、运行质量等能力关系直接。
- 缺点：会把 PowerContext 拉向调试和监控工具，无法完整表达工作接手、偏好继承和团队资产维护。

不做该 RFC 的影响是：PowerContext 的 source、memory、artifact、trigger、skill 等能力可能被分别讨论和实现，产品中心不稳定，后续设计难以判断哪些能力属于主路径，哪些只是底层支撑。

# Prior art

相关产品和方向包括：

- PowerMem：以 memory 为中心，解决长期记忆、混合检索、智能抽取、Agent 集成和技能沉淀问题。
- ContextSeek：与 skill、context 相关的探索，为 PowerContext 的 artifact 能力提供参考。
- Mem0 / OpenMemory：围绕添加、搜索、共享 memory 组织用户路径。
- LangMem / Letta / Zep / Graphiti：围绕 memory 或 graph context 的抽取、组织和召回。
- Continue / Claude Code：通过 context provider、MCP、skills、hooks 等方式将外部材料挂载进 Agent。

PowerContext 借鉴这些方向中的 source 接入、memory 抽取、Agent 挂载、反馈回流和技能沉淀能力，但产品中心转向 human-agent work handoff。

# Unresolved questions

该 RFC 合并前需要确认的问题：

- Phase 1 中 routine 是否必须进入首个可用版本，还是只保留 registry 类型和后续演进位置。
- Handoff context 的最小质量标准如何定义：任务时间线、关键判断、下一步状态之外是否需要强制包含风险、证据和责任边界。
- Artifact lifecycle 的阶段划分是否在 Phase 1 固定，还是根据内部 dogfood 反馈在 Phase 2 收敛。
- Team assets 的确认机制由人工显式批准开始，还是允许基于复用信号形成推荐资产。

刻意排除在本 RFC 范围之外的问题：

- 具体服务架构、数据模型、索引方案和存储选择。
- 具体 MCP server、skills pack、hooks adapter 的实现协议。
- 外部宣发材料、定价、商业包装和远期案例规划。
- 具体 eval benchmark 的设计和权重。

后续可能需要独立 RFC 的方向：

- Runtime manifest 和 mount 语义。
- Artifact registry 与 lifecycle。
- Handoff context schema 和质量评估。
- Trigger / feedback 模型。
- Personal state、team assets、team policy 的多租户治理模型。

# Future possibilities

该 RFC 的自然扩展包括：

- 将 handoff context 从一次性交接视图扩展为持续维护的工作状态。
- 将 artifact 从 memory、experience、routine、skill 扩展到 workflow、runbook 和面向人的小工具。
- 建立跨 Agent、跨工具、跨团队的 artifact projection 能力。
- 基于真实使用反馈维护团队级 reusable work assets。
- 将 PowerContext 自身 trace 纳入治理，使上下文系统也能被审计、评估和改进。

这些扩展都必须回到同一条判断标准：它们是否让工作更容易被下一个人或 Agent 接手。
