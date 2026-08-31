- Proposal Name: `agent_plugin_distribution_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#1410](https://github.com/oceanbase/powercontext/pull/1410)
- Tracking Issue: [oceanbase/powercontext#1405](https://github.com/oceanbase/powercontext/issues/1405)

# Summary

PowerContext 将使用现有的 `integrations/agent-plugin/powercontext/` [Agent Plugins](https://agent-plugins.org/)
package 作为可移植集成内容唯一的手工维护来源，并通过确定性投影生成宿主原生 distribution。Agent Skills、可移植 MCP
配置、插件元数据和共享命名只维护一次。Target adapter 根据经过校验的 target descriptor，把该来源转换为宿主打包方言。
依赖宿主生命周期的行为继续由手写 native runtime 实现。生成的 distribution 会提交到仓库用于评审和发布，但永远不是
权威来源。

# Motivation

PowerContext 当前为多个 Agent 宿主提供集成。这些宿主在扩展目录、MCP 配置、安装方式、生命周期 hook 和 runtime API
方面确实不同。我们不应该用一个虚构的通用宿主 API 掩盖这些差异。

但是，各个 distribution 也在独立维护本质上与宿主无关的内容：

- `powercontext-project-context` Skill 及其使用指导；
- MCP Server 的标识和连接意图；
- 插件名称、版本、描述和仓库元数据；
- operation 和 tool 的命名规范；
- 多个 distribution 共用的辅助资源。

这些副本已经出现名称、tool prefix、MCP 结构、版本和 Skill 行为不一致的问题。每项修正都必须在每个宿主中重新发现和
应用。增加新宿主会成倍增加维护成本，也使评审者把精力用于核对复制文本，而非判断有意的宿主差异。

目标不是让所有集成完全相同，而是建立一条唯一的所有权规则：

> 可移植行为只编写一次，宿主打包差异必须显式描述，只有依赖宿主 runtime 的行为才单独手写。

本 RFC 确立该规则，并定义执行它所需的生成边界。

# Guide-level explanation

## Mental model

Distribution 系统由四个具名部分组成：

| 部分 | 含义 |
| --- | --- |
| **Canonical Plugin** | 包含可移植元数据、Skills 和 MCP 配置，可直接安装的 Agent Plugins package。 |
| **Target Adapter** | 面向某一种宿主打包格式的确定性投影逻辑。 |
| **Native Runtime** | 使用宿主特有 hook、API 或生命周期语义的手写代码。 |
| **Target Distribution** | 用户针对某个宿主实际安装、并提交到仓库的文件。 |

它们之间的关系是：

```text
Canonical Plugin + Target Adapter + Native Runtime = Target Distribution
```

Distribution 的有效能力受以下交集约束：

```text
effective capabilities = canonical capabilities ∩ host capabilities ∩ adapter support
```

因此，生成机制不承诺不同宿主具备相同功能。它承诺每项可移植能力只有一个来源，任何遗漏都必须显式记录。

## Ownership rule

贡献者通过判断差异产生的原因，决定应该修改哪里：

| 变更 | 权威位置 |
| --- | --- |
| 多个宿主共享的 Skill instructions | Canonical Plugin |
| MCP Server 标识和可移植连接配置 | Canonical Plugin |
| 宿主 manifest 结构或配置字段映射 | Target Adapter |
| 无可移植表达的 provider-only manifest 字段 | 由 Target Adapter 消费、经过校验的 target descriptor |
| Session hook、tool 注册、事件处理或宿主 API | Native Runtime |
| 宿主目录中的生成副本 | 不在这里修改；修改来源并重新生成 |

例如，project memory recall 指导的修正只在
`integrations/agent-plugin/powercontext/skills/powercontext-project-context/SKILL.md` 中进行一次。Claude Code 专属
生命周期 hook 仍然在 Claude Code runtime 中修改。某个宿主采用不同的 MCP header 语法时，差异属于对应 target
descriptor 和 adapter，而不是另一份 MCP Server 定义。

## Contributor workflow

生成器提供三个稳定工作流：

```text
uv run python -m scripts.agent_plugins build
uv run python -m scripts.agent_plugins build --target codex
uv run python -m scripts.agent_plugins check
```

第一个命令重新生成所有 target distribution；第二个命令把本地迭代限制到一个 target；第三个命令执行校验，并在提交的
生成文件与全新投影不一致时失败。

常规变更按以下顺序进行：

1. 根据 ownership rule 修改 Canonical Plugin、Target Adapter 或 Native Runtime。
2. 重新生成受影响的 distribution。
3. 把 source diff 和 generated diff 放在一起评审。
4. 执行 conformance 和 drift 检查。
5. 同时提交 source 和 generated artifact。

生成结果必须是普通文件。安装包和源码归档不能依赖仓库 symlink、submodule 或用户本地执行构建步骤。

## Examples

### 修改共享 Skill 行为

Issue [#1378](https://github.com/oceanbase/powercontext/issues/1378) 要求 `powercontext-project-context` Skill 识别显式
memory 请求。接受后的 trigger contract 应在该 canonical Skill 中修改。Adapter 原样复制 Skill，不能重命名、替换正文、
追加任意 provider instruction 或覆盖 frontmatter。

### 修改 MCP 配置

规范的 `mcp.json` 使用 `powercontext` 作为 Server 标识，并表达 Agent Plugins 支持的连接设置。如果某个宿主以不同语法
引用环境变量中的 HTTP headers，对应 adapter 从 target descriptor 的 allowlist 数据中完成翻译。Target descriptor
不能用无关定义替换规范的 Server 标识、URL 或 transport。

### 修改生命周期行为

如果 Pi 需要通过 runtime event flush 或 restore context，该实现继续位于 Pi 的 TypeScript runtime。生成器可以打包
该 runtime 并校验其声明的能力，但不生成事件处理的业务逻辑。

## Migration

迁移采用增量方式：

1. 盘点现有 distribution，把每个文件分类为 portable source、adapter policy、native runtime 或 generated output。
2. 采用现有 Canonical Plugin，同时保持现有安装行为不变。
3. 每次为一个宿主添加 adapter，并把输出与现有 distribution 比较。
4. 将已迁移的 portable file 切换为生成所有权，并对该 target 启用 drift check。
5. 规范化名称，原子迁移 legacy `project-context` installation，并在有限 release window 内保留有文档的 tool alias。
6. 所有受支持的安装路径都消费 generated distribution 后，移除废弃副本和 alias。

Target 完成第 4 步之前，其当前目录仍是权威来源。迁移期间，一个文件不能同时声明两个来源。

# Reference-level explanation

## Scope and responsibility boundaries

本 RFC 负责打包内容的 source of truth、投影机制、generated artifact 策略和共享命名。它与现有工作组合，但不取代这些
工作：

| 工作项 | 职责 |
| --- | --- |
| [#1244](https://github.com/oceanbase/powercontext/issues/1244) | 提供现有 reusable Agent Plugin，作为 canonical source。 |
| [#1301](https://github.com/oceanbase/powercontext/issues/1301) | 负责 multi-host 安装和用户配置。 |
| [#1338](https://github.com/oceanbase/powercontext/issues/1338) | 定义 coding agent access 的预期能力和面向用户的一致性。 |
| [#1352](https://github.com/oceanbase/powercontext/issues/1352) | 负责更广泛的 Agent integration roadmap。 |
| [#1357](https://github.com/oceanbase/powercontext/issues/1357) | 定义带版本的 integration capability 词汇、状态和证据。 |
| [#1362](https://github.com/oceanbase/powercontext/issues/1362) | 定义 lifecycle-aware integration 行为和 host-neutral lifecycle contract。 |
| [#1378](https://github.com/oceanbase/powercontext/issues/1378) | 负责 canonical Skill 中的显式 memory routing 行为。 |
| [#1397](https://github.com/oceanbase/powercontext/issues/1397) | 定义独立 managed Skill package 的发布和安装生命周期。 |

本 RFC 明确不处理以下事项：

- 定义通用 lifecycle-hook API；
- 强制所有宿主暴露相同的能力或 tool；
- 从模板生成宿主原生业务逻辑；
- 发布或安装独立 managed Skill package；
- 在第一阶段构建面向整个生态的通用 compiler；
- 重写拥有独立发布生命周期、已经过评审的 managed Skill。

## Repository model

Source layout 在现有 canonical package 上扩展：

```text
integrations/
├── agent-plugin/
│   ├── powercontext/
│   │   ├── plugin.json
│   │   ├── mcp.json
│   │   └── skills/
│   │       └── powercontext-project-context/
│   │           ├── SKILL.md
│   │           ├── references/
│   │           └── scripts/
│   └── targets/
│       ├── target.schema.json
│       ├── claude-code.json
│       ├── codex.json
│       └── ...
├── claude-code/
├── codex/
└── ...
scripts/
└── agent_plugins/
    ├── __main__.py
    ├── model.py
    └── targets/
