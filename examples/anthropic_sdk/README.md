# PowerContext with the Anthropic Python SDK

[中文](README.zh.md)

This example answers one question with the official [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python).
Before the model request it asks PowerContext for PreparedContext and adds the returned history to the system prompt.
When PowerContext has nothing relevant, the request goes ahead without history.

[`chat.py`](chat.py) uses the async `PowerContextClient` and `AsyncAnthropic`:

1. Create or reuse the example Scope through a stable idempotency key, or use `POWERCONTEXT_SCOPE`.
2. With `--seed`, remember one billing decision in that Scope.
3. Call `prepare_context` with the question as the query and a 4000-byte budget.
4. Send one `messages.create` request. If PreparedContext is `ready`, its `content` is appended to the system prompt
   unchanged; if it is `empty`, the request carries only the instructions and the question.

## Install

You need Python 3.11+, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), and a running Server.
From a clone of this repository:

```bash
uv sync
uv run powercontext server run
```

Keep the Server running and use a second terminal for the commands below. To use the script in your own project
instead, install `powercontext[client]` and `anthropic`, copy `chat.py`, and run it with `python chat.py`.

## Configure

| Variable | Required | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Yes | Read by `AsyncAnthropic`. |
| `ANTHROPIC_BASE_URL` | No | Anthropic-compatible endpoint; read by `AsyncAnthropic`. |
| `ANTHROPIC_MODEL` | No | Messages model; defaults to `claude-sonnet-4-5`. |
| `POWERCONTEXT_URL` | No | Server URL; defaults to `http://127.0.0.1:8000`. |
| `POWERCONTEXT_TOKEN` | No | Bearer token when Server authentication is enabled. |
| `POWERCONTEXT_SCOPE` | No | Existing Scope ID. Without it the script creates or reuses the `anthropic-sdk-example` Scope. |

The trusted application process owns these values. They are sent only as transport credentials and never placed in a
prompt, a log line, or Memory. In a real application, choose and authorize the Scope in trusted code; never let model
output select it.

## Run

Manual smoke test against the running Server and a real model:

```bash
export ANTHROPIC_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.anthropic_sdk.chat
uv run python -m examples.anthropic_sdk.chat --seed
```

The first command uses a new Scope, so PreparedContext is empty and the model answers without history. The second
command saves the billing decision first, so PreparedContext is ready. Expected output has this shape; the Scope ID,
byte count, and answer text vary:

```text
Scope: scp_...
PreparedContext: empty (0 bytes)
Answer:
...
```

```text
Scope: scp_...
PreparedContext: ready (601 bytes)
Answer:
...
```

Pass a different question as the first argument. The default Server uses full-text search, so recall depends on the
question sharing terms with saved Memory; configure
[vector search](../../docs/en/docs/workflows/configure-vector-search.md) for semantic retrieval.

## Treat recalled content as history

PreparedContext is bounded, read-only text that already carries a notice marking it as untrusted history, plus exact
citations. Pass it to the model unchanged and do not trim it again. Current instructions, authorization, live system
state, and fresh validation take precedence over anything it says.

## Test without credentials

The offline tests replace both HTTP services with `httpx.MockTransport`. They check the prepare request, the system
prompt and messages sent through the real Anthropic SDK for ready and empty PreparedContext, and that credentials
appear only in the `x-api-key` and `Authorization` headers:

```bash
uv run pytest tests/examples/anthropic_sdk
```

These tests use simulated responses. They do not show that a particular Server or model provider returns them; use the
smoke test above for that.

For the shared integration pattern, including when to use a native SDK instead of an Agent host or framework adapter,
see [Use a native model SDK](../../docs/en/docs/develop/native-sdk.md).