---
title: FAQ
description: Common questions about Artifact families, evidence, review, and Agent integrations.
---

# Frequently asked questions

This page answers questions that often come up when learning PowerContext. For a deeper introduction to the
underlying model, see [Core concepts](./core-concepts.md). For step-by-step procedures, follow the links to the
relevant workflow guides.

## Artifact families at a glance

**Q: How do the Artifact families differ, and when should I use each?**

Imagine an Agent fixing a CSV amount-parsing bug in `amount.py`. Each Artifact family can capture or configure
a different part of that work:

| Family | Role in the bug-fix story | When to use it |
| --- | --- | --- |
| **Atomic Memory** | Stores a project constraint: "amounts are stored as integer cents; reject anything with more than two decimal places." | Store and update individual facts, preferences, decisions, or rules for retrieval in later sessions. |
| **Topic Memory** | Organizes precision rules, edge cases, and supporting discussions under "CSV amount handling," with a summary and detailed content. | Keep related information together, search summaries first, and expand details as needed. |
| **Profile** | Summarizes the current Scope's background and working preferences: "This is a financial data import project; the team prefers Chinese explanations and reproducible validation steps." | Provide overall background about a user, team, or project for later interactions. |
| **Handoff** | Captures "I confirmed the bug is in `cents()`; tomorrow the next Agent should run the failing tests and patch it." | Transferring the current state of unfinished work to another Agent or session. |
| **Experience** | Records the verified lesson: "Directly calling `int(Decimal(text) * 100)` silently truncated `1.999`; we now check precision before converting." | Preserving what was learned from a specific situation, with evidence. |
| **Skill** | Packages the reusable procedure: "How to verify amount-conversion fixes — run the test fixture, check edge cases, record results." | An operational procedure other Agents can load and follow. |
| **Prompt** | Configures this Scope's memory extraction instructions, such as "preserve currency, precision, and applicable conditions when extracting amount constraints." | Customize supported operation prompts and retain configuration revisions. |

Choose what to save based on how it will be used later. Each family has its own write or generation workflow;
capturing a Source does not automatically produce every Artifact in the table.

For details, see [Atomic Memory](../workflows/atomic-memory.md), [Topic Memory](../workflows/topic-memory.md),
[Scope Profiles](../workflows/use-profiles.md), [Memory and Handoff](../workflows/memory-and-handoff.md),
[Experience and Skill lifecycle](../workflows/experience-and-skill-lifecycle.md), and [operation prompts](../workflows/manage-prompts.md).

## Sources, Candidates, and the knowledge pipeline

**Q: Are Sources, Candidates, and Artifacts stages of one mandatory pipeline?**

Sources preserve input evidence, while Artifacts preserve versioned results. Workflows that require review
propose changes through Candidates:

```text
Source (evidence) ──┐
                    ├──→ Candidate (proposal) ──→ Review ──→ approved Artifact
existing Artifact ──┘
```

- A **Source** is raw evidence — a conversation turn, a tool result, a document.
- A **Candidate** is a proposal derived from evidence and/or existing Artifacts. It is `pending` until reviewed.
- An **Artifact** is a committed result; each Revision is immutable and has an exact reference.

With generation models and processing configured, Atomic Memory and Topic Memory can be generated and committed
directly from Sources. Profile generation either commits automatically or requires review, according to policy.
Generated Experience and Skill proposals are committed after Candidate approval.

Manual writes use the interfaces supported by each family. For example, `remember_memory` and the generic
Artifact API can create Atomic Memory without model extraction or Candidate review.

**Q: Can a pending Candidate be recalled into `PreparedContext`?**

No. A pending Candidate has not been committed as a formal Artifact and is excluded from recall. `PreparedContext`
selects committed Artifacts subject to authorization, lifecycle, and context configuration. For example, ordinary
memory recall uses current active Atomic Memory revisions; Profile must be explicitly selected in the assembly
configuration.

