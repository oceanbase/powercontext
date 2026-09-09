# PowerContext

Context for work that humans and agents hand off and continue.

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

Work rarely ends with whoever starts it. You hand a task to an agent, the agent gets part of the way, and later you or someone else takes over. The reasoning and current state often stay behind in that conversation.

PowerContext keeps context with the work across conversations. When you return, you can see what happened and continue from the current state. A new agent can do the same.

![You and agents hand work off and continue with stored context](docs/assets/readme-workflow.svg)

[Website](https://powercontext.oceanbase.io/) · [Read the documentation](https://powercontext.oceanbase.io/en/docs/)

## Pick up where the work left off

You see the context the work needs now: confirmed decisions, constraints, progress, evidence, and next steps. You can continue from there or hand the work to another person or agent without rereading the full history.

You decide what will matter later and what needs to move with the task. PowerContext stores durable information as Memory and organizes the current objective and state into a Handoff. You can record reusable approaches as Experience or Skill. PowerContext keeps every item within the scope of the work and preserves its sources and earlier revisions.

## Get started

Install the released package and matching Codex integration. Keep the Server running in its own terminal:

```bash
uv tool install "powercontext[cli,server]==0.2.0"
powercontext server run
```

In another terminal:

```bash
powercontext setup codex --ref powercontext-v0.2.0
```

Python 3.11+ is required. macOS and Linux are supported; Windows is `experimental`.
For current `master`, other hosts, and verification, follow the [Quick Start](docs/en/docs/get-started/quickstart.md)
and [installation guide](docs/en/docs/get-started/install-and-run.md). Keep the package and integration on the same ref.

## Integrations

| Type | Integrations | Tag |
| --- | --- | --- |
| Agent Host | Codex | `official` |
| Agent Hosts | Claude Code, DeepSeek Harness, Hermes, OpenClaw, OpenCode, Pi, WorkBuddy | `community` |
| Python Agent frameworks | Pydantic AI, LangChain, LangGraph | `community` |
| Evaluation | Bub | `evaluation` |

`official` means PowerContext project maintenance; `community` means a community contribution; `evaluation` means
for evaluation only. These tags do not imply host-vendor endorsement or equal capabilities.
See [Plugins, MCP, Skills, and setup](docs/en/docs/integrations/index.md) and the
[capability matrix](docs/en/docs/integrations/capabilities.md) for `released`, `master_only`, and `experimental` availability.

## Documentation

- [Manage context](docs/en/docs/workflows/index.md): Memory, Handoff, Experience, Skill, Sources, Scopes, and Artifacts.
- [Deploy and operate](docs/en/docs/operate/index.md): personal services, logs, metrics, tracing, and recovery.
- [Develop with APIs](docs/en/docs/develop/index.md): HTTP, Python, and application integration.
- [Benchmarks](https://powercontext.oceanbase.io/en/benchmarks/): methods, results, and limitations.
- [Contributing](CONTRIBUTING.md).

PowerContext is the successor to [PowerMem](https://www.powermem.ai/).

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
