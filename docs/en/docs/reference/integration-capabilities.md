---
title: Integration capability manifest
description: Versioned repository contract for PowerContext integrations.
---

# Integration capability manifest

This page is generated from integrations/capabilities.toml. It is a repository contract, not a public HTTP capability API. Implemented capabilities are backed by exact checked-in tool-surface probes.

candidate_review means candidates can be listed or read. It never grants approval authority.

## Support profiles

- **Minimal**: Memory read/write, Source capture, and Context injection.
- **Recommended**: Minimal plus Work Contract, Handoff, acknowledgement, and Task Outcome.
- **Full**: Recommended plus Experience, Skill, Candidate review, and External Skill.

## Current matrix

| ID | Kind | Availability | Profiles | Capabilities |
| --- | --- | --- | --- | --- |
| codex | Agent host | Master only | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| claude-code | Agent host | Master only | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| dsh | Agent host | Master only | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>handoff<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review<br>external_skill<br>slash_command |
| hermes | Agent host | Master only | minimal, recommended, full | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review<br>external_skill<br>slash_command<br>persistent_workstream_binding |
| openclaw | Agent host | Master only | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>pre_compaction_capture |
| opencode | Agent host | Master only | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>handoff<br>experience_read_or_generate<br>skill_read_or_generate<br>candidate_review |
| pi | Agent host | Master only | minimal | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>handoff<br>pre_compaction_capture<br>slash_command |
| workbuddy | Agent host | Master only | minimal, recommended | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint<br>work_contract<br>handoff<br>acknowledge<br>task_outcome<br>candidate_review |
| pydantic-ai | Framework adapter | Experimental | — | memory_read<br>memory_write<br>context_injection |
| langchain | Framework adapter | Master only | — | source_capture<br>context_injection |
| langgraph | Framework adapter | Master only | — | memory_read<br>memory_write<br>context_injection |
| bub | Evaluation harness | Master only | — | memory_read<br>memory_write<br>source_capture<br>context_injection<br>flush_or_checkpoint |

## Evidence

- **codex** (Implementation / docs / focused tests): 'integrations/codex/plugins/powercontext/.mcp.json', 'integrations/codex/plugins/powercontext/hooks/recall.py', 'docs/en/docs/how-to/configure-codex.md', 'docs/zh/docs/how-to/configure-codex.md', 'tests/codex_plugin/test_contract.py', 'tests/e2e/test_codex_service_chain.py'
- **claude-code** (Implementation / docs / focused tests): 'integrations/claude-code/plugins/powercontext/.mcp.json', 'integrations/claude-code/plugins/powercontext/hooks/user_prompt_submit.py', 'docs/en/docs/how-to/configure-claude-code.md', 'docs/zh/docs/how-to/configure-claude-code.md', 'tests/claude_code_plugin/test_contract.py', 'tests/e2e/test_claude_code_service_chain.py'
- **dsh** (Implementation / docs / focused tests): 'integrations/dsh/plugins/powercontext/src/tools.ts', 'integrations/dsh/plugins/powercontext/src/commands.ts', 'docs/en/docs/how-to/configure-dsh.md', 'docs/zh/docs/how-to/configure-dsh.md', 'integrations/dsh/plugins/powercontext/tests/plugin-surfaces.spec.ts'
- **hermes** (Implementation / docs / focused tests): 'integrations/hermes/plugins/powercontext/operations.py', 'integrations/hermes/plugins/powercontext/commands.py', 'docs/en/docs/how-to/configure-hermes.md', 'docs/zh/docs/how-to/configure-hermes.md', 'tests/integrations/test_hermes_provider.py'
- **openclaw** (Implementation / docs / focused tests): 'integrations/openclaw/plugins/memory-powercontext/src/tools.ts', 'integrations/openclaw/plugins/memory-powercontext/src/lifecycle.ts', 'docs/en/docs/how-to/configure-openclaw.md', 'docs/zh/docs/how-to/configure-openclaw.md', 'integrations/openclaw/plugins/memory-powercontext/src/lifecycle.test.ts'
- **opencode** (Implementation / docs / focused tests): 'integrations/opencode/plugins/powercontext/src/index.ts', 'docs/en/docs/how-to/configure-opencode.md', 'docs/zh/docs/how-to/configure-opencode.md', 'integrations/opencode/plugins/powercontext/tests/plugin.spec.ts'
- **pi** (Implementation / docs / focused tests): 'integrations/pi/plugins/powercontext/src/tools.ts', 'integrations/pi/plugins/powercontext/extensions/powercontext.ts', 'docs/en/docs/how-to/configure-pi.md', 'docs/zh/docs/how-to/configure-pi.md', 'integrations/pi/plugins/powercontext/tests/extension.spec.ts'
- **workbuddy** (Implementation / docs / focused tests): 'integrations/workbuddy/plugins/powercontext/.mcp.json', 'integrations/workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py', 'docs/en/docs/how-to/configure-workbuddy.md', 'docs/zh/docs/how-to/configure-workbuddy.md', 'tests/test_cli_workbuddy.py'
- **pydantic-ai** (Implementation / docs / focused tests): 'integrations/pydantic-ai/src/powercontext_pydantic_ai/toolset.py', 'docs/en/docs/how-to/configure-pydantic-ai.md', 'docs/zh/docs/how-to/configure-pydantic-ai.md', 'tests/pydantic_ai_adapter/test_toolset.py'
- **langchain** (Implementation / docs / focused tests): 'integrations/langchain/src/powercontext_langchain/middleware.py', 'docs/en/docs/how-to/configure-langchain.md', 'docs/zh/docs/how-to/configure-langchain.md', 'tests/langchain_middleware/test_middleware.py'
- **langgraph** (Implementation / docs / focused tests): 'integrations/langgraph/src/powercontext_langgraph/tools.py', 'docs/en/docs/how-to/configure-langgraph.md', 'docs/zh/docs/how-to/configure-langgraph.md', 'tests/langgraph_adapter/test_tools.py'
- **bub** (Implementation / docs / focused tests): 'integrations/bub/src/powercontext_bub/tools.py', 'integrations/bub/src/powercontext_bub/plugin.py', 'integrations/bub/README.md', 'e2e/bub/tests/test_workload_catalog.py'

## Issue 1357 acceptance coverage

| Requirement | Enforced by |
| --- | --- |
| Version, enum, and contradictory declarations | Schema validation tests |
| Kind and availability distinctions | Schema validation and status evidence tests |
| CLI, evidence, and actual tool surface | Integration manifest contract test |
| Documentation and tool exposure drift | Standard integration-manifest-check gate |
| Profiles and platform differences | Toolset-derived capability and profile validation |
| Tool visibility is not authorization | candidate_review contract definition |
