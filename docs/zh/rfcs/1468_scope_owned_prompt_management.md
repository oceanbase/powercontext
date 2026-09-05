- Proposal Name: `scope_owned_prompt_management`
- Start Date: 2026-09-05
- RFC PR: [oceanbase/powercontext#1468](https://github.com/oceanbase/powercontext/pull/1468)
- Tracking Issue: [oceanbase/powercontext#1465](https://github.com/oceanbase/powercontext/issues/1465)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0016](0016_pydantic_ai_inference_integration.md)、[RFC 0051](0051_experience_skill_artifact_families.md)、[RFC 0080](0080_memory_search_reranking.md)、[RFC 1396](1396_handoff_access_control.md)、[RFC 1437](1437_source_artifact_rest_api.md)

# Summary

本 RFC 为内置 PowerContext Runtime 使用的 operational prompt 定义 Scope-owned、可版本化的管理方案。一个已注册的
operational Prompt 以 `family=prompt` 的 Artifact 存储；其稳定 `artifact_id` 是 Server 注册的 prompt key，例如
`memory.extract`。Scope 仍然是唯一的 ownership 与隔离边界。Agent 或用户集成首先解析已有的 Scope binding，
不会再创建第二套 Prompt binding 模型。
这里的隔离指持久化与解析的分区；authentication 与 authorization 仍然是独立的 Server concern。

Prompt revision 使用唯一的 canonical content 结构：`schema_version`、`mode`、`instructions` 和
`demonstrations`。一条 demonstration 是一对类型化的 input 与 expected output。产品界面可以把会产生结果与
no-op 的 demonstration 展示为正例和反例两组，但 wire contract 和 persistence contract 不使用不正式的
`*_examples` 字段。

本设计不增加数据库表，也不增加 Prompt 专用 CRUD 接口。它复用已有 Artifact create、current read、conditional
replace、current-family list 与 exact-revision read 操作。正式新增的 HTTP 操作恰好只有两个：通用 Artifact
revision history list，以及只生成但绝不保存 Prompt demonstration 的接口。回滚通过读取不可变旧 revision，再将
其 content 写成一个新的、单调递增的 revision 完成。

执行 inference 时，Runtime 会按该操作的 Scope 解析并冻结一个精确的 Prompt selection。可编辑 instructions 只替换
该操作中可调节的 guidance。Server-owned trust rules、结构化 input/output schema、credential、model setting、tool
authority 与 resource limit 始终位于 Prompt content 之外。

# Motivation

内置 Runtime 当前有六类由 prompt 驱动的操作，以及七个内置 instructions 变体：

| Prompt key | Operation | 当前内置变体 |
| --- | --- | --- |
| `memory.extract` | 从有界 evidence 中抽取持久 Memory candidate | `coding`、`conversation` |
| `memory.rerank` | 对 Memory search 的 coarse candidate 重排 | listwise reranking |
| `experience.incubate` | 从 Task Outcome 提议 Experience candidate | incubation |
| `experience.generate` | 从选定 evidence 生成一个 Experience proposal | explicit generation |
| `skill.generate` | 生成一个 managed Skill proposal | explicit generation |
| `handoff.generate` | 从有界 evidence 生成一个 Handoff | handoff generation |

这些 instructions 在源代码中有版本，并在 Runtime composition 时绑定到 structured generator。这对单个部署而言安全且
确定，但无法满足四项产品需求：

1. 不同 Scope 无法定义不同的抽取或生成 guidance；
2. 操作者无法查看某个 Scope 中 prompt 变更的完整历史；
3. 无法在不修改部署配置的情况下恢复旧配置；
4. inference trace 无法区分 built-in selection 与某个特定 custom Prompt revision。

在 Prompt 记录上增加 `agent_id` 或 `user_id` 会违背 PowerContext ownership model。Agent 与用户可以通过
`ScopeBinding` 映射到不同 Scope，但持久状态由解析后的 Scope 拥有。专用 Prompt 表以及平行的 Prompt CRUD 接口会重复
Artifact 已有的 revision、head、optimistic concurrency、lineage 与 Scope isolation。

因此，本设计把 operational Prompt configuration 作为另一种 Scope-owned Artifact family，同时保留一个小而明确的
Runtime extension boundary。

# Guide-level explanation

## 用户模型

Dashboard 为当前 Scope 展示一个配置页。每个支持自定义的 built-in prompt key 有两种 mode：

- **Auto** 使用部署 Runtime 选定的内置 instructions；
- **Custom** 使用 Scope-owned instructions 与 demonstrations。

禁用的 operation 和不支持自定义的注入组件显示为不可用，并说明原因。不能仅因为 key 已注册，就把它展示为可配置的
Auto/Custom operation。

对于 `memory.extract`，custom editor 可以展示三个区域：

1. 抽取 instructions；
2. expected output 包含一条或多条 Memory candidate 的正向 demonstrations；
3. expected output 不包含 candidate 的反向 demonstrations。

所有 demonstrations 都持久化在一个有序 `demonstrations` 数组中。当 UI 需要分组时，由该 operation 的 Prompt
Definition 根据合法 expected output 判断它会产生结果还是 no-op。分类从类型化 output 推导，不是第二个持久化 policy
字段。

例如，存储的 content 为：

```json
{
  "schema_version": "powercontext.prompt.v1",
  "mode": "custom",
  "instructions": "保留持久的测试偏好和已经验证的失败经验，忽略临时请求。",
  "demonstrations": [
    {
      "input": {
        "evidence": [
          {
            "evidence_id": "source-1",
            "evidence_type": "source",
            "content": "每次发布前我都会执行核心链路的冒烟测试，耗时大约 20 分钟。"
          }
        ],
        "current_entries": []
      },
      "expected_output": {
        "candidates": [
          {
            "kind": "preference",
            "text": "用户每次发布前都会执行核心链路的冒烟测试，耗时大约 20 分钟。",
            "evidence_ids": ["source-1"],
            "intent": "add"
          }
        ]
      }
    },
    {
      "input": {
        "evidence": [
          {
            "evidence_id": "source-2",
            "evidence_type": "source",
            "content": "请把上一条消息中的代码发给我。"
          }
        ],
        "current_entries": []
      },
      "expected_output": {"candidates": []}
    }
  ]
}
```

`input` 与 `expected_output` 必须符合 `memory.extract` 已注册的 schema。Server 不接受用自由文本
demonstration label 代替合法 expected output。

## Scope selection

Prompt 的完整地址为：

```text
ArtifactAddress(
  scope_id,
  ArtifactRef(family="prompt", artifact_id=prompt_key, revision=revision),
)
```

因此，Prompt revision 的完整身份包含 Scope。同一个 `memory.extract` key 可以在两个 Scope 中拥有不同 revision，
且两者不共享状态。

面向 Agent 或用户的集成执行以下过程：

```text
Agent 或用户身份
        |
        v
解析已有 ScopeBinding
        |
        v
一个确定的 scope_id
        |
        v
Scope-owned Prompt head
```

不存在隐式 parent-Scope inheritance、跨 Scope `latest` 或 Agent/User fallback。复制配置会在目标 Scope 创建新的
Artifact revision，之后拥有独立的 lifecycle。

## 创建与更新

已有通用 Artifact API 用于创建 custom Prompt head：

```http
POST /v1/scopes/project:payments/artifacts
Content-Type: application/json
```

```json
{
  "family": "prompt",
  "prompt_key": "memory.extract",
  "content": {
    "schema_version": "powercontext.prompt.v1",
    "mode": "custom",
    "instructions": "保留持久的支付调试决策和已经验证的失败经验。",
    "demonstrations": []
  }
}
```

Server 使用 `prompt_key` 作为 `artifact_id` 并提交 revision 1。未知 key 会被拒绝；Custom 写入还要求实际组件支持
自定义。已注册 key 始终允许写入 Auto，以便操作者在 operation 不可用时清除 custom selection。调用方不能自行分配
任意 operational Prompt identity。

更新 Prompt 使用已有 conditional replacement 操作：

```http
PUT /v1/scopes/project:payments/artifacts/prompt/memory.extract
If-Match: "revision:4"
Content-Type: application/json
```

请求体包含完整 replacement `content`。请求成功后提交 revision 5；revision 4 仍不可变并可读取。并发更新携带
stale ETag 时返回 `412 Precondition Failed`。

切回 Auto 也是一次有版本记录的变更：

```json
{
  "content": {
    "schema_version": "powercontext.prompt.v1",
    "mode": "auto",
    "instructions": "",
    "demonstrations": []
  }
}
```

不存在 Prompt Artifact 时同样采用 Auto。操作者希望在历史中明确记录“恢复内置选择”时，可以持久化一个 Auto
revision。

## 生成 demonstrations

唯一的 Prompt 专用接口生成可编辑的 demonstration 建议：

```http
POST /v1/scopes/project:payments/prompts/memory.extract/demonstrations
Content-Type: application/json
```

```json
{
  "instructions": "保留持久的测试偏好和已经验证的失败经验。",
  "demonstration_count": 1
}
```

响应包含恰好一条符合 schema 的 demonstration：

```json
{
  "prompt_key": "memory.extract",
  "demonstrations": [
    {
      "input": {"evidence": [], "current_entries": []},
      "expected_output": {"candidates": []}
    }
  ]
}
```

该接口不会创建或替换 Artifact，不推进 head，也不会把输出静默合并到 current content。调用方评审并编辑这些建议后，
再通过正常 Artifact create 或 replace 操作保存。

## 查看历史与回滚

通用 revision-history 接口按 revision 从新到旧列出不可变历史：

```http
GET /v1/scopes/project:payments/artifacts/prompt/memory.extract/revisions?limit=50
```

每个 item 包含精确 identity、content digest 与 lineage identity。完整 content 通过已有 exact-revision 操作读取：

```http
GET /v1/scopes/project:payments/artifacts/prompt/memory.extract/revisions/2
```

回滚不改写历史，也不需要第三个接口：

1. 读取旧的 exact revision；
2. 读取 current head 与 ETag；
3. 使用旧 revision 的 `content` 和 current ETag 替换 current head；
4. 得到一个新 revision，其 content digest 与被恢复 revision 相同。

例如，revision 5 为 current 时恢复 revision 2，会创建 revision 6。Revision 2 到 5 继续可读。通用 request audit
标识 actor 与 request；与旧 content 相同可通过 content digest 确认。Server 绝不把 head pointer 向后移动。

# Reference-level explanation

## 术语与边界

| Term | 含义 |
| --- | --- |
| Operational Prompt | 一个已注册内置 inference operation 的 Scope-owned configuration |
| Prompt key | Server 注册并用作 Prompt Artifact ID 的稳定 operation identifier |
| Prompt Definition | 一个 prompt key 的 Server-owned typed contract |
| Prompt revision | 一个 Scope 中不可变的 `family=prompt` Artifact revision |
| Built-in selection | Auto mode 使用的 Server 内置 guidance 与 version |
| Compiled prompt | 一次调用实际使用的 invariant instructions、selected guidance、demonstrations 与 structured schema contract |

Operational Prompt 不是普通 user input、Source evidence、managed Skill、model credential，也不是任意 system message。
Managed Skill 告诉 Agent 何时以及如何执行可复用能力；operational Prompt 只调节 PowerContext 中一个固定的 inference
operation，不增加 tool 或 authority。

RFC 1396 为 reusable parameterized task template 预留了未来的 `family=prompt` lifecycle，并把当前内部 generation
prompt 归类为 Server-only configuration。本 RFC 只修改后一个边界：已注册 operational prompt 可以由 Scope-owned
Prompt Artifact 自定义。本 RFC 不引入 RFC 1396 作为未来能力描述的 reusable task-template lifecycle、approval
state、`prompt.use` 或 exact Prompt sharing。

## Prompt Definitions

Server 在 Runtime composition 时注册 Prompt Definition。注册结果在 composed Runtime 的整个生命周期内保持固定。每个
Definition 提供：

```python
class PromptDefinition(Protocol):
    key: str
    definition_version: str
    input_type: type[BaseModel]
    output_type: type[BaseModel]
    builtin_version: str
    invariant_instructions: str
    default_instructions: str

    def is_noop_output(self, output: BaseModel, /) -> bool: ...
```

初始 registry 恰好包含 Motivation 中列出的六个 key。`memory.extract` 在 Auto selection 时继续使用部署校验过的
`coding` 或 `conversation` profile，因此六类逻辑 operation 仍然对应当前七个内置 instructions 变体。

以下内容由 registry 而不是 persisted Prompt content 拥有：

- structured input/output type；
- invariant evidence、safety、secret、citation 与 identity rule；
- built-in selection 与 built-in version；
- model 与 request setting；
- input/output size limit；
- demonstration no-op classification；
- 与 Prompt content schema version 的兼容规则。

两个 Definition 不能注册相同 key。重复 key、Definition 内部不一致或缺少 built-in selection 时，Runtime composition
必须失败，而不是接受 untyped configuration。注册描述已知 contract，不代表实际组件支持自定义；后文的 capabilities
负责报告这一区别。

### Definition compatibility

`schema_version` 标识持久化 Prompt envelope，`definition_version` 标识部署中的 operation contract，
`builtin_version` 标识默认 guidance。三者不能混用。V1 保留四字段 Prompt content，不为每条 revision 再保存一个
版本选择字段，而是采用以下兼容策略：

- 同一 prompt key 和 content schema version 下，Definition 更新必须继续接受所有之前合法的 demonstration，并保持
  input 与 expected output 的含义。调整默认 guidance 不一定改变 typed contract。在同一 key 下破坏类型或语义兼容
  不属于 v1 支持的升级。
- 历史读取返回不可变的存储 content 和原始 digest，不使用当前 Definition 重新校验 demonstration。Operation 被禁用、
  替换为注入组件或出现不兼容时，历史仍可读。Resolution 校验不能改写历史 content，也不能向存储表示中插入新默认值。
- 写入，包括复制旧 content 回滚，使用部署中的兼容 Definition 校验。遇到不兼容 payload，返回 `422` 和
  `prompt_definition_incompatible`，head 不变。解析不兼容的已有 custom head 时，受影响 operation 返回 `503` 和
  相同 code，不能静默回退 Auto。管理读取和显式替换为 Auto 始终可用于恢复。
- 升级前，用目标 Definition 检查已有 custom head。不兼容 head 必须先显式迁移为新 revision，或切回 Auto，才能启用
  受影响 operation。迁移可能改变 content 和 digest，不等于 exact-content rollback。

同一部署的所有 worker 必须使用相同的 Definition、built-in profile/version 和 compiler version。V1 不支持混合版本
worker；部署工具必须先排空旧 worker 再切换版本。Version identifier 和 compiled digest 用于诊断，本身不能协调滚动
升级。恢复 Prompt content 只恢复 custom guidance，不恢复旧 model、compiler 或部署，也不保证推理输出完全相同。

## Prompt Artifact content

`PromptContent` 是 strict model，并拒绝未知字段：

```python
class PromptDemonstration(BaseModel):
    input: JsonValue
    expected_output: JsonValue


class PromptContent(BaseModel):
    schema_version: Literal["powercontext.prompt.v1"]
    mode: Literal["auto", "custom"]
    instructions: str
    demonstrations: tuple[PromptDemonstration, ...]
```

以下校验规则属于 public contract：

- 四个字段全部必填；
- `auto` 要求空 `instructions` 与空 `demonstrations` 数组；
- `custom` 要求 instructions 至少包含一个非空白字符；
- instructions 是 trimmed NFC text，最多 32,768 个字符；
- demonstrations 保持调用方顺序，最多 50 条；
- 每个 `input` 与 `expected_output` 都必须严格符合 Prompt Definition 注册的类型；
- 每条 demonstration 的 canonical JSON 最多 64 KiB；
- 完整 canonical Prompt content 最多 256 KiB。

Demonstration 只包含期望行为。Operation 有合法 no-op result 时，反例使用该结果作为 `expected_output`，而不是故意
错误的 output。没有合法 no-op 的 operation 只使用普通 input/output demonstration，其 classifier 始终返回 false，
UI 不虚构空结果或反例分组。`memory.extract` 通过非空和空 `candidates` 支持正反两组。

## Persistence 与 identity

不增加数据库表或 Prompt binding record。Persistence 复用：

| 已有存储 | Prompt 用途 |
| --- | --- |
| `pc_artifacts` | 按 `(scope_id, prompt, prompt_key, revision)` 保存不可变 Prompt content |
| `pc_artifact_heads` | 按 `(scope_id, prompt, prompt_key)` 保存 current Prompt revision |
| 已有 Artifact lineage tables | Prompt configuration 参与 generated lineage 时保存 exact Artifact input |
| 已有 system provenance Source | 保存 canonical create 或 replace request provenance |

Prompt family 在现有 Artifact repository 中增加 family-owned writer 和已注册 content model。它不增加 Prompt 专用
repository、head logic、revision counter 或 transaction。

Prompt key 使用已有 Artifact ID 语法，并由 Runtime registry 额外执行 allow-list 校验。初始 key 是全局稳定的 wire
vocabulary。重命名 key 属于兼容性变更；alias 需要显式的未来 migration，不能静默合并 history。

RFC 1437 为其四种 family 生成 Artifact ID，并把 Create outer shape 固定为 `family` 加 `content`，其中 Handoff 是
Server 已知 singleton 的例外。本 RFC 只为新增 Prompt family 局部修订该规则。`CreatePromptArtifactRequest` 增加必填
顶层 `prompt_key`；family writer 使用固定 registry 校验它，并把它作为 `artifact_id`。已有 generic Create family 的
request shape 不变。调用方只能选择已知 operation，仍不能分配任意 Artifact ID。把该 resource selector 隐藏在 Prompt
content 中会重复 identity，也会使 replacement content 依赖 Create transport shape。

## Revision semantics

适用正常 Artifact 保证：

- create 提交 revision 1；该 key 在 Scope 中已有 head 时返回 `409 Conflict`；
- replace 原子提交一个完整的 next revision；
- revision 是不可变正整数，绝不复用；
- 只有 content、lineage 与 derived state 全部持久后才推进 head；
- replacement 必须携带 `If-Match`；
- exact-revision read 绝不解析 `latest`；
- 本 RFC 不提供 physical delete 或 history rewrite。

用 canonical 相同的 content 替换 Prompt 是合法操作，并会产生另一个 revision。这样可以保留明确的 operator action，
也让 rollback 行为一致。客户端希望避免 no-op revision 时，可以在写入前比较 `content_digest`。

## Runtime resolution 与 compilation

Prompt resolution 在 `scope_id` 确定后、第一次 model request 前，针对每个 inference operation 执行，而不是只在
Runtime composition 时执行一次。

Resolver 返回一个不可变值：

```python
class ResolvedPrompt(BaseModel):
    key: str
    definition_version: str
    selection: Literal["built_in", "artifact"]
    artifact: ArtifactRef | None
    selected_version: str
    compiled_digest: str
    instructions: str
    demonstrations: tuple[PromptDemonstration, ...]
```

支持自定义的 built-in component 使用以下算法。注入或禁用组件在分派前遵循后文的可用性规则；注册本身不会让它们
接收 resolved Prompt。

1. 为 operation 选择已注册 Prompt Definition；
2. 读取 `(scope_id, prompt_key)` 对应的 current `family=prompt` head；
3. head 不存在或 mode 为 Auto 时使用 built-in selection；
4. 否则使用所选 Definition 校验 custom revision；
5. 以固定顺序编译 Server-owned invariant instructions、selected guidance、demonstrations 与 structured output
   contract；
6. 计算 canonical compiled digest；
7. 为完整逻辑 operation 冻结结果，包括其 retry。

在执行中的 extraction、rerank、generation 或 Handoff operation 期间修改 head，只影响下一个逻辑 operation。Memory
flush 在处理其 bounded Source window 前冻结 Prompt。Prompt head 变更不会自动重新生成已有 Memory 或其他 Artifact。

Compiler 保留 Server-owned instruction 及其 message priority。如果 inference adapter 支持 typed example message，
则通过该机制传入 demonstrations；否则使用 canonical JSON 和经过转义的 Server-owned delimiter。转义保护序列化
边界；delimiter 和 message priority 都不能证明模型会遵守语义指令。推理后仍需执行代码层校验。

实现可以按 `(definition_version, selection identity, compiled_digest)` 缓存 compiled prompt。它必须按照 Artifact
current read 相同的一致性规则使 head lookup 失效，也绝不能使用另一个 Scope 的 head。

## Provenance 与 observability

每个托管 inference span 记录：

- `powercontext.prompt.key`；
- `powercontext.prompt.selection`，值为 `built_in` 或 `artifact`；
- custom 时的 exact Artifact family、ID 与 revision；
- built-in version 与 Definition version；
- compiled prompt digest；
- demonstration count。

Prompt body 与 demonstration body 不写入日志，也不作为 metric label。

当一个 durable generated Artifact 已经记录 Artifact input 时，其 lineage 包含 generation 使用的 exact custom Prompt
`ArtifactRef`。这是 configuration lineage，而不是 factual evidence：它不授予 transitive read，不满足 Source citation
requirement，也不能让 Prompt instructions 支撑事实声明。Built-in selection 不创建 synthetic ArtifactRef，通过记录的
built-in version 与 compiled digest 标识。Memory rerank decision 等 ephemeral output 只在 operation trace 中记录相同
identity。

### Handoff generation 经 finalize 到 commit

Handoff prepare、finalize 和 commit 可以是独立请求。Prepare 选定的 Prompt 必须随 draft 传递；finalize 和 commit
不能读取届时的 current Prompt head 来重建 generation provenance。

已有 Handoff transport value 增加可缺省的 `generation` envelope，与可编辑 draft text 分离。它包含 Server 可验证
的 receipt，绑定 Scope、exact custom Prompt reference 或 built-in selection、Definition 与 built-in version、
compiled digest，以及原始 generated draft 的 digest。Server 在生成成功后签发 receipt；调用方不能仅提交一个原始
Prompt reference 就获得已验证 provenance。用途绑定的签名 receipt 不需要新表。Receipt 必须能够跨部署 worker 验证，
密钥轮换时继续支持尚未完成的 draft。私有签名材料不能向客户端暴露。

生命周期如下：

1. Prepare 冻结 selection，返回 generated draft 及其 `generation` envelope。
2. Finalize 验证 receipt 和 Scope，正常校验编辑后的 draft，并把 envelope 传入 `PreparedHandoff`。Commit 再次验证，
   不信任客户端提交的 prepared value。
3. Commit 比较最终可编辑字段与原始 draft digest。持久化 generation metadata 记录 `unchanged` 或 `edited`。编辑后的
   Handoff 保留生成来源，但不声称最终文本由模型原样生成。已有 evidence、citation、authorization 和 optimistic
   concurrency 校验仍然适用。
4. 已验证的 exact custom Prompt reference 进入 configuration lineage。Generation metadata 保存于已有 Handoff
   Artifact JSON，参与 canonical content digest 和 no-op 比较；receipt 字节与签名密钥不写入 Artifact。即使可见
   文本相同，不同 generation origin 也不能错误继承旧来源。

上述检查同样适用于 generic Handoff write 和内部 activation 路径。持久化 generation metadata 由 Server 推导，复制
读取响应中的 metadata 不能代替合法 receipt。没有 receipt 时，writer 拒绝调用方提供的已验证 metadata，只接受
明确未归因的 content。

例如，使用 Prompt revision 2 生成的 draft，在 commit 时即使 current 已是 revision 3，仍记录 revision 2。中间的
Prompt 更新不会使 draft 失效。Envelope 缺省表示手工或未归因 content，不推断 Prompt lineage；已提供但无效、被篡改
或跨 Scope 的 envelope 返回 `422`，不能静默降级。丢弃 envelope 会失去已验证归因，不会获得额外权限。

这要求同步扩展 strict Handoff Python model、已有 HTTP request/response schema、mapper 与客户端，以及持久化 Handoff
generation metadata 的读取方。不含该字段的旧 payload 仍合法。不保留该字段的旧客户端无法提供已验证 generation
provenance。这是已有 Handoff contract 和 JSON storage 的扩展，不增加 endpoint 或数据库表。

## HTTP contract

完整 v1 HTTP surface 为：

| 状态 | Method 与 path | operationId | 用途 |
| --- | --- | --- | --- |
| 已有，扩展 | `GET /v1/capabilities` | `get_capabilities` | 报告各 key 的实际自定义支持状态和部署内置标识 |
| 已有，扩展 | `POST /v1/scopes/{scope_id}/artifacts` | `create_artifact` | 接受 `family=prompt` 与已注册 `prompt_key` |
| 已有，扩展 | `GET /v1/scopes/{scope_id}/artifacts/prompt` | `list_artifacts` | 列出 Scope 中 current Prompt heads |
| 已有，扩展 | `GET /v1/scopes/{scope_id}/artifacts/prompt/{prompt_key}` | `get_artifact` | 读取 current Prompt head 与 ETag |
| 已有，扩展 | `PUT /v1/scopes/{scope_id}/artifacts/prompt/{prompt_key}` | `replace_artifact` | 提交完整 next Prompt revision |
| 已有，扩展 | `GET /v1/scopes/{scope_id}/artifacts/prompt/{prompt_key}/revisions/{revision}` | `get_artifact_revision` | 读取一个 exact immutable revision |
| **新增** | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` | `list_artifact_revisions` | 列出任意 Artifact family 的 immutable revisions |
| **新增** | `POST /v1/scopes/{scope_id}/prompts/{prompt_key}/demonstrations` | `generate_prompt_demonstrations` | 生成经过校验但不保存的建议 |

新增 endpoint 数量恰好是两个。不存在 Prompt-specific list、get、create、update、rollback、publish、validate、
preview、activate 或 delete endpoint。

已有 Handoff prepare/finalize/commit payload 同样按前文约定携带 `generation`，route 与 operation ID 不变。

新增的 Create union member 为：

```json
{
  "family": "prompt",
  "prompt_key": "memory.extract",
  "content": {
    "schema_version": "powercontext.prompt.v1",
    "mode": "custom",
    "instructions": "保留持久偏好。",
    "demonstrations": []
  }
}
```

`prompt_key` 只出现在 Prompt Create 中。Replace 从 path 选择同一个 identity，并按照 RFC 1437 继续只接受
`content`。生成的 system Source 使用已有 lineage-only 机制，target-bound 到最终的
`artifact_id=prompt_key` 与 revision。

`list_artifact_revisions` 接受已有 bounded `limit` 与 opaque `cursor` pagination model。它按 revision 降序排列，
返回 `ArtifactRevisionPage`；item 包含 `scope_id`、`family`、`artifact_id`、`revision`、`sources`、
`artifacts` 与 `content_digest`，不包含 content。Cursor 绑定完整 Scope、family、Artifact ID、authorization
constraint、filter 与 snapshot boundary。

`generate_prompt_demonstrations` 接受以下 strict request：

```json
{
  "instructions": "非空 custom instructions",
  "demonstration_count": 6
}
```

`demonstration_count` 是 1 到 20 的整数。响应返回 path 中的 `prompt_key` 和恰好对应数量的 typed
demonstrations。Server 在响应前校验并规范化 model output。它只会在已配置 inference request budget 内重试；不完整
或无效结果使整个请求失败，且绝不保存。

## Error 与 concurrency semantics

| 条件 | HTTP 结果 |
| --- | --- |
| Scope、Prompt head 或 exact revision 不可见或不存在 | `404 Not Found` |
| create 时 Prompt head 已存在 | `409 Conflict` |
| replace 缺少 `If-Match` | `428 Precondition Required` |
| `If-Match` stale 或不匹配 | `412 Precondition Failed` |
| 未知 prompt key、无效 mode/content、demonstration 不符合 schema，或不支持的 family/key 组合 | `422 Unprocessable Entity` |
| Custom 写入或生成建议的目标组件被禁用或不支持自定义 | `422`，code 为 `prompt_customization_unavailable` |
| replacement 的历史 content 与部署 Definition 不兼容 | `422`，code 为 `prompt_definition_incompatible`；head 不变 |
| 实际组件或 Definition 无法执行已有 custom head | `503`，code 为 `prompt_customization_unavailable` 或 `prompt_definition_incompatible` |
| Handoff generation receipt 无效或属于其他 Scope | `422`，code 为 `invalid_handoff_generation` |
| pagination cursor 无效或过期 | 已有 `400` 或 `410` cursor semantics |
| demonstration generation 需要的 inference provider 不可用 | `503 Service Unavailable` |
| provider output 在 request budget 内始终无效 | `500 Internal Server Error`，并返回稳定 public error code |

Error 不回显 Prompt 或 demonstration body。调用方无法通过响应细节区分隐藏的 Scope/Prompt 与不存在的资源。

## Authorization 与 trust boundary

Operational Prompt configuration 会改变一个 Scope 中所有兼容 inference operation 的行为。在 access-control model
下，current read、history list 与 exact read 需要相应 Scope read authority；create、replace 与 demonstration
generation 需要 `scope.admin`。Legacy static bearer 继续映射到配置的 administrative Principal。Server 的 policy
enforcement point 在分派到 writer 前检查 Prompt family mutation rule；其他 generic Artifact write 保留各自授权规则。

Runtime 内部使用 current Scope Prompt 是已授权 domain operation 的组成部分，不是隐式 exact-resource share。本 RFC
不允许通过 `prompt.user` 共享 operational Prompt revision，其 Prompt access profile 不声明可授予的 exact role。

Custom Prompt content 是不可信 configuration，执行边界如下：

| 代码强制约束 | 必需机制 |
| --- | --- |
| Prompt 不能替换已注册 schema 或 operation contract | Strict request model 与已注册 input/output 校验 |
| Prompt 不能修改 credential、model setting、resource budget、tool 或 authority | Prompt content 不含这些配置入口，仅由 Server composition 与 authorization 决定 |
| Prompt 不能分配 identity、越过 Scope 或引用任意缺失 evidence | 已有 family writer、Scope-bound resolution 与 operation-specific reference/identity check |
| 建议不能自行保存 | Generator 没有 Artifact write action；持久化需要单独授权的写入 |
| Prompt 不能从编译请求中移除 Server instruction | Server-owned compiler 控制 message role 与 instruction assembly |

Evidence-as-data、引用是否真实支持声明，以及任意自然语言输出中的 secret exclusion，还依赖模型行为。Instruction
不可编辑不等于模型保证遵守。尤其是 Memory output validation 检查结构和 evidence reference，但自由文本 candidate
不具备通用的语义 secret 检测能力。V1 将 secret exclusion 明确为 best-effort，不承诺对 instructions、demonstrations
或 generated content 提供万能 secret filter。Credential 必须留在 model input 之外，操作者不得把 secret 写入 Prompt
content。部署专用的过滤器必须说明覆盖范围与漏检限制。

Demonstration generator 把 supplied instructions 放在 Server-owned meta-prompt 中作为数据，并执行与手工案例相同的
typed validation 和 size limit。对抗测试必须分别覆盖 schema/authority 修改尝试、instruction injection，以及合成的
secret-like evidence。报告被拒绝的结构违规，也报告真实模型上观察到的语义泄漏；schema 接受某段文本不能单独证明
模型被绕过，有限测试集通过也不能证明绝对不泄密。

## 非 HTTP surface

Dashboard 使用上述 HTTP 操作。其 Agent 或用户 selector 会先解析为 Scope，再读写 Prompt state。三段式 editor 只是
一个 `PromptContent` 的展示方式，不是另一套 API contract。

Dashboard 内置六个固定 key 的标签与 editor metadata，通过扩展已有 `GET /v1/capabilities` 响应中的 `prompts` map
获得部署支持状态。无论是否存在 Prompt Artifact，每个已注册 key 都有一条记录：

| 字段 | Contract |
| --- | --- |
| `status` | `supported`、`disabled` 或 `unsupported`，依据实际 composed component |
| `reason` | supported 时为 `null`；否则为 `operation_disabled`、`provider_not_configured` 或 `injected_component` 等稳定原因 |
| `definition_version` | 部署中已注册 contract version |
| `builtin_version` | 部署的内置 guidance version；不表示注入组件使用它 |
| `builtin_profile` | Memory extraction 为 `coding` 或 `conversation`，其他 key 为 `null` |

`supported` 表示 Custom selection 和 demonstration generation 都已接入 built-in implementation；provider 临时故障
仍返回 `503`，不改变配置契约。该响应不包含 Scope-owned content、head、credential 或 model secret，不需要另加 Prompt
discovery endpoint。

对 supported key，Dashboard 将上述 metadata 与 Scope 的通用 Prompt Artifact list 合并：没有 head 或 head 为 Auto
时，展示报告的 built-in selection；Custom 时展示已存 revision。对 disabled/unsupported key，禁用 Custom 和生成建议
操作，显示原因，仍允许读取已存内容和历史。无法执行的已有 custom head 显示为 blocked，并提供显式切回 Auto 操作。
注入组件标记为外部管理，不能误称它正在使用所报告的 built-in profile。

Python Runtime 增加本 RFC 所述 Prompt Definition registry、Scope Prompt resolver、compiler 与 family writer。Public
Python caller 可以使用已有 generic Artifact client 做 persistence。V1 不增加 MCP tool、CLI command 或 host-specific
configuration file。

### 注入的 Runtime 组件

`open_builtin_runtime` 已支持注入 Memory/Experience candidate pipeline、Experience/Skill generator、Handoff
pipeline 和 Memory reranker。它们现有的 protocol 不接收 `ResolvedPrompt`。V1 不修改这些 protocol，也不要求第三方
实现参与托管 Prompt 自定义。

- 只有实际 built-in component 接收 Scope-resolved selection，key 才是 `supported`。仅配置 model 不足以证明支持。
  只向兼容 built-in pipeline 注入底层 inference provider，不会使该 pipeline 变成 unsupported。
- 被替换的 pipeline/generator/reranker 为 `unsupported`，reason 是 `injected_component`；未配置的 operation 为
  `disabled`。两种状态都拒绝 Custom create/replace 和 demonstration generation，不能保存成功后静默忽略 Custom。
- History 和 current read 始终可用。任何已注册 key 都允许写入 Auto。Head 不存在或为 Auto 时，注入组件保持原有行为，
  不生成虚假的 built-in Prompt selection 记录。
- 如果部署把此前托管的 key 切换成注入或禁用组件，而某 Scope 仍有 Custom head，则在调用组件前以
  `prompt_customization_unavailable` 拒绝 operation。操作者必须显式选择 Auto，或恢复兼容 built-in component，
  不删除或改写历史。

第三方主动接入 Prompt 的扩展 protocol 不在 v1 范围内；明确报告 unsupported 即可。

## Compatibility 与 migration

对没有 Prompt Artifact 的 Scope，built-in component 保留当前 selection，包括已配置的 `memory_extraction_profile`
和 rerank enablement；注入组件保留原有行为。Transport 和 Handoff content model 扩展仍要求 schema/client 同步更新，
不能据此声称旧 strict reader 会接受新 response field。

实现需要：

1. 把当前每个 instruction constant 拆为 immutable invariant 与 replaceable default guidance，同时不改变 Auto mode
   的 compiled behavior；
2. 注册六个 Prompt Definition；
3. 在 base Artifact family contract、generated HTTP model、mapper、repository type registry 与 family management
   writer registry 中增加 `prompt`；
4. 在六个 inference entry point 中按 Scope 解析 Prompt state，并检查实际组件支持状态；
5. 增加两个 OpenAPI operation，扩展已有 capabilities 与 Handoff payload，重新生成 checked-in HTTP sources 和
   client mapping；
6. 在 prepare/finalize/commit 间传递已验证的 Handoff generation metadata，记录 configuration lineage；
7. 在 tracing 中记录 Prompt identity 与 digest，并在部署升级前校验目标 Definition。

不需要 SQL migration 或 content backfill。已有部署不需要 Prompt row。在启用 custom mode 前，实现必须证明 Auto mode
编译出的 instructions 在行为上与当前实现等价。

## Validation requirements

只有测试证明以下内容后，实现才算完整：

- 两个 Scope 可以为同一个 prompt key 保存不同 current revision，且不会泄漏；
- Agent 或 user binding 在 Prompt lookup 前解析到预期 Scope；
- 缺省和显式 Auto configuration 都保持当前 built-in behavior；
- custom instructions 与 typed demonstrations 只进入正确 generator；
- 六个注册 key 均报告实际支持状态，支持的组件解析其 Prompt，未知 key fail closed；
- revision 不可变、history pagination 稳定、exact read 不跟随 head；
- stale replacement 失败，rollback 创建新 revision 且不删除历史；
- 兼容的 Definition 升级接受已有 custom head 并允许 exact-content rollback；不兼容 replacement 不改变 head，
  不兼容 resolution 失败而不回退 Auto；
- 部署 Definition 无法执行某 revision 时，其历史仍然可读；
- operation 执行期间修改 Prompt，只被下一次 operation 使用；
- Handoff 用 revision 2 生成、head 随后变成 revision 3，commit 仍记录 revision 2 来源；跨独立请求的编辑 draft、
  receipt 缺省、篡改、跨 Scope 重放、密钥轮换及考虑 provenance 的 no-op 比较都符合上述生命周期；
- generated demonstrations 符合已注册 input/output schema，且绝不隐式持久化；
- 各适用 key 的注入实现均报告 unsupported 并拒绝 Custom 写入和生成建议；组件切换后已有 custom head 阻止调用，
  head 不存在或为 Auto 时仍保留注入行为；
- Prompt Artifact list 为空时仍展示全部 key、实际支持状态和部署的 extraction profile；
- 代码拒绝 schema、authority 和 Scope 违规；真实模型对抗测试单独报告语义遵守情况和合成 secret 泄漏，
  不假设 Prompt instruction 能强制保证二者；
- trace 包含 exact Prompt identity 或 built-in version 与 digest，但不包含 Prompt body；
- OpenAPI contract、generated HTTP sources、unit tests 与至少一个真实 Runtime end-to-end scenario 通过。

# Drawbacks

将 `prompt` 加入 Artifact，会把 Artifact lineage 的含义从 factual evidence 扩展到显式分类的 configuration input。
Consumer 必须继续区分 configuration lineage 与 Source citation。

按 operation 解析 Prompt，会在 inference path 增加 repository read 或 cache validation。正确缓存必须 Scope-sensitive，
不能为了减少读取牺牲隔离。

Custom guidance 即使没有突破 hard invariant 与 schema，仍可能降低 output quality。Version history 与 rollback 可以限制
运营影响，但不能保证 custom prompt 有效。Demonstration generator 还会消耗 inference capacity，并可能在 provider
不可用时失败。

初始设计有意不提供 draft 与 approval。`scope.admin` 的变更在 Artifact transaction 提交后立即成为 current。

# Rationale and alternatives

## 复用 Artifact，而不是增加 Prompt 表

Artifact 已经提供所需的 Scope key、immutable revision、current head、atomic replacement、ETag concurrency、content
digest、lineage 与 exact read。在 `prompts`、`prompt_versions` 和 `prompt_bindings` 中重新实现这些语义只会增加同步与
migration 风险，不会产生新的 domain guarantee。

## 复用 generic CRUD，而不是增加 Prompt CRUD

Prompt-specific create、get、update、history、rollback 与 delete endpoint 会重复 Artifact contract。当前只缺少通用
history listing，而且每个 Artifact family 都能从中受益。Demonstration generation 是真正的 Prompt-specific action，
因此获得唯一的 Prompt-specific endpoint。

## Scope ownership，而不是 Agent 或 user ownership

PowerContext state 按 Scope 隔离。Agent 与 user identity 属于 integration 与 authorization concern。复用
ScopeBinding 可以保持唯一持久 ownership model：两个 Agent 可以通过共享 Scope 有意共享 Prompt 行为，也可以通过不同
Scope 获得不同行为。

## Typed demonstrations，而不是 example strings

自由文本正反例无法表达完整 model input/output contract，也难以校验。类型化的 `input` 与 `expected_output` 对可以
确定地编译、测试，并统一覆盖六个 operation。No-op expected output 表示反例，无需教给模型错误答案。

## 创建新 revision，而不是把 head 向后移动

向后移动 head 会抹掉 operator decision sequence，并造成含糊的 cache 与 audit 行为。把旧 content 复制到一次新的
conditional replacement 中，可以保留单调历史，并使用与所有更新相同的 failure semantics。

## 未选择的替代方案

- **仅使用 deployment prompt configuration：** 无法按 Scope 变化，也没有 Scope-local history。
- **在每个 inference request 上携带 raw prompt 字段：** 削弱 authorization、audit、caching 与 reproducibility。
- **Parent-Scope inheritance：** 使 effective configuration 依赖变化中的 graph，并增加 isolation 复杂度。
- **Mutable cross-Scope Prompt reference：** 允许一个 Scope 在没有本地 revision 的情况下改变另一个 Scope 的行为。
- **Prompt DSL 或 variables：** 在出现具体需求前增加 compiler 与 injection 复杂度。
- **Draft、review 与 activation workflow：** 重复 Candidate/Review 概念，且首个 administrative vertical slice 不需要。
- **专用 rollback endpoint：** exact read 加 conditional replacement 已经表达其安全语义。

如果不做此设计，operational prompt 仍固定在 deployment composition 阶段，客户只能 fork Runtime code 或运行多个部署
来获得不同 prompt 行为。

# Prior art

PowerContext 的 Memory、Experience、Skill 与 Handoff family 已经使用 immutable Artifact revision 与 current head。
Base Artifact REST API 已经暴露 create、current read、conditional replacement、family list 与 exact revision read。
本 RFC 把这些成熟 primitive 用于 operational Prompt configuration，不创建平行管理系统。

当前源代码也为每个 built-in instruction string 提供版本。这些 version 会继续作为 Auto selection 的 identity，并成为
compiled-prompt trace 的一部分。

# Unresolved questions

实现本 RFC 不需要解决其他未决问题。以下内容是明确 non-goal；如果未来有需求，需要另行设计：

- reusable parameterized task Prompt Artifact 与 `prompt.use` sharing；
- draft、approval、scheduled activation 或 staged rollout；
- parent-Scope inheritance 或 organization-level default；
- automatic evaluation、quality scoring 或 A/B traffic allocation；
- cross-Scope import 与 publication workflow；
- 通用 Prompt templating language。

# Future possibilities

后续 RFC 可以增加与 demonstrations 分开管理的 Prompt evaluation case，用稳定 dataset 对比 revision，并以显式质量阈值
控制 activation。另一份 RFC 可以定义 reusable task Prompt package 与 least-privilege `prompt.use` sharing，而不改变
本 RFC 引入的 operational Prompt identity。

如果明确规定 ownership 与 precedence，organization default、scheduled activation 或 percentage rollout 可以构建在
Scope-local immutable revision 之上。这些扩展都不需要改变 v1 规则：一个 inference operation 必须在第一次 model
request 前冻结一个精确的 effective Prompt selection。
