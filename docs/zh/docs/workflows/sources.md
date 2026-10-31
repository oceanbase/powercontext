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

采集成功表示证据已保存，不表示提取成功，也不表示内容一定值得长期保留。
通过 Source 引用追溯生成结果的输入。不要在同一身份下修改内容并重试采集，将其当作新的观察。

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
