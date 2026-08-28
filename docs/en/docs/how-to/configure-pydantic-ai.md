---
title: Pydantic AI adapter preview
description: Review the current Pydantic AI adapter API and its installation status.
---

# Pydantic AI adapter preview

The repository contains a preview adapter that lets a Pydantic AI agent share durable Memory through a running
PowerContext Server. It is not yet available as a supported standalone installation.

## Check availability before using it

`powercontext-pydantic-ai` is not currently published on PyPI. Its source package also requires a final
`powercontext[client]>=0.0.3`, which the current public package and the development version from `master` do not
satisfy. Therefore, both the old PyPI command and a direct Git subdirectory install fail dependency resolution.

Do not add this adapter to an application until compatible root and adapter packages have been released. Repository
contributors can run its tests through the root development environment; the remaining sections document the preview
API for development and review, not a supported installation path.

## Attach the preview capability

The example below uses OpenAI. For another provider, install the matching `pydantic-ai-slim` provider extra and
change the model string.

Attach the capability to an Agent:

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContext

agent = Agent(
    "openai:gpt-5.2",
    capabilities=[PowerContext(scope_id="project:example")],
)
```

The capability adds `powercontext_search`, `powercontext_remember`, and `powercontext_context`. It also requests
`prepare_context` from the latest textual user prompt and prepends at most one untrusted evidence block per run. A new
run prepares context again even when it starts from the previous run's message history.

Use only the toolset when automatic preparation and capture are not wanted:

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContextToolset

agent = Agent("openai:gpt-5.2", toolsets=[PowerContextToolset()])
```

## Set environment configuration

```bash
export POWERCONTEXT_PYDANTIC_AI_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_PYDANTIC_AI_TOKEN=opaque-server-token
export POWERCONTEXT_PYDANTIC_AI_SCOPE_ID=project:example
```

| Variable | Default | Validation and behavior |
| --- | --- | --- |
| `POWERCONTEXT_PYDANTIC_AI_BASE_URL` | `http://127.0.0.1:8000` | HTTP(S), without credentials, query, or fragment |
| `POWERCONTEXT_PYDANTIC_AI_TOKEN` | unset | Bare printable token stored as `SecretStr` |
| `POWERCONTEXT_PYDANTIC_AI_SCOPE_ID` | derived | Non-empty scope, deterministically bounded to 256 characters |
| `POWERCONTEXT_PYDANTIC_AI_TIMEOUT` | `10` | Positive seconds |
| `POWERCONTEXT_PYDANTIC_AI_MAX_BYTES` | `8000` | `512`–`32768` prepared-context bytes |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS` | `false` | Opt in to visible event capture |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_CHECKPOINT_EVERY` | `5` | `1`–`100` successful events per flush |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_MAX_BYTES` | `8192` | `512`–`32768` UTF-8 bytes per event |

Unlike the Codex and Claude Code plugin settings that accept a complete authorization value, this adapter accepts a
bare token. Do not include `Bearer ` or pass a complete `Authorization` header; the public Client adds the scheme.

Both `PowerContext` and `PowerContextToolset` accept a `PowerContextSettings` instance, a stable `id` (default
`powercontext`), and a fixed or callable `scope_id`:

```python
from pydantic_ai import RunContext
from powercontext_pydantic_ai import PowerContext, PowerContextSettings

settings = PowerContextSettings(timeout=5, max_bytes=4096)


def tenant_scope(ctx: RunContext[dict[str, str]]) -> str:
    return f"tenant:{ctx.deps['tenant_id']}"


capability = PowerContext(settings=settings, scope_id=tenant_scope)
```

The callback runs once per Agent run. Scope precedence is constructor string or callback, environment `SCOPE_ID`,
normalized Git origin, then `local:<sha256-of-project-path>`. Explicit configuration avoids Git subprocesses.

## Decide whether to capture events

Capture is off by default. Set `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS=true` only when sending the initial user text,
visible model text and tool calls, and completed tool arguments and results to the configured scope is acceptable.
Thinking/reasoning content is excluded. Events are redacted for credential-like keys and known environment/Codex
credentials, rendered within the configured byte limit, and stored under
`powercontext.pydantic-ai-capture-event/v1`.

Every successful Capture advances the run-local Source position. A checkpoint Flush runs after the configured number
of captures, and `after_run` flushes any remaining Source. Parallel tool results receive unique sequence numbers under
a run-local lock. Recall, Capture, and Flush fail open during Server failures; explicit tool failures become
`ModelRetry`. The first HTTP 401 or 403 logs one credential-free configuration warning.

Captured project content can remain sensitive after credential redaction. Protect the Server, scope, database, and
logs accordingly.

## Compare the MCP fallback

Connecting PowerContext MCP requires no adapter package, but it is a lower-capability option for Pydantic AI. MCP
provides explicit tools; it does not automatically call `prepare_context`, capture trajectory events, or Flush at
checkpoints and run completion.

The preview supports ordinary Pydantic AI runs. Durable execution through Temporal, DBOS, Prefect, or similar systems
is not yet validated. Handoff, Candidate Review, Experience, and Skill operations are not included.