**Q: I approved a Skill Candidate. Why can't my Agent use it yet?**

Approval, installation, and execution are three separate steps:

1. **Approval** creates an immutable Skill Revision in PowerContext.
2. **Export / install** places the exact Revision in a directory the Agent host discovers. For Codex, follow
   [Create and export a Skill](../workflows/create-and-export-skill.md) with `powercontext skill export --target codex`.
   Downloading a package through `download_skill_package` alone does not establish host installation or discovery.
3. **Discovery and use** happen on the Agent host. Verify that it discovers the exported `SKILL.md`; loading it does
   not itself authorize execution of its instructions.

Approving a Skill does not install it; installing it does not execute it. Each step is explicit so that you keep
control over what runs where.

## Atomic Memory

**Q: What's the difference between Source and Atomic Memory?**

- A **Source** is raw evidence: a chat message, a file, an HTTP trace. It is stored as-is.
- **Atomic Memory** stores an individual reusable fact, preference, decision, or constraint. An application can
  write it explicitly, or a model can extract it from Sources and compare it with related memories to decide
  whether to create, update, or merge.

Sources preserve the evidence; Atomic Memory preserves the distilled content and references its supporting evidence.

**Q: Can `search_memory` retrieve memories created through the generic Artifact API?**

Yes. Use `family=atomic-memory` when creating the Artifact. Both paths below create independent Atomic Memories
in the specified Scope:

| Path | Result |
| --- | --- |
| `POST /v1/scopes/{scope_id}/artifacts` with `family=atomic-memory` | Creates an active memory and updates its current search projection. |
| `POST /v1/memory/remember` | Creates an active Atomic Memory through the compatibility endpoint and updates the same search projection. |

Successfully committed active memories are eligible for `search_memory` and `prepare_context`; actual results
still depend on the Scope, authorization, and query conditions. Generic creation with `family=memory` is no longer
supported. Existing collection databases must first complete the [offline migration](../operate/atomic-memory-migration.md).

**Q: When I revise an Atomic Memory, does the old version get deleted?**

No. Revising creates a new content Revision and advances the current head while preserving the memory's lifecycle
state. Ordinary search uses the current content of active memories. Earlier versions remain readable through
`GET /v1/scopes/{scope_id}/artifacts/atomic-memory/{id}/revisions/{n}`. Restoring historical content creates a new
Revision and preserves existing history. See [Atomic Memory](../workflows/atomic-memory.md).

## Handoff

**Q: Is a Handoff created manually or by the Agent?**

Either. An application can call `handoff_current_work` directly when a human decides it's time to transfer work.
An Agent can also propose a Handoff as part of its tool flow. In both cases the flow is the same: a temporary
preview is returned first, and only `commit_handoff` writes a durable Revision.

**Q: How does the receiving Agent know what to do?**

The Handoff itself carries structured fields: the objective, the current state with evidence citations, the
disposition (`continuable` / `complete`), the next action, and any omissions. The receiving Agent reads this
content rather than the full prior conversation.

## Experience and Skill

**Q: What's the difference between an Experience and a Skill?**

- An **Experience** is a verified narrative: situation → action → outcome → lesson. It tells future readers
  *what happened and what was learned*.
- A **Skill** is a reusable procedure: name, description, instructions, validation steps. It tells future Agents
  *how to do something*.

A common pattern is that an approved Experience is used to generate a Skill Candidate, which is then reviewed
and (once approved) becomes installable.

**Q: Does PowerContext install Skills automatically?**

No. Skills live in the PowerContext store as Artifacts. To make one usable by an Agent, the application (or a
Remote Skill Receiver) must explicitly download and install it to the Agent's working directory.

## Middleware, tools, and MCP

**Q: I see "Middleware" everywhere in the tutorials. What is it?**

Middleware adds behavior around an Agent's model or tool calls. PowerContext's LangChain adapter,
`PowerContextMiddleware`, uses LangChain's public middleware API to prepare bounded context from the latest
non-empty user message before a model call. When recall returns content, it adds an untrusted historical
context block to that model request, without replacing the Agent's core loop.

