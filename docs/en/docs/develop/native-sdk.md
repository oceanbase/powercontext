---
title: Use a native model SDK
description: Add PowerContext PreparedContext to an application that calls OpenAI or Anthropic directly.
---

# Use a native model SDK

PowerContext does not wrap or replace your model SDK. The application keeps its own `openai` or `anthropic` client
and calls PowerContext separately through the async Python Client. No Agent host, prompt hook, plugin, or MCP
connection is required.

Use this pattern when an application, service, or script owns the model loop and you want to keep provider-specific
code in that application. If instead you want a host to capture prompts and recall context automatically, choose an
[Agent integration](../integrations/index.md).

## Request flow

For each turn, the application:

1. selects and authorizes one existing Scope in trusted code;
2. calls `prepare_context` with the current question as the query and a byte budget;
3. sends one model request, adding the returned `content` to the system prompt when the result is `ready`;
4. continues normally when the result is `empty`.

PowerContext is a separate service. The model provider request and the PowerContext request use independent clients
and credentials.

## Runnable examples

Two examples implement the same loop with different provider SDKs:

| SDK | Example | Command |
| --- | --- | --- |
| OpenAI | [`examples/openai_sdk`](https://github.com/oceanbase/powercontext/tree/master/examples/openai_sdk) | `uv run python -m examples.openai_sdk.chat --seed` |
| Anthropic | [`examples/anthropic_sdk`](https://github.com/oceanbase/powercontext/tree/master/examples/anthropic_sdk) | `uv run python -m examples.anthropic_sdk.chat --seed` |

Both create or reuse an example Scope through a stable idempotency key, save one billing decision with `--seed`, call
`prepare_context` with a 4000-byte budget, and send one request. Ready content is appended to the system prompt
unchanged; empty content sends the instructions and question without history.

Each example README lists installation, environment variables, expected output, and its offline tests.

## 1. Start a Server

Install the Server and CLI, then keep the Server running:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

From a repository checkout, `uv sync` followed by `uv run powercontext server run` is equivalent. See
[Install and run](../get-started/install-and-run.md) for release selection, storage, and platform requirements.

## 2. Run an example

The example modules live in the repository, not in the published wheel, so they must be run from a clone. The
`uv tool install` in step 1 installs only the Server and CLI. In a second terminal:

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
uv sync
```

Then, from the repository root, set the provider key and run one example:

```bash
export OPENAI_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.openai_sdk.chat
uv run python -m examples.openai_sdk.chat --seed
```

```bash
export ANTHROPIC_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.anthropic_sdk.chat
uv run python -m examples.anthropic_sdk.chat --seed
```

The first command in each pair uses a Scope with no relevant Memory, so PreparedContext is `empty` and the request
carries no history. The second saves the example decision first, so PreparedContext is `ready`:

```text
Scope: scp_...
PreparedContext: ready (601 bytes)
Answer:
...
```

Pass a different question as the first argument. Both examples also read `POWERCONTEXT_URL`, `POWERCONTEXT_TOKEN`, and
`POWERCONTEXT_SCOPE`.

## 3. Add it to your application

The provider code stays ordinary SDK code. Only the system prompt changes, and only when PowerContext returns content.

OpenAI:

```python
from openai import AsyncOpenAI
from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, PreparedContext

prepared = await powercontext.prepare_context(
    PrepareContextRequest(scope_id=scope_id, query=question, max_bytes=4000)
)
if not isinstance(prepared, PreparedContext):
    raise TypeError("ordinary context preparation returned an unexpected envelope")

system = SYSTEM_INSTRUCTIONS
if prepared.status == "ready" and prepared.content:
    system = f"{SYSTEM_INSTRUCTIONS}\n\n{prepared.content}"

completion = await openai.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ],
)
answer = completion.choices[0].message.content or ""
```

Anthropic:

```python
from anthropic import AsyncAnthropic
from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, PreparedContext

prepared = await powercontext.prepare_context(
    PrepareContextRequest(scope_id=scope_id, query=question, max_bytes=4000)
)
if not isinstance(prepared, PreparedContext):
    raise TypeError("ordinary context preparation returned an unexpected envelope")

system = SYSTEM_INSTRUCTIONS
if prepared.status == "ready" and prepared.content:
    system = f"{SYSTEM_INSTRUCTIONS}\n\n{prepared.content}"

message = await anthropic.messages.create(
    model=model,
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": question}],
    stream=False,
)
answer = "".join(block.text for block in message.content if block.type == "text")
```

Create the clients once per application and close them with `async with PowerContextClient(...)` and the provider
client's async context manager. `prepare_context` returns ordinary `PreparedContext` unless `include_reuse=True`; then
it returns a versioned reuse envelope. The examples use the ordinary response.

## PreparedContext contract

`PreparedContext` is the bounded, read-only result of one preparation request:

| Field | Meaning |
| --- | --- |
| `status` | `ready` when content was selected, `empty` when nothing relevant was available |
| `content` | Selected text for `ready`, or `None` for `empty` |
| `content_bytes` | UTF-8 size of `content` |

`max_bytes` accepts 512 to 32768 bytes and defaults to 8000. An `empty` result is normal for a new or unrelated Scope;
send the model request without history and do not invent context.

Pass `content` to the model unchanged. It is already bounded and carries its own notice that it is untrusted history,
plus citations. Do not truncate it again. Current instructions, authorization, live system state, and fresh
validation always take precedence over recalled text. Keep it out of the trusted-instruction section's authority:
treat it as data, not as a new policy.

PreparedContext is ephemeral. If a later turn needs context again, call `prepare_context` again with that turn's query.

## Configure access and Scope

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_TOKEN` | unset | Bearer token when Server authentication is enabled |
| `POWERCONTEXT_SCOPE` | unset | Existing Scope ID for the examples |

The trusted application owns these values. A Scope ID is a data partition key, not an access-control check or proof of
user identity. Choose and authorize it in trusted code; never let model output select another user's Scope. Credentials
belong in transport configuration only, never in a prompt, log line, or Memory entry.

Loopback HTTP is allowed by default. For non-loopback HTTP, use HTTPS or the explicit consent described in
[Connect to a remote Server](../operate/connect-remote-server.md). HTTPS certificate validation stays enabled.

## Retrieval and empty results

The default Server uses full-text search, so recall depends on the query sharing terms with saved Memory. For semantic
retrieval, configure [vector search](../workflows/configure-vector-search.md). Retrieval mode is a Server policy; the
native SDK caller only supplies the query and byte budget.

## Test without credentials

The example tests replace the PowerContext Server and the model endpoint with `httpx.MockTransport` while using the
real PowerContext Client and provider SDK. They check the prepare request, the system prompt and messages for `ready`
and `empty`, and that credentials appear only in request headers:

```bash
uv run pytest tests/examples/openai_sdk tests/examples/anthropic_sdk
```

These tests use simulated responses. They do not prove that a particular Server or provider returns them; use the
smoke-test commands above for that.

## Next

- [API Quick Start](api-quickstart.md): capture a Source and write Memory with the HTTP API.
- [Interfaces](interfaces.md): compare the Python Client, HTTP API, and Core SDK.
- [HTTP behavior](http-api.md): authentication, request IDs, errors, and concurrency.
- [Python Agent frameworks](../integrations/index.md#python-agent-frameworks): adapters that own the model loop.
