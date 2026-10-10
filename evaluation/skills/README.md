# Skill evaluations

Evaluate how agent Skills affect instruction following and tool selection. Keep each
suite's host setup, fixtures, and evidence requirements in its own directory.

| Suite | Measures | Entry point |
| --- | --- | --- |
| [skill-up](skill-up/README.md) | Claude Code routing and tool selection with and without the packaged PowerContext Skill, using mocked MCP replies. | `cd evaluation/skills/skill-up` then follow its README. |
| [ZCode native CLI](../zcode_guidance/README.md) | Native ZCode tool routing and authorization with a live model, controlled MCP replies, and a no-Skill baseline. | From the repository root, follow the ZCode guidance README. |

These results describe Skill guidance behavior. They do not establish live backend
acceptance or contribute to coding-task and memory-quality benchmark scores.
