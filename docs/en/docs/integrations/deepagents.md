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
the adapter only ensures every declarative subagent receives it too.

Deep Agents composes its own middleware stack and compiles each declarative subagent as a separate graph, so a
middleware passed to `create_deep_agent` wraps only the main agent's model calls. Passing `PowerContextScope` through
`context=` still reaches subagents, because Deep Agents forwards the parent runtime context to the `task` tool.

## Install

Install the LangChain integration first; the adapter ships in the same package and imports `deepagents` lazily.

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

```bash
uv pip install ./integrations/langchain
uv pip install "deepagents>=0.7,<0.8"
```

## Create the agent

```python
from powercontext_langchain import PowerContextScope
from powercontext_langchain.deepagents import create_powercontext_deep_agent

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
- Each declarative subagent receives one `PowerContextMiddleware(auto_capture=False)` appended to its own
  `middleware` list. The adapter copies the specification; the mapping you pass is not mutated.
- `context_schema` defaults to `PowerContextScope`. Any other value you pass is forwarded unchanged, and PowerContext
  then falls back to its environment settings for that agent.

## Subagents

Recall and capture are deliberately asymmetric:

- **Recall** runs in the main agent and in every declarative subagent, using the scope from `context=`. A delegated
  task therefore sees the same bounded history as its parent.
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

## Failure behavior

Recall and capture are best-effort. An unavailable Server, an invalid response, or request validation failure does not
replace the model response or stop the agent. HTTP 401 and 403 are logged once at error level without content or token
values; other failures are logged at debug level.

## Validated versions

Deterministic tests (14) run against scripted models and an in-process PowerContext Server over SQLite: middleware
injection, subagent recall, single-capture ownership, streaming (`astream`), interrupt/resume, repeated model calls
without context accumulation, and Scope isolation between runs.

A real-model run used PowerContext Server 0.1.dev1 (HTTP + SQLite), `deepagents` 0.7.39, `langchain` 1.5.0,
`langchain-core` 1.8.1, `langchain-openai` 1.8.0, and vLLM 0.29.0 serving Qwen2.5-0.5B-Instruct:

- recall reached the system prompt and the answer followed the stored rule;
- `auto_capture=True` added one Source for the completed turn;
- a later turn in the same Scope recalled the rule again.

Streaming and interrupt/resume were also exercised with that real model: `astream` delivered 17 chunks and stored one
Source, and an `interrupt_on` run paused before the tool, then stored exactly one Source after
`Command(resume={"decisions": [{"type": "approve"}]})`.

Automatic Source-to-Memory extraction was configured against local models but did not produce Memory: Qwen2.5-0.5B
returned a structured result the Server rejected (`InvalidInferenceOutputError`) and Qwen3-0.6B returned an empty
candidate list. Extraction needs a generation model that reliably follows the extraction schema, and semantic ranking
needs an embedding model; neither was available here, so both paths stay unvalidated.

JavaScript and Managed Deep Agents were not exercised: the JavaScript `deepagents` package exists (1.16.5) but
PowerContext ships no JavaScript adapter, and Managed Deep Agents is a hosted LangSmith deployment.