```

`integrations/agent-plugin/powercontext/` 仍可直接安装，并作为可移植内容的来源。
`integrations/agent-plugin/targets/` 保存经过 schema 校验的 target descriptor，而不是文本 patch 或 replacement template。
`scripts/agent_plugins/targets/` 保存 adapter 实现。

现有宿主目录仍然是安装和发布边界，其中同时包含 Native Runtime 文件和生成的投影。Target definition 记录准确的生成
路径，从而无需根据目录名称推断所有权。

## Artifact classes

每个 integration path 只能属于一个类别：

| 类别 | 手工修改 | 提交到仓库 | Drift check |
| --- | --- | --- | --- |
| Canonical source | 是 | 是 | 校验 conformance |
| Target descriptor 或 adapter | 是 | 是 | 根据 schema 和 contract 校验 |
| Native Runtime | 是 | 是 | 由宿主 integration 测试 |
| Generated distribution artifact | 否 | 是 | normalization 后逐字节比较 |

Target 格式允许注释时，生成文件包含 source marker。不允许注释的格式由 target definition 和 generator output manifest
跟踪。Marker 只用于提示，声明的 output ownership 才是权威依据。

## Canonical plugin contract

Canonical Plugin 遵循 Agent Plugins specification：

- `plugin.json` 是 portable manifest，并声明 specification schema version；
- `skills/` 的直接子目录遵循 Agent Skills；
- `mcp.json` 包含 portable MCP Server 配置，并使用相同的 specification version；
- 规范 Skill 的目录名和 frontmatter name 都是 `powercontext-project-context`；
- 所有引用路径都解析在 plugin root 内；
- 打包的 Skill 使用真实文件，不使用指向外部的 symlink。

规范 package 可以由兼容宿主直接安装。因此，这类宿主的 adapter 可以只是 identity projection 加 release metadata，而不是
维护第二套 manifest 方言。

## Target adapter contract

每个 Target Adapter 接收经过校验的 typed model，而不是未经处理的 source text。输入包括：

- Canonical Plugin model；
- target identifier 和 target capability record；
- 经过 schema 校验的 target descriptor；
- version 和 repository release metadata；
- repository root 和 allowlist output root。

输出包括：

- 从规范化相对路径到 bytes 的映射；
- structured diagnostics；
- projected、omitted 和 unsupported capability 的 machine-readable record。

Adapter 必须把每个 canonical component 分类为 `projected`、`unsupported` 或 `not_applicable`。静默遗漏属于错误。
不支持的 required capability 会使生成失败；不支持的 optional capability 必须产生显式 diagnostic 和 capability record。

Adapter 只包含结构转换，不包含内容分叉。它可以映射 provider field key、包裹 document、选择受支持的 component，以及
生成 provider metadata；不能重命名 canonical identity，也不能包含另一份 Skill 正文、portable MCP Server 定义或宿主
runtime 业务逻辑。

## Target descriptors

每个 maintained distribution 对应一个由 `target.schema.json` 校验的 descriptor。Descriptor 声明 target ID、output root、
adapter kind、capability-manifest entry、provider-only manifest field、native runtime root、compatibility alias 和
generated-output manifest 路径。

Target descriptor 必须满足：

- 只能填充 adapter schema 明确允许的 provider field；
- 可以引用 canonical component 和 OpenAPI operation identifier；
- 不能包含 Skill 正文、MCP document、可执行代码或任意文本 patch；
- 不能覆盖 canonical plugin version、Skill frontmatter 或 MCP Server 标识；
- 不能包含 secret 或环境专属 credential value。

有条件的结构转换属于经过评审的 Target Adapter，宿主行为属于 Native Runtime。这个边界刻意不引入通用 overlay language。

## Target set and capability manifest

`integrations/agent-plugin/targets/` 中通过校验的 descriptor 集合就是 normative target set。`integrations/`
其他位置存在目录，不代表它自动成为 generator target。每个 descriptor 必须解析到
[#1357](https://github.com/oceanbase/powercontext/issues/1357) versioned manifest 中的 `agent_host` entry，且不能引用
`unsupported` 或 `proposed` integration。

Generator 消费 target integration ID、availability 和声明的 capability set，以计算 effective intersection；并校验
projected 和 unsupported capability record 与 manifest 一致。Evidence path 只校验是否存在，其行为声明仍由专门的
integration test 负责。Generator 不从文件、tool 数量或 runtime introspection 推断 capability。

## Projection algorithm

生成是 repository input 的纯函数，按以下步骤执行：

1. 加载 Canonical Plugin 并进行 schema validation。
2. 校验 Agent Skills 和 MCP 配置，包括 path containment。
3. 加载并校验 target descriptor 及其引用的 capability record。
4. 计算 effective capability intersection。
5. 请求 Target Adapter 生成完整 output map 和 diagnostics。
6. 拒绝重复、绝对、越界、symlink 或未声明的 output path。
7. 将 UTF-8 文本规范化为 LF line ending、稳定 key ordering 和一个 trailing newline。
8. 生成普通文件，并移除先前由 generator 所有的 stale file。
9. 在 check mode 中不写文件，只比较规范化 output map 和已提交文件。
10. 输出可执行的错误，其中包含 source component、target 和违反的 contract。

生成不得依赖网络、wall-clock time、locale、用户配置或 secret。相同输入必须产生逐字节相同的输出。

删除 stale file 时，只能处理上一个 generated-output manifest 记录、并重新验证位于 target output root 下的路径。生成器
绝不能递归清理整个 integration directory。

## Skill projection

规范 built-in Skill 只有一个名称：`powercontext-project-context`。其目录名和 `SKILL.md` frontmatter 必须与该值完全一致。
所有 target distribution 使用同一个名称；adapter 不能缩短、限定或以其他方式编码该名称。无法保留名称的宿主将该 Skill
报告为 unsupported。

完整 canonical Skill 按字节原样复制。Version 1 不定义自由文本 extension slot，也不允许改写 Skill 正文。Skill 通过
canonical OpenAPI operation ID 指代 PowerContext operation，不嵌入宿主专属 callable spelling。Provider-specific
guidance 保持为单独的 Native Runtime asset。

Adapter 必须保留：

- canonical Skill name 和语义目的；
- 所有 portable trigger 和 safety instruction；
- 引用的 asset、script 和 reference；
- relative internal link structure。

无法保留这些属性的宿主应将该 Skill 报告为 unsupported，而不是发布具有误导性的残缺副本。

## MCP projection

规范 MCP Server identifier 为 `powercontext`。Portable transport、command、argument、URL、environment 和 header intent
在 Agent Plugins schema 支持时通过 `mcp.json` 表示。

Target adapter 可以转换配置语法，例如包裹 `mcpServers`、映射来自环境变量的 HTTP header，或者替换 provider 的
plugin-root placeholder。Provider-only 配置必须来自 allowlist 中的 target-descriptor field。Adapter 不改变 MCP wire
behavior，也不复制 OpenAPI operation contract。

Credential 只能引用 runtime environment variable 或 provider secret store。Canonical source、target descriptor、
generated artifact 和 diagnostic 都不能包含解析后的 credential。

## Native Runtime boundary

Native Runtime 继续负责：

- provider lifecycle hook 和 event ordering；
- native tool registration 和 invocation；
- session state 和 host storage API；
- provider-specific authentication 或 consent flow；
- host UI、command 和 error presentation。

生成器可以复制、打包或校验已声明的 Native Runtime entry point，但不生成其业务逻辑。当实际重复足以证明抽取价值时，
可以引入 shared runtime library；它不是本 RFC 的前提，也不意味着需要通用 lifecycle abstraction。

## Implementation language boundary

确定性 compiler 使用 Python 3.11+ 实现，因为 PowerContext 本身是 Python 项目，并且已经使用 Python 完成 repository
generator 和 validation。实现复用项目现有 schema tooling，并遵循 `scripts/generate_js_operations.py` 已有的 write/check
模式。

这一选择不限制 runtime language：

- TypeScript integration 保留 TypeScript Native Runtime；
- Python hook 和 integration 保留 Python Native Runtime；
- JSON、YAML、Markdown、OpenAPI 和 JSON Schema 继续作为 language-neutral contract。

Compiler 在渲染期间不执行任何 Node 或 Python provider runtime。Target-specific runtime build 继续由现有 package workflow
负责。

## Central naming contract

规范名称如下：

| Entity | Canonical form |
| --- | --- |
| Plugin | `powercontext` |
| Project context Skill | `powercontext-project-context` |
| MCP Server | `powercontext` |
| API operation | OpenAPI `<operation_id>` |
| MCP tool | `powercontext` Server 内的 `<operation_id>` |
| Native global tool | `powercontext_<operation_id>` |
| Transitional compatibility alias | 显式 target mapping，包括现有 `pc_*` 名称 |

该表是唯一的 naming contract。Generator 根据 Canonical Plugin 和 OpenAPI 校验这些值；Target descriptor 可以引用，
但不能重命名、限定、缩短或覆盖。宿主无法表示 canonical name 时，该 component 对此宿主属于 unsupported，而不是获得
另一个 identity。

现有 `pc_*` 名称并不总是 operation-ID 的机械前缀，因此 compatibility alias 必须逐项列出。它们是迁移辅助，不是
canonical name。新增 alias 必须记录引入和移除 release，在宿主支持时携带 deprecation metadata，并至少保留两个
minor release 和 90 天。

## Generated-artifact and release policy

Generated distribution 会提交到仓库，因为它们是用户可安装的 release artifact，这能使 adapter 影响可评审，也使使用者
无需安装 generator dependency 即可从 source archive 安装。修改 canonical input 或 adapter logic 的 pull request 必须
包含对应 generated diff。

CI 以 check mode 执行生成，并在以下情况失败：

- schema 或 Agent Plugins conformance error；
- generated drift 或 stale owned file；
- 未声明的 component omission；
- output path 越过 target root；
- portable packaged content 中存在 symlink；
- naming 或 capability manifest 不一致；
- 两次 clean render 检测到 non-deterministic output。

Generator version 不以 timestamp 形式写入产物。Distribution version 来自 canonical manifest 的单一值，并投影到代表
该 distribution 的所有 provider manifest 或 marketplace entry。独立发布的 Native Runtime package 只有在 target
descriptor 将对应字段标记为独立所有时才能保留自身版本，也不能把该版本表示为 canonical plugin version。

## Compatibility and rollout

第一次 rollout 保持现有安装路径和 runtime 行为。投影期间发现的差异必须先分类，再做规范化：

- accidental divergence 在 canonical source 中修复；
- provider constraint 编码在 target descriptor 或 adapter 中；
- 真实 capability difference 记录到 capability manifest；
- lifecycle difference 继续保留在 Native Runtime code 中。

第一次 source migration 将现有 `project-context` 目录和 frontmatter 原子重命名为
`powercontext-project-context`。Generated distribution 不会同时生成两个 Skill name。Installer 可以在兼容窗口内识别并
替换 legacy directory，但 documentation、manifest 和 generated Skill 只使用 canonical name。

修改 native tool prefix 可能影响 prompt 和保存的配置。Generated distribution 至少保留 tool alias 两个 minor release
和 90 天；移除 alias 必须包含 release note 和 target descriptor 变更。无法安全暴露 alias 的宿主继续保留 legacy tool
name，直到单独评审 breaking change。本 RFC 不改变持久化 PowerContext memory format 或 HTTP API。

## Security and authority

投影属于打包过程，不属于授权过程。Generated declaration 不会授予宿主 runtime 和用户配置本身不具备的 capability、
permission 或 access。

生成器对 source reference 和 output 执行 path containment，只报告 structural diagnostic；diagnostic 不能包含解析后的
secret、prompt body、stored memory 或 access token。Target descriptor 是经过评审的 source file，可以引用 environment
variable name，但不能包含 credential value。

# Drawbacks

- 仓库仍会保存生成副本，从而增加 diff 和 checkout 体积。
- Generator 和 adapter schema 会成为需要持续维护、具有自身兼容性负担的基础设施。
- 贡献者必须理解 canonical source、adapter policy、Native Runtime 和 generated output 的区别。
- 即使 PowerContext 行为不变，provider format 变化也可能要求紧急更新 adapter。
- 现有 distribution 迁移及 alias 并存期间，部分变更会暂时产生更大的 diff。
- Target descriptor 的严格限制可能迫使一些原本可由宽松模板快速表达的场景新增小型 adapter module。

# Rationale and alternatives

## Why this design

Agent Plugins 为 Skill 和 MCP Server 定义了可信的可移植最小集合，同时没有声称要标准化所有 Agent runtime。将它作为
Canonical Plugin 后，中心副本本身有效，并可被兼容宿主直接消费。确定性 adapter 隔离无法避免的打包差异；Native
Runtime 边界则避免生成过程掩盖生命周期语义。

提交 generated output 是用仓库体积换取可评审性、离线安装和发布确定性。选择 Python compiler 与仓库现有 toolchain
一致，也能使生成器不依附任何一个 TypeScript 宿主。

## Alternatives considered

**继续独立维护。** 该方案没有迁移成本，但每增加一个宿主或修正一项共享内容都会放大 drift，无法满足所有权目标。

**只支持直接消费 Agent Plugins 的宿主。** 这是最简单的架构，但会因为原生打包或生命周期 surface 不同而放弃已有的
有效集成。Agent Plugins 是 portable core，不是完整的 PowerContext integration contract。

**选择某个现有 provider distribution 作为来源。** Claude Code 或 Codex manifest 包含 provider assumption，会使其他
provider 看起来像有损派生。Vendor-neutral canonical package 的边界更清晰。

**采用通用第三方 plugin compiler。** 目前没有成熟工具覆盖 PowerContext 所需的 Agent Plugins、committed provider
package、capability evidence 和 handwritten runtime 组合。在 target semantics 稳定前依赖外部 compiler 只会转移维护
成本。未来可以让 adapter contract 成为成熟外部 compiler 的 backend。

**使用 multi-agent Skill installer 作为 distribution system。** `npx skills` 等工具可以把一份 Skill 复制到多个宿主
位置，但不投影 MCP 配置、plugin manifest、marketplace metadata、capability degradation 或 Native Runtime packaging。
它们仍可作为安装消费方使用，但不能充当 source compiler。

**从模板生成 Native Runtime code。** 这能减少表面重复，却会把行为隐藏在模板中、耦合无关宿主 API，并使 lifecycle
变更更难评审。语义确实一致时，后续抽取 shared runtime library 更安全。

**从 distribution symlink 到共享目录。** Symlink 在 archive、package manager、Windows 和 plugin containment check 中
都比较脆弱。生成普通文件具有更好的可移植性。

**不提交 generated artifact。** 这会缩小仓库，但把 generator 和 toolchain 要求转嫁给安装者，也使 release content
在评审中不够直观。

**使用 TypeScript 实现 compiler。** TypeScript 与多个 Native Runtime 一致，但 compiler 是 repository build tool，
不是 runtime code。Python 能减少新 toolchain coupling，并与已有 generator 对齐。

# Prior art

[Agent Plugins specification](https://agent-plugins.org/specification) 定义了 canonical package model、Skill 和 MCP 配置
的固定位置、client extension namespace、schema versioning 和 path-containment rule。本 RFC 直接采用它作为 portable
source，而不是另行发明 plugin format。

[Agent Skills specification](https://agentskills.io/) 定义了 Skill directory 和 `SKILL.md` contract。它支持 portable
Skill content，但有意不处理 provider packaging、MCP projection 和 native lifecycle integration。

[Dodo Payments agent plugin](https://github.com/dodopayments/dodo-agent-plugin) 是相近的工程先例。它维护 Agent Plugins
source、provider metadata，以及一个带 drift checking、为多个宿主生成 package 的统一 generator。该项目放弃
symlinked Skill content 的实践也支持本 RFC 要求生成真实文件。PowerContext 采用其 build/check 模式，但不引入不受限的
overlay mechanism。

[wshobson/agents](https://github.com/wshobson/agents) 展示了更大 catalog 规模下的生成方式。其 adapter 和 capability-oriented
tooling 把大量 plugin 投影到多个 Agent harness。对本 RFC 有价值的经验是：target support 应显式且可测试，而不是把
target condition 分散到内容各处。

PowerContext 已在 `scripts/generate_js_operations.py` 中使用相同的 source/generate/check 模式，把 OpenAPI operation
投影到多个 TypeScript integration。本 RFC 将这一已经验证的仓库惯例推广到 plugin packaging，同时继续让 OpenAPI
contract 作为 operation identifier 的权威来源。

# Unresolved questions

没有阻碍本 RFC 接受的架构问题。Target descriptor 定义 normative target set；generated artifact 随各 target 迁移逐步
提交并启用 drift check；version 1 不提供自由文本 Skill extension slot；compatibility alias 至少保留两个 minor release
和 90 天；capability-manifest 边界已在上文定义。

Shared TypeScript 或 Python runtime library、公开第三方 adapter API 和自动宿主安装属于独立决策，需要各自的证据，
不阻碍本 distribution model。

# Future possibilities

相同 compiler model 可支持多个 PowerContext plugin、公开的第三方 adapter、manifest-driven support matrix、marketplace
metadata、provenance attestation 和 reproducible release bundle。如果其他项目收敛到相同需求，成熟的 adapter interface
可以被抽取为通用 Agent Plugins distribution tool。

当迁移数据证明 runtime 存在稳定重叠后，TypeScript 和 Python integration 可以共享小型 native library，而无需改变
canonical packaging model。Compatibility window 结束后，可以移除 generated alias 和 legacy provider shim，并在宿主
支持时优先直接消费 Agent Plugins。
