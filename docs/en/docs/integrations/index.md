---
title: Connect Agents
description: Choose Plugins, MCP, Skills, an Agent Host, or a Python framework adapter.
---

# Connect Agents

Choose the integration mechanism, then the host or framework:

- **Plugins** package host configuration, tools, hooks, or Skills. Dedicated plugins can join the host lifecycle;
  the portable Agent Plugin provides MCP configuration and a Skill.
- **MCP** exposes Agent tools at the Server's `/mcp` endpoint. MCP alone does not provide automatic prompt capture,
  recall hooks, or Skill installation.
- **Skills** provide discoverable workflow instructions. Installing a Skill does not start the Server or grant host
  tool permissions. Use the [portable Agent Plugin](agent-plugin.md) in a compatible host, or
  [export a managed Skill](../workflows/configure-agent-skill-targets.md).

## Agent Hosts

The 9 Agent Hosts use `official` for PowerContext project maintenance and `community` for community contributions.
`official` does not imply endorsement by a host vendor.

| Agent Host | Stewardship | Integration |
| --- | --- | --- |
| [Codex](codex.md) | `official` | Prompt Hook + MCP + Skill |
| [Claude Code](claude-code.md) | `community` | Prompt Hook + MCP + Skill |
| [DeepSeek Harness](dsh.md) | `community` | HTTP plugin + pc_* + /pc |
| [Hermes](hermes.md) | `community` | MemoryProvider + /pc |
| [OpenClaw](openclaw.md) | `community` | Memory plugin + lifecycle hooks |
| [OpenCode](opencode.md) | `community` | HTTP plugin + pc_* |
| [Pi Coding Agent](pi.md) | `community` | Extension + pc_* + /pc |
| [WorkBuddy](workbuddy.md) | `community` | Prompt Hook + MCP + Skill |
| [ZCode](zcode.md) | `community` | Prompt Hook + MCP + Skill |

Each integration connects to a separately running Server. Follow its guide in the table for installation, connection settings, authentication, and diagnostics.

For remote endpoints, see [Connect to a remote Server](../operate/connect-remote-server.md): PowerContext clients
allow loopback HTTP by default and require explicit consent for non-loopback HTTP. Host-native MCP policies remain separate.

To select multiple hosts interactively, run:

```bash
powercontext setup
```

Use the same ref as the Server. `powercontext setup codex dsh` installs, configures, and diagnoses both agents. Use `powercontext config agent codex dsh` to update installed connections without reinstalling or enabling them; consult each Agent guide for native requirements.

## Experimental MCP connections

[Cindy](cindy.md) offers scoped Ghost recall tools and optional automatic recall and original-prompt capture,
with a separate native MCP connection for the full Server tool catalog. Users do not need DSH. It remains under
evaluation and is not counted in the qualified Agent Host list.

## Automatic prompt privacy

The following prompt adapters enable capture by default. Existing capture preferences remain effective
when updating a plugin; this policy adds credential checks without changing capture defaults.

| Host | Automatic input boundary | Disable capture |
| --- | --- | --- |
| Claude Code | `UserPromptSubmit` text | `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false` or setup `--no-capture-prompts` |
| Codex | `UserPromptSubmit` text | `POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false` |
| WorkBuddy | Submitted joined prompt text | `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS=false` |
| OpenCode | User text parts, excluding synthetic parts | `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false` |
| Pi | `before_agent_start` prompt | `POWERCONTEXT_PI_CAPTURE_PROMPTS=false` |
| ZCode | `UserPromptSubmit` text | `POWERCONTEXT_ZCODE_CAPTURE_PROMPTS=false` |
| Kimi Code (experimental) | Text parts of `UserPromptSubmit` | setup `--no-capture-prompts` |

Before automatic capture, these adapters recognize conventional credential assignments (API/access keys
and tokens, client/private keys, secrets, passwords, passphrases, authorization and cookies), Bearer values
of at least eight token characters, common provider/GitHub/Slack token shapes, private-key blocks including
unfinished blocks, and exact occurrences of the configured Server credential. Recognition is best effort:
unknown formats, obfuscation and credentials without recognizable markers can pass. Conservative matching
also skips examples such as `password=example`. Ordinary words such as “token budgets” are not assignments.
The shared synthetic corpus is `tests/fixtures/automatic_prompt_privacy.json`; it defines tested examples,
not proof that arbitrary credentials are detected.

Authorization, Proxy-Authorization, Cookie and Set-Cookie assignments consume the complete header value,
including schemes, cookie attributes and folded continuation lines, before ordinary assignments are matched.
This conservatively removes any other prose on the same header line from the automatic query.

A recognized input is skipped as a whole for capture. The adapter never redacts a Source body and then
attaches the original event's identity or receipt to it. Accepted events keep their existing host text
normalization and identity semantics; distinct events and retries follow the host's documented contract.
Local diagnostics or configured observations report a bounded skip reason (`sensitive_content`, or
Kimi's `sensitive`) without the rejected text. Pi reports this through its configured diagnostic sink;
`off` disables that output.

Automatic read-only preparation remains independent: recognized fragments are replaced with `[REDACTED]`
before building the bounded query, even when capture is disabled. Preparation may therefore lose some
query specificity; a valid historical response can still be delivered. This changes only the automatic
query, not stored Source evidence or an explicit tool call. Authentication headers still carry the
configured credential to the selected Server as required by that connection.

If prompts must not leave the host, disable automatic capture **and** the host's automatic preparation
hook/extension. Disabling capture alone still allows read-only requests, and detection is not a
confidentiality guarantee. Keep secrets out of prompts, configure trusted endpoints and review operational
access. This policy does not scan stored Sources, rewrite immutable revisions, prevent prompt injection,
or classify all explicit Source writes at the Server.

## Python Agent frameworks

All 3 adapters are `community`. Install them in the application environment; do not use
`powercontext setup <host>`:

| Framework | Integration | Current availability |
| --- | --- | --- |
| [Pydantic AI](pydantic-ai.md) | Toolset for Memory read/write and context | `experimental` |
| [LangChain](langchain.md) | Middleware for context and optional Source capture | `master_only` |
| [LangGraph](langgraph.md) | Recall hook and Memory tools | `master_only` |

## Evaluation integrations

[Bub is for evaluation only](evaluation.md). It is not part of the Agent Host or Python Agent framework support lists.

## Capabilities and availability

The [capability matrix](capabilities.md) is generated from `integrations/capabilities.toml`.
The 9 Agent Hosts and Bub are `master_only`; Pydantic AI is `experimental`.
`released` means available from a specified release tag, `master_only` means implemented but unreleased, and
`experimental` carries no stability guarantee. These labels are separate from stewardship and the
Minimal / Recommended / Full capability profiles.

Use the same release tag or commit for the Server and integration. Check [installation requirements](../get-started/install-and-run.md):
Windows support is `experimental`, and does not imply that every host or optional backend supports Windows.
