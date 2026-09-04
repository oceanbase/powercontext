- Proposal Name: `agent_plugin_distribution_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#1410](https://github.com/oceanbase/powercontext/pull/1410)
- Tracking Issue: [oceanbase/powercontext#1405](https://github.com/oceanbase/powercontext/issues/1405)

# Summary

PowerContext 使用一个符合 [Agent Plugins](https://agent-plugins.org/) 规范的插件，作为可移植集成内容的来源。Agent Skills、可移植 MCP 配置、插件元数据和共享名称都在这里维护。

每个 maintained Agent integration 都使用同一个 Agent Integration Core，复用 PowerContext client 和标准集成逻辑。手写 Target Hook 把宿主生命周期事件和 API 连接到 Core Operation。Target Profile 描述该映射、支持的 capability、packaging rule 和 target-owned extension。

Target Distribution 根据这些输入确定性构建。它是可安装制品，不是另一份 source of truth。

本 RFC 定义这套模型的所有权规则和符合性要求。它不规定仓库布局、实现语言、命令行接口、模板引擎或 Agent Integration Core 的交付形态。

# Motivation

PowerContext 的多个集成重复维护了并非宿主特有的内容：

- Skill guidance 和安全规则；
- MCP Server identity 和连接意图；
- 插件名称、版本、描述和仓库元数据；
- scope 解析、context 准备、Source capture、budget、diagnostic 和错误处理；
- operation 和 tool 命名。

这些副本已经在名称、MCP 结构、tool prefix、Skill 行为和 hook 行为上出现差异。修复一个集成不会修复其他集成。

当前大多数 maintained Agent plugin 已经执行相同的 scope resolution、context preparation、Source capture、request budget、diagnostic 和 fail-open flow。这些是 PowerContext integration rule，不是宿主功能。公共逻辑已经足够明确，没有分别维护的理由。

宿主之间也确实存在差异。它们使用不同的 package layout、生命周期事件、payload、安装 API 和用户界面。Agent Plugins 统一了 Skills 和 MCP Server，但有意把 hook 和其他 client extension 留给宿主定义。因此，PowerContext 需要共享行为的公共来源和显式宿主映射，而不是一套通用宿主 API。

所有权规则如下：

> 可移植内容和共享行为只维护一次。宿主差异必须显式描述。只有依赖宿主的行为才保留手写 target code。

## 目标与非目标

本 RFC 要求维护中的 Agent distribution：

- 在宿主支持时，从 Canonical Agent Plugin 使用公共 Skills 和 MCP 配置；
- 对自身支持的 PowerContext lifecycle operation 使用项目维护的 Agent Integration Core；
- 在经过校验的 Target Profile 中记录 component merge rule 和 hook mapping；
- 根据已声明来源确定性构建完整 target artifact；
- 如实报告能力差异，不伪造对等能力。

本 RFC 不做以下事情：

- 统一宿主生命周期 API，或要求宿主使用相同事件名称；
- 要求所有宿主公开相同能力或 tool；
- 选择实现语言、package manager、process model 或 build command；
- 要求从模板生成手写 hook logic；
- 定义自动安装或公共 plugin compiler API；
- 管理由其他来源维护并独立发布的 reviewed Skill。

## 相关工作

本 RFC 规定 distribution ownership 和 projection rule。相邻契约由以下工作负责：

| Work item | 职责 |
| --- | --- |
| [#1244](https://github.com/oceanbase/powercontext/issues/1244) | 提供现有的 reusable Agent Plugin。 |
| [#1301](https://github.com/oceanbase/powercontext/issues/1301) | 负责 multi-host installation 和用户配置。 |
| [#1357](https://github.com/oceanbase/powercontext/issues/1357) | 负责带版本的 integration capability contract 和 evidence。 |
| [#1362](https://github.com/oceanbase/powercontext/issues/1362) | 负责 lifecycle behavior 和公共 integration operation 语义。 |
| [#1378](https://github.com/oceanbase/powercontext/issues/1378) | 负责 canonical Skill 中的 explicit memory routing。 |
| [#1397](https://github.com/oceanbase/powercontext/issues/1397) | 负责 separately managed Skill package 的发布和安装。 |

# Guide-level explanation

## 心智模型

这套模型有四类权威输入：

| 概念 | 职责 |
| --- | --- |
| **Canonical Agent Plugin** | 可移植元数据、Skills、MCP 配置和共享名称。 |
| **Agent Integration Core** | 所有 maintained Agent plugin 共用的 PowerContext client 和标准集成逻辑。 |
| **Target Profile** | 声明式 packaging、merge、hook、capability、compatibility 和 output ownership 规则。 |
| **Target Hook** | 连接特定宿主生命周期事件、payload、result、API 和 Core Operation 的手写代码。 |

输出是 **Target Distribution**。它由普通的可安装文件组成，由 portable content、Agent Integration Core、Target Profile 和手写 Target Hook 组装而成。

```text
Target Distribution = Project(
    Canonical Agent Plugin,
    Agent Integration Core,
    Target Profile,
    Target Hook,
    Release Metadata,
)
```

投影保证每个共享 component 只有一个来源，并显式记录 target 之间的功能差异。

## 判断修改来源

选择能保持复用的最小来源：

| 修改 | 来源 |
| --- | --- |
| Portable Skill instruction 或 asset | Canonical Agent Plugin |
| MCP Server identity 或可移植连接设置 | Canonical Agent Plugin |
| PowerContext client、scope、prepare、capture、checkpoint、budget、diagnostic 或 fail-open 行为 | Agent Integration Core |
| 宿主事件名称、payload mapping、output mapping、package field、alias 或 generated path | Target Profile |
| 宿主事件注册、payload decoding、lifecycle timing、state、result injection 或宿主 API 调用 | Target Hook |
| Target Distribution 中的 generated file | 无；修改其来源并重新构建 |

Agent Integration Core 是 PowerContext 集成行为的默认所有者。当前大多数 maintained plugin 已经重复实现了相同流程，没有分别维护的实际理由。Target Hook 只负责宿主边界并依赖 Core；Core 不依赖 Target Hook。如果宿主无法保留 Core Operation 的语义，Target Profile 必须记录该约束及其 capability difference，而不是增加另一套实现。

## 映射生命周期 hook

PowerContext 为自身负责的行为定义带版本的 Core Operation。Target Profile 把宿主事件映射到这些 operation，并记录手写 Target Hook 的调用方式。

```text
Host Event
  -> Handwritten Target Hook
  -> Agent Integration Core Operation
  -> PowerContext Server
  -> Core Result
  -> Target Hook
  -> Host Result
