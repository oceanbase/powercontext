---
title: 集成能力清单
description: PowerContext 集成的版本化仓库契约。
---

# 集成能力清单

本页由 integrations/capabilities.toml 生成。它是仓库契约，不是公开 HTTP capability API。已实现能力均由精确的工具面 probe 支撑。

candidate_review 表示可列举或读取候选材料，绝不授予批准权限。

## 支持 Profile

- **Minimal**：Memory 读写、Source capture 和 Context injection。
- **Recommended**：Minimal 加上 Work Contract、Handoff、acknowledgement 和 Task Outcome。
- **Full**：Recommended 加上 Experience、Skill、Candidate review 和 External Skill。

## 当前矩阵

| ID | Kind | Availability | Profiles | Capabilities |
| --- | --- | --- | --- | --- |
| codex | Agent 宿主 | 仅 master | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| claude-code | Agent 宿主 | 仅 master | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| dsh | Agent 宿主 | 仅 master | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>handoff<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review<br>external_skill<br>slash_command |
| hermes | Agent 宿主 | 仅 master | minimal, recommended, full | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review<br>external_skill<br>slash_command<br>persistent_workstream_binding |
| openclaw | Agent 宿主 | 仅 master | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>pre_compaction_capture |
| opencode | Agent 宿主 | 仅 master | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>handoff<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review |
| pi | Agent 宿主 | 仅 master | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>handoff<br>pre_compaction_capture<br>slash_command |
| workbuddy | Agent 宿主 | 仅 master | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| pydantic-ai | 框架适配器 | 实验性 | — | memory_read<br>memory_write<br>context_injection |
| langchain | 框架适配器 | 仅 master | — | source_capture<br>context_injection |
| langgraph | 框架适配器 | 仅 master | — | memory_read<br>memory_write<br>context_injection |
| bub | 评测 harness | 仅 master | — | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint |

## 证据

- **codex** (实现 / 文档 / 聚焦测试): 'integrations/codex/plugins/powercontext/.mcp.json', 'integrations/codex/plugins/powercontext/hooks/recall.py', 'docs/en/docs/how-to/configure-codex.md', 'docs/zh/docs/how-to/configure-codex.md', 'tests/codex_plugin/test_contract.py', 'tests/e2e/test_codex_service_chain.py'
- **claude-code** (实现 / 文档 / 聚焦测试): 'integrations/claude-code/plugins/powercontext/.mcp.json', 'integrations/claude-code/plugins/powercontext/hooks/user_prompt_submit.py', 'docs/en/docs/how-to/configure-claude-code.md', 'docs/zh/docs/how-to/configure-claude-code.md', 'tests/claude_code_plugin/test_contract.py', 'tests/e2e/test_claude_code_service_chain.py'
- **dsh** (实现 / 文档 / 聚焦测试): 'integrations/dsh/plugins/powercontext/src/tools.ts', 'integrations/dsh/plugins/powercontext/src/commands.ts', 'docs/en/docs/how-to/configure-dsh.md', 'docs/zh/docs/how-to/configure-dsh.md', 'integrations/dsh/plugins/powercontext/tests/plugin-surfaces.spec.ts'
- **hermes** (实现 / 文档 / 聚焦测试): 'integrations/hermes/plugins/powercontext/operations.py', 'integrations/hermes/plugins/powercontext/commands.py', 'docs/en/docs/how-to/configure-hermes.md', 'docs/zh/docs/how-to/configure-hermes.md', 'tests/integrations/test_hermes_provider.py'
- **openclaw** (实现 / 文档 / 聚焦测试): 'integrations/openclaw/plugins/memory-powercontext/src/tools.ts', 'integrations/openclaw/plugins/memory-powercontext/src/lifecycle.ts', 'docs/en/docs/how-to/configure-openclaw.md', 'docs/zh/docs/how-to/configure-openclaw.md', 'integrations/openclaw/plugins/memory-powercontext/src/lifecycle.test.ts'
- **opencode** (实现 / 文档 / 聚焦测试): 'integrations/opencode/plugins/powercontext/src/index.ts', 'docs/en/docs/how-to/configure-opencode.md', 'docs/zh/docs/how-to/configure-opencode.md', 'integrations/opencode/plugins/powercontext/tests/plugin.spec.ts'
- **pi** (实现 / 文档 / 聚焦测试): 'integrations/pi/plugins/powercontext/src/tools.ts', 'integrations/pi/plugins/powercontext/extensions/powercontext.ts', 'docs/en/docs/how-to/configure-pi.md', 'docs/zh/docs/how-to/configure-pi.md', 'integrations/pi/plugins/powercontext/tests/extension.spec.ts'
- **workbuddy** (实现 / 文档 / 聚焦测试): 'integrations/workbuddy/plugins/powercontext/.mcp.json', 'integrations/workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py', 'docs/en/docs/how-to/configure-workbuddy.md', 'docs/zh/docs/how-to/configure-workbuddy.md', 'tests/test_cli_workbuddy.py'
- **pydantic-ai** (实现 / 文档 / 聚焦测试): 'integrations/pydantic-ai/src/powercontext_pydantic_ai/toolset.py', 'docs/en/docs/how-to/configure-pydantic-ai.md', 'docs/zh/docs/how-to/configure-pydantic-ai.md', 'tests/pydantic_ai_adapter/test_toolset.py'
- **langchain** (实现 / 文档 / 聚焦测试): 'integrations/langchain/src/powercontext_langchain/middleware.py', 'docs/en/docs/how-to/configure-langchain.md', 'docs/zh/docs/how-to/configure-langchain.md', 'tests/langchain_middleware/test_middleware.py'
- **langgraph** (实现 / 文档 / 聚焦测试): 'integrations/langgraph/src/powercontext_langgraph/tools.py', 'docs/en/docs/how-to/configure-langgraph.md', 'docs/zh/docs/how-to/configure-langgraph.md', 'tests/langgraph_adapter/test_tools.py'
- **bub** (实现 / 文档 / 聚焦测试): 'integrations/bub/src/powercontext_bub/tools.py', 'integrations/bub/src/powercontext_bub/plugin.py', 'integrations/bub/README.md', 'e2e/bub/tests/test_workload_catalog.py'

## Issue 1357 验收覆盖

| 要求 | 强制方式 |
| --- | --- |
| 版本、枚举和矛盾声明 | schema 校验测试 |
| kind 和 availability 区分 | schema 与状态证据测试 |
| CLI、证据和实际工具面 | integration manifest 契约测试 |
| 文档和工具暴露漂移 | 标准 integration-manifest-check 门禁 |
| profile 和平台差异 | toolset 推导的能力与 profile 校验 |
| 工具可见性不是授权 | candidate_review 契约定义 |
