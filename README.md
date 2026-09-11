# PowerContext

Context for work that humans and agents hand off and continue.

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

Work rarely ends with whoever starts it. You hand a task to an agent, the agent gets part of the way, and later you or someone else takes over. The reasoning and current state often stay behind in that conversation.

PowerContext keeps context with the work across conversations. When you return, you can see what happened and continue from the current state. A new agent can do the same.

![You and agents hand work off and continue with stored context](docs/assets/readme-workflow.svg)

[Website](https://frf12.github.io/powercontext/) · [Installation walkthrough](https://frf12.github.io/powercontext/en/docs/get-started/quickstart/)

PowerContext 1.0.0 includes the guided setup. The commands below install the stable release and connect
the matching Agent integration.

## Pick up where the work left off

You see the context the work needs now: confirmed decisions, constraints, progress, evidence, and next steps. You can continue from there or hand the work to another person or agent without rereading the full history.

You decide what will matter later and what needs to move with the task. PowerContext stores durable information as Memory and organizes the current objective and state into a Handoff. You can record reusable approaches as Experience or Skill. PowerContext keeps every item within the scope of the work and preserves its sources and earlier revisions.

## Install, configure, and connect your Agent

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and your Agent's CLI.
Python 3.11+ is required; uv can provision it. macOS and Linux are supported; Windows support is `experimental`.

Install 1.0.0 and open the interactive configuration wizard in a dedicated directory:

```bash
uv tool install --force "powercontext[cli,server]==1.0.0"
mkdir -p powercontext-config
cd powercontext-config
powercontext config init --language en --output .env
```

If Python dependency downloads are slow or fail, follow the [mirror retry instructions](https://frf12.github.io/powercontext/en/docs/get-started/install-and-run/#retry-dependency-downloads-with-a-mirror).

The wizard asks about storage, local or remote access, memory capabilities, Dashboard, model APIs, and Agent
connections. Choose **Full memory capabilities** to test automatic Memory and Topic Memory; this requires separate
Generation and Embedding API credentials. An Agent subscription does not provide those Server credentials.
Choose **Basic memory** to save and retrieve memories explicitly without additional model APIs.

The wizard writes one `.env` environment file and `.env.next-steps.md`.
If seekdb needs installing, it asks once and installs the dependency in the background. Follow the printed
connection details and start Server in this terminal:

```bash
powercontext server run --env-file .env
```

Keep Server running. In another terminal, return to `powercontext-config`, load only the client settings,
and check the connection:

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Continue with `.env.next-steps.md` to create and bind the selected Scopes, install the matching plugins, and launch
a new Agent session. The [complete walkthrough](https://frf12.github.io/powercontext/en/docs/get-started/quickstart/)
covers Codex and Claude Code, Dashboard login, SSH forwarding, HTTPS prerequisites, and observable acceptance checks.
For example, the matching Codex installation is:

```bash
powercontext setup codex --ref powercontext-v1.0.0
powercontext doctor codex
```

`doctor` verifies integration setup. To verify automatic memory, check that a real prompt becomes a Source,
produces a Topic, evolves after a related prompt, and can be recalled in a new session using the same Scope.

For a Server on another machine, use HTTPS or follow the
[remote connection guide](docs/en/docs/operate/connect-remote-server.md). Setup recognizes remote HTTP URLs from
flags or environment variables and asks for explicit consent; automated setup uses `--allow-insecure-http`.

Codex is `official`; other hosts and Python Agent frameworks are `community`; Bub is `evaluation` only.
These tags describe PowerContext integration maintenance and use. See the
[capability matrix](https://frf12.github.io/powercontext/en/docs/integrations/capabilities/) for supported features and availability.

<table>
<tr>
<td align="center" width="120"><a href="docs/en/docs/integrations/codex.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/claude-code.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/dsh.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/hermesagent.png?raw=true&size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /></picture><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/pi.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/pi.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120" alt="Pi Coding Agent" width="48" height="48" /></picture><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/openclaw.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/en/docs/integrations/opencode.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/opencode.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120" alt="OpenCode" width="48" height="48" /></picture><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="https://thesvg.org/icons/workbuddy/default.svg?size=120" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/pydantic-ai.md"><img src="https://thesvg.org/icons/pydantic/default.svg?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/langchain.md"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/integrations/langgraph.md"><picture><source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langgraph.png?size=120"><img src="https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120" alt="LangGraph" width="48" height="48" /></picture><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

Applications can use PowerContext through the async Python client, HTTP API, MCP, or the in-process Core SDK. See the [interface reference](https://frf12.github.io/powercontext/en/docs/develop/interfaces/) to choose an entry point.

Explore the [22 Chinese Jupyter tutorials and a complete team workflow](examples/jupyter/README.md) to run Memory, context preparation, Handoff, Experience, Skill, and a real Agent step by step. The first seven tutorials need no model or API key.

## What changes with PowerContext

![Compact comparison of PowerContext results on LoCoMo and SWE-bench Pro](docs/assets/readme-benchmark-summary.svg)

See the [methods, full results, and limitations](https://frf12.github.io/powercontext/en/benchmarks/) behind these comparisons.

## Build PowerContext

```bash
make install
make check
make test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the complete development workflow.

## Learn more

- [Get started](https://frf12.github.io/powercontext/en/docs/get-started/quickstart/)
- [Connect Agents](https://frf12.github.io/powercontext/en/docs/integrations/)
- [Manage context](https://frf12.github.io/powercontext/en/docs/workflows/)
- [Deploy and operate](https://frf12.github.io/powercontext/en/docs/operate/)
- [Develop with APIs](https://frf12.github.io/powercontext/en/docs/develop/)

PowerContext is the successor to [PowerMem](https://www.powermem.ai/).

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
