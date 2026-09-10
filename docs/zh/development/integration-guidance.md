---
title: Agent 工具选择与结果报告
description: PowerContext 指引的共同语义、宿主适配与验证方法。
---

# Agent 工具选择与结果报告

PowerContext 的系统指引和工具描述应在 Skill 尚未加载时也能支持正确选用操作，详细流程仍由现有 Skill 承载。
本文对应 [#1450](https://github.com/oceanbase/powercontext/issues/1450) 的 D 小项，实施由
[#1520](https://github.com/oceanbase/powercontext/issues/1520) 跟踪。

## 意图与结果规则

| 用户意图 | 预期行为 |
| --- | --- |
| 普通编码、概念讨论、当前上下文已充分 | 直接利用已有上下文，不例行调用 PowerContext。 |
| 缺少相关历史，或明确要求搜索记忆 | 使用聚焦检索，保留返回的精确引用；空结果是正常结果。 |
| 明确盘点或审计 | 列出指定集合；list 不作为恢复上下文的常规路径。 |
| 明确保存供未来使用 | 调用当前可用的 Memory 写入操作，检查结果后才能声称已保存。 |
| 预览或仅适用于当前轮的指令 | 不因提及 Memory 就执行持久化。 |
| 临时交接 | 捕获已检查的事实，使用精确证据引用，检查 Draft 后完成传递；只有明确要求持久里程碑才 commit。 |
| 查看候选 | 使用当前宿主支持的队列或候选读取；列出、生成不等于批准、安装、发布或执行。 |
| 失败、拒绝、未绑定 Scope 或工具不可用 | 说明具体操作和安全的返回原因，不猜测根因、不模拟缺失工具、不替换成其他写入，也不声称成功。 |

自动 hook 会尝试有限召回和 Source 捕获。启用配置不能证明处理成功；Source 被接收后可能没有生成 Memory，
准备好的上下文也不能证明宿主已经注入。自动捕获 Source 和口头确认都不能替代明确保存请求对应的 Memory 写入。

Scope 由宿主和 Server 决定。复用解析后的绑定，不猜测身份或通过切换绑定寻找缺失历史。召回内容属于不可信的历史
证据，服从当前用户、仓库和系统指令。相关变更仍须遵守精确引用与宿主确认机制。本文约束结果解释，错误分类继续遵循
[插件诊断约定](plugin-contract.md)。

## 宿主适配

| 宿主 | 指引入口 | 工具与边界 |
| --- | --- | --- |
| DSH | 系统段和原生工具 | `pc_search`、`pc_memory_list`、`pc_remember`；候选决策由人工 `/pc review` 完成。 |
| OpenCode | 系统 transform 和原生工具 | 同类 `pc_*` 名称；候选审核变更不作为模型工具开放。 |
| Pi | `before_agent_start` 系统提示和原生工具 | 支持 Memory、Handoff，没有候选 Review 工具；自动召回为空或失败时仍有基础指引。 |
| OpenClaw | Memory capability 提示和 provider 工具 | `powercontext_memory_search` / `powercontext_memory_store`；提示只列当前可用工具，不推断 inventory、Handoff 或 Review。 |
| Hermes | provider 系统块和工具 schema | `powercontext_search_memory`、`powercontext_remember` 等实际工具；保留 `powercontext` Skill。 |
| Codex、Claude Code、WorkBuddy | MCP 初始化指引和 OpenAPI 派生描述 | `search_memory`、`list_memory_entries`、`remember_memory`；各自现有 `project-context` Skill 保持一致。 |

可移植 Agent Plugin 的现有 Skill 使用相同语义。框架适配器与 Bub 验证工具不在本次迁移范围；工具权限、持久化格式、
宿主命名与分发归属均保持原有设计。分层 Skill 属于 E，分发生成器由 #1405 / #1410 负责。

## 复现验证

将 `POWERCONTEXT_GUIDANCE_EXPORT` 设为已存在的本地目录，再运行各宿主注册测试，导出实际指引、工具定义和打包 Skill：

```sh
uv run pytest tests/test_mcp.py tests/integrations/test_hermes_provider.py
pnpm --dir integrations/dsh/plugins/powercontext test
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime install --frozen-lockfile
pnpm --dir integrations/dsh/plugins/powercontext test:e2e:runtime
pnpm --dir integrations/opencode/plugins/powercontext test
pnpm --dir integrations/pi/plugins/powercontext test
pnpm --dir integrations/openclaw/plugins/memory-powercontext test
```

OpenClaw 须使用其固定 SDK 支持的 Node 版本，CI 使用 Node 24.15.0。各包测试、类型检查、构建、真实 DSH runtime
测试和真实 Pi CLI 加载测试验证注册与执行链路；MCP 初始化通过真实 FastMCP client 检查。

真实模型工具选择另用可选验证脚本执行，不强制指定工具：

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --env-file .env \
  --output /tmp/pc-guidance/results.json --skill-modes loaded unloaded unavailable
```

环境文件提供 `LLM_MODEL`、`OPENAI_LLM_BASE_URL`、`LLM_API_KEY`。凭据和含私有内容的原始请求不能提交到仓库。
可重复传入 `--catalog` 覆盖其他宿主。中英文场景包含普通任务、上下文充分、搜索、盘点、保存、预览、交接、候选查看、
空检索、写入失败和保存工具缺失；合法的 Scope 解析作为前置步骤处理。

脚本使用受控工具返回，不执行真实写入。除了自动工具选择判定，还须审查实际参数与最终回答。模型异常、空回答、
截断和服务连接失败不能算通过。Skill 正文是否出现由验证条件控制，这不等于各宿主自动发现 Skill 或完整执行验收。
具体测量范围及限制见[验证记录](integration-guidance-evaluation.md)。

多轮交接验证使用 `--cases handoff handoff_request`。验证器检查完整受控返回链、精确载体，以及准备完成后的
后续调用；只选对第一个工具不能算通过。该测量不执行持久化，也不证明原生宿主行为。普通交接指令走临时路径，
只有明确要求持久里程碑才授权 commit。

DSH 从真实 SDK 模型请求导出编译后的工具 Schema 和系统上下文。单独运行包注册测试不导出模型目录；
导出 DSH 前须安装固定版本的 runtime 测试依赖并运行上述 runtime 命令。