```

依赖关系是单向的：`Target Hook -> Agent Integration Core -> PowerContext Server`。Core 负责 scope 解析、request budget、Server 调用、response validation、diagnostic、idempotency 和 fail-open 行为，不依赖任何 Target Hook code。

Target Hook 负责 event registration、宿主可用字段、output shape、lifecycle ordering、invocation multiplicity、宿主 state 和 result injection。Target Profile 记录这些选择，以便校验 hook 行为和 capability claim。这是 PowerContext 内部契约，并不表示不同宿主的生命周期模型可以互换。

## 组合插件 component

组合过程遵循所有权，不采用通用 deep merge：

1. Canonical plugin field 和 portable component 保持 canonical identity。
2. Target data 只能增加 Target Profile 允许的 namespaced client extension 和 provider field。
3. Agent Integration Core asset 来自已声明的 Core source。
4. 手写 Target Hook file 只占用分配给该 hook 的路径。
5. Generated path 只能按照已记录的 output ownership 替换或删除。
6. 未声明的 collision 或 override 属于错误。

如果多个输入共同组成一个 target artifact，Target Profile 必须记录对应规则。Build 必须拒绝有歧义的 precedence。任意 text patch 和无边界 overlay 不是有效的 merge rule。

安装到现有用户环境属于另一个 merge boundary。Installer 必须保留 user-owned data，只修改 distribution 已声明拥有的 key 或 path。

## 示例

### 修改共享 Skill 行为

Project memory routing 的修正属于 canonical `powercontext-project-context` Skill。每个支持该 Skill 的 target 都会在下一次 projection 中获得相同内容。Host-specific invocation hint 保留在已声明的 client extension 中，不能替换 canonical safety 或 routing rule。

### 转换 MCP 配置

Canonical Agent Plugin 使用 `powercontext` 标识 MCP Server，并声明 portable connection intent。如果宿主使用不同字段引用环境提供的 authorization header，由 Target Profile 声明该映射。Target 保持 canonical server identity，且不会把 resolved credential 写入 source 或 generated file。

### 复用 prompt hook

Codex、Claude Code 和 WorkBuddy 可以为 prompt event 使用不同 payload 或 command form。它们的手写 Target Hook 把宿主事件映射到同一个 Core Operation。Agent Integration Core 负责 context preparation、Source capture、request budget、diagnostic 和 fail-open behavior。各 Target Hook 处理宿主 input 和 output shape，Target Profile 负责记录并校验映射。

## 修改共享内容

修改 Skill、MCP 配置、Core Operation、Target Hook 或 Target Profile 时：

1. 修改 authoritative source。
2. 构建所有受影响的 Target Distribution。
3. 一起评审 source change 和 projected difference。
4. 执行 conformance、contract 和 drift check。
5. 按仓库正常流程发布 source 和 derived artifact。

项目可以提供全量 target 和单一 target 的命令，但命令名称不属于本 RFC。

## 新增或迁移 target

迁移按 target 逐步进行：

1. 盘点当前 target，把每个文件归入 Canonical Agent Plugin、Agent Integration Core、Target Profile、Target Hook 或 generated output。
2. 增加 Target Profile，描述当前已支持行为，不要消除有意保留的差异。
3. 使用 Canonical Agent Plugin 的投影替换 portable content 副本，并把重复的公共行为替换为对 Agent Integration Core 的调用。
4. 对比构建后的 distribution 与原可安装制品，并测试可观察行为。
5. 启用 drift check，再删除旧的维护副本。

迁移期间，每个 path 只能有一个 source of truth。尚未迁移某个 component 的 target 可以继续拥有它，但 profile 必须记录这个状态。同一 component 不能同时归 canonical source 和 target 所有。

# Reference-level explanation

`MUST`、`MUST NOT`、`SHOULD`、`SHOULD NOT` 和 `MAY` 表示规范性要求。

## 术语

| 术语 | 定义 |
| --- | --- |
| **Canonical Agent Plugin** | 拥有 PowerContext 可移植集成内容并符合 Agent Plugins 规范的插件。 |
| **Agent Integration Core** | 所有 maintained Agent plugin 共享的、由项目维护的 PowerContext client 和标准集成逻辑。 |
| **Core Operation** | Agent Integration Core 公开的带版本 PowerContext integration operation。 |
| **Target Profile** | 一个 maintained target 的经过校验的 machine-readable contract。 |
| **Target Hook** | 把宿主特有生命周期事件、payload、result 和 API 转换为 Core Operation 调用的手写 target code。 |
| **Projection** | 根据已声明输入组装 Target Distribution 的确定性过程。 |
| **Target Distribution** | 一个 target 的完整可安装制品。 |
| **Output Ownership Record** | 记录 generated path 及其 source owner 的 machine-readable list。 |

## Source ownership

每个维护中的 integration component 必须有且只有一个 authoritative source。Generated artifact 不得成为 authoritative source，也不得独立编辑。

项目必须能够直接识别每个 generated path 的 source owner，不能依靠文件名或目录约定推断所有权。

## Canonical Agent Plugin

Canonical Agent Plugin 必须符合 Agent Plugins 规范。Skills 和 MCP 配置必须符合各自引用的规范。

Portable component 必须使用稳定的 PowerContext identity。Target Profile 或 Target Hook 不得静默重命名或替换 canonical Skill、MCP Server 或 operation。

Canonical Skill body 不得插入 host-specific instruction。宿主可以通过已声明的 client extension 或其他 target-owned surface 提供附加 guidance。Target Profile 必须说明该 guidance 如何加载，以及它与 canonical guidance 冲突时如何处理。

如果宿主无法保留 required portable component，target 必须把它报告为 unsupported。它不得以 canonical identity 发布不完整的 component。

## Agent Integration Core

Agent Integration Core 是所有 maintained Agent plugin 共用的 PowerContext client 和标准集成逻辑的权威实现。它必须公开带版本的 Core Operation，并负责 operation 可观察的 input、output、idempotency、budget、diagnostic 和 failure semantics。

Core 的交付形态属于实现决定。项目可以通过 library、executable、service boundary、target-compatible client binding 或其他带版本机制公开同一个实现。本 RFC 不要求所有 Target Hook 使用相同语言或 process model。

Core 必须负责公共 client transport、error normalization、prepared-context validation、scope resolution、context preparation、Source capture、checkpoint 和 flush sequencing、request budget、diagnostic、idempotency，以及适用时的 fail-open policy。它必须复用 Server 已有的 domain operation，不得重复 Memory、Handoff、persistence、ranking 或 rendering policy。

Core 不得负责 host event registration、host lifecycle timing、host payload 或 result shape、host session state、host user interface，以及 host privacy 和 consent control。Core 不得 import 或依赖 Target Hook code。

每个 maintained Agent plugin 都必须使用同一个由项目维护的 Core implementation。项目可以为不同 target 提供不同 packaging form，但这些形态必须来自同一个已声明的 Core release，且不得形成独立的 behavior owner。Target Hook 不得重新实现 Core Operation。如果宿主无法以 operation 要求的数据或生命周期语义调用它，target 必须把该 capability 报告为 unsupported 或 not applicable。

## Target Profile

每个 maintained target 必须有一个经过校验的 Target Profile。它的序列化格式和仓库位置属于实现决定。

Profile 必须声明：

- target identity 及引用的 capability record；
- projected canonical component 和显式 omission；
- target 使用的 Core Operation；
- lifecycle hook mapping；
- component merge 和 conflict rule；
- target-owned extension data 和 Target Hook input；
- compatibility alias 及其生命周期；
- generated output ownership；
- 独立所有的 release metadata，如存在。

Profile 不得包含 secret、resolved credential、executable business logic、Skill body、完整 MCP document 或任意 text patch。

## Target Hook

Target Hook 必须在宿主边界手写。它负责 host event registration、payload decoding、lifecycle timing 和 multiplicity、host state、result injection、host API 调用，以及 host-specific privacy 或 consent behavior。它必须调用 Core Operation 实现 PowerContext 集成行为，不得分叉 portable content 或 Core behavior。

Target Hook 依赖 Agent Integration Core，Core 不得依赖 Target Hook。Target Hook 必须具备聚焦于宿主边界的 test。Hook 的存在不代表某项 capability 已经成立；已声明的 hook mapping 和 behavioral evidence 仍然是判断依据。

## Hook mapping

对于 capability contract 涵盖的每个 Core Operation，Target Profile 必须声明：

- host event 或 invocation surface；
- Target Hook entry point 和 input mapping；
- result mapping 和 injection surface；
- 会影响行为的 timing、ordering 和 multiplicity constraint；
- failure 和 diagnostic behavior；
- operation 属于 supported、unsupported 还是 not applicable。

不允许静默遗漏。如果 host event 缺少 Core Operation 所需的数据或时机，hook mapping 不得声称两者语义等价。如果一个 supported capability 没有同时具备 Core Operation、hook mapping 和相互一致的宿主边界 behavioral evidence，hook conformance validation 必须失败。

## Merge rule

Merge behavior 必须显式且有边界。Canonical core field 保持 canonical。Target-specific manifest data 和 file 必须使用宿主定义并记录在 profile 中的 namespace 或 ownership boundary。

如果两个输入声明同一 field 或 output path，build 必须应用已声明规则，否则失败。它不得根据 file order、directory order、discovery order 或 adapter implementation detail 选择结果。

删除 stale generated file 时，只能处理上一个 Output Ownership Record 记录的路径，并重新校验这些路径位于 target output root 内。Build 不得递归清理混合所有权的 integration directory。

## Capability

Target Profile 必须引用 [#1357](https://github.com/oceanbase/powercontext/issues/1357) 维护的带版本 integration capability contract，不得创建第二套 capability vocabulary。

Projected capability 需要同时满足：

- canonical component 或 Core Operation 提供该行为；
- Target Hook mapping 保留所需语义；
- target capability record 声明支持；
- focused test 提供 behavioral evidence。

File、tool count 或 adapter branch 不能作为 capability evidence。

## 确定性投影

Projection 必须是 versioned repository input 或 release input 的纯函数。它不得依赖 network access、wall-clock time、locale、ambient user configuration、resolved secret 或未声明的 environment state。

相同输入必须产生逐字节相同的规范化输出。Build 必须使用 stable serialization，并拒绝 absolute、escaping、duplicate、symlinked 或 undeclared output path。

确定性适用于完整 Target Distribution，包括打包的 Agent Integration Core asset、手写 Target Hook source 或 built output、provider manifest 和任何 target-specific build step 的输出。Target 可以使用宿主 build tool，但必须声明并固定它的 input、version 和 output。

Projection 应生成 portable plugin content、provider manifest、operation map、能够声明式表达的 hook registration、capability matrix 和 output ownership record 等结构性制品。它不得生成手写 Target Hook 的 business logic。确定性要求完整 distribution 能够由已声明输入复现，不要求每个输入本身都由生成器产生。

项目必须提供 write mode 和 non-writing check mode。Check mode 必须报告 drift、stale owned file、undeclared omission、invalid mapping 和 nondeterministic output。具体命令属于实现细节。

## 命名与兼容性

Canonical name 如下：

| Entity                | Canonical form                                    |
| --------------------- | ------------------------------------------------- |
| Plugin                | `powercontext`                                    |
| Project context Skill | `powercontext-project-context`                    |
| MCP server            | `powercontext`                                    |
| API operation         | OpenAPI `<operation_id>`                          |
| MCP tool              | `<operation_id>` within the `powercontext` server |
| Native global tool    | `powercontext_<operation_id>`                     |

宿主能够表示这些名称时，target 必须保持 canonical name。Compatibility alias 必须在 Target Profile 中显式声明，具有已记录的引入和移除策略，并与 canonical identity 分开。

## Generated artifact 与 release

项目可以把 generated distribution 提交到仓库，也可以在 release 时生成，或者同时采用两种方式。无论选择哪一种，发布制品都必须能够根据已声明输入复现，并通过 check mode。

Generated artifact 必须由普通文件组成。安装过程不得依赖 repository symlink、submodule 或未声明的 local build。

Canonical plugin version 和独立发布的 Agent Integration Core version 可以不同。Target Profile 必须声明每个 version field 的所有者，避免把一个来源的版本表述成另一个来源的版本。

## 安全

Projection 是 packaging，不是 authorization。Generated declaration 不会授予 runtime 和用户配置没有提供的权限或访问能力。

Build 必须校验 source 和 output path containment。Source、profile、generated artifact 和 diagnostic 不得包含 resolved credential、access token、prompt body、stored memory 或其他 user content。

Profile 可以引用 environment variable name 或 provider secret store，但不得包含 credential value。

## 符合性

Maintained target 满足以下条件时，符合本 RFC：

- portable Skill 和 MCP 配置来自 Canonical Agent Plugin；
- 每项已声明 lifecycle capability 都使用共享 Agent Integration Core 中对应的 Core Operation；
- hook、capability 和 merge mapping 已显式声明、相互一致并经过校验；
- 每个 generated path 只有一个 source owner；
- 完整 distribution 可以复现；
- check mode 可以发现 drift 和 invalid ownership；
- focused test 验证已声明的宿主行为和 failure semantics。

# Drawbacks

这套模型增加了 profile schema、build check、共享 Agent Integration Core 和宿主边界测试。Provider format 变化时，即使 PowerContext 行为没有变化，也可能需要更新 Target Profile 或 Target Hook。迁移复制文件到 generated ownership 的过程中也会产生较大的 diff。

这些成本可以直接评审和测试。独立副本的工具成本更低，但会让行为 drift 成为日常维护的一部分。

# Rationale and alternatives

这套设计让 Agent Plugins package 本身保持可用，让公共 client 和 integration behavior 只有一个实现，并把宿主差异保留在经过校验的 Target Profile 和手写 Target Hook 中。单向依赖形成清晰边界，同时不假设不同宿主的生命周期模型可以互换。

如果不采用这套设计，portable file 和 hook behavior 仍然是彼此独立的维护面。Drift check 可以发现复制文件的差异，但不能建立公共所有权，也不能阻止语义等价的 integration behavior 分化。

## 独立维护所有 distribution

这种方式不需要 projection system，但会保留现有 drift，并重复处理每次共享修正。

## 只共享 Skills 和 MCP 配置

这种方式使用 Agent Plugins 的 portable 部分，但 client orchestration、diagnostic、scope 和 failure behavior 仍由多个实现分别维护，无法满足公共实现规则。

## 定义通用宿主生命周期 API

宿主事件的时机、payload 和 guarantee 不同。PowerContext 需要由 hook 调用自身的 Core Operation，不需要再定义一套宿主 runtime 标准。

## 从模板生成 Target Hook business logic

模板可以隐藏重复，却会使宿主生命周期代码更难评审。本标准通过 Core 共享公共行为，在宿主边界保留手写 hook，并只对结构性制品和校验数据使用生成。

## 使用不受限 overlay 或 deep merge

Overlay 的 precedence 难以审计，也允许 target configuration 在内部形成 canonical content fork。本 RFC 使用显式 ownership 和有边界的 merge rule。

# Prior art

[Agent Plugins specification](https://agent-plugins.org/specification) 定义了 portable package，以及 Agent Skills 和 MCP 配置的固定位置。它也定义了 namespaced client extension，但没有为其分配 portable lifecycle semantics。本 RFC 沿用这个边界，不把 PowerContext hook behavior 加入 portable core。

[Agent Skills specification](https://agentskills.io/) 定义 Skill directory 和 `SKILL.md` contract，不定义 MCP projection、hook behavior 或 host package assembly。

PowerContext 已经把 OpenAPI operation metadata 投影到 integration code，并对结果执行 drift check。本 RFC 采用相同的 source、projection 和 check 模式，并增加所有权与 lifecycle mapping 规则。

# Unresolved questions

Ownership 和 projection model 没有阻碍接受本 RFC 的未决问题。以下实现选择不属于本 RFC：

- Agent Integration Core 的交付形态和 language binding；
- Target Profile 的序列化格式和 schema 位置；
- build command 名称和 projection 的内部架构；
- 首批迁移的 target 和 Core Operation；
- generated distribution 是提交到仓库、在 release 时生成，还是两者兼有。

这些选择必须满足本 RFC 的 ownership、mapping、determinism 和 conformance 规则，不改变这里定义的架构。

# Future possibilities

同一模型可以支持更多 PowerContext plugin、更多 Core Operation、公共 Target Profile 和可复现的 release attestation。如果其他项目采用相同的 ownership 和 mapping rule，projection tool 也可以独立为 reusable package。
