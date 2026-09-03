---
title: 集成能力矩阵
description: PowerContext 集成的生成式能力矩阵。
---

# 集成能力矩阵

本矩阵由仓库 contract 生成，不是公开 HTTP capability API。

candidate_review 仅可列举和读取候选材料，不授予决策权限。

## 可用性

- **已发布**: 可从带有 PowerContext tag 的发布版本获得。
- **仅 master**: 已在当前 master 分支实现，但尚未发布。
- **实验性**: 已为评估实现；行为与支持不承诺稳定，可能变更。
- **提议中**: 在 issue、PR 或 RFC 中规划，当前不受支持。
- **不支持**: 当前不支持；manifest 必须记录维护中的理由。

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
