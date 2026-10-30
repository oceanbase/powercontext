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
适配器可通过 `TextEvidence.locator` 报告行范围；远程 worker observation 在文本证据投影中保存该信息，提取时原样保留，
不会猜测偏移。适配器报告的位置必须对应准确的证据文本，不能指向随后被编辑的文件。
不透明来源和调用方提供的普通引用可以省略 locator。其他生成接口保留显式传入的 locator，不推测行号或标题路径。
读取时不会扫描文本定位，也不会给历史引用回填推测的位置。已有 worker manifest 保留原指纹；
worker 采用扩展后的文本证据 schema 时，须先提高 Source Definition 版本再注册。
内置 Content 与 OpenDAL 的 version-1 定义继续使用冻结的 `TextEvidenceV1` 结构；其 journal locator 在提取时附加，
不会改变已注册投影的 schema。

locator 会经过 Candidate 负载、Artifact 血缘、Handoff 引用、HTTP/MCP 读取以及便携备份导出恢复而保留。
同一 Source 的不同位置是不同的引用。缺失的 locator 在序列化时仍被省略，已有记录及其规范编码保持兼容。
精确 Artifact Revision 校验及 Source 使用资格校验继续生效。

Server 数据库修订 `v1_4_1_006` 增加可选的 `pc_artifact_source_locators` 关系表，按现有 Artifact Revision
及来源引用序号关联。已有托管数据库须在 Server 启动前按[数据库迁移流程](../operate/database-migrations.md)升级；
迁移不会重写已有血缘行或历史负载。Atomic Memory 向量绑定记忆内容，不包含这份血缘位置元数据，因此升级不会导致既有向量失效，
也无需重新嵌入。Handoff 内容中新增 locator 时，内容摘要随之变化，与其他内容修改一致。
旧版本程序会拒绝含新 `artifact_source_locator` 记录类型的便携备份；不含该记录的旧备份继续受支持。
