- 提案名称：`declarative_artifact_families_and_generation_profiles`
- 开始日期：2026-09-14
- RFC PR：[oceanbase/powercontext#1618](https://github.com/oceanbase/powercontext/pull/1618)

# Summary

本 RFC 为两件今天必须改源码才能完成的事定义受治理的扩展点：引入“生成出来的是哪一类派生制品”，以及改变“内容怎么生成”。

**Artifact Family 是声明式的**：一个管理员安装的扩展包只含一份闭合 JSON 清单与若干 JSON Schema 文档，运行时不执行任何扩展代码。清单同时声明 schema 版本、Review 策略、演进方式、lineage 纪律、检索投影与上下文贡献的默认行为。家族可并行持有多个 `schema_version`，历史 Revision 永远按其记录版本校验与解码。

**Generation Profile 是 Scope 拥有的版本化制品**：`generation-profile` 是内置的配置型 Artifact 家族（与本 RFC 之外的用户画像家族 `family=profile` 是两回事，命名不复用 `profile`），以不可变 Revision 存储，声明目标家族、prompt 身份、模型目录条目、有界设置、输出上限、空结果字段、失败策略。它**不声明任何 schema**——输出契约按目标家族当前的 `schema_version` 解析。

两者结合后，生成流水线是一段完全通用的路径：证据信封进 → 目录模型出 → 按目标家族 schema 校验 → 落 pending Candidate → 人工审批 → 不可变 Revision。S1 的扩展家族只走 **review** 型生命周期，制品只能由已批准的候选产生，暂不提供通用直写路径。

六个不变量不因扩展而放松：精确证据、schema 校验、Review policy、授权、资源边界，以及由 Runtime 拥有的上下文组装权。

# Motivation

组织可能想改变一个 proposal 的生成方式，或产出一个领域特定的结果：运行手册、故障简报、需求决策、支持解决方案。今天安全的扩展选择很窄。调用方只能提供 Source，并使用内置的 Memory、Experience、Skill 与 Handoff 行为。家族身份在领域值上已是自由字符串，但每条执行路径各自维护一份闭集——制品类、授权画像、ID 前缀、处理绑定、契约枚举——家族集合一变就要改平台源码，因此“不 fork 即可扩展”今天不成立。

# Guide-level explanation

## 五个新概念

**扩展包（Extension Package）。** 一个只含数据的分发单元：根部一份 `powercontext.extension.json`，加上内联或同目录的 JSON Schema 文档。没有可执行代码。

**制品家族扩展（Artifact Family）。** 清单里声明的一个新类型：内容 schema 与版本、Review 策略、演进方式、lineage 纪律，以及可选的投影与上下文贡献默认行为。

**生成档案（Generation Profile）。** 一个 Scope 拥有、可版本化的配置对象，回答“这个家族的内容怎么生成”：用哪个 prompt、用目录里的哪个模型、允许多少输出、超时与重试上界、证据范围如何收窄、空结果与失败怎么处理。它不定义家族是什么，也不能更换输出类型。

**模型目录（Model Catalog）。** 部署方声明的可用模型与端点清单，以及每项上的硬上界。Scope 级档案只能引用目录条目名，不能自带 URL。这条约束同时是出向流量策略的挂载点。

**生成溯源（Generation Provenance）。** 每个由档案产生的 Candidate 与 Revision 都记录“由哪个档案的哪个 revision 生成”，以及一份结构化溯源（prompt 引用、生效边界、目标 schema 版本）与其派生摘要（供判等与比对）。溯源不是事实证据，因此它与证据元组分开存放。

## 例一：新增一个领域家族

管理员安装一个扩展包并在服务端配置中启用 `runbook`。清单声明：`review_policy = "review"`；`cardinality = "collection"`；lineage 要求替换型候选带精确 target 与非空 Source 证据；内容 schema `acme.runbook.v1` 约束 `{schema, title, summary, symptoms[], steps[], failure_handling[]}`，其中 `schema` 为 `required + const` 的版本标记；声明检索投影与上下文贡献的默认行为。

一次故障关闭后：

1. 生成目标取故障相关的 Source 证据与已批准的同类 Experience，在平台适配器内调用目录模型。模型输入是平台固定的有界证据信封，家族只能收窄哪些证据参与。
2. 输出按 `acme.runbook.v1` 校验后进入 Draft → Candidate 路径，并带上生成溯源。若 `steps` 为空（证据不支持任何处置步骤），按 `noop_field` 判定为空结果：不写候选、返回显式 `no_op`、记一次无内容诊断。
3. 此时它不可搜索、不可注入。pending/rejected Candidate 与 Artifact 检索、PreparedContext 完全隔离。
4. 人工 Review 批准，在同一数据库事务中提交不可变 Runbook Revision，更新检索投影，并把 Candidate 置为 approved；任一步失败整体回滚。
5. 因为清单声明了投影，Runbook 进入检索；因为声明了上下文贡献且部署方逐家族放行，它才可能以有界、带引用、被包裹为不可信历史的形式进入上下文，且不能注入原始 system/developer 指令。

## 例二：只改生成方式，不改类型

管理员已装载一个 `runbook` 扩展家族（安装与清单形状见例一），认为自动生成的运行手册步骤过于发散，想更换模型并收紧输出：

1. 部署方已在目录中声明 `small-reasoner` 与 `strong-reasoner` 两个条目，并给出上界。
2. Scope 管理员以 `family=generation-profile`、`artifact_id=runbook.generate` 写入 revision 1，内容指定 `target_family="runbook"`、`prompt.ref="runbook.generate"`（扩展清单里的模板 id）、`model="small-reasoner"`、有界设置与 `output_max_bytes`。
3. 之后产生的每个 Runbook Candidate 都记录该档案的确切 revision 与结构化溯源；既有 Revision 不受影响。
4. 要回滚，读取旧 revision 的 content，再写成一个新的、单调递增的 revision。

观察到的差异：候选与正式 Revision 的 lineage 里多出一条精确的生成溯源引用；证据、schema、Review 与授权路径不变。把 prompt 换成另一个模板 id、把模型换成另一个目录条目、或改动任一有界设置，都会产生新的档案 revision，因此“变了什么”在 lineage 里可见。同一 `target_family` 也可并存多条档案、按 profile key 解析：例如 `runbook.fast`（小模型、低延迟）与 `runbook.slow`（强模型、高质量），生成时指名 key 即得不同的模型与预算。

## 作者流程

扩展是纯数据，作者流程不涉及平台内部结构，也不涉及写代码：

```text
powercontext extension init ./runbook --family runbook        # 生成清单与 schema 骨架
powercontext extension validate ./runbook --powercontext 1.0.0  # 离线校验
powercontext extension lock ./runbook                          # 写入 powercontext.extension.lock.json
powercontext extension diff ./runbook@1 ./runbook@2 --against powercontext.extension.lock.json
```

示例包是纯数据目录，`init` 生成除锁文件外的全部内容：

```text
powercontext-runbook/
├── powercontext.extension.json        清单：家族声明、prompt 模板、兼容区间
├── powercontext.extension.lock.json   lock 产出的语义指纹，可提交进仓库
├── schemas/runbook.v1.json            一版本一份内容 schema，$ref 限于文档内
└── README.md
```

包里没有代码与可执行入口、没有 ID 前缀、没有模型 URL 与凭据、没有画像，也没有投影与渲染函数——它们分别属于平台、部署方配置与 Scope 管理员。

由于扩展不含代码，`validate` **不需要执行任何扩展代码**，只做 JSON 解析、schema 检查与引用解析；平台版本是一根必填的离线轴，未指定即失败。`validate`、readiness 与发现端点共用同一套错误码与定位信息，`diff` 的退出码区分向后兼容与破坏性变更。

## 扩展家族的一生

```text
安装包 → 管理员启用 → 显式生成或（后续切片）自动触发 → pending Candidate
      → 人工 Review → 不可变 Revision → 投影 → 召回 / 注入 → 治理变更（停用 / 降级 / 重装）
      → 停用：停止生成与召回，历史仍按家族描述校验与渲染
```

停用不等于删除。已提交的 Revision 与 lineage 全部保留，且因为家族描述随激活落库，停用后精确读取仍能返回**按 JSON Schema 校验与渲染**的结构化内容，而不只是裸字节。

## 默认拒绝与两层失败策略

扩展家族的授权画像在未显式声明时按 default-deny 注册：沿用周围 Scope 的读取权限，但不可创建分享绑定，也不参与上下文注入。家族在声明投影与上下文贡献之前，既不可搜索也不可注入；上下文贡献还需要部署方逐家族放行。default-deny 必须**可解释**：四个状态（已声明 / 已授权 / 已声明投影 / 已放行注入）与“下一步缺什么”都在 `GET /v1/extensions` 上可见，否则首次接入只会表现为“看不出效果”。

失败被分成两类，二者不能混同：

- **激活期 fail-fast。** 无效或自相矛盾的清单（未知字段、非法 schema、重复家族、命名不兼容、`target_family` 未声明）在启动时被拒绝，该扩展保持未激活，并以无内容的原因码出现在 readiness 与 `GET /v1/extensions` 里。它不阻止 Runtime 启动，也不影响其它家族。
- **运行期 fail-soft。** 一次生成失败、输出不满足 schema、超时、请求超限或证据不足时，在持久化候选之前失败，只影响该次生成；无关能力继续工作。

## 与既有用户的关系

- Prompt 的 Scope 级自定义指令保持原样，仍是内置家族 guidance 的唯一入口。生成档案不替换它，只能引用它。
- `family=profile` 的用户画像家族继续保留其 per-scope 的 `activation_mode`，不迁移到通用 Review policy。
- 内置家族一个都不迁移：它们继续走既有的 typed 生成端点。内置家族接入生成档案不属于本 RFC 的 S1（见 Future possibilities）；在该工作落地之前，`generation_profile` 对内置候选恒为空。
- 既有 Handoff 内容里的生成来源信息继续有效；它移入通用溯源槽是后续工作，不是本切片前置。

# Reference-level explanation

## 范围与接口

| Surface | 变更 |
| --- | --- |
| HTTP | 新增 `family=generation-profile` 的通用 Artifact 写入；新增通用生成端点；开放读取路径的 `family` 参数；候选操作复用现有端点；新增只读发现端点。 |
| Python Client | 家族参数从生成枚举变为字符串；新增档案内容模型与发现模型。 |
| Runtime | 启动期装载扩展清单并构造薄包装类型；生成路径按解析后的档案构造 `InferenceLimits` 与模型设置。 |
| Host 集成 | 不新增自动注入行为；未声明贡献器的家族不进入上下文。 |
| MCP | 不新增自动注册或档案写入工具；只读白名单新增发现端点，使 Agent 能列出可用家族与档案 key。 |
| 持久化 | 新增生成溯源表、家族描述表 `pc_extension_families` 与模板文本快照表 `pc_extension_prompt_templates`；`pc_artifacts` 与候选版本表增加 `schema_version` 列（由内容标记派生）与结构化生成溯源列；档案与扩展家族制品复用 `pc_artifacts`/`pc_artifact_heads`。 |
| CLI | 新增 `powercontext extension` 命令组（新命令提供者）：作者侧 `init`、`validate`、`lock`、`diff`；管理员侧 `enable`、`print-config`、`generation-profile`。config 向导新增 extensions 步骤。 |
| 配置 | 新增扩展包路径清单、模型目录，均为服务端配置，无 HTTP 写接口。 |

## 扩展清单

清单是扩展包根部的 `powercontext.extension.json`，结构闭合：未知字段或未知枚举值都导致装载失败。装载在启动期完成，顺序固定，任一步失败即整包跳过。

```text
powercontext.extension.json
  extension_id          str    全局唯一
  version               str    人类可读标签
  compatibility         {powercontext: 版本区间}
  families[]            见下，上限 16
  prompt_templates[]    {id, text}，上限 32
  side_effects          {network, filesystem, subprocess}，S1 仅允许 "none"
  diagnostics           "content-free"
```

### 家族声明

| 声明项 | 必填 | 说明 |
| --- | --- | --- |
| `family` | 是 | 全局唯一；`^[a-z][a-z0-9-]*$`，≤128 字符。制品 ID 前缀由平台按 family 派生，作者不声明前缀 |
| `content_schemas` | 是 | `{schema_version: JSON Schema}`，一版本一份文档 |
| `current_schema_version` | 是 | 新写入使用的版本；既有候选与 Revision 按各自记录的版本校验与解码，见“版本标记与记录” |
| `review_policy` | 是 | S1 只接受 `review`；注册后固定，不是运行时旋钮 |
| `cardinality` | 是 | `singleton` 演进同一逻辑产物；`collection` 按显式 target 新增或修订 |
| `lineage` | 是 | `{target: optional\|required\|forbidden, require_source: bool, allowed_ref_families: [...]}` |
| `projection` | 否 | `none` 或 `fts-heads`；声明后才可被检索 |
| `prepared_context` | 否 | `{display_name}`；声明后仍需部署方逐家族放行才可注入 |
| `schedule` | 否 | S1 不接受；显式生成是唯一入口，见“调度与自动触发” |

清单 schema 的体积与嵌套深度、家族数与模板数上限，与既有 `MAX_ARTIFACT_FAMILY_LENGTH` 一起集中定义在 `limits.py`。

### 装载顺序与失败隔离

```text
读清单 + 闭合校验 → 兼容矩阵（平台版本区间）→ 逐版本 check_schema + 预解析全部 $ref + 校验版本标记为 `required + const` 且等于自己的版本键
→ 结构 lint（逐层 `additionalProperties: false`、`required` 非空、无死 `$defs`）+ 骨架载荷的投影/渲染 dry run（含非空数组的逐元素渲染路径）
→ family 与 `prompt_templates[].id` 查重，校验 target_family 已声明
→ 构造薄包装类型并登记 → 落库家族描述 → 交给 runtime / repository / 授权层
```

任一步失败即整包跳过，产生类型化错误码与无内容日志，readiness 非阻塞。家族集合一经装配即不可变，不做运行期后注册。内置家族名与内置 prompt key 组成保留集，`prompt_templates[].id` 声明同名即冲突。

## 声明式家族的内容校验

### 薄包装类型

每个家族在装载期构造一组类型，接进既有的严格解码入口：

```text
RunbookContent = RootModel[dict[str, Any]] + after 校验器（运行该家族 schema 的 Draft202012Validator）
RunbookDraft   = ArtifactDraft[RunbookContent] 的子类，ClassVar family = "runbook"
Runbook        = Artifact[RunbookContent]      的子类，ClassVar family = "runbook"
```

`RootModel` 让载荷保持平铺：存储的 JSON 就是内容本身，不出现包装键。这条形状与内置家族一致，`codec` 的严格解码与字节往返因此原样可用。该形状依赖四条既有不变量：类型同一性（`_require_content` / `_require_proposal` 的 `type(content) is expected`）、`ArtifactRepository` 从 `model_fields["content"].annotation` 反推内容类型的反射路径、`RootModel` 的载荷平铺与字节往返、以及多家族类型隔离。四条不变量由既有的 `codec`、`ArtifactRepository` 与 `CandidateRepository` 保证。

### 清单 schema 是唯一权威

动态类自身的 `model_json_schema()` 不携带清单里的约束——它由 Pydantic 从类定义生成，只反映 `RootModel[dict[str, Any]]` 这一层形状。因此：

- 生成档案的溯源必须取**清单 schema 的文本**（或其规范化摘要），不得取 `ContentType.model_json_schema()`。
- 发现端点、`diff` 与家族描述都以清单 schema 为准。
- 审批校验、生成输出校验、停用后解码校验全部走同一个 `Draft202012Validator`——即 `builtin/runtime/relational.py:1763-1768` 里既有的 `_json_schema_validator` 加空 `Registry()` 的封闭引用语义，不引入新的依赖面；错误码按扩展家族词汇表映射，不复用 Source Definition 的错误类型。

`$ref` 解析限定在文档内；`http(s)`、`file` 与未知 URN 一律拒绝。已发布过的 `schema_version` 在仍有历史 Revision 引用期间必须保留声明：家族可以并行持有多个版本，新写入只使用 `current_schema_version`，历史 Revision 与 pending 候选按各自记录的版本校验与解码。平台不在读取时改写任何已提交字节。

### 版本标记与记录

内容里的 `schema` 字段是版本权威：每个 `content_schemas` 条目必须 `required + const` 强制它等于自己的版本键。存储侧另有一列记录版本，用于约束与反查：

- `pc_artifacts` 与 `pc_artifact_candidate_versions` 各增加一列 `schema_version`，写入时**从已校验的内容标记派生**，读取时断言两者一致，不一致即类型化错误——内容始终是唯一权威，列只是索引。
- 该列带指向 `pc_extension_families(family, schema_version)` 的复合外键 `RESTRICT`，使“只要仍有历史 Revision 引用某个版本，其描述就必须保留”成为数据库级保证，而不是开发者纪律。
- 因此 `GET /v1/extensions` 与 `diff` 能用一条 SQL 反查仍被引用的版本，不必在各方言上提取 JSON。
- 内置家族不声明 schema，其该列为 `NULL`；声明式家族的该列非空——以 `family` 是否属内置集合与 `schema_version` 是否 `NULL` 互斥表达，DDL 不引用描述表。

非向后兼容的变更因此不会破坏历史可解释性：每个版本按自己那份 schema 校验与渲染，平台不改写任何已提交字节。代价是新旧内容在展示与检索层的形态可能不再统一——渲染与投影都按内容自身携带的字段进行，平台不做跨版本的字段改写。

### 载荷规范化

`dump_model` 使用 `model_dump_json(by_alias=True)`，它是**保序但不规范化**的：载荷的键序决定存储字节。内置家族靠模型字段定义顺序获得稳定性，而声明式家族的键序来自作者或模型输出，因此同一语义内容可能产生不同字节。S1 的处置是在包装模型的 `mode="before"` 校验器内递归按键排序（数组顺序保留），生成与审批两条入口由构造保证一致。

## 检索投影与上下文贡献的默认规则

声明式家族无法携带投影函数与渲染函数，因此这两个行为由平台用**一条固定规则**决定，不引入任何可配置方言：

- **投影**：深度优先遍历内容树，取每一个字符串值、排除版本标记 `schema`，按与载荷规范化一致的稳定顺序拼接为可搜索文本；数组条目之间插入换行分界。这条规则恰好复现示例家族的预期投影，不附加配置项。
- **渲染**：按同一条遍历递归渲染：标量字段 `Label: value`（标签取 schema `title` 注解，无注解回退字段名），数组按索引逐元素 `Label[1]:`、`Label[2]:`，嵌套对象以路径标签展开；section 标题取 `prepared_context.display_name`。
- **边界不变**：投影只返回文本，分析、索引写入与 `lifecycle_state='active'` 过滤全部留在平台；贡献条目带精确 `ArtifactRef` 引用与单条字节上限，预算超限时按整条数组元素裁剪并留截断标记，不在元素内部做字符截断，递归深度与元素数上限进 `limits.py`；信任包络、引用格式与截断仍由 Runtime 生成。

**激活与升级时重建该家族的投影**：schema 或投影声明变化后，既有 head 的索引必须与原声明重新一致，否则检索结果会静默偏离。重建复用同一入口，在家族激活完成后执行一次。

标签是内置特性，`pc_artifact_tags` 有家族 CHECK 约束，扩展家族 S1 不带标签，契约上 `TaggableArtifactFamily` 保持闭集。

## 生成档案

### 存储与身份

生成档案是内置的配置型制品家族 `generation-profile`：以不可变 Artifact Revision 存储，`artifact_id` 即 profile key（例如 `runbook.generate`），版本维度是 `revision`。写入走通用 Artifact create/replace，由 Scope 管理员执行；回滚通过读取旧 revision 再写成新的单调递增 revision 完成。

由于档案本身是同一 Scope 内的 Artifact，它可以直接进入 `pc_artifacts` 的复合外键。

**revision 是「拥有者可写状态的版本」，不是可复现的生成身份。** `(artifact_id, revision)` 只标识管理员写入的不可变引用快照；实际生效的配置在解析时刻由该 revision、目录条目内容、扩展包身份与 `target_schema_version` 共同决定，由解析身份 `digest` 标识。因此：变更档案字节 ⇒ 新 revision；目录条目内容变化 ⇒ revision 不变而 `digest` 必变。

### 内容模型

```text
GenerationProfileContent
  schema_version      "powercontext.generation-profile.v1"
  target_family       str           该档案只服务一个家族
  prompt               object        {ref}，命名空间由 target_family 决定
  model               str           模型目录中的条目名，不是 URL
  model_settings      object        目录条目上界内的有界设置
  timeout_seconds     int           <= 目录条目上界
  max_requests        int           <= 目录条目上界
  output_max_bytes    int           <= 家族 schema 的内容上限
  noop_field          str | null    输出中该字段为空/缺失即判定为空结果
  failure             object        {on_model_error, on_invalid_output} ∈ {raise, noop}
  evidence_policy     object        只能收窄证据集合，不能放宽（按 Source kind 与 Artifact family）
```

**Prompt 引用只有一个命名空间，由目标家族决定。** `target_family` 是内置家族时，`prompt.ref` 必须指向 prompt key；是扩展家族时，必须指向本部署已装载扩展清单里的 `prompt_templates[].id`。两者都是版本化的（前者由 definition/builtin 版本，后者由包内容寻址），生成溯源记录**解析后模板文本的摘要** `template_digest`。

**输出契约不属于档案。** 家族持有清单 schema，档案只能引用它并收紧 `output_max_bytes`。

**输入形状由平台持有。** 模型输入是固定的有界证据信封（`evidence_id` + kind + content + truncated，条目数与单条字符数有上限），扩展只能按 Source kind 与 Artifact family **收窄**参与的证据集合，不能声明自己的输入 schema。这是声明式方案的明确取舍，见 Drawbacks。

**`noop_field` 与 `failure` 复用既有的声明与构造期校验**：`PromptDefinition.noop_field` 按输出类型字段校验，档案的 `noop_field` 同样要按 `target_family` 当前 schema 校验字段存在性。运行时空结果今天由 `proposal is None` 判定，`no_op` 是生成响应状态，落库的候选状态仍是 `pending`。空结果与失败是两件事——后者是模型错误或 schema 不合法，前者是“证据不支持任何实质结果”的正常结局。

### 解析与冻结

生成时，Runtime 为操作解析一个精确的档案 revision，并在整个操作期间冻结它，包括模型重试。解析**以 profile key 为键**，不由目标家族反查——同一家族可以有多条画像，按家族解析是歧义的；家族身份由解析出的 `target_family` 反推。机制沿用 Prompt 的既有做法：一个 ContextVar 持有解析结果，`current_generation_profile(profile_key)` 只读该操作绑定的选择，从不读可变的全局 head；并发 Scope 之间互不干扰。优先级只有两层：Scope 档案 → 无画像（走内置 Auto 与部署设置）。调用方不能凭请求决定画像内容：调用方只能指名一个 profile key，画像正文由管理员写入并受授权与预算校验。

生成溯源是**结构化字段加派生摘要**：`prompt_ref`（解析后的 prompt key 或模板 id）与模板文本摘要 `template_digest`、模型身份四元组 `catalog_entry`（目录条目名）/ `provider` / `model`（模型 id）/ `base_url`、生效边界 `effective_limits {model_settings, timeout_seconds, max_requests, output_max_bytes}`（档案声明与目录条目上界逐项取 min 之后真正生效的值）、扩展包身份 `{extension_id, version, lock_digest}`、`target_schema_version`，以及由 `digest_input_version` 冻结输入契约的 `digest`。结构供审计与 `diff` 阅读。

### 授权

生成档案家族注册到统一的家族授权画像表：基础动作 `artifact.read`，附加动作 `generation-profile.use`，可分享状态为空，不可授予绑定角色。写入需要 `SCOPE_ADMIN`，与 Prompt 一致：档案影响整个 Scope 的生成行为，仅凭 Artifact 所有权不足以改写它。

### 生成入口

内置家族继续走既有的 typed 生成端点（`POST /v1/experience/generate` 等），其调用期参数由各自的服务层拥有。扩展家族没有等价端点，因此新增一个通用生成端点，S1 的显式触发就是它：

```text
POST /v1/generation/generate
  scope_id     str
  profile_key  str | null    与 family 二选一
  family       str | null    与 profile_key 二选一；走人工提案
  proposal     object | null  只给 family 时必填；按该家族 current_schema_version 校验
  sources      [SourceRef]
  artifacts    [ArtifactRef]
  target       ArtifactRef | null
  reason       str | null
```

- 给 `profile_key` 时按画像生成：解析该 Scope 的画像 revision（该 key 在 Scope 内不存在即报错），取证据、调用目录模型、按目标家族 `current_schema_version` 校验输出，落 pending Candidate 并写入生成溯源。
- 只给 `family` 时是人工提案：载荷按该家族的 `current_schema_version` 校验，生成溯源为空。**没有画像的家族因此仍能产出候选**，不会变成死路；这是内置 propose 端点语义在扩展家族上的对应物。
- 授权：需要 `scope.contribute`；带 `target` 修订既有制品时另需该制品的 `artifact.write`。不满足时在持久化之前拒绝。
- 错误：画像 key 不存在或家族未注册返回 `generation_profile_unknown` / `family_unknown` 且零写入；输出不合法按画像的 `failure` 处理，绝不落候选；`profile_key` 与 `family` 同时给出或同时缺失都是类型化错误。
- 调用方只能指名 profile key，**不能提交画像内容**：正文始终由管理员写入并受授权与预算校验。

## 模型目录

目录是服务端配置，由部署方声明，形状为 `name → {provider, model, base_url, credential env 名, bounds}`。既有的 `InferenceConfig.generation_*` 在配置装载时归一为名为 `default` 的目录条目，因此现有部署无需改动即可继续工作。档案的 limits 与目录条目的上界逐项取 min，被 clamp 的项记无内容诊断。

## 生成溯源

`ArtifactLineage` 增加一对可空的非证据字段，`generation_source: ArtifactRef | null` 与 `generation_provenance: GenerationProvenance | null`，沿用全有全无校验。`GenerationProvenance` 是**结构化字段加派生摘要**，结构化字段记录解析时刻的完整快照而非可变引用：`prompt_ref`、模板文本摘要 `template_digest`、扩展包身份 `{extension_id, version, lock_digest}`、模型身份四元组 `catalog_entry`/ `provider` / `model`（模型 id）/ `base_url`、生效边界 `effective_limits {model_settings, timeout_seconds, max_requests, output_max_bytes}`、`target_schema_version`、`digest`、`digest_input_version`。只记条目名或 key 会在目录或包变更后丢失身份，血缘必须仍能回答“当时用的到底是什么”：目录条目内容不另存历史版本，其取证由本快照承担；扩展模板文本随激活按 `template_digest` 落库于 `pc_extension_prompt_templates` 且只增不删，升级或卸载之后仍可从该锚点恢复。

摘要用于判等、去重与跨部署比对——一个 64 位十六进制串，可建索引、不泄漏内容；结构用于审计与 `diff`——运维与评审要能直接读出“用哪个 prompt、限流多少、落在哪一版目标 schema”，而哈希只能回答“是否相同”。这个形状不是新造的：`builtin/artifacts/handoff/generation_metadata.py:44-58` 的 `HandoffGenerationOrigin` 已经是「结构化字段 + `compiled_digest` + `original_draft_digest`」。

摘要的输入契约必须显式冻结：`digest` 是 `rfc8785` 规范化后对**固定字段子集**取的 `sha256`，该子集由 `digest_input_version` 标识，首个版本即包含模型身份四元组、生效边界 `effective_limits`、模板文本摘要、包标识与 `target_schema_version`——目录条目内容（provider / base_url / bounds）的变化改变摘要，而不只是条目名；此后新增的可选字段默认**不进入**摘要输入，避免“加一个字段 ⇒ 所有历史记录与新记录都判为不同”的假差异。内容是否被人工编辑是另一回事，由另一个摘要承担，不与配置摘要混用。

持久化沿用既有形状：`pc_artifacts` 没有溯源列，生成溯源同样放在独立表，由 `ArtifactRepository` 在写 revision 时写入、在读取 lineage 时回填。候选侧在 `pc_artifact_candidate_versions` 增加 `generation_profile_family` / `generation_profile_artifact_id` / `generation_profile_revision` / `generation_provenance` 四列，再增加一列**派生**的 `generation_digest`，五列同生同灭，并带指向 `pc_artifacts` 的复合外键（档案与制品同 Scope，外键成立）。

`_candidate_draft` 在批准时把它带进新的 Artifact draft，从而让“由谁生成”在 Revision 的 lineage 里可见。

## Family 分派的注册化

家族路径由枚举改为注册表驱动

批准仍在单一数据库事务内完成：按候选记录的 `schema_version` 校验提案、执行声明式 lineage、写 Artifact、更新派生索引、`mark_approved` 五步同事务，任一步失败整体回滚。`cardinality = "singleton"` 时，单例 target 在**候选创建时**冻结：显式给出的 target 直接记录，未给出的在该时刻解析当前 head 并记录进候选行的 `target_*` 列（首次创建尚无 head，记录“预期不存在”）；审批只对记录值做 CAS，不在审批时重新解析 latest，从而消除创建与审批之间的 head 漂移。`collection` 保持显式语义。

Owner 语义对扩展家族是必填的：新家族会自动被 `logical_artifacts()` 覆盖，缺 owner 关系会让整个 Scope 的上下文不可用。

## 持久化变更清单

| 表 | 性质 | 变更 |
| --- | --- | --- |
| `pc_artifact_generation_provenance` | **新增** | 按 `(scope_id, family, artifact_id, revision)` 记录 Artifact Revision 的生成溯源：档案引用与结构化生成溯源。与既有 `pc_artifact_publications` 同形，引用列带指向 `pc_artifacts` 的复合外键。 |
| `pc_extension_families` | **新增** | 家族描述：`(family, schema_version)` 主键，存该版本的 JSON Schema 文本、来源扩展标识与激活时间；只增不删，被制品表与候选表以 `RESTRICT` 复合外键引用。它是停用后仍能校验与渲染历史 Revision 的依据，也是 `diff` 的权威输入。 |
| `pc_extension_prompt_templates` | **新增** | 模板文本快照：`template_digest` 主键，存 `template_id` 与解析后的模板文本；只增不删，被生成溯源以 `RESTRICT` 外键引用。它是生成溯源模板锚点的依据：模板文本的版本轴与家族描述的 schema 版本轴相互独立，同一 `(family, schema_version)` 下可并存多份模板文本。 |
| `pc_artifact_candidate_versions` | 修改 | 增加 `schema_version` 列（由内容标记派生，非空并带指向 `pc_extension_families` 的复合外键）、生成溯源四列 + 派生的 `generation_digest` 列、一条五列同生同灭的 CHECK，以及指向 `pc_artifacts` 的复合外键。既有 `target_*` 列不变。 |
| `pc_artifacts` | 修改 | 增加 `schema_version` 列：内置家族为 `NULL`，声明式家族非空（内置集合 ⇔ `NULL`），并带指向 `pc_extension_families(family, schema_version)` 的 `RESTRICT` 复合外键。其余列不变。 |
| `pc_artifact_heads` | 不变 | `family` 本就是无 CHECK 的自由字符串；`searchable_text` 已是通用列，扩展家族复用同一套 active-head 过滤。 |

## 契约变更

需要开环的位置是那些必须接受扩展家族的地方：通用 Artifact 读取路径的五个 `{family}` 路径参数、`BaseArtifactFamily` 驱动的响应字段（`ArtifactCreated`、`ArtifactCollectionItem`、`ArtifactRevision`）、`CandidateFamily`、`ContextAssemblySection.family`，以及候选响应与提案的联合（新增扩展家族分支）。

写入路径不需要开环：扩展制品只由批准产生，`CreateArtifactRequest` 与 `replace_artifact` 的判别联合保持不变，`generation-profile` 作为内置家族按 `CreatePromptArtifactRequest` 的同样方式手写一个可枚举变体。

保持闭集的位置：`TaggableArtifactFamily`（标签是内置特性），以及 `PromptKey`（prompt key 由服务端注册）。开环会改到 `powercontext.http` 重新导出的家族类型（枚举 → 字符串），typed 集成会看到类型变化：这是有意的 breaking change，需随契约变更运行 `make api-generate` 与 `make contract-test`，并在 release notes 单列一条。

新增只读发现端点 `GET /v1/extensions`：返回已激活扩展、家族、当前与历史 `schema_version`、投影与贡献器的声明与放行状态、可用档案 key 与兼容性；每个家族给出四态——`declared`（清单声明）→ `authorized`（授权画像已注册）→ `projection`（投影已声明）→ `prepared_context`（贡献已声明且部署方已放行）——以及“下一步缺什么”。检索与注入失败据此区分「家族不存在 / 投影未声明 / 未放行」三种原因码，而不是笼统的不可用。需要 `server.observe`，且必须 content-free：不含包安装路径、配置片段或凭据。

## 装卸、停用与历史可读性

持久化保证数据不丢，不保证语义可解释。制品内容始终是 `pc_artifacts` 里的 JSON，head 与 lineage 也都在；包被卸载后失去的是解释这些字节的三样东西：授权入口的家族登记、family → 内容类型的映射、以及按 `schema_version` 校验所需的 schema。

读取路径上有三道独立的关：

1. **授权入口。** `artifact_family_profile()` 对未注册家族抛 `AccessInvalidRequestError("artifact-family")`，命中读取、写入与可分享性校验，因此**资源发现与可分享性校验必须跳过未注册家族**。readiness 的门槛不是家族画像而是 owner 关系：`require_scope_content_ready` 逐个取 `logical_artifacts()` 的 owner，`topic-memory` 已有豁免先例。
2. **类型注册表。** `_decode_row` 需要 family → 内容类型；未注册家族没有类型。
3. **schema 校验。** 声明式方案的优势在这里：`pc_extension_families` 存了每个 `schema_version` 的 JSON Schema 文本，因此停用后**仍可按当时版本校验载荷并渲染字段结构**，而不只是返回未解码字节。

停用契约：

- **停用是运维动作，不是删除。** 停用只移出装配、不移出注册：已装载未启用的包按 `enabled=false` 保持注册，只有包卸载才失去注册。停用后该家族不再生成、不再接受写入、不参与召回与注入；已提交 Revision 保留。
- **未注册家族的精确读取返回按家族描述校验与渲染的结果**，并明确标注该版本来自描述表而非活跃注册。
- **pending 候选冻结**：审批需要内容类型与 lineage 规则。未注册家族的候选可以列出并按其记录版本校验，但 `approve` 返回类型化错误。
- **停用有历史的家族只告警不阻塞**：运维被显式告知哪些 `schema_version` 将只能靠描述表解释。
- **描述表不可清理**：只要仍有历史 Revision 引用某个 `schema_version`，其描述必须保留；由 `pc_artifacts` 与候选表 `schema_version` 列上的 `RESTRICT` 复合外键保证。

状态机：

| 事件 | 契约 |
| --- | --- |
| 升级：新增 `schema_version` | 保留旧版本声明；`current_schema_version` 前进；既有 head 的投影重建 |
| 升级：改 `projection` | 触发该家族投影重建；历史 Revision 内容不变 |
| 升级：改 `prepared_context` | 注入渲染按新声明；历史 Revision 不变 |
| 停用 | 停止生成与召回；读取改走家族描述；pending 候选冻结 |
| 重装：同 `schema_version` | 恢复生成、召回与写入，行为与停用前一致 |
| 重装：不同 `schema_version` | 按升级处理；历史 Revision 按其记录版本解码，存储字节不迁移 |
| 降级 | 仅当目标版本仍声明覆盖现存 Revision 与 pending 候选所需的全部 `schema_version`，否则拒绝激活 |

## 执行边界与资源策略

**S1 不执行任何扩展代码**：生产路径上运行的只有清单解析与 JSON Schema 校验，两者都不执行扩展作者提供的逻辑，`side_effects` 全部为 `none`。因此不需要为扩展代码设置沙箱、能力边界或进程资源上限；`validate` 也只做数据校验。

仍然适用的是由档案驱动的模型调用边界：

| 维度 | 策略 |
| --- | --- |
| 时间 | 档案的 `timeout_seconds` 叠加 `max_requests`，外层叠加既有 worker 超时 |
| 输出 | 家族 schema、`output_max_bytes`、证据信封的条目与字符上限 |
| 请求数 | 档案的 `max_requests`，受目录条目上界限制 |
| 网络 | 只经平台适配器，按“模型目录与出向策略”的规则校验 |
| Secret | 只在平台适配器内部按环境变量读取；清单纯数据、不含凭据面 |
| 诊断 | content-free：只带扩展 ID、family 与原因码 |

## 调度与自动触发

S1 只有显式触发生成：调用方（Agent、CLI 或集成）在证据齐备时经通用生成端点发起一次生成，档案不产生自动调度。

后续切片接入自动触发时沿用两个设计点：由家族声明证据过滤条件，以及在“窗口内有合格 Source 但全部被过滤”时记一次无内容诊断，使静默失效可被发现。

## 作者工具

| 命令 | 解决什么 | 关键输出 |
| --- | --- | --- |
| `powercontext extension init <dir> --family <name>` | 从可校验的骨架起步，避免各家写法漂移 | 清单、`schemas/<family>.v<k>.json`、prompt 模板 |
| `powercontext extension validate <path> --powercontext <version>` | 离线复现激活期的全部校验，并挡住无约束的 schema | 与激活期相同的类型化错误码 + JSON Pointer 定位；结构 lint 与骨架 dry run 的结论；`--json` 可机读 |
| `powercontext extension lock <path>` | 固定当前包的语义指纹，供后续升级比对 | `powercontext.extension.lock.json`（清单与全部 schema 的 `rfc8785` 摘要），可提交进仓库 |
| `powercontext extension diff <old> <new> [--against <lock>]` | 升级影响预览 | 受影响家族与画像、被移除的 `schema_version`、需要重建的投影、兼容性结论；退出码区分向后兼容与破坏性变更 |

四条命令共享同一条约束：不装载、不连接数据库、不执行扩展代码。平台版本是 `validate` 的必填轴——兼容矩阵本身属于激活期校验，缺这一轴就会给出与激活期不一致的绿灯。文件指纹不承担包完整性（那是分发层的职责），`lock` 关心的是**语义**影响。

## 管理员工具

管理员侧复用同一命令组的另外三条子命令，避免手写清单与档案 JSON：

| 命令 | 解决什么 | 关键输出 |
| --- | --- | --- |
| `powercontext extension enable <id>` | 把包放进启用清单 | 更新服务端配置中的扩展路径与启用项 |
| `powercontext extension print-config` | 让人知道还差哪几步 | 可直接粘贴的启用清单项、模型目录骨架与逐家族放行项 |
| `powercontext generation-profile new\|set\|rollback` | 写入或回滚生成档案 | 写前按家族 schema 与目录上界校验；写后回显新 revision 与结构化溯源 |

`powercontext config` 向导新增 extensions 步骤，复用既有的双语提示与文档渲染管线，使“启用一个扩展家族”与“配置 inference”处在同一条引导路径上。发现端点返回的四态与「下一步缺什么」正是这些命令的信息来源。

## 实现与验收

实现按两条切片推进；本节验收是全局门槛，不随切片重排：

- **S1**：一个 Runbook 扩展家族 + 一条生成档案，覆盖精确证据 → Candidate → 人工 Review → 检索与上下文贡献 → 停用后的精确读取；含通用生成端点、`pc_extension_families` 与 `schema_version` 落库、模板文本快照、生成溯源。对应验收 1、2a–2e、3–16、17、19–22、25–27。
- **S2**：`extension lock/diff`、`enable` / `print-config` 成型、config 向导 extensions 步骤、发现端点四态细化。对应验收 18、23、24。

验收覆盖外部行为：

1. 一个样例扩展（随实现提供，不作为本 RFC 的附件）从精确证据派生类型化 Candidate，经人工 Review 批准，提交不可变 Revision；证据与溯源在 lineage 中可精确读出。
2. 生成身份的变更可观测：**2a** 改档案内容 ⇒ 新的精确 profile revision；**2b** 改家族 schema ⇒ 新的精确 `schema_version`；**2c** 同一 `prompt_templates[].id` 下换文本 ⇒ 新的扩展包版本身份；**2d** 目录条目内容变化 ⇒ 不产生新版本，但 `digest` 必变。四者分别在 lineage 中以 `generation_profile_revision`、`target_schema_version`、`extension_package` 与 `template_digest`、`digest` 可见；2d 另要求结构化快照能在条目改名、换模型或撤下后读出当时的模型与端点。**2e** 同一 `schema_version`、同一 `prompt_templates[].id` 下，两个包版本携带的模板文本不同 ⇒ 两份模板文本快照都留存；卸载扩展包之后两段文本都仍可读出，且各自 revision 的 `template_digest` 都能与原文核对。
3. 无效清单（未知字段、非法 schema、重复家族、命名冲突、`target_family` 未声明）在激活期被拒绝，只在 readiness 与发现端点中可见；其余家族与生成路径不受影响。运行期失败在持久化 Candidate 之前终止。
4. 生成器无法分配最终 Artifact 身份、批准自己的 Candidate、发布内容或自我授权；相关 API 不存在。
5. Artifact ID 由平台按 family 派生前缀并加 CAS 分配，与内容无关；作者不声明前缀，也没有前缀冲突这一类失败。
6. 空结果按 `noop_field` 判定：不写候选、返回显式 `no_op`、诊断 content-free，且与 `failure` 路径可区分。
7. `cardinality` 生效：`singleton` 在候选未给 target 时演进同一逻辑产物并做 head CAS；`collection` 保持显式语义。单例 target 在候选创建时冻结：候选创建与审批之间 head 被并发推进时，审批按候选记录的 target 做 CAS 并返回版本冲突；首次创建按“预期不存在”比对，head 已被并发创建时同样返回版本冲突。
8. 声明式 schema 的完整表达力生效：`allOf`/`if`/`then`、嵌套 `minLength`/`maxItems`、`const`、`additionalProperties: false` 与跨家族载荷拒绝都在激活期与审批路径上被强制。
9. 载荷规范化生效：同语义不同键序的载荷产生相同存储字节。
10. 未声明投影的扩展家族不可检索；未声明或未放行的贡献器不注入；直接请求该家族组装返回 422。
11. 未批准的 Candidate 不出现在检索与 PreparedContext 中。
12. 注入内容保持有界、带精确引用，并被包裹为不可信历史；扩展无法注入原始 system/developer 指令。
13. 越界的档案内容（超出目录或家族上界）在写入时被拒绝；档案与清单中的 URL 被 schema 拒绝。
14. 出向校验拒绝非 http/https、localhost、环回、私有与保留地址，并在配置装载与请求两处生效。
15. 诊断不含内容；模型输出与证据正文不出现在错误、readiness、发现端点或 capabilities 中。
16. **停用安全**：停用含历史 Revision 的家族后，相关 Scope 的上下文仍可用（未注册家族在资源发现与可分享性校验中被跳过；readiness 只受 owner 关系约束），精确读取按家族描述校验并渲染，pending 候选可列出但审批被拒；重装同版本后行为与停用前一致。
17. 升级抬高 `schema_version` 后，升级前创建的 pending 候选按其记录版本审批；投影声明变化触发重建且索引与新声明一致。
18. `powercontext extension validate` 对合法包返回成功、对非法包返回与激活期一致的类型化错误码（同一套校验代码与定位信息，`--json` 可机读），平台版本是必填轴且未指定即失败，不连接数据库，**且不执行任何扩展代码**；`init` 生成的包可直接通过 `validate`，而结构 lint 与骨架 dry run 会在无约束的 schema 上失败；`lock` 产出的摘要可提交进仓库，`diff --against` 正确报告被移除的 `schema_version` 与需要重建的投影，并以退出码区分向后兼容与破坏性变更。
19. 各数据库方言各验证一次扩展家族的提交、检索、精确读取与停用降级路径。
20. 通用生成端点可用：给 `profile_key` 时按画像生成并写入溯源；只给 `family` 时走人工提案并按 `current_schema_version` 校验；两者都给或都不给、以及未注册的 key 或 family，都返回类型化错误且零写入。
21. 没有画像的扩展家族仍能经人工提案产出候选，且这类候选的生成溯源为空；同一家族存在多条画像时解析按 profile key 而非家族，指定不同 key 得到不同模型与预算。
22. **版本落点**：内容标记与 `schema_version` 列始终一致（列由内容派生，不一致即类型化错误）；未登记的版本无法写入；清理仍被引用的描述被 `RESTRICT` 拒绝；停用与降级后仍能按记录版本校验与渲染。
23. **default-deny 可解释**：`GET /v1/extensions` 给出四态与「下一步缺什么」；检索与注入失败区分「家族不存在 / 投影未声明 / 未放行」；MCP 只读客户端能列出扩展家族与可用档案 key。
24. **管理员闭环**：从空配置到「家族可生成」全程不需要手写 JSON——`extension enable` / `print-config` 产出配置片段，`generation-profile` 子命令写入档案并回显 revision 与结构化溯源，config 向导覆盖同一步骤。
25. Runbook 的 `steps[]` 按原序、以 `Steps[1]:`… 形式出现在 PreparedContext；`symptoms[]` / `failure_handling[]` 同样可达；超预算时按整条元素裁剪且截断标记可见。
26. 投影覆盖嵌套字符串：`steps` 中的词可被检索命中；`schema` 版本标记不进入投影。
27. 同语义不同键序的载荷产生相同的 `searchable_text` 字节（与载荷规范化同序）。

# Drawbacks

- 扩展家族的表达力有明确天花板：不能自定义投影与渲染、不能自定义 lineage 校验、不能声明输入形状、不能做输出变形。需要这些能力的领域只能通过拓展声明种类和后续的代码钩子扩展点获得。
- 模型输入固定为平台的有界证据信封，家族无法表达自己的输入契约。
- 清单 schema 是唯一权威，而动态类型不携带约束。
- 新增两张表与两处列（`schema_version`、生成溯源），各数据库方言的迁移与探测逻辑都要同步维护。
- 平台不做内容迁移：历史 Revision 按其记录版本校验与渲染，不做跨版本的字段改写。因此字段改名或语义变化会让新旧内容在展示与检索层的形态不一致（旧字段名照旧出现在渲染文本与检索词里），想统一只能靠新家族名或后续切片补一个展示层投影。
- 多版本并存意味着描述表只增不减，长期需要运维侧的归档或压缩策略。
- 家族可以并行持有多个 `schema_version`，但平台不替作者判断哪个版本更合适；作者仍需靠锁定样例与 `diff` 自行维护兼容性。

# Rationale and alternatives

- **为什么家族用声明式而不是代码。** 声明式对于拓展家族开发友好，虽然代码型家族表达力更强，但代价是三笔：运行期要执行扩展代码（需要沙箱与能力边界）、包卸载后类型消失、家族集合变化要改平台源码与契约枚举。解决成本高。
- **为什么档案不声明 schema。** 输出契约由目标家族解析，档案只能收紧上界。若档案能更换输出 schema，等于档案能凭空造出类型，穿透 `codec` 的严格校验与 `_require_content` 的类型保证。
- **为什么保留多个 `schema_version`，而不是收敛为单版本。** 单版本要求演进始终向后兼容（新 schema 必须接受旧载荷），保留多个换来历史可解释性不依赖任何作者纪律，也不需要运行期执行任何迁移代码。
- 让 prompt 返回无类型 JSON 直接写入单一通用表：抹除家族语义、生命周期、Review 与兼容性，并穿透 `codec` 的严格类型校验。
- 把每个新结果都当作 Memory：混淆持久事实/决策与领域特定交付物。
- 让自定义输出自动进入 PreparedContext：绕过选择、引用、预算、信任与授权。
- 档案自带 `base_url`：让 Scope 管理员可指定任意出向目标，且 egress 策略无处挂载。
- 让档案 revision 跟随目录条目内容或扩展包身份变化：目录条目的写者是部署方、包版本的写者是扩展作者，而档案写入只需要 `SCOPE_ADMIN`；让 revision 跟随等于要求目录只增不改，并把模型可达性的控制权从部署方移交给各 Scope，带来运维负担，而不是可复现性。
- 为扩展家族另开一套 `/v1/extensions/...` 操作面：把同一资源拆成两套 API 面，与单一 repository 路由冲突。
- 扩展家族提供管理直写路径：会要求 `CreateArtifactRequest` 的判别联合开环，并把“扩展预置内容”引入信任面。S1 只保留审批产生。

# Prior art

[RFC 0050](0050_artifact_candidate_review_inbox.md) 确立 Review 由家族固定决定，Candidate 不是 Artifact，不进入搜索或 PreparedContext。

[RFC 1468](1468_scope_owned_prompt_management.md) 确立配置型家族的 Scope 级版本化模式，并明确模型设置、credential、schema 与资源限制位于 Prompt content 之外。

[RFC 1458](1458_artifact_generation_source_access.md) 确立生成证据的准入。

[RFC 1489](1489_prepared_context_text_assembly.md) 确立 Runtime 对最终内容、精确引用与预算的所有权。

# Unresolved questions

- 自动触发的切片边界：复用 source-window 轮次还是引入独立的调度绑定。
- 代码钩子扩展点（自定义投影、渲染、lineage）是否开放，以及它需要的信任边界与沙箱语义。
- 既有 prompt ref 语义收敛到统一溯源槽的迁移路径与时机。
- 扩展家族是否纳入 tags 与跨 Scope 发布，以及它们各自要求的授权词汇扩展。
- 描述表的归档方式：保留策略已定（只增不删 + `RESTRICT`），但长期归档与压缩仍无路径。
- 扩展家族在 Dashboard 上的呈现方式；S1 明确不呈现。

# Future possibilities

- 自动触发：接入既有 source-window 轮次，沿用证据过滤与“全被过滤”诊断。
- 内置家族接入生成档案：让 Experience、Skill 等内置家族也能被档案定向生成。
- 增加投影与渲染的声明式字段列表，增强声明式表达能力
- 代码钩子作为逃生口：为需要自定义投影、渲染与 lineage 的家族提供受能力边界约束的扩展点，同时保留声明式作为默认。
- 扩展提供向量投影或自定义排序，与既有 FTS 融合。
- 货币成本预算：需要模型计价数据与更细的用量归属。
- 扩展包的签名与来源校验，使“管理员控制的软件包边界”具备可验证的供应链证据。
