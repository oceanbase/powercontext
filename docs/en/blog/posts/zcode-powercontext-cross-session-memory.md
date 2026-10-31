---
slug: zcode-powercontext-cross-session-memory
title: "A new conversation shouldn't mean starting over"
description: "Starting with two automatically generated memories of AgentMeld's working conventions, I walk through prompt capture, memory recall, and work handoffs in my ZCode integration with PowerContext."
cover: assets/blog/zcode-powercontext-cross-session-memory/cover.png
cover_alt: A blue ribbon carries project memories between two workspaces, continuing context across sessions.
date: 2026-10-27
authors:
  - jackie-cqz
categories:
  - Engineering
draft: false
pin: false
source: https://jackie-cqz.github.io/posts/memory-across-sessions/
---

*ZCode × PowerContext: integrating memory across sessions*

In earlier agent development work, I worked on memory capture and storage and drew on [Hermes Agent's memory design](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory). Its built-in system keeps user preferences and communication habits in `USER.md`, and environment details and project conventions in `MEMORY.md`, loading them when a session starts. It made lasting preferences more concrete to me: some things should not need explaining every time.

Recently, our team's work on a personal desktop agent has been moving quickly. The features I am responsible for span different branches, and some involve different harnesses. I can switch back to the code, but a fresh conversation often means explaining the interface conventions, design choices, and task progress all over again.

That gave me a concrete goal when integrating ZCode with PowerContext: could project background we had already settled remain useful in the next task? ZCode handles the work at hand, PowerContext preserves reusable materials, and the plugin connects them to ordinary conversations.

My work focused on the ZCode plugin: hook-based prompt capture and context preparation, native MCP and skill connections, Scope workflows, installation diagnostics, and acceptance checks. I will start with an actual automatic memory-generation example, then explain those choices and how to check recall in a fresh conversation.

## Start with two working conventions from AgentMeld

For this acceptance check, I used my own project, [AgentMeld](https://github.com/jackie-cqz/AgentMeld), a multi-agent collaboration workspace for AI coding teams. I added two habits for future development in an ordinary conversation. Translated into English, the message was:

> EXAMPLE PROMPT
>
> For future AgentMeld work, plan first, have multiple workers implement, and have multiple reviewers review. Commit after each small piece of work.

I then verified that automatically generated memory preserved both conventions:

- **Working method**: plan first, have multiple workers implement, then have multiple reviewers review.
- **Commit habit**: commit after each small piece of work, keeping commits incremental.

**WORKFLOW ILLUSTRATION**

1. **Session A · Describe working habits**

   Describe AgentMeld's collaboration workflow and incremental commit habit in an ordinary conversation.

2. **Server · Generate memory**

   Background processing generates memory. Both the working method and commit habit were verified in the result.

3. **Fresh session · Check recall**

   Next, open a new task in the same Scope. Ask only for the project's working method and commit habit, then inspect recalled material and sources.

What I added and confirmed at the time was this automatically generated memory of the two conventions. Both matter in everyday development: how implementation and review are organized, and how frequently changes should be committed. The steps later in this article show how to check their retrieval in a fresh conversation.

These are the details I want a memory system to preserve: working habits already explained in chat, turned into project material with a source and a way to read it, so the next task can use them.

## Keep the things worth remembering

What a project needs to carry forward might be a convention, the reasoning behind a decision, or the state of unfinished work. Keeping its source makes it easier to check later.

PowerContext organizes that information through these kinds of material:

| Concept | Role in a project |
| --- | --- |
| Source | Preserves original material, such as a project description submitted by a user |
| Memory | Preserves reusable project knowledge and its sources |
| Scope | Organizes context for different projects or areas of work |
| Handoff | Records an ongoing task's objective, progress, blockers, next steps, and evidence |

The AgentMeld description above can first be captured as a Source. Background processing can then extract reusable working conventions and commit habits, which a later task retrieves for its current question.

Continuing unfinished work requires more than a lasting convention. A receiver also needs to know what changed, what was checked, and what remains incomplete. Handoff organizes that information.

PowerContext Server stores these records. The ZCode plugin adapts them to the host so they can participate in the normal workflow.

## Bring memory into everyday work

I want people to keep discussing projects with their agents as usual. Tools should be available for explicit searches and saves, while a new message can also bring back relevant materials from earlier work.

ZCode's plugins, lifecycle hooks, MCP, and skills provide places to connect that experience. This integration uses three main entry points:

| Entry point | Responsibility |
| --- | --- |
| Hook | Prepares context and captures material at lifecycle events such as prompt submission |
| MCP | Lets the model explicitly search or save memory and manage handoffs |
| Skill | Guides when to use these capabilities and how to handle their results |

The overall path looks like this:

```text
User
  ↓
ZCode
  ├─ Lifecycle hooks ── HTTP ──────┐
  ├─ Native MCP client ────────────┤
  └─ Skill: guides tool use        │
                                  ↓
                         PowerContext Server
                           ├─ Source
                           ├─ Memory
                           ├─ Scope
                           └─ Handoff
```

The plugin is installed on the ZCode side. The Server is a separate process that must be started independently.

One choice I made was to leave storage, retrieval, and background processing with PowerContext Server, while keeping the ZCode plugin focused on host adaptation. Memory capabilities and conversation entry points can then evolve in their respective places, without a second memory store inside the plugin.

The plugin source lives in [`integrations/zcode/plugins/powercontext`](https://github.com/oceanbase/powercontext/tree/master/integrations/zcode/plugins/powercontext), with this main structure:

```text
powercontext/
├─ .zcode-plugin/
├─ hooks/
├─ scripts/
├─ shared/
├─ skills/
└─ .mcp.json
```

Hook declarations, the MCP connection, and the skill are installed together, without requiring users to assemble them individually.

## Find today's context in the next conversation

Submitting an ordinary message gives the plugin a chance to bring back existing material and retain the new input for later processing. The key implementation entry point is `UserPromptSubmit`.

After a user submits a message and before the model analyzes it, the hook attempts to resolve the current Scope, prepares relevant context, and independently captures the current prompt.

```text
User submits a message
    ↓
Resolve the current Scope
    ↓
Request PreparedContext
    ↓
Capture the current prompt as a Source
    ↓
Output available context to ZCode and continue the task
```

This involves two different jobs.

**Recall existing material.** The plugin requests PreparedContext from the Server, which selects and renders relevant content. The current ZCode integration uses a bounded context of up to 8000 bytes, avoiding a full replay of historical materials on every turn.

**Preserve the current input.** The prompt is captured as a Source for configured Server processing. Keeping the source first and extracting reusable knowledge afterwards gives project details mentioned in ordinary conversation a path into later tasks.

A complete automatic memory workflow therefore crosses several stages:

```text
Ordinary project description
    → Source
    → Background Memory extraction
    → A later task requests PreparedContext
    → ZCode supplies context to the model
```

This explains why installing the plugin is only part of setup. ZCode's model handles the current conversation; Server Generation handles extraction and generation. Their model connections are configured separately.

Another choice was to keep automatic hooks from interrupting ordinary work where possible. A temporary memory-service failure lets the conversation continue; explicit searches and saves use MCP tools with visible results. I also added runtime observations and diagnostics to help distinguish capture, context preparation, and connection problems.

### Scope associates material with its project

Cross-session reuse requires capture and recall to resolve to the same Scope.

The plugin checks an explicit Scope, session binding, workspace binding, and the Server default Scope in that order. A workspace binding derives a lookup key from the normalized path and maps it to an actual Scope; the path itself is not a Scope ID.

New tasks in the same project should continue to resolve to the same Scope. Stable workspace bindings make long-term use across several projects easier to manage than placing everything in one default scope.

## Put the pieces into use

The following Windows PowerShell workflow installs the Server and plugin from the same PowerContext checkout to keep their interfaces aligned. Prepare Git, uv, Python 3.11+, and Node.js 24+ available to ZCode.

### Install PowerContext

```powershell
git clone https://github.com/oceanbase/powercontext.git
Set-Location powercontext

uv tool install --force ".[cli,server]"
```

### Configure and start the Server

Generate a local configuration for the Server:

```powershell
New-Item -ItemType Directory -Force '.powercontext' | Out-Null

powercontext config init --language en `
  --output '.powercontext/server.env'
```

Choose storage, the listening address, and model connections in the wizard. SQLite and `127.0.0.1:8000` are suitable for a first local run.

Automatic Memory generation from ordinary conversation needs Generation configured, along with Source processing and Memory scheduling. The relevant schedule settings include:

```ini
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
```

The two schedules process Source windows and schedule Memory generation respectively. Sixty seconds is a check interval; queues and model calls also affect completion time.

Explicit memory writes and full-text search can be used without configuring Generation. Semantic retrieval needs the corresponding Embedding configuration. See [model configuration and complete memory](https://github.com/oceanbase/powercontext/blob/master/docs/zh/docs/get-started/configure-models.md) for model and background-processing settings.

The configuration may contain credentials; keep it local to the Server and out of Git. Validate it and start the service:

```powershell
powercontext config validate `
  --env-file '.powercontext/server.env'

powercontext server run `
  --env-file '.powercontext/server.env'
```

Leave this terminal running.

### Install the ZCode plugin

Fully exit ZCode, including its system-tray process. Open another PowerShell terminal and replace this path with the absolute path to the checkout:

```powershell
$pcRepo = 'C:\path\to\powercontext'

powercontext setup zcode `
  --source $pcRepo `
  --server-url 'http://127.0.0.1:8000'

powercontext doctor zcode
```

The installer copies and registers the plugin in ZCode's user configuration while preserving other model and plugin settings.

`doctor` checks installation declarations and read-only connectivity. After reopening ZCode, confirm that native MCP tools are loaded in an actual task.

## Open a new conversation and see what comes back

To see whether memory helps, start with a specific project convention. Describe it in an ordinary conversation, then open a fresh task without the old transcript and check whether it can be recovered.

First confirm that ZCode can call `list_scopes` through PowerContext MCP, and identify the actual Scope used for this test.

To follow the AgentMeld example, submit the same project description without additionally asking the model to call a memory-writing tool. In English, it means:

> EXAMPLE PROMPT
>
> Going forward, the AgentMeld project will use planning, implementation by multiple workers, and review by multiple reviewers. Commit after each small piece of work on this project.

This contains two lasting conventions: how to organize collaboration and how frequently to commit changes.

Check three things next:

- The input was captured as a Source in the correct Scope.
- Generated Memory preserves both “planning → multiple workers implement → multiple reviewers review” and “incremental commits.”
- The Memory source information refers back to this Source.

Once generation is confirmed, open a completely new task in the same project and Scope. Ask only:

> EXAMPLE PROMPT
>
> What are AgentMeld's working method and commit habit? Answer from existing project context; if there is no evidence, say so.

Leave both conventions out of the new question, and do not paste the previous conversation. Inspect that turn's PreparedContext and host input to connect the answer to the material saved here.

Follow the Source, Memory, PreparedContext, and host input records to connect the answer to specific material. Isolated test directories, object references, and inspection steps are documented in the [desktop acceptance guide](https://github.com/oceanbase/powercontext/blob/master/integrations/zcode/acceptance/desktop.md).

## Leave unfinished work ready to continue

Project conventions help an agent understand the background. Continuing unfinished work needs more specific state: which files changed, which checks ran, and what remains blocked.

Handoff can organize that progress for the next task to read.

For example, if an API migration is only partially complete and you want to continue in another task, give an explicit request:

> EXAMPLE PROMPT
>
> Inspect the current code and completed checks, then commit a persistent handoff milestone for this work. Record the objective, completed and unfinished work, blockers, actual verification results, and next steps. Return the exact saved reference.

The receiver can continue from material such as:

```text
Objective: complete the API migration
Completed: changed modules and corresponding files
Remaining: call sites that still need migration
Verification: commands actually run and their results
Next: priority blockers and suggested order
Evidence: related files, Sources, and artifact references
```

A persistent handoff goes through preparation and commit. After a successful commit, its returned exact version gives the receiving task a clear place to read from.

### Boundaries to keep in view

- Automatic capture saves a Source first. Memory generation depends on background configuration, extraction policy, and processing progress; confirm writes through actual results and readback.
- Cross-session reuse needs the correct Scope. Decide whether branches should share material according to the work; other harnesses need their own integrations. This article demonstrates ZCode.
- Check memory generation and fresh-session recall separately. A task reading Memory or Handoff still needs to inspect current code and verification state.

## Give the next task a clear starting point

What first made this matter to me was the repeated explanation. A branch changes, a conversation changes, and the work is still ongoing. Having a place to recover interface conventions, design decisions, and previous progress makes the next step easier to take.

Hooks, MCP, skills, Scope, and Handoff all serve that goal in this integration: give useful material a path to persist and the next task a way to find it. Installation diagnostics, runtime observations, and acceptance checks help me establish whether that path actually works.

Implementation references:

[PR #1752](https://github.com/oceanbase/powercontext/pull/1752) · [PR #1814](https://github.com/oceanbase/powercontext/pull/1814) · [PR #1850](https://github.com/oceanbase/powercontext/pull/1850)

I hope opening a new task can eventually involve less “let me explain this project first” and more “let's continue where we left off.”
