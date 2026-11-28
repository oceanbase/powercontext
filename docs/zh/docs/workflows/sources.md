---
title: Sources 与采集
description: 采集证据、查看 Source 记录并配置后续处理。
---

# Sources 与采集

Sources 在信息被筛选为知识之前保留证据。Content Source 保存采集的文本，其他 Source 类型可引用 Connector 提供的材料。
Work Contract 和 Task Outcome 也会保留 Source 证据。

## 采集与检查

1. 选择已有 Scope。[API Quick Start](../develop/api-quickstart.md)演示通过 `POST /v1/sources/content` 采集内容，
   并保留返回的 Source 引用。
2. 使用 `GET /v1/scopes/{scope_id}/sources` 浏览 Source journal，再通过
   `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` 读取选中的 Source。
3. 将符合条件的证据处理为 Memory，需要配置生成模型，并启用定时处理或显式执行 Memory flush。
   步骤见[启用提取与向量搜索](../get-started/configure-models.md)。

两个 Source 读取接口都会原样返回随 Content Source 采集的 `metadata`：详情响应在记录上返回，列表响应在每个条目上返回。
没有调用方元数据的 Source（包括字段出现之前写入的记录）返回 `{}`。客户端连接不支持该字段的旧 Server 时会看到字段缺失
（`null`），这不能证明 Source 没有元数据；只有显式的 `{}` 才表示该 Source 没有调用方元数据。元数据不参与
`content_digest`、Source 身份或 journal position。

采集成功表示证据已保存，不表示提取成功，也不表示内容一定值得长期保留。
通过 Source 引用追溯生成结果的输入。不要在同一身份下修改内容并重试采集，将其当作新的观察。

## Source 正文大小上限

单条 Content Source 每次写入默认最多 20 万字符。部署可以通过 `POWERCONTEXT_SERVER_SOURCE_CONTENT_MAX_CHARS`（`1` 到
`1000000`）调整该边界，上限本身保证单条 Source 仍能放进一个 payload 列。`GET /v1/source-limits` 通过
`source_content_max_chars` 返回生效值，超过该值的写入会返回 `422 invalid_request`，错误消息中会写明当前上限。

该边界适用于单条采集、批量采集的每一项、Working Note，以及 `source_type: content` 的
`POST /v1/scopes/{scope_id}/sources`。字符串内容按原始字符数计算，
其他 JSON 值按规范化 JSON 字符数（与 Server 存储的文本一致）计算。

内置集成与 Gateway 仍按 20 万字符准备采集，尚未读取 `GET /v1/source-limits`，因此调低上限可能拒绝它们在新上限到
20 万之间的写入。通过 `POST /v1/scopes/{scope_id}/sources` 创建 `source_type: content` 的 Source 现在也遵循该上限；
在此设置出现之前，这条路径没有 Server 侧边界。

调低上限不会重写或隐藏已有证据：此前写入的 Source 仍可通过详情与列表接口读取，也仍可参与处理；只有新的写入会被校验。

## 采集一段对话

将下面的 JSON 对象序列化为字符串，通过 `POST /v1/sources/content` 的现有 `content` 字段提交。
批量接口 `POST /v1/sources/content/batch` 的每一项也可使用同样的字符串。

```json
{
  "type": "powercontext.session.v1",
  "session_id": "session-123",
  "messages": [
    {"role": "user", "content": "把示例保留在指南里。"},
    {"role": "assistant", "content": "我移动了示例。"}
  ]
}
```

为每段提交使用一个稳定的 `source_id`。实时采集应在收到 Assistant 最终文本后提交。
导入历史对话时，把一轮或多轮对话按时间顺序放在同一个 `messages` 数组中。两种角色均可连续出现，
也均可出现在首位。调用方在提交前应对两种角色的文本应用隐私过滤。

顶层 `type` 值属于 `powercontext.session.` 命名空间时声明会话 Source。将 `type` 放在首位，
服务端即可在后续 JSON 格式有误时识别会话声明。普通文本、使用其他类型值的 JSON，以及没有顶层会话标记的
JSON 均按原始文本采集。

会话序列化后的总长最多 48,000 字符，包含 JSON 转义和空白。加上紧凑 UTF-8 JSON 序列化的元数据，合计最多
56,000 字符。原子记忆默认输入预算为 64,000 估算 tokens。较低的配置预算或较长的自定义提示词可能使已接收的
单条 Source 超出处理预算，导致 journal cursor 停在原位，直至修正配置。

