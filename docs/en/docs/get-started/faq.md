---
title: FAQ
description: Common questions about Memory, Handoff, Experience, Skill, and the knowledge lifecycle.
---

# Frequently asked questions

This page answers questions that often come up when learning PowerContext. For a deeper introduction to the
underlying model, see [Core concepts](./core-concepts.md). For step-by-step procedures, follow the links to the
relevant workflow guides.

## The four Artifact families at a glance

**Q: I keep seeing Memory, Handoff, Experience, and Skill. When should I use each?**

The four families play different roles in the same work loop. Imagine an Agent fixing a CSV parsing bug in
`amount.py`:

| Family | Role in the bug-fix story | When to use it |
| --- | --- | --- |
| **Memory** | Stores the durable project constraint: "amounts are stored as integer cents; reject anything with more than two decimal places." | A fact, decision, or rule that should hold across many sessions and Agents. |
| **Handoff** | Captures "I confirmed the bug is in `cents()`; tomorrow the next Agent should run the failing tests and patch it." | Transferring the current state of unfinished work to another Agent or session. |
| **Experience** | Records the verified lesson: "Directly calling `int(Decimal(text) * 100)` silently truncated `1.999`; we now check precision before converting." | Preserving what was learned from a specific situation, with evidence. |
| **Skill** | Packages the reusable procedure: "How to verify amount-conversion fixes — run the test fixture, check edge cases, record results." | A reusable, validated recipe other Agents can load and follow. |

A single bug fix often produces all four: the constraint it honors (Memory), the state it hands over (Handoff),
the lesson it learned (Experience), and the procedure worth repeating (Skill).

For details, see [Memory and Handoff](../workflows/memory-and-handoff.md) and
[Experience and Skill lifecycle](../workflows/experience-and-skill-lifecycle.md).

## Sources, Candidates, and the knowledge pipeline

**Q: Are Sources, Candidates, and approved Artifacts stages of one mandatory pipeline?**

No. They are independent values that *can* be combined, but capturing evidence does not by itself produce
approved knowledge:

```text
Source (evidence) ──┐
                    ├──→ Candidate (proposal) ──→ Review ──→ approved Artifact
existing Artifact ──┘
```

- A **Source** is raw evidence — a conversation turn, a tool result, a document.
- A **Candidate** is a proposal derived from evidence and/or existing Artifacts. It is `pending` until reviewed.
- An **Artifact** is what you get after approval — an immutable Revision with a stable reference.

You can also write Memory directly with `remember_memory` without going through Source extraction or Candidate
review.

**Q: Can a pending Candidate be recalled into `PreparedContext`?**

No. Pending Candidates are excluded from recall. Only approved, current Revisions participate in
`PreparedContext`. This prevents unreviewed model output from silently becoming the Agent's working context.

**Q: I approved a Skill Candidate. Why can't my Agent use it yet?**

Approval, installation, and execution are three separate steps:

1. **Approval** creates an immutable Skill Revision in PowerContext.
2. **Export / install** copies an exact Revision to the Agent host (for example via Remote Skill distribution or
   manual `download_skill_package`).
3. **Execution** happens when the Agent's host discovers and loads the installed `SKILL.md`.

Approving a Skill does not install it; installing it does not execute it. Each step is explicit so that you keep
control over what runs where.

## Memory

**Q: What's the difference between Source and Memory?**

- A **Source** is raw evidence: a chat message, a file, an HTTP trace. It is stored as-is.
- **Memory** is refined knowledge: a decision, a constraint, a fact. It has been deliberately written (either by
  the application or by an approved extraction pipeline).

Sources are inputs; Memory is the result of curation.

**Q: I created a Memory through the generic Artifact API but it doesn't show up in `search_memory`. Why?**

There are two write paths with different effects:

| Path | Result |
| --- | --- |
| `POST /v1/scopes/{id}/artifacts` with `family=memory` | Creates a standalone Memory Artifact; **not** part of daily recall. |
| `POST /v1/memory/remember` | Writes to the Scope's default Memory; **is** searched by `search_memory` and used in `prepare_context`. |

Use `/v1/memory/remember` for facts you want the Agent to actually recall.

**Q: When I revise a Memory, does the old version get deleted?**

No. Revisions are immutable. Revising a Memory creates a new Revision and retires the old one from active
recall, but the old Revision remains readable through `GET .../revisions/{n}`. This gives you an audit trail and
a way to recover earlier wording.

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
