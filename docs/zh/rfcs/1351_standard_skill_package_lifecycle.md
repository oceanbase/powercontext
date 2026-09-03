- Proposal Name: `standard_skill_package_lifecycle`
- Start Date: 2026-08-21
- RFC PR: [oceanbase/powercontext#1351](https://github.com/oceanbase/powercontext/pull/1351)
- Related RFCs: [RFC 0031](0050_artifact_candidate_review_inbox.md)、
  [RFC 0051](0051_experience_skill_artifact_families.md)、
  [RFC 0072](0072_scoped_statistics_and_usage.md) 和
  [RFC 1304](1304_experience_skill_review_page.md)

# Summary

本 RFC 将 PowerContext 受管 `skill` Family 从只包含指令的记录升级为受治理的标准 Agent Skill 包，并闭合从
发现或创作，到 Review、Skills Library 检索、目标发布、使用观测、修订、废弃和安全取消发布的完整生命周期。

每个受管 Skill Revision 拥有一个以 `SKILL.md` 为根入口的不可变包。包还可以包含 `scripts/`、`references/`、
`assets/`，以及 Agent Skills 格式允许的其他有界文件。PowerContext 保存完整、内容寻址的包快照，在导入外部
Skill 时保留精确包，并把同一份已批准内容发布到兼容的 Codex 和 Claude Code 目标。Agent Adapter 只负责选择
位置和报告兼容性，不会静默改写已批准的包。

实现同时支持 PowerContext Server 所在宿主机上的 configured target，以及独立验收的远端分发切片。远端模式下，
Server 保存 target 的期望 Revision，Codex 或 Claude Code 集成内的轻量 Receiver 通过
默认通过 HTTPS Pull、校验、原子安装并回传精确 Receipt。远端宿主不需要运行完整 PowerContext Server 或数据库，但必须有
一个经过注册的 Receiver；Server 不通过 SSH 或远程文件系统主动写入 Agent 目录。

包声明内容和需求，不声明权限。Review、批准、检索、发布以及可选的 `allowed-tools` 字段都不会授予执行、文件
系统、网络、Secret 或安装依赖的权限。脚本执行仍由接收 Skill 的 Agent 和宿主策略负责。本 RFC 定义静态校验
与环境兼容性评估，但不新增通用的 PowerContext 脚本 Runner。

闭环如下：

```text
发现、上传或生成一个包
  -> 捕获精确包快照
  -> 校验格式、文件、来源和风险
  -> 创建 pending Candidate
  -> Review
  -> 批准为不可变 Skill 包 Revision
  -> 将当前 active Head 加入 Skills Library 索引
  -> 显式把精确 Revision 发布到本地 target，或声明为远端 target 的期望状态
  -> 远端 Receiver 在可用时收敛并回传 observed Revision 和 digest
  -> 在集成能够观测时记录有界的 selected/invoked/outcome 证据
  -> 提议后继 Revision、废弃、退役或安全取消发布
```

# Motivation

## 当前受管 Skill 还不是标准包

当前受管 Skill 保存 `name`、`description`、`instructions` 和 `validation`。发布时生成一个 `SKILL.md` 和一个
PowerContext manifest。这足以 Review 指令核心，却无法保留包含脚本、参考资料、模板、示例、许可证或二进制
资源的常规 Agent Skill 包。

当前 External Skill Registry 已经为本地包下的每个普通文件计算 fingerprint。然而显式导入只快照 `SKILL.md`，
随后要求生成模型产出新的指令型内容。这适合语义 Fork，但不适合精确 Import：即使用户选择了特定 package
fingerprint，有用的脚本或参考资料仍可能丢失。

因此现有流程并不完整：

```text
包含脚本和参考资料的外部包
  -> 精确的全包 fingerprint
  -> 仅 SKILL.md 的快照
  -> 生成的指令核心
  -> 仅 SKILL.md 的发布结果
```

PowerContext 需要从 Import 到 Publication 使用同一份 package contract，确保 Reviewer 批准的内容就是 Agent
最终发现的内容。

## 包可移植不代表运行时可移植

一个包可以复制到不同宿主机，但其脚本仍可能依赖特定操作系统、架构、解释器、可执行程序、工作目录、网络
策略或环境变量。即使 Codex 和 Claude Code 都接受相同的包结构，它们也可能运行在不同宿主策略之下。

PowerContext 不能通过为每个 target 生成不同且未经 Review 的包来解决差异。同一份已批准包始终是内容权威。
target 专属的环境 Profile 和可重建兼容性评估负责解释该 target 是否可用。能力缺失会产生 `incompatible`、
`unknown` 或 `manual_review_required`，不会触发自动改写包或安装依赖。

## 持续增长的 Library 需要受治理的工作集

包存储可以对相同内容去重，但仅靠存储无法消除发现噪音。如果把所有已批准 Skill 发布到每个 Agent 目录，
会产生名称冲突、暴露过期包，并在没有当前项目使用证据时持续扩大 Agent 工作集。

PowerContext 因此区分：

| 层次 | 含义 |
| --- | --- |
| External Registry | 对外部系统拥有的 Agent-native 包进行可重建观测 |
| Governed Library | 已批准的 PowerContext 受管 Skill 和可见外部 Registration |
| Active managed heads | 可进入常规 Library 检索和新发布的受管 Skill |
| Published set | 实际存在于某个已配置 Agent target 中的精确受管 Revision |
| Usage evidence | 对选择、调用、校验和任务结果的有界观测 |

Library 可以增长，但检索只覆盖当前合格 Head，发布仍然要求针对每个 target 显式执行。

## 批准不是生命周期终点

RFC 0051 和 RFC 1304 已经建立 Candidate Review、不可变 Artifact Revision 和显式的宿主本地发布。它们有意
延后了包托管、退役、取消发布、排序和使用归因。要形成可用的 Skills 产品，需要补齐剩余状态转换，同时继续
保留这些信任边界。

设计必须在不修改历史的前提下支持纠正和退役：

```text
Skill@1 已批准并发布
  -> 后续使用发现缺少一项校验步骤
  -> 精确使用证据指向 Skill@1
  -> Review 批准 Skill@2
  -> target 报告 update_available
  -> 显式发布只替换完整且仍受管的包
  -> Skill@1 仍可被精确读取
```

# Guide-level explanation

## 把 Skill 理解成包，而不是流程记录

一个标准受管 Skill 如下：

```text
release-check/
├── SKILL.md
├── scripts/
│   ├── verify.py
│   ├── linux/
│   │   └── prepare.sh
│   └── windows/
│       └── prepare.ps1
├── references/
│   └── release-policy.md
├── assets/
│   └── report-template.json
└── LICENSE
```

`SKILL.md` 是包入口。它的 YAML frontmatter 提供可发现的名称和描述，Markdown 正文告诉 Agent 何时以及如何
使用这个包。其他文件仍是普通 package resources；PowerContext 不会把它们转换为工作流图，也不会在 Import、
Review、批准、检索或发布阶段执行它们。

包字节是内容权威。Skills Library 展示的 name、description、compatibility 和其他解析值，都是从精确
`SKILL.md` 派生并校验的缓存，不是另一份可以独立编辑的内容。

## 理解内容进入 Library 的四种方式

### 发现外部 Skill

Discovery 记录本地 Registration、locator、package fingerprint、Agent kind、host 和 installation scope。外部目录
仍是内容权威。PowerContext 不会把包字节复制到受管 Artifact 中；目录消失或 fingerprint 漂移会使 Registration
变为 unavailable。

### 精确导入外部 Skill

用户选择一个可见的 `external_skill_id`、精确 fingerprint 和 **Import**。PowerContext 捕获包根目录下每个允许的
文件，确认源 fingerprint 在捕获期间没有变化，保存 canonical package snapshot，并创建 tree digest 完全相同的
pending Candidate。

精确 Import 不需要 LLM，也不会改写 `SKILL.md`。批准后会创建新的 PowerContext 受管 Skill 身份，其第一个
Revision 完整包含捕获的包。外部包和导入后的受管 Skill 成为两个独立内容权威，并通过 lineage 建立联系。

### Fork 外部 Skill

**Fork** 首先把相同的精确外部快照保存为不可变 Source evidence。之后由人工或已配置的生成模型提出另一份完整
包。Review 展示原始包与提案包的文件树和 diff。批准只会创建提案中的受管 Revision；原始快照仍可作为证据读取。

### 创作或生成受管 Skill

用户可以上传完整包，也可以由已配置的生成模型根据精确 SourceRef 和 ArtifactRef 证据提出一个包。生成在批准
事务之外完成。PowerContext 先校验和保存包，再创建 pending Candidate。模型不能批准自己的 Candidate、分配最终
Artifact identity、发布包或获得脚本执行权限。

## Review 最终会被发布的包

Skill Review 详情展示：

- 标准元数据和精确 package digest；
- 有界文件树，包括 path、size、media type、content digest 和 executable 状态；
- 以惰性文本或经过安全处理的 Markdown 展示 `SKILL.md`；
- 对有界 UTF-8 文件提供文本预览；
- 二进制资源只显示元数据；
- 后继 Revision 或 Fork 的文件 diff；
- 静态校验、来源、许可证、依赖、Secret scan 和风险发现；
- 在不执行包内脚本的前提下展示已知 target 兼容性。

Review 操作仍然是 Candidate 操作。修订 Candidate 会产生完整 replacement Candidate version。批准会提交一个不可变
package Revision。编辑已批准 Skill 始终创建后继 Candidate，不会重新打开或修改已批准 Revision。

## 不加载全部包也能浏览当前 Skills

Skills Library 检索可重建 projection，而不是检索 ZIP 字节。对于 active 受管 Skill，PowerContext 索引：

- name 和 description；
- 有界的 `SKILL.md` 正文；
- 标准 compatibility 和 metadata 值；
- package path；
- `references/*.md` 和 `references/*.txt` 中有界的文本。

主索引记录脚本和资源路径，但不会把完整脚本源码或二进制内容混入默认语义文本。选择结果后，系统会先解析精确
ArtifactRef 和 package digest，再由 UI 读取包详情。

Pending 和 Rejected Candidate 永远不进入 Library 检索。历史 Revision 仍可被精确读取，但不进入默认 current-head
索引。

## 将生命周期与 Revision 分开

每个受管 Skill 都有独立于不可变 package Revision 的治理状态：

| 状态 | Library 行为 | 发布行为 |
| --- | --- | --- |
| `active` | 进入常规检索 | 可以显式发布或更新 |
| `deprecated` | 显示替代指导；默认推荐排除 | 现有 binding 保留；新发布需要显式 override |
| `retired` | 从常规检索隐藏；精确读取保留 | 阻止新发布和更新；仍可安全取消发布 |

改变 lifecycle state 不会创建或修改包字节。Deprecated Skill 可以指向一个 replacement managed Skill。使用次数
和相似度不会自动改变生命周期。

## 向 Codex 和 Claude Code 发布同一份包

Agent target 标识已配置的 Agent kind、host、installation scope、package root，以及是否允许 managed
publication。Target Adapter 校验标准包和目标路径规则，把精确已批准包写入 staging directory，校验完整 tree
digest，再原子移动到目标位置。

Codex 和 Claude Code 获得完全相同的 package bytes。它们的 Adapter 可以拒绝不兼容的名称、格式或 target，但
不能改写 frontmatter、删除脚本或向包内加入 manifest。PowerContext 在标准包之外保存发布归属和已观测 digest。

Publication 不会执行脚本。Unpublication 只有在精确 Artifact identity 和 tree digest 仍与已记录的 managed
binding 匹配时才删除包。被修改或不属于 PowerContext 的目录会报告 `drifted` 或 `conflict`，并保持不变。

## 通过 Agent-side Pull 向远端主机分发

跨宿主分发不把 PowerContext Server 变成远程文件管理器。首次使用时，管理员在 Dashboard 或 CLI 为指定 scope、
Agent kind 和项目填写容易识别的机器名称，并创建一次性 enrollment code；用户在远端安装或启用 PowerContext
Plugin/Integration，选择本地项目并提交该 code。Dashboard 以机器名称作为主标识，把稳定 `target_id` 仅放在技术详情中；
Receiver 注册成功后状态变为已连接，并自动补充主机名和工作区名。
Enrollment 不上传远端绝对路径。

之后用户在 Dashboard 选择这个远端 target 和精确 Skill Revision，点击 **Publish**。Server 只更新该 target 的期望
状态：

```text
Dashboard / CLI
  -> Server 保存 target 的 desired Revision、tree digest 和 generation
  -> 远端 Receiver 通过常驻 watch、Agent 启动前置或显式 sync 请求 reconcile
  -> Receiver 下载精确 canonical package，校验 digest，在本机 staging 后原子安装
  -> Receiver 回传 observed Revision、tree digest、generation 和结果
  -> Dashboard 只在 Receipt 匹配时显示 current
```

Receiver 由 Codex 或 Claude Code 的 PowerContext Plugin/Integration 携带，职责只是同步包和上报结果；它不是另一套
PowerContext Server。Plugin 是 bootstrap，受管 Skill 是动态数据，因此每次发布 Skill 不需要重新安装 Plugin。
如果远端没有安装或启用 Receiver，Server 只能保持 `pending` 或 `offline`，不能声称已经下发成功。

Receiver 的 Agent Adapter 在远端本机解析安装根目录。项目级 Codex target 使用 `.agents/skills/<name>/`，项目级
Claude Code target 使用 `.claude/skills/<name>/`。浏览器和 Server 都不提交或解释远端绝对路径。两个 Adapter 安装
同一份已批准 package bytes，只校验各自的名称、格式、installation scope 和环境兼容性，不修改包内容。

首个远端切片只支持项目级 Codex 和 Claude Code target、显式 Publish/Update/Unpublish、Linux systemd user service
承载的常驻 watch，以及手动 preflight/sync。远端离线不是失败：下次 reconcile 仍以最新期望状态收敛。
WebSocket/SSE 即时唤醒、Fleet Policy、灰度发布、自动发布和依赖安装不属于首个远端切片。

## 描述环境需求，但不授予权限

可移植 Skill 应优先使用一个跨平台实现。需要多个变体的包可以将它们放在 `scripts/` 下。标准
`compatibility` 文本继续供人阅读。PowerContext 受管包还可以包含可选的 namespaced 文件
`powercontext.runtime.yaml`：

```yaml
schema: powercontext.skill-runtime.v1
variants:
  - id: python
    entrypoint: scripts/verify.py
    interpreter: python
    requirements:
      operating_systems: [linux, darwin, windows]
      commands:
        python: ">=3.11"
      network: none
      writable_roots: [workspace]
  - id: windows-powershell
    entrypoint: scripts/windows/prepare.ps1
    interpreter: pwsh
    requirements:
      operating_systems: [windows]
      commands:
        pwsh: ">=7"
      network: required
```

这个可选扩展属于 package digest。不了解它的 Consumer 可以忽略并继续使用 `SKILL.md`。Exact Import 不会插入
或修改该文件；为外部包增加该文件必须通过 Fork。

Agent environment profile 报告已观测的操作系统、架构、命令版本、网络策略、可写根目录、依赖安装策略和环境
变量名称，但从不保存 Secret value。PowerContext 比较精确包需求和 target profile，返回包含原因的
`compatible`、`incompatible`、`unknown` 或 `manual_review_required`。

Requirement 表达需要什么，Environment 和后续 Execution Request 控制实际 grant。包声明
`network: required` 不会因为被批准或发布而获得网络访问。

## 用结果推动改进

Agent Integration 只能为它能够真实观测的状态记录有界 usage observation：

```yaml
skill: artifact:skill/skill_release_check@2
package_digest: sha256:1234...
target_id: codex-project
selected: true
invoked: true
validation: passed
outcome: success
task_source: source:task-outcome/task_456
```

如果 Integration 知道 Skill 被选中，却无法证明脚本或指令真正被使用，`invoked` 保持 `unknown`。Publication
不是 Invocation，Invocation 也不等于 Task Success。

Usage Observation 是不可变 Source evidence。它可以更新有界 Aggregate，也可以针对精确 Skill Revision 发起
successor Candidate，但永远不会修改内容、批准 Candidate、提升权限、退役 Skill，或仅凭计数证明有用性。

# Reference-level explanation

## 范围以及与现有 RFC 的关系

本 RFC 定义：

- PowerContext 受管 Skill 的标准包格式；
- 完整的内容寻址 package capture 和数据库存储；
- 精确 External Import 和语义 Fork 行为；
- package-level Review，以及从 instruction-only managed Skill 迁移的方法；
- current-head Library 检索、治理生命周期和有界使用证据；
- Codex 与 Claude Code 的环境评估、发布、drift detection 和安全取消发布；
- 后续远端 Agent-side Pull 扩展的 target 注册、期望状态收敛、Delivery Receipt 和安全边界；
- 公共 package read 语义和实现验收标准。

本 RFC 细化 RFC 0051 的 instruction-only managed Skill content 和 RFC 1304 的 two-file managed projection。它不
改变 Experience content、通用 Candidate identity、Candidate CAS、Review terminal transition 或 Artifact lineage
语义。

本 RFC 不定义：

- 通用 Workflow、DAG、Routine 或 Procedure Runtime；
- 自动执行、安装依赖、解析 Secret 或 Sandbox grant；
- SSH、Server 主动写入远端文件系统或浏览器指定任意远端路径；
- 常驻 Fleet Orchestrator、即时推送通道、自动发布或通用设备管理；
- 组织级 RBAC、Reviewer identity、package signing 或 marketplace billing；
- 自动语义合并、自动发布、自动退役或无界后台生成；
- 通用二进制提取、OCR、恶意软件结论或完整代码搜索。

## 标准基线

受管包遵循通用 Agent Skills package baseline：

- package root 包含 UTF-8 `SKILL.md`；
- YAML frontmatter 包含必需的 `name` 和 `description` 字符串；
- `name` 由 1 到 64 个小写字母、数字或单个连字符组成，不能以连字符开头或结尾，不能包含连续连字符，并且
  必须与 package directory name 匹配；
- `description` 非空且最多 1,024 个字符；
- 保留 `license`、`compatibility`、`metadata` 和 `allowed-tools` 等可选标准字段；
- 保留 `scripts/`、`references/`、`assets/`、license、template 以及其他有界 package file；
- 未识别但语法有效的 frontmatter field 仍属于精确包，不会被改写。

PowerContext 把 `allowed-tools` 当作不受信任的 package content。它可以用于展示或兼容性判断，但不是 Tool Grant，
也不能绕过 Agent Policy。

通用基线使用所有已配置 Agent Adapter 都能接受的约束。某个 target Adapter 可以报告更严格的不兼容，但不能
通过改写内容扩大已批准 package contract。

## Skill package content model

新的 managed Skill Revision 使用带 discriminator 的 content model：

```yaml
schema: powercontext.skill-package.v2
format: agent-skills
entrypoint: SKILL.md
package:
  tree_digest: sha256:...
  archive_digest: sha256:...
  file_count: 7
  uncompressed_size: 18234
  archive_size: 9541
metadata:
  name: release-check
  description: Verify a release candidate before publication.
  license: Apache-2.0
  compatibility: Python 3.11 or newer.
```

`package` 标识权威 package snapshot。`metadata` 是用于校验、列表和检索的确定性解析缓存。每次写入和读取包时，
缓存 metadata 都必须与 `SKILL.md` 匹配；不匹配属于 integrity error。缓存不能独立编辑。

Review report、compatibility assessment、lifecycle state、publication binding 和 usage aggregate 不属于
`SkillPackageContent`。它们具有不同权威和变化频率。

## Canonical package capture

Package identity 代表内容，不代表完整 filesystem image。Capture 保留：

- 所选 package root 下每个允许的 regular file；
- 规范化 POSIX relative path；
- 精确 file bytes；
- 被约化为不可执行 `0644` 或可执行 `0755` 的 regular-file mode。

Capture 不保留 modification time、user/group ID、ownership、extended attribute、ACL 或 empty directory。这些值会
随宿主变化，不属于 Agent Skill 内容。

Tree digest 对按路径排序、带 domain separation 的 canonical stream 计算：

```text
format version
relative path length + relative path
normalized mode
file length
file sha256
```

之后 PowerContext 创建 deterministic ZIP：entry 排序、timestamp 固定、mode 规范化、移除宿主专属 extra field，并
使用固定压缩策略。`tree_digest` 是内容身份；`archive_digest` 校验存储和分发的 ZIP。即使原始 ZIP 的顺序和压缩
方式不同，语义相同的输入仍得到同一 tree digest。

初始边界延续当前本地 Registry 的规模：

| 边界 | 值 |
| --- | --- |
| Regular file | 256 |
| 未压缩总字节 | 4 MiB |
| Canonical ZIP 字节 | 5 MiB |
| `SKILL.md` 字节 | 128 KiB |
| UTF-8 编码后的 path 字节 | 512 |

Importer 对下列情况直接拒绝，而不是静默排除：

- absolute path、`..`、NUL、无效 UTF-8 path 和 package root 外路径；
- symlink、hard-link alias、socket、device、FIFO 和其他特殊文件；
- case-folding 或 Unicode-normalization path collision；
- 重复 ZIP member；
- 不支持的加密或超出 decompression bound；
- 超出任意边界的包；
- 缺失、非 UTF-8、格式错误或不符合标准的 `SKILL.md`；
- 被已配置 Secret 或 Package Policy 阻止的文件。

如果 `.env`、`.git`、`node_modules` 或其他路径被禁止，错误会指出具体路径。Exact Import 不会静默丢弃这些内容
后再声称结果完整。

对于实时外部目录，Capture 会把每个文件写入隔离的 staging snapshot，计算 staged digest，然后再次解析外部
Registration。如果源 fingerprint 已变化，Capture 以 typed conflict 失败并且不持久化 Candidate。Package
snapshot 使用 staging bytes，而不是稍后再次读取 live directory。

## Package persistence

初始实现新增一个不可变 content-addressed table：

```text
pc_skill_packages
  scope_id
  tree_digest
  archive_digest
  archive_bytes
  manifest
  file_count
  uncompressed_size
  archive_size
  created_at

PRIMARY KEY (scope_id, tree_digest)
```

`archive_bytes` 使用 SQLAlchemy `LargeBinary`；SQLite 存储为 BLOB，MySQL/OceanBase variant 使用 `MEDIUMBLOB`。
`manifest` 是 canonical JSON，包含每个 entry 的 path、digest、size、media type 和 normalized mode。任何索引都不
包含 `archive_bytes` 或 `manifest` 内容。

`pc_artifacts.content`、Candidate proposal content 和捕获的 external snapshot Source 只保存有界 package
reference。Package insertion 与第一个持有它的 Source 或 Candidate write 在一个数据库事务中完成。复用相同
`(scope_id, tree_digest)` 时，先校验现有 archive 和 manifest digest，再返回现有记录。

前五个本地切片新增 `pc_skill_packages` 和 `pc_skill_publications` 两张业务表。Lifecycle 复用现有 Artifact
Head，Search 使用通用可重建 Projection，Usage Evidence 复用现有 Source Store。第六个远端分发切片新增
`pc_agent_skill_targets`，并迁移 `pc_skill_publications` 以表达远端期望状态和最新 Receipt；首个远端切片不新增任务
队列表或 Receipt 历史表。

已批准 Artifact Revision 以及仍被保留的 Candidate 或 Source evidence 会保持 package reachable。初始实现不做
自动 Package GC。后续 Collector 只能在文档化 retention period 结束后，删除不再被 Artifact、Candidate 或 Source
引用的 package。

## External reference、Import、Fork 与 Update

各操作具有不同的权威语义：

| 操作 | Package authority | 是否复制 | 是否需要 LLM |
| --- | --- | --- | --- |
| Discover/reference | 外部本地包 | 否 | 否 |
| Exact import | 批准后的新 managed Artifact | 精确 canonical snapshot | 否 |
| Fork | 批准后的新 managed Artifact | 精确 source snapshot 加 proposed replacement | 只有模型辅助语义修改才需要 |
| External update | 显式新 Import/Fork 前仍是外部包 | 新的精确 snapshot | Exact Import 不需要；Fork 可选 |

Exact Import Candidate 的 proposed `tree_digest` 必须等于捕获的 external snapshot digest。Candidate revision 可以
修改 Review annotation，但如果修改 package content，就不能继续称为 Exact Import；编辑任意文件都会把操作变为
Fork，并产生新的 proposed digest。

先前导入的上游包发生变化后，Registry 会在 import provenance 旁显示新的 external fingerprint。PowerContext
不会自动更新 managed Skill。用户可以把它导入为新的 managed Skill、Fork，或针对当前 managed ArtifactRef 提出
successor Revision。

## Validation 与风险评估

Validation 分为三层：

1. **Package validation**：path safety、bounds、canonicalization、标准 metadata、digest 和 media detection。
2. **Static governance validation**：Secret pattern、license、executable file、dependency manifest、runtime declaration、
   network/secret/write requirement 和可疑 binary inventory。
3. **Target compatibility**：Agent format、package name、environment profile 和可选 runtime variant。

这些层都不会执行 package script。Scanner finding 是 Review evidence，不是包安全或恶意的证明。Review UI 会展示
Scanner version，并在适用时说明覆盖不完整。

确定性 risk level 用于分流，不授予权限：

| Risk | 最低触发条件 |
| --- | --- |
| `instruction_only` | 只有 `SKILL.md` 和惰性文本/资源 |
| `local_script` | 任意 executable 或 script file |
| `workspace_write` | 声明需要写 workspace |
| `network` | 声明网络需求或 network-oriented dependency |
| `secrets` | 声明 Secret/environment requirement |
| `privileged` | System path、process、container 或其他 elevated requirement |

部署策略可以要求风险更高的包经过更严格 Review 或 Publication confirmation，但 risk 永远不会授权导致该等级的
能力。

## Candidate 与批准事务

`SkillPackageContent` 继续作为 Family proposal type，因此通用 Candidate storage 和 CAS 可以继续复用。Candidate
detail 通过 Skill Package Store 解析 package reference。

Approval 在一个事务中完成：

1. 锁定 expected pending Candidate head；
2. 解析并校验精确 package reference；
3. 重复执行必需的 deterministic validation；
4. 校验 scope 和直接 SourceRef/ArtifactRef lineage；
5. 用不可变 package content 创建或修订 `skill` Artifact；
6. 为新 Skill 创建初始 governance row，或为 successor Revision 保留现有 lifecycle；
7. 更新 current-head search projection；
8. 一起提交 Candidate terminal result 和 Artifact Revision。

Stale Candidate、目标 Artifact head、package mismatch 或 validation version conflict 返回 `409` 或 typed validation
failure。Approval 不会获取远程内容，也不会替换成另一 package digest。

## 从 instruction-only managed Skill 迁移

现有已批准 Revision 继续通过当前 instruction-core content model 精确读取，不会被原地改写，并保留历史 publication
语义。

实现支持带 discriminator 的 union：

```text
powercontext.skill-instruction.v1  -> 现有 name/description/instructions/validation
powercontext.skill-package.v2      -> 标准 package reference 和 parsed metadata
```

从 v1 Skill 创建 successor 时，先渲染当前 deterministic `SKILL.md`，将其 canonicalize 为单文件 v2 package，并把
完整包作为起始 Candidate 展示。批准后下一个 Artifact Revision 才成为 v2。该转换是显式且可 Review 的；读取旧
Revision 永远不会触发迁移。

新的 Exact Import 和新 Package Upload 使用 v2。现有语义生成最初可以生成单文件标准包，只有在精确证据和 Review
支持时才增加 script 或 reference。

## Search projection 与 Skills Library

ZIP BLOB 永远不直接参与 Search。`skill_searchable_text(package)` 从精确包确定性提取有界文本：

```text
name
description
compatibility 和 metadata 值
SKILL.md body
排序后的 package path
references/*.md 与 references/*.txt 中有界的 UTF-8 text
```

当前 managed head 把文本写入 `pc_artifact_heads.searchable_text`。SQLite 把现有仅面向 Experience 的可重建 FTS5
projection 替换为按 scope、Family、Artifact ID 和 Revision 建立的通用 `pc_artifact_fts`。这是对现有可重建投影的
替换，不是新增 Skill 表。OceanBase 继续使用通用 Head field 上的全文索引。两个 Backend 在搜索 Skill 时都过滤
`family = 'skill'` 和 Lifecycle State。重建 projection 会解析精确 package reference，并在提取前校验 package
digest。

默认 Skill Search 不返回历史 Revision、Pending/Rejected Candidate、未显式请求的 Deprecated Skill 或 Retired
Skill。Projection 不包含完整 script source 或任意 binary extraction。后续 Code Search 或 Vector Search 使用独立
通道，并保留 path 和 content-digest provenance。

Skills Library 提供统一 read model，同时保留 authority：

```text
managed current heads + governance + publication + usage projection
UNION
visible external registrations + local availability
```

每一行都暴露 `authority = managed | external`。Search 不会把 external registration 转成 managed Artifact，也不会
把 managed package 当成仍由 upstream source 控制。

## Managed lifecycle 与工作集治理

Lifecycle state 是针对逻辑 managed Skill 的可变治理状态，不保存在 package 内。它扩展现有权威 Head row，而不新增
`pc_skill_governance`：

```text
pc_artifact_heads
  scope_id
  family
  artifact_id
  revision
  searchable_text
  lifecycle_state        active | deprecated | retired
  replacement_artifact_id nullable
  governance_generation

PRIMARY KEY (scope_id, family, artifact_id)
```

现有 row 迁移为 `active`，Governance Generation 为零。Lifecycle update 必须满足 `family = 'skill'`，并使用
expected `governance_generation` CAS；它不会改变不可变 Artifact Revision，也不会移动 Head 的 `revision` pointer。
`replacement_artifact_id` 如果存在，必须指向同 Scope 的另一个 managed Skill Head。Lifecycle transition 显式进行：

```text
active <-> deprecated
active 或 deprecated -> retired
retired -> 无自动转换
```

本 RFC 中 Retirement 不可逆。错误退役后可以 Fork 或创建新的逻辑 Skill，同时 Retired history 继续可审计。
Deprecation 可以指定一个同 scope replacement，并可以被显式撤销。

Scope 和 target policy 可以限制 Pending Candidate、package bytes、active searchable head 和 published package 数量。
超过 budget 会用 typed error 阻止新操作，但绝不会驱逐或退役现有 Skill。

## Agent target 与环境兼容性

`AgentSkillTarget` 继续作为已配置 publication boundary，并增加 environment profile，或增加能够观测该 Profile 的
Provider：

Server 以 workspace 作为本机路径边界。未显式设置 `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` 时，workspace 默认为 Server
启动目录，并自动生成 `codex-project -> <workspace>/.agents/skills` 与
`claude-project -> <workspace>/.claude/skills` 两个允许受管发布的项目级 target。目录缺失只表示当前没有外部 package；
首次由用户确认本机安装时才创建。服务管理器或容器必须通过 `POWERCONTEXT_SERVER_WORKSPACE` 固定 workspace。显式
`POWERCONTEXT_SERVER_EXTERNAL_SKILLS` 完整覆盖自动 target，并继续承担自定义路径、用户级 target、环境 Profile 和关闭
本机发现等高级配置。Dashboard 不接收用户输入的本机路径。

```yaml
target_id: codex-project
agent_kind: codex
host_id: host-123
installation_scope: project
path: /workspace/.agents/skills
allow_managed_publish: true
environment:
  operating_system: linux
  architecture: x86_64
  commands:
    python: 3.12.4
    bash: 5.2.26
  network_policy: disabled
  writable_roots: [workspace]
  dependency_install_policy: denied
  environment_names: [CI]
```

Secret value 永远不进入 Profile。Observed Profile 具有 deterministic fingerprint 和 timestamp。Compatibility 由精确
Artifact Revision、package tree digest、environment fingerprint 和 adapter version 共同确定：

```text
compatible
incompatible(reason...)
unknown(reason...)
manual_review_required(reason...)
```

Compatibility 是可重建 Assessment，不是 Artifact。环境变化会使 Assessment 失效，但不会改变 Skill Revision。
已知 Agent-format incompatibility 会阻止 Publication。未知 runtime compatibility 可以在现有显式确认后发布，因为
Publication 不是 Execution；但 UI 必须保留警告，不能声称脚本一定可运行。

## Publication、Distribution 与 Unpublication

初始实现支持宿主本地的 configured target 和精确 authenticated package download，不会向任意浏览器路径或远程
宿主主动推送。

Package download 解析调用方有权访问的精确 ArtifactRef，并返回包含 canonical ZIP bytes 的有界 JSON envelope：

```text
package: {tree_digest, archive_digest, file_count, uncompressed_size, archive_size}
archive_base64: <canonical ZIP encoded as base64>
```

调用方解码后校验两个 digest。该 envelope 让生成式 JSON Client contract 保持一致，同时保留字节精确分发；Server
不会返回可变的 filesystem path。

初始五个本地切片的 Publication state 保存在本 RFC 新增的第二张业务表中：

```text
pc_skill_publications
  scope_id
  target_id
  artifact_id
  desired_revision
  desired_tree_digest
  observed_revision nullable
  observed_tree_digest nullable
  destination
  state
  selected_runtime_variant nullable
  environment_fingerprint nullable
  generation
  updated_at

PRIMARY KEY (scope_id, target_id, artifact_id)
```

Publication 在 target filesystem 上 staging canonical package，安全解压，重新计算 tree digest，再原子 rename。Target
package 只包含已批准 package file。现有 `powercontext.json` ownership file 不再写入发布包；Ownership 由
`pc_skill_publications` 表示，并通过 observed destination tree digest 校验。

可观测 publication state 与 runtime compatibility、external discovery 分开：

```text
unpublished | current | update_available | conflict | drifted | incompatible
```

Safe Update 或 Unpublication 需要 expected publication `generation`、精确 recorded Artifact identity、destination 和
observed tree digest。如果本地内容已变化，PowerContext 报告 Drift 并保持原样。Unpublication 只移除完整的 managed
package 及其 binding，不会删除已批准 Artifact 或 package history。

## 远端 Agent-side Pull 与期望状态收敛

本节规定已实现的第六个切片 Contract。远端分发沿用相同的
`pc_skill_publications` 期望/观测模型，但由 target-local Receiver 而不是 Server 本地 Publisher 产生观测结果。

### Target 注册与本地路径归属

远端 Receiver 使用一次性 enrollment code 注册一个稳定的 `target_id`。注册至少绑定：

- 不透明的 host/installation identity、`agent_kind` 和 project installation scope；
- 允许访问的 `scope_id` 和 Server origin；
- target-local Adapter version、environment fingerprint 和最近在线时间；
- 独立的 target credential subject；Secret value 只保存在远端操作系统 Secret Store 或等价安全存储中。

第六个切片新增第三张业务表，持久保存 enrollment、认证主体和 target liveness：

```text
pc_agent_skill_targets
  scope_id
  target_id
  display_name
  agent_kind
  installation_scope
  delivery_mode
  installation_id nullable
  state
  enrollment_token_digest nullable
  enrollment_expires_at nullable
  credential_subject nullable
  credential_verifier nullable
  receiver_version nullable
  environment_fingerprint nullable
  machine_hostname nullable
  workspace_name nullable
  last_seen_at nullable
  generation
  created_at
  updated_at

PRIMARY KEY (scope_id, target_id)
UNIQUE (scope_id, agent_kind, installation_scope, installation_id)
UNIQUE (enrollment_token_digest)
UNIQUE (credential_subject)
UNIQUE (credential_verifier)
```

`display_name` 是管理员提供、可通过 target generation CAS 修改的人类可读名称；重命名不改变凭据或任何分发绑定。
`target_id` 由 Server 生成并保持稳定，只用于 API、审计和故障排查。`installation_id` 是 Receiver 为本地
Agent/project installation 生成的不透明身份，不是 filesystem path；它在 enrollment 前为空，在 enrollment transaction
中写入。Receiver 同时上报不含绝对路径的 `machine_hostname` 和 workspace basename `workspace_name`，便于 Dashboard
按名称、主机、工作区或技术 ID 搜索和消歧。首个远端切片只允许
`delivery_mode=agent_pull`，并使用
`pending | active | revoked` target state：

- 创建 enrollment 时，Server 生成 `pending` row，只保存高熵一次性 code 的 digest 和 expiry；
- enrollment transaction 同时校验 pending state、expiry、token digest 和 expected target `generation`，绑定唯一
  `installation_id`、`credential_subject` 和 verifier，清除 token digest，再转为 `active`；
- target display name 的修改使用同一 `generation` CAS，但不改变 credential、`target_id` 或 publication identity；
- Secret credential value 只保存在远端操作系统 Secret Store；Server 只保存用于验证的 hash、key 或 provider
  reference；
- `last_seen_at` 用于派生 `offline` 展示，离线不是持久 target state；
- enrollment 和 revoke 使用 target `generation` 做 CAS；credential rotation 若后续引入，也必须复用同一 CAS contract；
- `revoked` target 的 enrollment、reconcile、download 和 Receipt 全部被拒绝；credential verifier 被清除或通过
  provider 撤销，历史 target identity 仍保留用于审计。

一条 target row 可以在尚未发布任何 Skill 时独立存在。首个远端切片不把现有 host-local path configuration 迁入
该表，也不复用 `pc_external_skill_registrations`；后者只是外部包的观测，不是远端设备或认证 Authority。
三个 Unique Contract 分别阻止同一 installation 重复注册、一次性 code 被两个 target 消费，以及一个 credential
subject 同时代表多个 target；数据库对 nullable unique column 允许多个 `NULL`。

Server 只保存逻辑 installation scope，不保存或接受浏览器传入的远端绝对路径。Receiver 根据本地已注册 workspace
解析 package root，并拒绝逃逸该 root 的 Skill name 或 archive path。一个 credential 只能代表其绑定的 `target_id`，
不能在 reconcile 请求中切换为另一个 target。

每条 `agent_pull` publication 必须解析到同 scope 的 `active pc_agent_skill_targets` row；host-local publication 继续解析
现有 Server configuration，不要求 target table row。Revoke 不级联删除 publication 或 package history，只阻止远端
认证并让 Dashboard 显示 target 已不可收敛。

### Publication schema 扩展

第六个切片迁移现有 `pc_skill_publications`，增加或变更：

```text
desired_state          # published | unpublished
observed_generation nullable
destination nullable   # host-local 必填；agent_pull 必须为空
last_error_code nullable
observed_at nullable
```

原有 `generation` 继续表示 Server desired state 的 CAS generation。`observed_generation` 表示最新有效 Receipt 已处理的
generation；旧 generation 的 Receipt 不能更新 observed fields。远端 Unpublish 把 `desired_state` 改为
`unpublished`。最后一个 desired Revision/digest 继续保留为 intent history，但它本身不是删除 Authority；安全删除
依据是 Receiver 在 reconcile 中报告并在本地再次校验的 credential-bound ownership checkpoint。成功 Receipt 将
observed Revision/digest 变为空，并把 state 设为 `unpublished`。

`destination` 对现有 host-local publication 仍然必填；对 `agent_pull` 必须为空，因为路径由 Receiver 本地解析。远端
切片将 publication state 扩展为：

```text
unpublished | pending | current | update_available | delivery_failed | conflict | drifted | incompatible
```

`pending` 表示 desired generation 尚无匹配 Receipt；`delivery_failed` 携带有界 `last_error_code`。`offline` 根据 active
target 的 `last_seen_at` 派生，不写入每条 publication state。

Schema migration 必须在 SQLite 和 OceanBase 上执行相同的确定性 backfill：

- 现有 `state=unpublished` row 写入 `desired_state=unpublished`，其余 row 写入 `desired_state=published`；
- 现有 row 写入 `observed_generation=generation` 和 `observed_at=updated_at`；
- 现有 host-local `destination` 原值保持不变，只有新的 `agent_pull` publication 使用 `NULL`；
- 现有 row 写入 `last_error_code=NULL`；
- backfill 完成后 `desired_state` 为 non-null，并限制为 `published | unpublished`。

首个远端切片不新增 `pc_skill_delivery_receipts`。Receipt 在
`(scope_id, target_id, artifact_id)` row 上校验 `publication.generation == receipt.generation` 后更新最新 observed fields：
相同 generation 的成功结果可以覆盖失败结果，失败结果不能覆盖已经成功的结果，重复相同结果是 no-op，旧
generation 一律不能更新当前 state。这足以实现幂等收敛。若以后需要完整 Receipt 审计历史，应复用现有
Source/Event Store；审计记录不能成为当前 publication state 的 Authority。

Receiver 在标准 package 之外维护一个 credential-bound、完整性受保护的本地 ownership checkpoint。每个 managed
artifact 的 checkpoint 至少包含 `target_id`、ArtifactRef、tree digest、applied generation 和状态；package script
不能读写该状态。Receiver 还维护一个有界 pending-action journal，使 package rename 和 checkpoint 更新之间发生崩溃
时能够恢复：最终目录匹配已授权 action 时完成 checkpoint 并补发 Receipt；仍匹配旧 checkpoint 时放弃 staging；
其他情况报告 `conflict`，不会猜测 ownership。

### Reconcile，而不是一次性投递队列

远端 Publication 是期望状态：

```text
Server authority                         Remote target observation
desired_state                            observed state/result
desired_revision                         observed_revision nullable
desired_tree_digest                      observed_tree_digest nullable
generation                               observed_generation nullable
delivery_mode = agent_pull               bounded error code
```

Dashboard 的 Publish、Update 或 Unpublish 只以 CAS 更新 desired state 和 `generation`。Receiver 在 reconcile request
中提交本地 ownership checkpoint 和实际目录 tree digest：

```yaml
target_id: codex-project-7f31
last_processed_generation: 11
observed:
  - artifact_ref: artifact:skill/skill_release_check@1
    tree_digest: sha256:abcd...
    applied_generation: 9
```

Server 使用 target credential 校验请求，并确认 checkpoint 中的 ArtifactRef/tree digest 指向同 scope、同
`artifact_id` 的精确 approved package。Reconcile observation 可以作为本次动作的本地 precondition，但只有成功
Receipt 才更新 authoritative observed fields。Response 使用区分明确的 action shape：

```yaml
# install
generation: 12
action:
  operation: install
  desired:
    artifact_ref: artifact:skill/skill_release_check@2
    tree_digest: sha256:1234...

---
# unpublish
generation: 13
action:
  operation: unpublish
  artifact_id: skill_release_check
  expected_local:
    artifact_ref: artifact:skill/skill_release_check@2
    tree_digest: sha256:1234...
    applied_generation: 12
```

对于 Unpublish，`expected_local` 来自本次经过认证的 Receiver checkpoint，并且必须与同 Artifact binding 的 exact
approved package 匹配；它不盲目使用 Server 上最后一次 observed 或 desired digest。这样即使 package 已安装而成功
Receipt 丢失，下一次 reconcile 仍能安全确认并移除 Receiver 实际拥有的精确目录。

Response 不包含任意 destination path、shell command、dependency install instruction 或未批准的 package body。
Package body 继续通过现有精确 Download operation 获取，并且 credential 只能下载当前 target desired state 引用的
Artifact Revision。相同 `(scope_id, target_id, generation, artifact_id)` 的 reconcile 和 Receipt 必须幂等；短暂断网、
重复请求或 Server 重启不会导致重复目录或回退到旧 Revision。

离线只表示 target 尚未收敛，不把 desired state 改回失败或丢弃动作。`current` 只在最新 generation 的精确 Receipt
匹配 desired Revision 和 tree digest 时成立；在此之前 Dashboard 显示 `pending` 或 `offline`。较旧 generation 的
Receipt 不能覆盖较新的 observed state；如果部署启用审计，可以把它写入现有 Source/Event Store。

失败 Receipt 将 `observed_generation` 写为本次 generation，保留上一个成功 observed Revision/digest，并将 state
设为 `delivery_failed`。只要 desired state 尚未满足，reconcile 就会在有界退避后重新返回同 generation 的幂等
action；重试不会增加 publication generation。只有新的 Dashboard intent 才推进 desired `generation`，后续成功
Receipt 会清除 `last_error_code` 并替换失败状态。

### Receiver 安装与 Receipt

对于 `install`，Receiver 必须按下列顺序执行：

1. 使用绑定 target credential 读取精确 package envelope；
2. 在有界 staging directory 中校验 archive digest、安全解压并重算完整 tree digest；
3. 运行 Agent-format 和 target-local compatibility 校验，但不执行脚本或安装依赖；
4. 如果最终目录和本地 checkpoint 已经精确匹配本次 desired Artifact/digest，不重写目录，直接进入 Receipt；
5. 否则只在目标不存在，或现有目录和本地 checkpoint 同时匹配且 action 授权替换时，先持久化 pending-action
   journal，再原子 rename 完整 package；
6. 从最终目录再次观测 tree digest，原子更新本地 checkpoint，清理 journal，并回传 Receipt；任何 identity、digest
   或 checkpoint 不匹配都报告 `drifted` 或 `conflict`，保持目录不变。

Receipt 至少包含 `target_id`、`generation`、operation、ArtifactRef、expected/observed tree digest、结果、environment
fingerprint、Receiver version 和有界 error code。Package body、Secret、任意命令输出和绝对路径不进入 Receipt。
Server 必须以 credential 绑定的 target identity、generation 和 digest 校验 Receipt，不能把一次 HTTP 成功当作安装
成功。通过验证的最新 Receipt 按上述 generation 和成功优先规则更新 `pc_skill_publications`，不写入独立 Receipt 表。

对于 `unpublish`，Receiver 先校验 authenticated action、`expected_local`、本地 checkpoint 和实际 tree digest 四者
一致并持久化 pending-action journal，再把完整 managed package 原子 rename 到 Receiver-private quarantine，写入
“absent” checkpoint 并回传 Receipt，最后清理 quarantine。若用户或其他工具修改了目录，Receiver 回传 `drifted`
或 `conflict` 并保持内容不变。Receiver 的 ownership、credential、pending-action journal 和 Receipt checkpoint 都
保存在标准 package 之外。

### Codex 与 Claude Code 触发方式

| Agent | 首个远端切片的 Receiver 载体 | 项目级安装根 | 同步触发 |
| --- | --- | --- | --- |
| Codex | PowerContext 轻量 Receiver | `.agents/skills/` | systemd user service 运行 `remote-watch`，或 Agent 启动前置/`remote-sync` |
| Claude Code | PowerContext 轻量 Receiver | `.claude/skills/` | systemd user service 运行 `remote-watch`，或 Agent 启动前置/`remote-sync` |

集成必须验证 Agent 在哪个 discovery boundary 读取 Skill。如果 SessionStart 晚于该 Agent 的本次扫描，新安装包只
能声明为下一 session 可发现，不能把 `installed` 等同于本次 session 已加载。需要“首次会话即可使用”的部署应在
启动 Agent 前运行同一个 reconcile preflight。`remote-watch` 只定期触发同一个 reconcile；后续 SSE/WebSocket 也只能
用于唤醒 Receiver，package 仍通过相同的 authenticated pull transport 获取。

## Usage observation 与 Evolution

拥有该集成的 Agent Integration 可以在有界 Task 或 Agent completion boundary 捕获 `skill-usage` Source：

```yaml
skill_ref: artifact:skill/skill_release_check@2
package_digest: sha256:...
target_id: codex-project
selected: true
invoked: true | false | unknown
validation: passed | failed | unknown
outcome: success | failure | unknown
task_source: source:task-outcome/task_456
environment_fingerprint: sha256:...
```

Adapter 不能根据 Retrieval、Publication、Prompt Inclusion 或模型提到 Skill 就推断 `invoked=true`。Unknown 是正常值。
Source 默认不记录 Prompt、Secret、Command Argument 或无界输出。

可重建 daily projection 可以按精确 Skill Revision 聚合 selected、invoked、validation-passed、success 和 failure
count。这些 count 支持 Library health view，但不会自动改变 Search eligibility 或 Lifecycle。

已配置 generation model 可以使用调用方选择的精确 Usage Source 提出 Successor Candidate。Exact Import、Storage、
Review、Lifecycle Change、Publication、Unpublication 和 Usage Recording 仍然是非 LLM 基础能力。

## Public 与 Dashboard operation

实现暴露具有下列语义的 operation；最终 OpenAPI 命名遵循现有 `/v1/skill/...` 风格：

| Operation | 结果 |
| --- | --- |
| List Library | 返回保留 Authority 的 Managed Head 和 External Registration，并支持过滤 |
| Get package manifest | 返回精确 Managed Revision metadata 和 file tree，不返回 binary body |
| Download package | 为有权访问的精确 Managed Revision 返回 canonical ZIP |
| Upload package proposal | Canonicalize 调用方提供的 ZIP，并创建 pending managed Candidate |
| Import external Skill | 从选定 fingerprint 创建 Exact Import Candidate 或 Fork Candidate |
| Update lifecycle | 对 active、deprecated 或 retired 执行 CAS transition |
| Inspect publication | 返回 configured target 的 publication 与 runtime compatibility |
| Publish | 把精确 Approved Revision 发布到一个 configured target |
| Unpublish | 只移除完整的 managed target package |
| Record usage | 捕获有界、精确的 usage Source evidence |
| Create/enroll/revoke remote target | 创建一次性 code、绑定或撤销 credential-bound target registration |
| Publish/unpublish remote desired state | 以 publication generation CAS 声明精确 Revision 或期望缺席 |
| Reconcile remote target | 比较 target observation 与最新 desired generation，返回幂等动作 |
| Download remote package | 只允许 target credential 下载其当前 generation 引用的精确包 |
| Record delivery receipt | 记录精确 generation、ArtifactRef、digest 和安装结果 |

List Library 的每一项都返回可展示的出处。未引用 external snapshot 的 managed Skill 归为 `powercontext`；exact import
归为 `external_import`；fork 归为 `external_fork`；尚未进入 Review 的 registration 在浏览器中归为 `external`。后面三类
同时显示 registration 的 `host_id`、`agent_kind`、`external_skill_id`、`installation_scope` 和 `locator`。对于 managed
Skill 的后续 Revision，Runtime 先检查直接 SourceRef，再沿上游 Skill ArtifactRef 追溯最初的 external snapshot，避免一次
修订后把接管来源错误地显示成 PowerContext。该投影复用已持久化的 Source lineage 和 external snapshot，不新增表或历史
数据迁移；旧数据没有 external snapshot 时只声明为 PowerContext 来源，不猜测是人工提交还是模型生成。

浏览器提交 `target_id`、创建目标时选择的 Agent kind、精确 ArtifactRef、expected Candidate version 或
governance/publication generation，以及显式 operation intent。浏览器永远不提交任意 destination path、package
digest replacement 或 execution grant。
远端 operation 已进入 OpenAPI。管理员通过 `remote-status`、`remote-target-create`、`remote-target-rename`、`remote-publish`、
`remote-unpublish` 和 `remote-target-revoke` 完成完整生命周期；Receiver 端通过 `remote-enroll`、`remote-watch`、
`remote-sync`、`remote-service-install` 和 `remote-service-uninstall` 收敛本地目录并管理 Linux user service。CLI 在
未显式提供 expected generation 时先读取最新状态再提交 CAS；自动解析不会绕过 CAS，竞争更新仍返回 conflict。
管理员可以显式提供 generation 以实现自动化中的 compare-and-swap。

Skills Dashboard 在交付区提供“本机目录 / 远端机器”选择。远端模式要求创建时填写机器名称，支持按名称、Receiver
上报的主机名、工作区名或技术 ID 搜索，并可在不改变分发身份的前提下重命名。它还支持 Codex 或 Claude Code 项目目标、
一次性注册引导、目标与分发状态自动刷新、精确 Revision 分发、请求安全移除和凭据撤销。页面只在创建时展示一次注册口令，并
直接给出可复制的 Receiver 安装和带 `--install-service` 的 `remote-enroll` 命令；关闭前未保存口令时，管理员撤销
pending target 并重新添加。远端模式在 pending 时每两秒、稳定时每十秒静默刷新，页面不可见或切回本机模式时停止。
Dashboard 把 Publish/Unpublish 表述为期望状态请求，只有匹配的 Receiver Receipt 才显示已安装或已移除。
存在尚未确认移除的 publication 时，页面禁止撤销 target，避免先使 Receiver 凭据失效而永久失去安全清理能力。
Server 可通过 `POWERCONTEXT_SERVER_PUBLIC_URL` 一次性配置远端可达地址；未配置时，Dashboard 自动采用当前 HTTPS
来源，显式启用不安全开关后也可以采用当前 HTTP 来源；否则由远端 CLI 的既有 Server 配置提供连接地址。添加 target
不要求重复填写服务地址。

HTTPS 仍是默认传输边界。受保护的内部测试网络可以在一期 PoC 中显式启用直连明文 HTTP：Server 设置
`POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true`，远端注册同时传入 `remote-enroll --allow-insecure-http`。任一端单独
启用都不足以放行：Server 开关关闭时继续拒绝非 loopback HTTP 的 Receiver 请求；CLI 未提供参数时，会在发送一次性
注册口令前拒绝该 URL。如果 Server 自身以未鉴权方式绑定非 loopback 地址，操作者还必须单独设置
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`；它表示接受所有 Server route 暴露，不能由仅针对
Receiver 的传输例外隐式开启。只有 Server 开关已启用时，Dashboard 才接受公布的 HTTP 地址，并持续展示明文传输警告、在
可复制命令中加入 Receiver 参数。Receiver 把许可和凭据一起保存到 owner-only 配置文件，因此一次同步、watch 模式和
systemd user service 共用同一传输策略。该配置字段为向后兼容的增量字段，不新增数据库表，也不需要历史数据迁移。
明文链路不会加密注册口令、target credential、技能包或 Receipt，只能用于受保护的内部测试网络，不能视为生产环境中
HTTPS 的替代方案。

### 远端分发 CLI 流程

默认情况下，Server 必须提供远端可达的 HTTPS URL；上文定义的内部 HTTP PoC 显式例外是唯一明文替代方案。远端机器
只安装 `powercontext[cli]` Receiver，不安装 Server 或数据库，也不接受
Server 入站连接。管理员先创建 project target：

```bash
powercontext --server-url https://powercontext.example.com \
  skill remote-target-create --scope-id project:demo --agent-kind codex
```

远端 operator 在目标 project 中输入一次性 enrollment code；省略命令行参数会使用无回显 prompt，并把 target credential
以 owner-only 权限写入 `.powercontext/remote-skill-target.json`：

```bash
cd /srv/project
powercontext --server-url https://powercontext.example.com \
  skill remote-enroll --workspace "$PWD" --install-service
```

内部 HTTP PoC 显式例外对应的命令为：

```bash
powercontext --server-url http://powercontext.internal.example:8765 \
  skill remote-enroll --workspace "$PWD" --install-service --allow-insecure-http
```

`--install-service` 以目标 ID 创建独立的 `systemd --user` unit，并立即 `enable --now`。unit 只引用 owner-only 配置文件，
不复制 credential。已有注册可运行 `powercontext skill remote-service-install`，需要停用时运行
`powercontext skill remote-service-uninstall`；不支持 systemd 的环境可以前台运行 `powercontext skill remote-watch`。

管理员发布精确 approved package Revision，不需要手工查询首次或当前 publication generation：

```bash
powercontext --server-url https://powercontext.example.com \
  skill remote-publish --scope-id project:demo --target-id codex-abc123 \
  --revision 2 release-check
```

常驻 Receiver 默认每五秒执行 reconcile。Codex 写入 `.agents/skills/`，Claude Code 写入 `.claude/skills/`。如果要求
当前首次会话必定发现刚发布的 Skill，仍在启动 Agent 前显式执行一次 preflight：

```bash
powercontext skill remote-sync
codex  # 或 claude
```

管理员可以随时检查 desired/observed 状态、请求安全移除或撤销 credential：

```bash
powercontext skill remote-status --scope-id project:demo --target-id codex-abc123
powercontext skill remote-unpublish --scope-id project:demo --target-id codex-abc123 release-check
powercontext skill remote-target-revoke --scope-id project:demo codex-abc123
```

`remote-publish` 和 `remote-unpublish` 只改变 Server desired state；只有后续成功 watch/sync Receipt 才把状态变为
`current` 或 `unpublished`。Dashboard 自动刷新只读取该持久状态，不把 HTTP 请求成功当成安装成功，也不声称当前
Agent session 已重新扫描。

## Security 与信任边界

每个 Package 和 Candidate 都是不受信任内容。PowerContext：

- 使用有界 safe parser 解析 ZIP 和 YAML，不允许 custom tag；
- 惰性渲染 package text，永远不加载内容中指定的远程资源；
- 不记录 package body、Secret、usage argument 或任意 Source body；
- 不在 Scan、Import、Index、Review、Approval、Publication 或 Compatibility Assessment 中执行脚本；
- 不在 Publication 中安装依赖；
- 不把 `allowed-tools`、compatibility text、runtime requirement 或 risk level 当成 Permission；
- 不会因为调用方知道 digest 就暴露 package；
- 通过 scope 和精确 Source、Candidate 或 Artifact reachability 授权读取；
- 在每次 exact read、diff、download 和 publication 前校验 digest；
- 默认对所有非 loopback 远端连接强制 HTTPS；内部 PoC 例外要求 Server 和 Receiver 双端显式同意，并持续展示明文风险；
- 为每个远端 target 使用独立 credential，只允许读取自己的 desired state、下载其中的精确 Artifact 并回传 Receipt；
- 在 Server 端绑定 Receipt 的 target identity、generation 和 digest，不接受浏览器或 Receiver 指定任意远端路径；
- 延续 RFC 1304 的 Restrictive Browser CSP 与 Safe Rendering Rule。

`scope_id` 继续是业务分区，不是 ACL。需要组织级授权的部署必须通过 Server Authentication 和 Policy 执行；本 RFC
不会从 Scope Name 推断 User Permission。

## Implementation slices

实现按五个可独立 dogfood 的本地切片和一个独立验收的远端切片组织；远端实现不改变前五个切片的验收：

1. **Package foundation**：canonical package validation、`pc_skill_packages`、v1/v2 content union、exact read 和
   SQLite/OceanBase round trip。
2. **Exact import 与 package Review**：完整 external snapshot、非 LLM import、Fork 语义、file tree、inert preview、
   digest 可见的 successor comparison 和 approval transaction。
3. **Library 与 lifecycle**：通用 SQLite/OceanBase Artifact FTS Adapter、`pc_artifact_heads` 上的 Lifecycle column
   与 CAS、filter 和 replacement guidance。
4. **Agent delivery**：environment profile、compatibility assessment、`pc_skill_publications`、精确 Codex/Claude
   Code publication、package download、drift detection 和 safe unpublication。
5. **Observed evolution**：有界 usage Source 和显式触发 successor Candidate。聚合 health view 可在以后作为可重建
   projection 增加，不需要改变 usage evidence。
6. **Remote target reconcile**：`pc_agent_skill_targets`、`pc_skill_publications` 远端字段迁移、Codex/Claude Code
   Plugin Receiver、一次性 enrollment、per-target credential、desired-state reconcile、精确 package Pull、原子
   安装、Delivery Receipt、离线收敛和 safe remote unpublication。

任何切片都不会引入 PowerContext Script Runner。所有切片都保留 Exact Read 和既有 Instruction-only Revision。
远端能力的发布声明以本节独立验收为准；`installed`、Receipt `current` 与 Agent 当前 session 已发现仍是三个不同事实。

## Acceptance

| 场景 | 通过条件 |
| --- | --- |
| Standard package | 包含 script、reference、asset、license 和 optional metadata 的有效 `SKILL.md` package 可以精确 round-trip |
| Canonical identity | 等价 directory 与 entry 顺序不同的 ZIP 输入得到相同 tree digest |
| Executable mode | Script normalized executable bit 在 capture、storage、download 和 publication 后仍保留 |
| Complete snapshot | 允许的 hidden/nested file 保留；forbidden file 产生带路径的 rejection，而不是静默省略 |
| Archive safety | 拒绝 traversal、duplicate entry、symlink、special file、collision、malformed YAML 和超界 decompression |
| Mutable source | External content 在 capture 期间改变会产生 conflict 且不创建 Candidate |
| Exact import | Import 保留 source tree digest，不需要 LLM，并且只创建 pending Candidate |
| Fork | 原始精确包作为 Source evidence 保留，proposed package 有独立 digest 和可见 diff |
| Approval | 只有 expected pending version 提交一个 immutable package Artifact Revision 和 current search projection |
| Legacy read | 现有 instruction-only Revision 继续精确可读，访问时不触发迁移 |
| Legacy successor | 从 v1 创建 successor 时，在批准前展示显式 one-file v2 package conversion |
| SQLite package store | 最大 canonical ZIP 与 manifest 能通过 SQLite commit、read 和 digest check |
| OceanBase package store | 相同 package 使用 `MEDIUMBLOB` round-trip，list/search query 不加载 ZIP bytes |
| Search | 通用 Artifact FTS 默认只返回 active approved Skill head；精确 name/description query 返回预期结果 |
| External search | External availability 仍是本地状态，不会成为 managed authority |
| Lifecycle | Head Governance CAS 控制 Deprecation 与 Retirement，保留所有 Revision，且不会自动 delete 或 publish |
| Compatibility | 同一 package 针对 Codex 和 Claude Code environment profile 获得独立且带原因的 Assessment |
| No execution | Import、Review、Index、Approval、Compatibility、Publication、Unpublication 都不执行 package script |
| Publication | Codex 和 Claude Code target 获得同一 approved package tree，且不注入额外 package file |
| Safe update | 只有 identity/digest 匹配且完整的 managed destination 能被替换 |
| Safe unpublication | 只移除完整 managed destination；drift 或 foreign content 保持不变 |
| Initial schema | 前五个本地切片只新增 `pc_skill_packages` 和 `pc_skill_publications`；远端切片另新增 `pc_agent_skill_targets` 并迁移 publication 字段；SQLite FTS 是可重建的替换投影 |
| Usage truth | Selected、invoked、validation 和 outcome 保持独立，并保留 unknown observation |
| Evolution | Usage evidence 可以针对精确 Revision 创建 pending successor，但不能修改或批准它 |
| Scope | Package、Library、Lifecycle、Publication、Usage 和 Download operation 不能跨 caller scope |
| Browser trust | 在真实 Chromium 中 Candidate 和 package content 保持惰性，包括恶意 Markdown、SVG 和 filename |
| Packaging | Package Review 和 Library 所需 Server template/static asset 被包含在 wheel 中 |
| Local defaults | 未配置高级 target 时，本机 Codex 与 Claude Code 分别解析 workspace 下的 `.agents/skills/` 与 `.claude/skills/`；目录在用户确认安装前不创建 |

实现必须运行 `make check`、`make test`、`make docs-test`，API 变化还要运行 `make contract-test`。还必须验证真实
SQLite Server flow、OceanBase package round trip、真实 Codex 和 Claude Code package discovery，以及 Browser flow：
Exact Import、File Inspection、Approval、Search、Publication、Drift、Unpublication、双语、Keyboard Operation 和
窄屏布局。

### 远端分发切片验收

已实现的第六个切片必须满足下列条件，且不能以前五个切片的本地测试替代：

| 场景 | 通过条件 |
| --- | --- |
| Enrollment | 一次性 code 只能激活指定 pending target；重放、重复 installation 或跨 scope 使用被拒绝 |
| Remote schema | 新增 `pc_agent_skill_targets`，并迁移 `pc_skill_publications`；不新增任务队列表或 Receipt 历史表 |
| Schema backfill | SQLite 与 OceanBase 对现有 desired state、observed generation/time、destination 和 error field 得到相同结果 |
| Target uniqueness | 同一 installation、enrollment token 或 credential subject 不能绑定多个 active target，revoked credential 不能继续调用 |
| No full remote Server | 远端仅安装 Plugin/Integration Receiver，不需要 PowerContext Server 或数据库 |
| Agent roots | Codex 与 Claude Code Adapter 分别在远端本机解析 `.agents/skills/` 和 `.claude/skills/`，Server 不接收绝对路径 |
| Exact delivery | Receiver 下载 desired ArtifactRef 的 canonical package，并在安装前后校验 archive/tree digest |
| Atomic install | 中断、磁盘错误或校验失败只留下可清理 staging，不暴露半个 package，也不覆盖完整旧版本 |
| Offline convergence | target 离线期间多次 Update 后，下一次 reconcile 直接收敛到最新 generation，不回放过期 Revision |
| Receipt truth | 只有 credential、target、generation、ArtifactRef 和 digest 全部匹配的 Receipt 才能产生 `current` |
| Idempotency | 重复 reconcile、download 和 Receipt 不产生重复目录、重复 binding 或状态回退 |
| Lost receipt recovery | 安装成功但 Receipt 丢失后，Receiver 通过本地 checkpoint 对同一 desired package 幂等补报，不重写或冲突 |
| Failed delivery retry | 失败 Receipt 保留最后成功观测，并以同 generation 有界重试；只有新 intent 推进 generation |
| Safe remote update | 本地 target tree 漂移时不替换内容，并回传 `drifted` 或 `conflict` |
| Safe remote unpublication | Receipt 丢失后仍只删除 authenticated checkpoint 与实际 tree 匹配的完整 managed package；foreign content 保持不变 |
| Transport isolation | 非 loopback 明文 HTTP 默认被拒绝，仅在 Server 与 Receiver 双端显式同意后放行；一个 target credential 不能读取或确认另一个 target 的状态 |
| Discovery boundary | 测试明确区分 installed、当前 session 已发现和下一 session 可发现，不作虚假成功声明 |
| No execution | Reconcile、安装和 Receipt 全流程不执行脚本、不安装依赖、不扩大 Agent permission |

# Drawbacks

- 完整 Package Governance 比 Instruction-only Record 增加 ZIP Parsing、BLOB Persistence、File-level Review 和更多
  Failure State。
- 通用 Lifecycle Column 扩展了 `pc_artifact_heads`，SQLite 还必须把 Experience-only FTS 重建为识别 Family 的
  Artifact Projection。
- 在当前边界下 Database BLOB 简单且具备事务性，但它不是 Large Package 或高频 Remote Distribution 的最终方案。
- 通用 Standard Baseline 可能拒绝某个 Agent 的宽松 Parser 能接受的 Package。
- Static Validation 无法证明 Script 安全或有用，而更强的 Sandbox Execution 被有意排除在范围外。
- Lifecycle、Publication、Compatibility 和 Usage 是独立维度，会增加 UI 和 API 复杂度。
- 远端期望/观测状态、credential lifecycle 和 eventual convergence 会增加本地发布没有的运维与故障状态。
- Exact Import 可能保留冗余或低质量文件；正确处理方式是可见 Review 或 Fork，而不是静默规范化。
- 在 Agent Integration 能区分真实 Invocation 与 Retrieval/Mention 之前，Usage Evidence 会不完整。

# Rationale and alternatives

| 备选方案 | 决策 |
| --- | --- |
| 保持 Managed Skill 为 instruction-only | 拒绝；无法保留或 Review 常规 Agent Skill package |
| 把 ZIP bytes 直接放入通用 Artifact JSON | 拒绝；Base64 放大 payload，并使通用 Artifact read 与 package transfer 耦合 |
| 只保存 filesystem path | 拒绝；Path 是 host-local 且可变，无法支持 immutable Review 或 distribution |
| 初始就为每个 package file 保存一行 | 拒绝；当前 4 MiB package 使用单个 transactional canonical ZIP 加 manifest，schema 与 I/O 更简单 |
| 新增独立 `pc_skill_governance` 表 | 拒绝；Lifecycle 治理当前逻辑 Artifact，适合放在现有权威 Head row，并保持独立 CAS |
| 只在本地文件系统保存 Publication Ownership | 拒绝；Safe Unpublication、Target Removal、多 Server Instance 和未来 Remote Delivery 都需要持久 Target Binding |
| 立即使用 object store | 延后；`SkillPackageStore` abstraction 保留该路径，不需要现在新增部署依赖 |
| Exact Import 时让 LLM 重新生成 Instructions | 拒绝；会丢失 package bytes 并改变 authority；模型辅助修改应定义为 Fork |
| 把 PowerContext metadata 放进每个 published package | 拒绝；Publication 必须保留 approved standard package tree |
| 为 Codex 和 Claude Code 生成不同 approved package | 拒绝；Target Adapter 应报告 Compatibility 和 Location，而不是制造未 Review 的 Content Variant |
| Publication 时自动安装 Dependency | 拒绝；Publication 不是 Execution 或 Environment Mutation Authority |
| 由 Server 通过 SSH、SCP 或远程文件系统 Push | 拒绝；扩大 Server 权限和网络可达面，且无法安全处理离线、NAT 与本地 drift |
| 使用一次性任务队列投递远端包 | 拒绝；离线 target 容易丢动作或回放旧动作；desired-state reconcile 天然幂等并收敛到最新状态 |
| 每发布一个 Skill 就发布新版 Plugin | 拒绝；Plugin 只作为稳定 bootstrap，受管 Skill 必须作为精确 package data 独立更新 |
| 在每次 User Prompt 前同步 | 拒绝；增加请求延迟和噪音；常驻 watch 在 prompt 路径之外同步，启动前置只保障首次会话发现 |
| 发布所有 Active Library Skill | 拒绝；Library Inventory 与 Agent Working Set 具有不同规模和意图 |
| 自动退役未使用或低成功率 Skill | 拒绝；Observation Coverage 和 Attribution 不完整，Count 不能替代 Review |
| 在本 RFC 中实现 Script Runner | 拒绝；Package Governance 与 Host Policy 已能闭合可用本地流程，无需再发明执行平台 |

如果不采用完整 Package Model，External Import 将继续丢失内容，Review Surface 会与 Agent 实际使用的内容脱节，
Script、Asset、Compatibility 和 Usage Governance 也无法得到忠实表达。

# Prior art

- [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx) 定义了包含
  `SKILL.md` 以及可选 script、reference、asset 和 metadata 的 package。本 RFC 把该 package 作为 portable content，
  同时将 PowerContext governance 留在标准 authority boundary 之外。
- [OpenAI Skills API](https://developers.openai.com/api/reference/go/resources/skills) 使用可下载 ZIP bundle 和 immutable
  Skill version。本 RFC 同样分离 logical Skill identity、immutable content version 和 package distribution。
- Skillsgate 校验标准 frontmatter、应用 package size limit、映射多个 Agent installation target 并复制 directory
  package。PowerContext 借鉴 package/target separation，但不会静默排除文件，也不会把 Installation 当成 Execution。
- RFC 0051 定义 External/Managed Content Authority、精确本地 fingerprint、Candidate Evolution 和 Execution Boundary。
  本 RFC 提供它有意延后的 Managed Package Format。
- RFC 1304 定义 Typed Review、显式 Publication、Safe Update 和 Browser Trust Boundary。本 RFC 将这些 Contract 从
  两个生成文件扩展到精确 approved package，并增加 Safe Unpublication。
- 现有 Memory 与 Experience Index 展示 Authoritative Row 加可重建 SQLite/OceanBase Search Projection 的模式。
  Skill Search 复用该分离方式，而不是索引 ZIP bytes。

# Unresolved questions

没有未决问题阻塞本 RFC。实现必须在发布前确认文档化的 canonical ZIP test vector 在所有受支持 Python 版本上
得到一致结果。

下列决策被有意排除在本 RFC 之外：

- 具体 credential provider、短期 token exchange、设备证明、轮换和吊销的组织级实现；
- Object-store Selection 和 Package GC Retention；
- 组织级 Owner、Reviewer Identity、RBAC，以及 Privileged Package 的双人批准；
- Package Signature、Transparency Log、Vulnerability Database 和 Marketplace Trust Level；
- 通用 Code Search、Embedding、Hybrid Ranking、Automatic Recommendation 和 Just-in-time Mounting；
- Dependency Environment Creation、OCI Execution 和 PowerContext-owned Sandbox Runner；
- 是否使用另一个 Artifact Family 表示可复用 Procedure 或 Workflow 语义。

# Future possibilities

自然扩展包括：

- 在常驻 Pull reconcile 已被真实验证后，用 SSE 或 WebSocket 只做低延迟唤醒，并增加 Fleet Policy、灰度发布和
  批量 target 视图；
- 在 per-target credential contract 之上增加短期 token exchange、自动轮换、设备证明或 mTLS；
- 在保留数据库 metadata 和 tree digest 的前提下增加 Object-backed `SkillPackageStore`；
- Signed Package Manifest 和 Organization Trust Policy；
- 具有精确 package/chunk provenance 的 Path-level Code Search 和 Semantic Search；
- 在检索质量得到验证后增加 Per-project Enabled Set 和 Temporary Task-scoped Mounting；
- 根据 package、lock file、runtime variant、platform 和 environment fingerprint 建立隔离 Dependency Cache；
- 单独 Review 的 Sandboxed `SkillRun` Contract，包含 Read-only Package Mount、Explicit Grant、Resource Limit 和有界
  Evidence；
- 针对 Unused、Failing、Drifted、Incompatible、Unowned 或 Upstream-outdated Skill 的 Governance Dashboard。

这些扩展必须保留核心 Contract：Approved Package Revision 是不可变内容；Environment、Publication 和 Execution
Authority 是包外的显式 Binding；Observed Outcome 可以提出变更，但不能静默改写受治理历史。