**Q: When should I use Middleware vs MCP tools?**

First distinguish automatic recall from explicit tool calls; then choose how to connect those tools:

| Integration | Behavior and connection |
| --- | --- |
| **LangChain Middleware** | Requests context over HTTP before eligible model calls, without waiting for the model to request a search. |
| **LangGraph tools** | `powercontext_tools()` supplies native LangChain tools for explicit Memory operations through the Python HTTP Client. No MCP connection is involved. |
| **MCP tools** | A configured MCP client discovers and invokes the Server's exposed tools. The host application can call them directly or offer them to a model. |

For example, Middleware can supply the CSV project's amount constraints, while an explicit tool saves a new
user-confirmed rule. Tool-driven Memory access does not require MCP; MCP is a protocol, not a rule about who
triggers an operation. See [LangChain](../integrations/langchain.md), [LangGraph](../integrations/langgraph.md),
and [Choose an interface](../develop/interfaces.md) for setup and available operations.

**Q: Does integrating PowerContext require rewriting my Agent?**

Not for the supported LangChain integration. Keep your model and application tools, install the adapter, and
configure its connection to a running PowerContext Server. The following wiring example assumes `model` and
`application_tools` already exist, `server_url` is the Server's HTTP base URL, `scope_id` identifies an existing
Scope you may access, `token` is a bare bearer token or `None` for an unauthenticated Server, and `question` is
the current user input. Both examples assume HTTPS for a remote Server or HTTP at a loopback address.
Where access control is enforced, the caller also needs permission to resolve the Scope and perform the
requested Memory operations; see [Scopes and access](../workflows/scopes-and-access.md).
The Agent calls use the async API:

```python
from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware, PowerContextScope

agent = create_agent(
    model,
    tools=application_tools,
    middleware=[PowerContextMiddleware()],
    context_schema=PowerContextScope,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": question}]},
    context=PowerContextScope(scope_id=scope_id, base_url=server_url, token=token),
)
```

This Scope object configures the LangChain middleware, not the separate LangGraph tools. Fields left as
`None` fall back to the middleware's settings; omit its token setting when using an unauthenticated Server.

**Q: Does Middleware injection pollute my conversation history?**

The injected context block changes only the current model request; the middleware does not append it to Agent
state or a checkpointer. Later model calls can request context again using the latest user message. Ordinary
conversation and tool messages remain subject to the application's history policy, and information repeated
in an assistant's answer can remain in that history. Optional completed-turn Source capture is a separate,
default-off feature; see the [LangChain recall and capture lifecycle](../integrations/langchain.md).

**Q: What is MCP, and when does it matter?**

MCP (Model Context Protocol) standardizes how compatible clients discover and call tools exposed by a Server.
Use PowerContext's MCP endpoint when your host supports MCP and you want that tool interface. The host still
needs the connection, authentication, and Scope configuration appropriate to the exposed operations.
`powercontext_tools()` is not an MCP client: its Memory tools call HTTP endpoints such as
`/v1/memory/remember`, and work with MCP disabled. The MCP tool catalog is a separate, curated interface rather
than the complete HTTP API; see [Choose an interface](../develop/interfaces.md).

**Q: Can I combine Middleware with explicit Memory tools?**

Yes, but their configurations must agree. The following example combines **LangChain Middleware and
HTTP-backed LangGraph tools**, not MCP tools. Each package defines its own `PowerContextScope` class; the
LangGraph tools do not recognize a LangChain Scope object and instead fall back to `POWERCONTEXT_LANGGRAPH_*`.
Passing `PowerContextScope(scope_id=...)` from the LangChain package therefore does not configure tool writes.

For a single-Scope application process, set both integrations' connection, Scope, and token settings from the
same values before starting the Agent. This example uses the `model`, `server_url`, `scope_id`, `token`, and
`question` described above and deliberately passes neither package's Scope object:

