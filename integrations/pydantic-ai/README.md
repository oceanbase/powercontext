# PowerContext for Pydantic AI

This directory contains a preview `powercontext-pydantic-ai` adapter. It connects a Pydantic AI agent to a running
PowerContext Server through the public asynchronous Python Client. It provides three tools, prepares relevant context
before model requests, and can optionally capture bounded agent events and flush them into Memory.

## Availability

The adapter is not currently published on PyPI. Its source metadata requires a final `powercontext[client]>=0.0.3`,
which the current public root package and the development version from `master` do not satisfy. Do not use the old
PyPI command or a direct Git subdirectory install; both fail dependency resolution. Repository contributors can run
the adapter tests through the root development environment.

The remaining sections document the preview API for development and review; they are not a supported installation
path. The example uses OpenAI. For another provider, use the matching `pydantic-ai-slim` provider extra and change the
model string after compatible packages are released.

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContext

agent = Agent(
    "openai:gpt-5.2",
    capabilities=[PowerContext()],
)
result = agent.run_sync("Which API constraints have we already agreed on?")
print(result.output)
```

`PowerContext` contributes these model tools:

- `powercontext_search(query, limit=10, mode="auto")`
- `powercontext_remember(text, kind="agent-note", reason=None)`
- `powercontext_context(query)`

Each tool returns the complete public HTTP response as JSON, including citations, status, and revision fields. Client
failures become Pydantic AI `ModelRetry` signals instead of empty search results. To install only the tools without
automatic recall or capture, pass `PowerContextToolset()` through the Agent's `toolsets=` argument.

## Configuration

Environment variables use the `POWERCONTEXT_PYDANTIC_AI_` prefix.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server HTTP base URL |
| `TOKEN` | unset | Bare Server token; the Client adds the `Bearer` scheme |
| `SCOPE_ID` | derived | Durable project scope |
| `TIMEOUT` | `10` | HTTP timeout in seconds |
| `MAX_BYTES` | `8000` | Maximum prepared-context bytes |
| `CAPTURE_EVENTS` | `false` | Capture visible agent trajectory events as Sources |
| `CAPTURE_CHECKPOINT_EVERY` | `5` | Flush after this many successful captures |
| `CAPTURE_MAX_BYTES` | `8192` | UTF-8 byte limit for one captured event |

The `TOKEN` value is deliberately different from the Codex and Claude Code plugin authorization settings: provide
only the opaque token, not `Bearer TOKEN` or a complete `Authorization` header. It is stored as Pydantic `SecretStr`
and passed to `PowerContextClient`, which constructs the header.

Both components accept `settings=`, `id=` (default `powercontext`), and `scope_id=`. `scope_id` can be a fixed string
or a callable receiving the current `RunContext`. Resolution order is constructor value or callback, environment
`SCOPE_ID`, normalized Git origin, then `local:<sha256-of-project-path>`. A callback is evaluated once per run.

## Recall, capture, and trust

Automatic recall uses the latest textual user prompt and prepends one current-run system request containing bounded
prepared context. That block is explicitly labeled untrusted historical evidence. Recall, capture, and flush fail
open when the Server is unavailable; explicit Memory tools still surface `ModelRetry` so the model can recover.

Capture is disabled by default. Enabling `CAPTURE_EVENTS=true` means you consent to sending the initial user text,
visible model text and tool calls, and completed tool arguments and results to the configured PowerContext scope.
Thinking/reasoning parts are excluded. Credential-like keys, known credential environment values, and Codex auth
values are redacted, but ordinary prompts and tool results can still contain sensitive project data. Captured events
use schema `powercontext.pydantic-ai-capture-event/v1`, are byte-bounded, and are flushed at checkpoints and after the
run.

PowerContext MCP requires no Pydantic AI-specific package and remains a useful lower-capability alternative. It does
not provide automatic `prepare_context`, trajectory capture, or checkpoint/final flush. Temporal, DBOS, Prefect, and
other durable-execution integrations have not been validated for the preview. Handoff, Candidate Review, Experience,
and Skill operations are outside this adapter's scope.
