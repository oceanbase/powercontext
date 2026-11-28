---
title: Deep Agents
description: Add bounded PowerContext recall to a Deep Agents agent and its declarative subagents.
status: community
---

# Deep Agents

`community`

`create_powercontext_deep_agent` connects a [Deep Agents](https://github.com/langchain-ai/deepagents)
`create_deep_agent` agent to a separately running PowerContext Server. It is a thin adapter over
`PowerContextMiddleware` from `powercontext-langchain`: the middleware still performs recall and optional capture, and
the adapter only ensures the built-in `general-purpose` subagent and every declarative subagent receive it too.

Deep Agents composes its own middleware stack and compiles each declarative subagent as a separate graph, so a
middleware passed to `create_deep_agent` wraps only the main agent's model calls. Passing `PowerContextScope` through
`context=` still reaches subagents, because Deep Agents forwards the parent runtime context to the `task` tool.

## Install

Install the adapter package; it depends on `powercontext-langchain` and `deepagents`.

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

```bash
uv pip install ./integrations/deepagents
```

## Create the agent

```python
from powercontext_langchain import PowerContextScope
from powercontext_deepagents import create_powercontext_deep_agent

agent = create_powercontext_deep_agent(
    model,
    tools=application_tools,
    system_prompt="You are a deployment assistant.",
    subagents=[
        {
            "name": "researcher",
            "description": "Reads the repository and reports deployment steps.",
            "system_prompt": "You research deployment steps.",
        }
    ],
    auto_capture=True,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "How should we deploy this service?"}]},
    context=PowerContextScope(),
)
```

`create_powercontext_deep_agent` forwards every other keyword argument to `create_deep_agent`, including `backend`,
`checkpointer`, `store`, `interrupt_on`, and `response_format`.

## What the adapter changes

- The main agent receives one `PowerContextMiddleware(auto_capture=...)`. An existing `PowerContextMiddleware` in
  `middleware=` is reused instead of duplicated.
- The built-in `general-purpose` subagent receives one `PowerContextMiddleware(auto_capture=False)`; Deep Agents
  would create that subagent without recall otherwise. A harness profile that disables it
  (`GeneralPurposeSubagentProfile(enabled=False)`) is respected, including when the agent relies on Deep Agents'
  deprecated default model, so opting into PowerContext does not re-enable delegation the application turned off.
- Each declarative subagent receives one `PowerContextMiddleware(auto_capture=False)` appended to its own
  `middleware` list. An existing `PowerContextMiddleware(auto_capture=True)` is replaced by an equivalent
  recall-only copy, so a delegated turn is never captured. The adapter copies the specification and the middleware;
  the objects you pass are not mutated.
- `context_schema` defaults to `PowerContextScope`. Any other value you pass is forwarded unchanged, and PowerContext
  then falls back to its environment settings for that agent.

## Subagents

Recall and capture are deliberately asymmetric:

- **Recall** runs in the main agent and in every subagent, including the built-in `general-purpose` one, using the
  scope from `context=`. A delegated task therefore sees the same bounded history as its parent.
- **Capture** stays on the main agent. Subagents run with `auto_capture=False`, so a delegated turn is stored once,
  by the parent, even when several subagents run during the same turn.

Compiled subagents (`{"name": ..., "runnable": ...}`) and remote `AsyncSubAgent` specifications own their graph and
are returned unchanged. Add `PowerContextMiddleware` inside those graphs explicitly; the adapter does not reach into
them.

Recalled context still modifies only the current `ModelRequest`. It never enters agent state, the checkpointer, or a
subagent's transcript, and it cannot accumulate across repeated model calls or context compaction.

## Configure connection and scope

The adapter reuses the LangChain integration's client, settings, and Scope. See
[LangChain](langchain.md#configure-connection-and-scope) for `POWERCONTEXT_LANGCHAIN_*` variables, token handling,
remote-Server consent, and the rule that an explicit `PowerContextScope` wins over environment configuration.

## Capture

`auto_capture` defaults to `False`. When enabled, a successful main-agent run captures the latest user message and
final non-empty assistant response through `/v1/sources/content`; the normal Source-to-Memory extraction happens later.
Failed, cancelled, interrupted, or resumed runs are not captured, and retries do not create a second Source for the
same turn because the Source identity is derived from the scope and the turn text.

The adapter inherits LangChain's [automatic prompt privacy](index.md#automatic-prompt-privacy) through
`PowerContextMiddleware`. Each recall query, in the main agent and in every subagent, has recognized credentials
replaced by `[REDACTED]`, and a captured turn whose user message or assistant response contains a recognized credential
is skipped as a whole. Detection is best effort, so captured content is not guaranteed to be free of secrets.

## Failure behavior

Recall and capture are best-effort. An unavailable Server, an invalid response, or request validation failure does not
replace the model response or stop the agent. HTTP 401 and 403 are logged once at error level without content or token
values; other failures are logged at debug level.

## Leave prompt-time context to the gateway

When the agent sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each model call and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. `create_powercontext_deep_agent` adds `PowerContextMiddleware` to the main
agent, the built-in `general-purpose` subagent, and every declarative subagent, and the middleware adds such a block to
each model call. The adapter has no option to leave recall out, so build an agent whose model goes through the gateway
with Deep Agents' own `create_deep_agent`, and remove any `PowerContextMiddleware` from `middleware=` and from the
subagent specifications. The middleware never stores recalled context in agent state, so an existing thread can
continue behind the gateway.

The application owns the model client. Point it at the gateway's Responses route, and name the conversation in an
`X-PowerContext-Conversation-ID` header:

```python
import os

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI


def gateway_agent(thread_id: str):
    model = ChatOpenAI(
        model="approved-model",
        base_url="http://127.0.0.1:8787/gateway/v1",
        api_key=os.environ["PC_GATEWAY_KEY"],
        use_responses_api=True,
        default_headers={"X-PowerContext-Conversation-ID": thread_id},
    )
    return create_deep_agent(model, tools=application_tools, subagents=subagents)
```

Without the adapter, the gateway takes over what the adapter did:

- Recall: the gateway prepares context for every model request it carries. A declarative subagent without its own
  `model` uses the main agent's model, so its requests carry the same header; its user message is the task
  description, so the gateway prepares context for the task. A subagent that names its own `model`, and a compiled
  subagent's graph, receive context only when their model also points at the gateway.
- Capture: `auto_capture` no longer applies. The gateway captures each released response that does not call a tool
  as one turn, from the request's last user message to that response. A subagent's final answer is therefore captured
  as a turn of its own, beside the main agent's turn; the adapter's rule that only the main agent captures does not
  apply.
- Tools: neither this adapter nor `powercontext-langchain` offers Memory tools, so there is no tool to keep or remove.

`PC_GATEWAY_KEY` holds the gateway's `PC_GATEWAY_API_KEY` value. Use one stable ID per conversation, such as the
checkpointer's thread ID, of 1 to 256 visible ASCII characters; never one ID for every conversation. A combined
deployment (`PC_GATEWAY_CORE_UDS`) prepares once per user message of a conversation and replays that context to the
tool-loop steps of the message. Without the header, it infers the conversation from the opening of the request:
`ChatOpenAI` sends the thread's full history on every call unless `use_previous_response_id=True`, and a history that
opens with system messages and a user message qualifies. Without the header, a subagent task gets its own inferred
conversation, because it starts a new history; a subagent with `mode: "fork"` carries the parent's history and shares
its inferred conversation. The header still keeps apart conversations that open the same way, and gives capture and
prefix extraction a conversation identity that stays the same across gateway restarts; an inferred ID is local to the
gateway process and serves only preparation replay. A standalone gateway (`PC_GATEWAY_CORE_BASE`) injects only with
`PC_GATEWAY_INJECTION=on`. See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection).

Use the Responses route (`use_responses_api=True`). There the gateway calls the model without streaming, captures the
checked response, and replays it as a stream to a client that streams. On the Chat Completions route, an enforcing Core
withholds a streamed response and the gateway returns HTTP 400. With a non-enforcing Core, the gateway releases the
stream unchanged and, with `PC_GATEWAY_SEDIMENT=on`, captures the completed turn from it.

Configure Core and the gateway as well:

- Run Core in a non-enforcing privacy mode (`disabled`, `shadow`, or `advisory`) unless every request meets the
  enforcing rules: an enforcing Core withholds a Responses request without `store: false` or without an output limit
  within the target's token budget, and the gateway returns HTTP 400. Restart the gateway after changing the mode,
  because it discovers the mode at startup.
- For this Responses route, set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to a Core target whose endpoint,
  `openai-responses` protocol and model match the request. Without it, the route returns HTTP 501
  `responses_route_disabled`. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to the provider key and
  `PC_GATEWAY_SEDIMENT=on`. Do not set `PC_GATEWAY_UPSTREAM_URL*`; these legacy variables prevent startup.
- Set a nonempty `PC_GATEWAY_API_KEY` even on loopback, and set `PC_GATEWAY_CORE_TOKEN` to an authenticated service
  identity. Core access control must be `enforced`; grant it `gateway.user` on the selected Scope and `server.observer`
  on the Server resource for `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` (default `powercontext`). Because this guide
  enables capture, grant `scope.contributor` (which includes `scope.read`) rather than `scope.viewer`.
  See [Gateway authentication and target requirements](../operate/deploy-server.md#gateway-model-destination).
- The gateway prepares and captures every conversation in the one Scope that `PC_GATEWAY_DEFAULT_SCOPE` names. Set it to
  the Scope the adapter used, so that the gateway recalls the Memory captured before.

## Runnable examples

`integrations/deepagents/examples/quickstart.py` runs one captured turn against a Server and an OpenAI-compatible
model endpoint. `integrations/deepagents/examples/full_flow.py` also flushes the captured turn into Memory, waits for
the extracted entry to become searchable, and asks a paraphrased question in a new session. The full flow needs a
generation model and an embedding model configured on the Server; the quickstart only needs recall.

## Validated versions

Deterministic tests (21) run against scripted models and an in-process PowerContext Server over SQLite: middleware
injection, recall on declarative subagents and on the built-in `general-purpose` subagent, capture normalization for a
subagent that arrives with `auto_capture=True`, single-capture ownership, streaming (`astream`), interrupt/resume,
repeated model calls without context accumulation, and Scope isolation between runs.

A real-model run used PowerContext Server 0.1.dev1 (HTTP + SQLite), `deepagents` 0.7.39, `langchain` 1.5.0,
`langchain-core` 1.8.1, `langchain-openai` 1.8.0, and vLLM 0.29.0 serving Qwen2.5-0.5B-Instruct:

- recall reached the system prompt and the answer followed the stored rule;
- `auto_capture=True` added one Source for the completed turn;
- a later turn in the same Scope recalled the rule again.

Streaming and interrupt/resume were also exercised with that real model: `astream` delivered 17 chunks and stored one
Source, and an `interrupt_on` run paused before the tool, then stored exactly one Source after
`Command(resume={"decisions": [{"type": "approve"}]})`.

Automatic Source-to-Memory extraction and semantic recall were validated with two vLLM-served models: Qwen3-4B for
generation and bge-small-en-v1.5 (`--runner pooling`) for embeddings. A captured turn was flushed into Memory, and a
paraphrased query ("What deployment command should we use?") returned a non-empty prepared context that the agent
recalled in a new session. With only Qwen2.5-0.5B or Qwen3-0.6B the extraction produced an unusable or empty result,
so these models are too small for the extraction schema.

JavaScript and Managed Deep Agents were not exercised: the JavaScript `deepagents` package exists (1.16.5) but
PowerContext ships no JavaScript adapter, and Managed Deep Agents is a hosted LangSmith deployment.