`session_id` 须为不超过 256 字符的非空白文本；
消息数量为 1–512 条，每条消息包含 `user` 或 `assistant` 角色和非空白的 UTF-8 文本。未知字段、重复的
JSON 对象键、不支持的会话版本及超限返回 HTTP 422。批量请求被拒绝时，本次提交的 Source 均不写入。
较长的历史对话可分段，每段使用不同的 Source ID。单条消息必须能装入一个会话文档。

两个读取接口均返回原始字符串。完全相同的重试沿用原 Source 引用和 journal position。原先按原始文本采集的
Source 在重试时继续沿用原有解释，即使其正文形似无效会话。便携包恢复在写入前校验已标记会话。
同一身份下内容或元数据不同则返回 HTTP 409。不同身份的片段即使内容重叠，也会分别存储。
原子记忆、主题记忆和 Profile 均使用结构化角色。Assistant 的观察提供上下文，工作完成声明须有佐证。
Profile 更新须有用户证据。

部署生效的 Content Source 上限也适用于序列化后的会话。

## 选择采集入口

- Agent Prompt Hook：在[接入指南](../integrations/index.md)中选择宿主对应的采集开关。
- 安装前的 Codex Prompt：Server 运行时，使用 `powercontext import-sessions --host codex` 从 `CODEX_HOME`
  或 `~/.codex` 导入 CLI 所在机器上的历史用户 Prompt，也可以写入远程 Server。该命令只写入普通 Content
  Source，在 Codex home 目录下维护与目标 Server 绑定的持久 checkpoint，并跳过无法解析到 Scope 的
  workspace。提取仍是独立操作；已配置的后台处理仍可处理导入的 Source。可先用 `--dry-run` 预览扫描结果。
- 文件与对象存储：使用 [OpenDAL Connector](ingest-text-files-with-opendal.md)。
- Scope Profile 的主体证据：按 [Scope Profile](use-profiles.md)写入。
- 应用或评测事件：使用应用对应适配器；[Bub 采集](../integrations/evaluation.md)需要显式启用。

## 声明 Source 权威性

受信任的 adapter 可以声明其材料的可信程度。Source Definition 上的可选 `memory_evidence` 字段携带版本化的
`MemoryEvidenceDeclaration`，其中 `authority` 取 `untrusted`、`user_asserted`、`repository_attested` 或
`system_attested`，`verification` 取 `unknown`、`verified` 或 `not_verified`。它只能由 adapter 或部署代码提供，
Source 正文、引用、模型输出和检索排名都不能。

```python
from powercontext.sources import AdapterSourceDefinition, MemoryEvidenceDeclaration

definition = AdapterSourceDefinition(
    existing_adapter,
    memory_evidence=MemoryEvidenceDeclaration(
        authority="repository_attested",
        verification="not_verified",
    ),
)
```

远程 Source observation 在受信任的部署代码中配置同一声明，键为精确的 Definition 名称、版本与 manifest
fingerprint。声明缺失、无效或不可用时解析为 `untrusted`/`unknown`，不阻塞采集、处理或既有检索。

Observation manifest 描述 payload 必须携带的每个字段的序列化形态。即使模型禁止额外字段，validation alias 也不要求
worker 重命名序列化后的键。
对于 Union，若另一分支的等价扁平基础字段 schema 能覆盖 alias 分支的序列化输出，可保留 validation manifest。
无法证明分支覆盖时使用 serialization schema，但默认值为 `null` 的字段除外：该字段在 payload 里可以是 `null`，
其下的改名形态不得替换已接受的 manifest。要让这样的字段以改名后的键出现，嵌套模型必须按 alias 序列化，从而保持
validation 键的权威性。改变已注册 Definition 的序列化表示需要新的 Definition 版本。

每个 Memory revision 记录当时接受的声明与精确 Source materialization。保留的证据是权威且带版本的；其旁的索引
投影是派生数据，维护可以丢弃它并从保留事实重建，且不查询当前 registry。声明不增加排序权重、不改变分数，
也不授予访问权限。

按精确 revision 读回保留事实：

