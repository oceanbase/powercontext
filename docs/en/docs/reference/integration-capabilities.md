---
title: Integration capability matrix
description: Generated capability matrix for PowerContext integrations.
---

# Integration capability matrix

This generated matrix is a repository contract, not a public HTTP capability API.

candidate_review permits listing and reading candidates only; it never grants decision authority.

## Availability

- **Released**: Available from a tagged PowerContext release.
- **Master only**: Implemented on the current master branch but not yet released.
- **Experimental**: Implemented for evaluation; behavior and support can change without stability guarantees.
- **Proposed**: Planned in an issue, pull request, or RFC; it is not currently supported.
- **Unsupported**: Not supported; the manifest must record a maintained rationale.

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
