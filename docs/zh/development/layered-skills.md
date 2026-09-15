---
title: PowerContext 分层 Skill
description: 先识别请求意图，再按需读取 Memory、Handoff 或 Review 的详细流程。
---

# PowerContext 分层 Skill

Skill 入口负责简短路由：什么时候搜索、盘点、保存、交接或查看候选；参考文件负责参数、结果解释和审批边界。
这是 [#1450](https://github.com/oceanbase/powercontext/issues/1450) 的 E 小项，由
[#1620](https://github.com/oceanbase/powercontext/issues/1620) 跟踪，复用已合并 #1522 的工具路由语义。

普通编码和上下文充分的概括不需要加载 Skill，也不需要 PowerContext 调用。明确操作仍须调用实际工具并检查结果。
参数已明确时可以直接调用；需要流程细节时只读取相关领域。不要求先读入口、一次加载全部领域或每轮响应前加载。
中英文触发意图写在宿主实际可见的 Skill 描述中。

## 宿主组织与兼容性

| 宿主 | 可发现入口 | 详细流程 |
| --- | --- | --- |
| Codex、Claude Code、WorkBuddy、可移植 Agent Plugin、Pi、OpenCode | 保留 `project-context` | 本地 `references/scope-memory.md`、`work-handoff.md`、`review-publication.md`。 |
| Hermes | 保留 `powercontext` | 同样三个参考领域，使用 Hermes 工具名和人工 Review 命令。 |
| MiniMax | 保留 `powercontext-project-context` | 保留现有 Scope/Memory、Handoff、Review、HTTP 边界参考和示例。 |
| OpenClaw | `powercontext-project-context` | 打包 Scope/Memory 和 Handoff 参考；不宣称支持盘点或候选 Review。 |
| DSH | 保留运行时 `project-context` 路由 | 独立注册 `powercontext-memory`、`powercontext-handoff`、`powercontext-review`，不依赖文件路径。 |

文件型宿主参考 MiniMax 现有的本地参考文件组织。参考文件随入口一起安装。OpenCode 的 npm 包包含完整 Skill 目录；
WorkBuddy 安装器替换所有已安装 Markdown 中的 Python 和 Scope helper 占位符；OpenClaw 使用 SDK 的 manifest
`skills` 目录机制。DSH 复用运行时 Skill 服务，让宿主发现领域描述并直接加载单个领域。

tracking issue 中的 `using-powercontext` 表达路由角色，不强制新增同名入口。现有名称和安装路径保持兼容，E 不新增
另一套分发生成器。统一命名、生成式投影和迁移继续由 [#1405](https://github.com/oceanbase/powercontext/issues/1405)
和 [#1410](https://github.com/oceanbase/powercontext/pull/1410) 负责。框架适配器和 Bub 不在这次 Skill 迁移范围。

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

## 实测记录与完成边界

基线主分支 `0ed20c54` 的多数入口混合了多个领域，OpenClaw 没有打包 Skill，DSH 只注册单一入口；MiniMax 已经分层。
这是源码和打包证据，不是严格配对的模型前后对照实验。

[2026-09-16 原始记录](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-layered-20260916.jsonl)
保留 400 个观察：模型 `step-3.7-flash`，温度 0，输出上限 6,000 token，自动工具选择，覆盖十个宿主目录和中英文请求。
主矩阵有 320 个场景，包含八种意图，以及可选读取和 Skill 不可用两种模式。原自动校验通过 289 个，转录审查验收
**262/320**。80 个普通编码和充分上下文场景都没有 Skill 或数据调用。40 个保存请求都选择了写入工具，但其中一个改变
Scope，一个在报告中编造了引用。缺失工具后的替代调用、包含非活动条目的盘点、无证据的 verified 声明、响应包装对象和
扩大事实含义仍记为失败。

另外两个各 40 场景的探针明确要求先读 Skill，再搜索或交接，分别有 24 和 21 个场景实际读取了资源；自动校验通过 24
和 15 个。这两组没有完成语义验收，不计为验收通过。第二组使用严格的独立载体验证。早期合并报错不能明确区分读取与
操作混合的原因；最终评估器保留被拒绝的调用，并分别报告读取器缺失、混合操作、超出次数和路径缺失。非交接调用的
Scope 偏移也会被拒绝。对应回归测试保护这些校验修正，历史分数保留原值。

原始记录区分完整路由/报告验收、选中工具和成功读到文件。它不能证明所有模型始终遵守 Skill，也不能替代所有原生产品
的自动发现验证。这些失败与 D 的既有观察继续保留在 #1450。E 的实现 PR 只关闭子 issue，父 issue 等整体验收和残余
模型行为有明确处置后再关闭。