```python
from powercontext.builtin.runtime import AtomicMemoryEvidenceAnnotation

memory = runtime.atomic_memory.for_scope(scope_id)
annotation: AtomicMemoryEvidenceAnnotation = await memory.evidence(artifact_id, revision=3)
for source in annotation.sources:
    print(source.source_ref.source_type, source.declaration.authority, source.declaration.verification)
```

无法再被佐证的声明解析为 `untrusted`/`unknown`。若记录与精确 Source 材料不再一致，会抛出
`AtomicMemoryEvidenceError`，调用方可通过 `PowerContextError` 家族捕获。

Source 读写受所选 [Scope 与访问策略](scopes-and-access.md)约束。

## 引用 Source 中的具体位置

Source 引用可携带可选的 `locator`。Source 在 Scope 内的身份仍由 `(source_type, source_id)` 确定；
locator 只描述证据位置，不会选择其他 Source 或 Artifact Revision。
使用 `name` 代替 `source_type` 的 HTTP 输入同样接受该字段。

```json
{
  "source_type": "document",
  "source_id": "design-note",
  "locator": {
    "kind": "line_range",
    "start_line": 40,
    "end_line": 55,
    "heading_path": "架构 / 约束"
  }
}
```

- `kind: sequence` 使用严格的非负整数 `sequence`，表示 Source 在当前 Scope journal 中的位置。
  当前 journal 从 1 开始。提交的位置必须与该 Source 的记录一致，否则明确拒绝，不会跳转到其他记录。
  序列化后的零值与没有 locator 可以区分。
- `kind: line_range` 使用严格的正整数 `start_line` 和 `end_line`，包含首尾两行，范围必须为 1 至 200 行。
  非法范围在 HTTP 返回 `invalid_request`；范围倒置或跨度超限时，校验详情包含 `source_locator_range`，不会静默截断。
- 行范围可附带 `heading_path`，最多 1024 个 Unicode 字符，按字符而非 UTF-8 字节计数。
  行号对应适配器提供的准确解码文本；此功能不引入字节偏移量。

Atomic Memory 提取会为内置 Content Source 及没有更细位置的 Source，根据持久化的采集位置生成 journal locator。
若适配器在 `LocatedTextEvidence.locator` 中报告 sequence locator，提取时会使用 Source 实际持久化的 journal 位置生成引用，
已有 observation 也遵循此规则；适配器提供的证据保持原样。
适配器可通过 `LocatedTextEvidence.locator` 报告行范围；远程 worker observation 在文本证据投影中保存该信息，提取时原样保留，
不会猜测偏移。适配器报告的位置必须对应准确的证据文本，不能指向随后被编辑的文件。
不透明来源和调用方提供的普通引用可以省略 locator。其他生成接口保留显式传入的 locator，不推测行号或标题路径。
读取时不会扫描文本定位，也不会给历史引用回填推测的位置。已有 worker manifest 保留原指纹；
worker 采用扩展后的文本证据 schema 时，须先提高 Source Definition 版本再注册。
`TextEvidence` 为已有 Source 插件保留原始 schema；`TextEvidenceV1` 是该稳定模型的别名。
需要位置的插件须显式采用 `LocatedTextEvidence`，并先提高 Source Definition 版本再注册扩展后的 schema。
内置 Content 与 OpenDAL 的 version-1 定义保持原始结构，其 journal locator 在提取时附加。

locator 会经过 Candidate 负载、Artifact 血缘、Handoff 引用、HTTP/MCP 读取以及便携备份导出恢复而保留。
同一 Source 的不同位置是不同的引用。缺失的 locator 在序列化时仍被省略，已有记录及其规范编码保持兼容。
精确 Artifact Revision 校验及 Source 使用资格校验继续生效。

Server 数据库修订 `v1_5_0_004` 增加可选的 `pc_artifact_source_locators` 关系表，按现有 Artifact Revision
及来源引用序号关联。已有托管数据库须在 Server 启动前按[数据库迁移流程](../operate/database-migrations.md)升级；
迁移不会重写已有血缘行或历史负载。Atomic Memory 向量绑定记忆内容，不包含这份血缘位置元数据，因此升级不会导致既有向量失效，
也无需重新嵌入。Handoff 内容中新增 locator 时，内容摘要随之变化，与其他内容修改一致。
旧版本程序会拒绝含新 `artifact_source_locator` 记录类型的便携备份；不含该记录的旧备份继续受支持。
