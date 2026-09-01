# PowerContext

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="theme/powercontext/assets/images/powercontext-reverse.png">
  <img alt="PowerContext" src="theme/powercontext/assets/images/powercontext-color.png" width="480" />
</picture>

**Not only memory**

[![PyPI version](https://img.shields.io/pypi/v/powercontext)](https://pypi.org/project/powercontext/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

*[English](README.md) · [中文](README_CN.md) · [日本語](README_JP.md)*

</div>

PowerContext is the upgraded version of [PowerMem](https://www.powermem.ai/) and a context runtime for human-agent
collaboration. It turns shared work into project context that can be understood, handed off, and continued.

## Quick start

You need macOS or Linux, Python 3.11 or newer, and [`uv`](https://docs.astral.sh/uv/). Choose your entry:

- already have an AI application and do not use an Agent Host: follow the
  [HTTP API lifecycle tutorial](docs/en/docs/tutorials/api-quickstart.md) to complete the first Source, Memory,
  PreparedContext, Experience, Skill, and Review loop over HTTP;
- use Codex, Claude Code, DSH, OpenCode, or another Host: follow the
  [Agent step-by-step quickstart](docs/en/docs/tutorials/agent-quickstart.md) for its actual Memory, automatic-recall,
  and Handoff surface.

The commands below are the shorter shared installation path.

### 1. Install PowerContext, then add integrations for Agent Hosts

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"

# Only the Agent Host path needs one or more integrations. Every setup command installs from master.
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup claude-code --source oceanbase/powercontext --ref master
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext setup openclaw --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext setup pi --source oceanbase/powercontext --ref master
powercontext setup workbuddy --source oceanbase/powercontext --ref master

# Or install several hosts in one pass.
powercontext setup select --host codex --host claude-code --host opencode \
  --source oceanbase/powercontext --ref master
```

The first command installs the CLI and local Server from the latest `master` revision in an isolated environment.
Every setup command installs its integration from the same `master` revision. Run setup again to refresh an existing
integration. HTTP API users need only the first install command and can skip every `powercontext setup` command.

### 2. Start and verify the local Server

Keep the Server running in one terminal:

```bash
powercontext server run
```

In another terminal, verify the service and plugin:

```bash
powercontext doctor
powercontext doctor integrations
powercontext doctor codex  # Replace codex with the host you installed.
```

By default, the Server listens on `127.0.0.1:8000`, exposes Streamable HTTP MCP at `/mcp`, and persists data in a
local SQLite database. Explicit Memory operations work without configuring an inference provider.

### 3. Complete the Agent Memory and Handoff loop

Start a new session from one project directory and follow the prompts in the
[Agent step-by-step quickstart](docs/en/docs/tutorials/agent-quickstart.md). It shows how to:

1. select and diagnose an installed Agent Host;
2. save explicit project Memory and recover it in another session;
3. use one-line, `pc_*`, or `/pc` Handoff according to the Host's real capabilities;
4. verify a non-Codex DSH-to-OpenCode continuation with one exact Revision.

No generation model is required for this first loop. Configure inference only when you continue to model-backed
extraction and vector search. For the Codex-specific Hook and one-line flow, continue with the
[complete Codex tutorial](docs/en/docs/tutorials/codex-quickstart.md).

### 4. Or add the HTTP API to your own AI

Without an Agent Host, call `POST /v1/context/prepare` before each model request and supply the returned read-only,
untrusted historical context to the model. Call `POST /v1/memory/remember` only after explicit user or business-policy
authorization. The [HTTP API lifecycle tutorial](docs/en/docs/tutorials/api-quickstart.md) provides one small Python
learning path; use the [Scalar API Reference](https://oceanbase.github.io/powercontext/api/) for every endpoint and
schema.

## Core capabilities

| Capability | Core value |
| --- | --- |
| Memory extraction and management | Explicitly record decisions, constraints, outcomes, state, and next steps worth reusing over time; with a generation model configured, Memory can also be extracted from Sources. Revisions and retirements preserve history |
| Bounded request-time recall | Before an agent handles a request, generate one schema-validated, cited `PreparedContext` based on project scope, relevance, and a byte budget; recall failures do not block the original task |
| Handoff | Organize the objective, verified progress, blockers, next step, and evidence into an inspectable work package so another session, task, model, or agent host can continue from a clear state |
| Sources and evidence lineage | Preserve the original sources of knowledge and link Memory and Artifacts with exact citations; capturing a prompt creates only a Source and does not directly turn it into Memory |
| Experience and Skill governance | A model or caller can only submit a Candidate; an immutable revision is created only after Review, and a Skill must still be exported explicitly—it cannot approve, install, or execute itself |
| Local and service deployment | Use SQLite directly for local development, choose OceanBase for team deployments, and integrate with existing systems through HTTP/OpenAPI, MCP, authentication, and OpenTelemetry |

## Benchmarks

### [LoCoMo](https://github.com/snap-research/locomo)

![LOCOMO benchmark comparison showing PowerContext accuracy, search latency, and answer token usage against PowerMem and a full-context baseline](docs/assets/locomo-benchmark-comparison.svg)

### [SWE-bench Pro public v2](https://github.com/scaleapi/SWE-bench_Pro-os)

![SWE-bench Pro public v2 comparison showing an increase from 82.35% with PowerContext off to 86.73% with PowerContext on](docs/assets/swe-bench-pro-public-v2-comparison.svg)

The evaluation ran in a Codex environment, with both the PowerContext OFF and ON groups using the `gpt-5.6-sol`
model.

---

## Integrations

PowerContext provides official integrations and installation guides for Codex, Claude Code, DeepSeek Harness, Hermes
Agent, Pi Coding Agent, OpenClaw, OpenCode, WorkBuddy, Bub, Pydantic AI, LangChain, and LangGraph. These integrations
use the same scoped data and history-preserving contracts through PowerContext Server; the host integrations do not
start or embed the Server.

### Official integrations

<table>
<tr>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-codex.md"><img src="https://github.com/openai.png?size=120" alt="Codex" width="48" height="48" /><br /><sub><b>Codex</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-claude-code.md"><img src="https://github.com/anthropics.png?size=120" alt="Claude Code" width="48" height="48" /><br /><sub><b>Claude Code</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-dsh.md"><img src="https://github.com/deepseek-ai.png?size=120" alt="DeepSeek Harness" width="48" height="48" /><br /><sub><b>DeepSeek Harness</b></sub></a></td>
<td align="center" width="120"><a href="integrations/hermes/README.md"><img src="https://github.com/NousResearch/hermes-agent/blob/main/website/static/img/logo.png?raw=true&size=120" alt="Hermes Agent" width="48" height="48" /><br /><sub><b>Hermes Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-pi.md"><img src="https://github.com/earendil-works.png?size=120" alt="Pi Coding Agent" width="48" height="48" /><br /><sub><b>Pi Coding Agent</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-openclaw.md"><img src="https://github.com/openclaw.png?size=120" alt="OpenClaw" width="48" height="48" /><br /><sub><b>OpenClaw</b></sub></a></td>
</tr>
<tr>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-opencode.md"><img src="https://github.com/anomalyco.png?size=120" alt="OpenCode" width="48" height="48" /><br /><sub><b>OpenCode</b></sub></a></td>
<td align="center" width="120"><a href="integrations/workbuddy/README.md"><img src="docs/assets/workbuddy.svg" alt="WorkBuddy" width="48" height="48" /><br /><sub><b>WorkBuddy</b></sub></a></td>
<td align="center" width="120"><a href="integrations/bub/README.md"><img src="https://github.com/bubbuild.png?size=120" alt="Bub" width="48" height="48" /><br /><sub><b>Bub</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-pydantic-ai.md"><img src="https://github.com/pydantic.png?size=120" alt="Pydantic AI" width="48" height="48" /><br /><sub><b>Pydantic AI</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-langchain.md"><img src="https://github.com/langchain-ai.png?size=120" alt="LangChain" width="48" height="48" /><br /><sub><b>LangChain</b></sub></a></td>
<td align="center" width="120"><a href="docs/en/docs/how-to/configure-langgraph.md"><img src="https://github.com/langchain-ai.png?size=120" alt="LangGraph" width="48" height="48" /><br /><sub><b>LangGraph</b></sub></a></td>
</tr>
</table>

Python agent applications can use the [LangChain middleware](docs/en/docs/how-to/configure-langchain.md), the
[LangGraph node and tools adapter](docs/en/docs/how-to/configure-langgraph.md), the
[Pydantic AI middleware](docs/en/docs/how-to/configure-pydantic-ai.md), or the
[Bub plugin](integrations/bub/README.md).

## Development

Install the locked development environment and hooks:

```bash
make install
```

Run the main validation commands before opening a pull request:

```bash
make check
make test
make docs-test
```

After changing `openapi/powercontext.yaml`, run `make contract-test`. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
complete workflow and [`docs/en/development/`](docs/en/development/core-protocol.md) for implementation guides.

## Community

Questions and feedback are welcome in [Discord](https://discord.com/invite/74cF8vbNEs). Use
[GitHub Issues](https://github.com/oceanbase/powercontext/issues) for reproducible defects and focused feature
requests.

## License

PowerContext is licensed under the [Apache License 2.0](LICENSE).