```python
import os

from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware
from powercontext_langgraph import powercontext_tools

connection = {"BASE_URL": server_url, "SCOPE_ID": scope_id, "TOKEN": token}
for prefix in ("POWERCONTEXT_LANGCHAIN", "POWERCONTEXT_LANGGRAPH"):
    for key, value in connection.items():
        name = f"{prefix}_{key}"
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

agent = create_agent(
    model,
    tools=powercontext_tools(),
    middleware=[PowerContextMiddleware()],
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": question}]},
)
```

Obtain credentials from your application's secret configuration, not a hard-coded token. Environment settings
are process-wide: configure them once at startup, not per request in a concurrent multi-Scope application.
Do not assume a later LangChain Scope override also redirects the tools. Verify both the recalled Scope and
the stored entry's Scope. See [LangChain connection and Scope settings](../integrations/langchain.md#configure-connection-and-scope)
and [LangGraph connection settings](../integrations/langgraph.md#configure-the-connection).

## Importing and forking external Skills

**Q: I already have a Skill package. Should I import it or fork it?**

Suppose the team has a CSV amount-checking package with `SKILL.md`, a check script, and reference notes.
Choose the mode according to whether you want to preserve that package or propose different instructions:

| Mode | Use it when | What happens |
| --- | --- | --- |
| **`import`** | You want to bring the existing package under management without rewriting its files. | The Runtime proposes the captured, validated package directly, preserving its file paths and bytes. No generation model is required. |
| **`fork`** | You want project-specific instructions based on the external package. | A configured generator uses the captured package as evidence for a new proposal. The generated package is not guaranteed to retain the original scripts or resources. |

Neither mode edits the external package. A returned Candidate is pending until Review approval creates a new
managed Skill Artifact; fork can also return `no_op` without a Candidate. Here, fork means generating a Skill
proposal, not making a GitHub repository fork. See [External Agent-native Skills](../develop/interfaces.md#external-agent-native-skills)
for the interface and [Install Skills in Agents](../workflows/configure-agent-skill-targets.md) for target setup.

**Q: If I edit the external files after importing, does the managed Skill update automatically?**

No. The external registration identifies a scanned package using its fingerprint; an approved managed Skill
Revision preserves the imported snapshot. Changing a script or reference file can change that fingerprint,
even if `SKILL.md` is untouched. Resolving the old fingerprint then reports `unavailable`, and importing it is
rejected rather than silently selecting the changed package. Rescan and explicitly select the new fingerprint
to import the new content; this does not automatically replace the previously approved Skill.

An unavailable external registration does not mean that the approved managed Revision was deleted. You can
still read or download that exact Revision. In the CSV example, adding a non-finite-value rule to the external
reference notes does not add it to the already imported copy.

**Q: A fork returned a Candidate. Can it replace the original package now?**

Not on that evidence alone. Capturing Source evidence from the original package does not mean its scripts and resources
are included in the generated proposal. Inspect the proposed package, including whether commands in its
instructions refer to files that are actually present. In the CSV example, adding an instruction to check
non-finite values does not prove that the check script was retained, updated, or run.

Package validation and matching digests establish structure and content identity, not correct behavior on
your inputs. Review the proposal and validate the intended task in an authorized, isolated workspace before
relying on it. Approval, export or installation, and execution remain separate steps; see
[Review Candidates](../workflows/review-candidates.md) and
[Experience and Skill lifecycle](../workflows/experience-and-skill-lifecycle.md).

## Still stuck?

- For the underlying model, see [Core concepts](./core-concepts.md).
- For end-to-end procedures, see the [Workflows](../workflows/index.md) section.
- For API details, see the OpenAPI contract in `openapi/powercontext.yaml`.
- If something here is unclear or wrong, please
  [open an issue](https://github.com/oceanbase/powercontext/issues/new) so we can improve this page.
