---
title: PowerContext 分层 Skill
description: 先识别请求意图，再按需读取 Memory、Handoff 或 Review 的详细流程。
---

# PowerContext 分层 Skill

Skill 入口负责简短路由：什么时候搜索、盘点、保存、交接或查看候选；参考文件负责参数、结果解释和审批边界。

普通编码和上下文充分的概括不需要加载 Skill，也不需要 PowerContext 调用。明确操作仍须调用实际工具并检查结果。
参数已明确时可以直接调用；需要流程细节时只读取相关领域。不要求先读入口、一次加载全部领域或每轮响应前加载。
中英文触发意图写在宿主实际可见的 Skill 描述中。

## 宿主组织

| 宿主 | 可发现入口 | 详细流程 |
| --- | --- | --- |
| Codex、Claude Code、WorkBuddy、可移植 Agent Plugin、Pi、OpenCode | `powercontext-project-context` | 本地 `references/scope-memory.md`、`work-handoff.md`、`review-publication.md`。 |
| Hermes | `powercontext:powercontext-project-context` | 同样三个参考领域，使用 Hermes 工具名和人工 Review 命令。 |
| MiniMax | `powercontext-project-context` | 保留现有 Scope/Memory、Handoff、Review、HTTP 边界参考和示例。 |
| OpenClaw | `powercontext-project-context` | 打包 Scope/Memory 和 Handoff 参考；不宣称支持盘点或候选 Review。 |
| DSH | 运行时 `powercontext-project-context` 路由 | 独立注册 `powercontext-memory`、`powercontext-handoff`、`powercontext-review`，不依赖文件路径。 |

文件型宿主参考 MiniMax 现有的本地参考文件组织。参考文件随入口一起安装。OpenCode 的 npm 包包含完整 Skill 目录；
WorkBuddy 安装器替换所有已安装 Markdown 中的 Python 和 Scope helper 占位符；OpenClaw 使用 SDK 的 manifest
`skills` 目录机制。DSH 复用运行时 Skill 服务，让宿主发现领域描述并直接加载单个领域。

各宿主的 Skill 入口、运行时注册和安装路径统一使用 `powercontext-project-context`，不提供其他入口别名。
DSH 可独立加载的领域 Skill 保留各自的领域名称。框架适配器和 Bub 不提供这些 Skill 入口。

## 流程边界

搜索用于相关历史，盘点用于明确请求的集合。空搜索不授权盘点。明确保存须使用 Memory 写入，自动接收 Source 不能算作
已保存 Memory。修改条目时保留精确引用。

临时交接须返回完整准备载体，包括必需的可空字段和生成回执。使用各宿主实际支持的高级操作或捕获、激活、完成流程。
仅明确要求持久里程碑时才能 commit。已检查事实如果没有精确 PowerContext 引用，仍使用 declared 声明。
接收确认要检查当前证据、能力和权限；确认回执不是工作已执行的证明。

查看或生成候选不授权批准、发布、安装或执行。各宿主保留实际工具和审批渠道。OpenClaw 保留私密会话与工具可用性限制。

Skill 缺失不影响已有完整工具指引。参考文件缺失时说明精确 Skill/路径，操作失败时说明具体操作与安全的返回原因。
区分空结果、拒绝、不可用和未知状态，不猜测根因，不盲目重试结果未确认的写入，也不无依据声称成功。

## 验证

`uv run pytest tests/test_layered_skills.py` 读取各入口和可达参考文件，验证缺失资源定位，并检查 OpenCode/OpenClaw
npm 包内容。安装器回归实际执行 OpenCode 参考文件复制和 WorkBuddy 占位符替换。Pi 包测试调用 SDK Skill 加载器；
OpenClaw 包测试在隔离配置中执行 `skills list --json`；DSH runtime 测试检查模型可见领域描述并通过真实 `skill` 工具加载。
这些测试与模型路由证据分别记录。

按[工具路由验证](integration-guidance.md)导出目录，再启用可选资源读取：

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --layered-skills --env-file .env \
  --output /tmp/pc-guidance/layered.json --skill-modes unloaded unavailable
```

重复 `--catalog` 可覆盖其他宿主。评估器提供读取实际打包文本的可选工具，记录每次请求，不强制加载 Skill 或调用数据工具。
unavailable 模式不提供读取工具。此受控读取器是评估设施，不是宿主原生加载器或分发源。MiniMax 等 MCP 目录验证不能
证明各产品原生自动发现行为。路由和参数检查与绑定转录的结果审查分别验收，保留失败观察和评估限制。

明确要求读取的 `skill_search` 和 `skill_handoff` 探针，必须在业务操作前成功读取对应的 Memory 或 Handoff 流程。
只读入口、读错领域或操作后补读均不能通过。报告列出预期资源和操作前实际读取的资源，并保留更具体的失败原因。
普通请求不强制读取 Skill；Skill 不可用时仍验证可以独立使用的工具。

具体运行配置、转录和观察结果放在评估报告中，PR 记录交付状态。
