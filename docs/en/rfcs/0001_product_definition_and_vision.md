- Proposal Name: `product_definition_and_vision`
- Start Date: 2026-07-07
- RFC PR: [oceanbase/powercontext#0001](https://github.com/oceanbase/powercontext/pull/0001)
- Tracking Issue: [oceanbase/powercontext#0001](https://github.com/oceanbase/powercontext/issues/0001)

# Summary

PowerContext is a context runtime layer for humans and agents working together. It turns work advanced by humans and agents into context that later participants can understand, take over, and continue. This RFC defines the product positioning, core concepts, phased scope, and acceptance criteria for PowerContext as the first product definition and vision document.

The core statement is:

> Work context layer for humans and agents.

More specifically:

> PowerContext turns human-agent work into handoff-ready context.

This RFC defines the product object, design concepts, and Phase 1 and Phase 2 requirements. Implementation details, code structure, launch materials, and long-term scenario planning remain outside its scope. It records the intended product direction rather than the APIs or features available in a particular Core revision.

# Motivation

Memory, context, AgentOps, RAG, workflow, and observability products are converging toward a similar capability set. Whether the entry point is memory, context, AgentOps, or workflow, mature products tend to include similar parts:

- Connect documents, code, tickets, conversations, traces, events, and human input.
- Preserve long-term information for users, teams, projects, or agents.
- Retrieve relevant material during task execution.
- Record agent, tool, model, and workflow execution.
- Distill summaries, conventions, SOPs, workflows, skills, or structured content.
- Collect human feedback, task outcomes, eval scores, and quality signals.
- Hand stable processes over to agents or workflows.

This means source, memory, retrieval, trace, artifact, and feedback are no longer enough to define differentiation by themselves. Differentiation depends on the product center around which those parts are organized.

The current mainstream directions are roughly:

- Stronger agents: help agents remember more, find more relevant context, improve from failure, preserve state during long tasks, and complete more steps automatically.
- Self-evolving organizations: automatically distill context, merge and upgrade experience, use feedback to evolve memory and skills, and make workflows increasingly automated.

Both directions focus mainly on agent capability improvement or automation loops, rather than how humans and agents complete long-running work together. Humans can become mechanical operators: they provide input and wait for output, but cannot easily understand intermediate judgments, take over follow-up state, or reuse work experience.

PowerContext chooses a different problem:

- After humans and agents advance a piece of work together, how can later humans or agents understand, take over, and continue it?
- When a user lets an agent execute for a while, how can intermediate decisions and judgments be preserved?
- When a new participant takes over a project, how can they naturally understand its evolution instead of seeing only scattered memories, skills, or dashboards?

# Guide-level explanation

The product object of PowerContext is not memory, trace, artifact, or workflow in isolation. It is work advanced by humans and agents together.

Around this object, PowerContext handles source, artifact, feedback, automation, and related capabilities. These capabilities are not the differentiation by themselves. The difference is that they serve one goal:

> Work should remain handoff-ready after humans and agents advance it together.

## Product choice: collaboration and handoff

PowerContext starts from work handoff.

In real organizations, agents do not run in isolation. Humans set goals, judge risks, correct direction, and take over results. Agents execute tasks, expose uncertainty, preserve process, and move follow-up work forward.

In this framing, context is not an agent's internal state, and it is not an ever-growing collection of organizational material. Context is the condition for collaboration and handoff. Handoff means a piece of work advanced by humans and agents can be understood, taken over, and continued.

Therefore, PowerContext is not centered on:

- How agents become stronger.
- How context evolves automatically.
- How systems reduce human participation.

It is centered on:

- How humans understand what agents have done.
- How agents inherit judgments humans have already made.
- How one participant takes over work left by another participant.
- How work stays continuous across multiple humans, agents, and tools.

Typical scenarios include:

- A human delegates a task to an agent.
- An agent returns uncertainty to a human.
- One agent continues another agent's task.
- A conversation is distilled into a later workflow.
- A review, incident, or debugging experience becomes a reusable team asset.
- An organization preserves work judgments across agents, tools, and teams.

PowerContext lets humans and agents face the same work state through different views, capabilities, and entry points.

## Core concepts: Source, Artifact, Trigger

The first version of PowerContext exposes only three concepts:

```text
source -> artifact <- trigger
```

- Source: system input, meaning references and evidence from external work material.
- Artifact: context output created and maintained by PowerContext.
- Trigger: control signals that affect system behavior and artifact state.

Other capabilities should be organized under these concepts. Memory, trace, workflow, skill, and eval should not become parallel top-level entries.

### Source

Source is external data connected to PowerContext.

It includes tickets, OTel, code, documents, agent traces, reviews, incidents, human notes, and similar material. Source is connected through plugins. PowerContext does not replace existing user systems. It uses external material to create context artifacts.

Source is first work evidence, and only secondarily debugging material. Raw data remains in user systems. PowerContext organizes its handoff semantics and runtime views.

### Artifact

Artifact is context output created by PowerContext.

Typical artifacts include:

- Short-term and long-term memory: long-term judgments and short-term working memory.
- User preferences: personalized preferences, conventions, and constraints.
- Routines: scheduled tasks, recurring actions, and situational actions.
- SOPs: skills, workflows, and runbooks.
- Small tools or apps: human-facing operational content.

An artifact is not raw data, and it is not a one-off retrieval result. It must be able to enter later work, be understood by humans, be used by agents, or be maintained further.

### Trigger

Trigger is external control over PowerContext. It affects artifact lifecycle, evolution, and use.

It differs from source:

- Source provides material.
- Trigger provides signal and control.

Triggers can include usage feedback, human confirmation, human rejection, task success or failure, eval scores, external events, scheduled signals, or explicit operations on a class of artifacts.

### Handoff context

Handoff context is the context view for the next participant. It is not a new raw data collection or a complete execution log. It is handoff material organized from sources and artifacts.

The first version of handoff context should help the next participant answer at least four questions:

- What has already happened?
- Which key judgments were made?
- What is the current state and next step?
- Which sources or artifacts support those judgments?

## User path

The first version is organized around four actions:

- Connect source.
- Create artifact.
- Mount artifact.
- Send trigger.

Mount artifact means projecting an artifact into a target agent through MCP, skills, hooks, or a context provider. This is a key action that PowerContext must help users complete.

A typical path is:

1. The user declares sources and mount targets in a personal runtime manifest.
2. The user connects external systems, such as OTel, tickets, and code repositories.
3. PowerContext reads source references, but does not copy the original systems.
4. The user or agent creates artifacts from source refs.
5. The user mounts artifacts into the agent product they use.
6. A human or agent receives handoff context when taking over work.
7. Feedback flows back through triggers and affects personal state and team assets.

This differs from traditional memory products. The user does not start from `memory.add`; they start from existing work systems and distill the handoff-ready parts into artifacts.

## Personal first, team aggregation

Configuration should be personal first, rather than strongly unified at the team level:

- Personal state: personal sources, mounts, feedback, and work context.
- Team assets: shared artifacts confirmed or repeatedly reused.
- Team policy: default policies, shared artifacts, and recommended mounts maintained by the team.

This lets the product naturally degrade to individual use. In multi-user settings, results can be aggregated into team assets.

# Reference-level explanation

This reference-level explanation defines product semantics and boundaries. It does not define code architecture, database tables, service decomposition, or implementation details. RFC 0002 records the corresponding SDK product model and implementation boundaries; the source tree and development guide describe the implementation available on a specific revision.

## Terminology contract

| Term | Definition | Not |
| --- | --- | --- |
| Work | The tasks, judgments, and state changes advanced by humans and agents around a goal | A single memory, trace, or workflow |
| Source | References and evidence from external work material | A raw system copied and owned by PowerContext |
| Artifact | Context output created and maintained by PowerContext | Raw data, one-off retrieval results, or unmaintainable summaries |
| Trigger | External signals that affect artifact lifecycle, evolution, and use | Ordinary source content |
| Handoff context | The context view for the next participant | A complete log, full knowledge base, or plain debug trace |
| Personal state | Personal sources, mounts, feedback, and work context | Team-wide mandatory configuration |
| Team assets | Shared artifacts formed through confirmation or reuse | Automatic merging of all personal state |
| Team policy | Team-maintained defaults, shared artifacts, and recommended mounts | A full replacement for personal runtime state |

## Product line

The PowerContext product line is:

```text
Understand work -> Shape handoff context -> Support human-agent collaboration -> Collect reuse feedback -> Maintain reusable work assets
```

More concretely:

1. Understand a piece of work advanced by humans and agents together.
2. Organize inheritable judgments, preferences, habits, and skills into handoff context.
3. Assemble the right context view when a human or agent takes over work.
4. Collect feedback during later use to judge whether the handoff worked.
5. Continuously maintain reusable work assets for the organization.

## Capability boundaries

Source, artifact, trigger, feedback, mount, and related capabilities must follow one test:

> Does it make the work easier for the next human or agent to take over?

Therefore, Phase 1 and Phase 2 boundaries are:

| Capability | Should support | Should not become |
| --- | --- | --- |
| Source | Reference external work evidence and preserve traceability | A replacement for user-owned data systems |
| Artifact | Distill handoff-ready, mountable, maintainable context output | A one-off summary without lifecycle |
| Trigger | Let feedback, events, and confirmation signals affect artifacts | Another content input channel |
| Mount | Project artifacts into agent entry points | Ungoverned implicit injection |
| Handoff context | Help humans or agents understand past work and next state | A full log browser or ordinary dashboard |

Key tradeoffs:

- Long-term information matters only when it serves handoff. Short-term working memory should not automatically become the work storyline.
- Execution records are first work evidence, and only secondarily debugging material. PowerContext's own trace should also be governed.
- Accumulated preferences, habits, skills, and automation are only part of the goal. They should eventually become human-facing material.
- PowerContext does not bypass existing user data systems. Raw source remains in user systems. PowerContext organizes its handoff semantics and runtime views.

## Target users

The early entry point can be an individual developer connecting their own agent and distilling context across agents or projects. The primary target is organizations with multiple humans and agents collaborating.

Typical users include:

- Teams using multiple agents in engineering, operations, data, or knowledge work.
- Teams that need to hand off tasks, judgments, and processes between humans and agents.
- Organizations that need to turn agent work traces into team experience.
- Organizations that want to preserve human judgment and responsibility instead of hiding work inside automation.

These organizations need more than stronger agents. They need more handoff-ready work.

## Difference from adjacent products

Adjacent products usually organize the main path around memory, context providers, or graph retrieval:

- Mem0 / OpenMemory: add, search, and share memory.
- LangMem / Letta / Zep / Graphiti: extract, organize, and retrieve memory or graph context.
- Continue / Claude Code: mount external material into agents through context providers, MCP, skills, and hooks.

The PowerContext main path is:

```text
user source system -> source ref -> handoff artifact -> MCP / skill / hook projection -> agent memory injection -> human-agent work handoff -> feedback trigger
```

PowerContext expands the product scope of PowerMem. It is not a separate memory system next to PowerMem. Context is broader than memory. It can include extracted preferences, experience, durable work products, short-term working context, and later reusable skills, routines, and workflows.

PowerMem is memory-centered. It mainly addresses long-term memory, hybrid retrieval, intelligent extraction, agent integration, and skill distillation. PowerContext is broader and shifts the product center from memory to context.

The relationship is:

| Project | Product center | Technical object | Primary question |
| --- | --- | --- | --- |
| PowerMem | Memory | memory record, vector / graph search, skill store | How agents remember and retrieve |
| PowerContext | Context | work, source ref, context item, handoff view | How humans and agents jointly take over work |

In PowerContext, memory is a subset of context. Long-term preferences, project conventions, team experience, short-term working memory, routines, and skills are all context forms.

## Product positioning

The most important near-term goal is to form a clear, usable loop:

> Personal mount on demand, team aggregation afterward; externally tell the work handoff story, internally preserve the self-evolving capability base.

The positioning is:

- Externally lead with work handoff: this is the differentiation gap and avoids directly competing in the crowded self-evolving system story.
- Internally keep self-evolving capabilities as the base: existing memory, experience, and skill capabilities are already part of that direction and should not be discarded.
- Keep both in one PowerContext product: use work handoff as the external story and self-evolution as the capability base, rather than splitting them into two products.

Requirement layers:

| Layer | Goal | Required content |
| --- | --- | --- |
| Runtime layer | Can be used in practice | Minimal source, artifact, trigger, and mount loop |
| Self-evolving base | Prove this is not static memory | Generation, update, retirement, and reuse of memory, experience, skill / routine |
| Handoff experience | Make the external story visible | Handoff context, handoff view, agent injection |

## Phase 1: late July / early August 2026

Goal: deliver the first usable version, start internal use, and validate usability, handoff quality, and product boundaries.

Functional requirements:

- Runtime manifest: support mounting sources and artifacts into target agents or related runtime entry points.
- Source provider: support at least code repositories, agents, or OTel integration so users can begin using the base capability in their own projects.
- Artifact registry: support four artifact types: memory, experience, routine, and skill. This can include previous PowerMem memory and experience, as well as ContextSeek skill-related content. Routine does not have to be complete in the first phase.
- Handoff context: generate a handoff view from source refs and artifacts. The primary expected outputs are a task timeline and a plan, then preference accumulation on top of them.
- Trigger / hook: support human feedback, task result feedback, and agent lifecycle hooks.
- Codex dogfood: prioritize Codex use cases, including memory injection, skills mounting, and handoff summary collection.

Trial and evaluation:

- Individual developers can connect their own agent workflow.
- Internal evaluation scores usability, handoff quality, and artifact maintainability.
- The first demos should show handoff context generated from an agent work trace and then distilled into memory / routine.

Acceptance criteria:

- Architecture boundaries are clear: source does not copy original systems, artifacts are manageable, and triggers can flow back.
- Internal users can complete one end-to-end trial in the Codex scenario.
- The product forms a minimal source, artifact, trigger, and mount loop.
- The handoff view helps later humans or agents understand past work, key judgments, and next state.

## Phase 2: September 1, 2026

Goal: deliver the first formal version. Based on Phase 1 feedback, converge the product path, provide a stable personal mounting experience, and check that the experience remains consistent from individual use to enterprise multi-user scenarios.

Functional requirements:

- Improve manifest and CLI usability.
- Improve three mounting forms: MCP server, skills pack, and hooks adapter. More mainstream agent support can be added, but only where the team has enough capacity to validate it. It is acceptable to support fewer integrations if they remain usable.
- Converge artifact lifecycle based on internal feedback, decide whether expected layers and effective timing should change, and check artifact quality.
- Review multi-tenant enterprise scenario design from the handoff perspective, ensuring that the experience chain remains consistent from individual users to enterprise multi-user settings.
- Add basic profile and dashboard capabilities so users can understand the relationship among personal state, team assets, and handoff context.

Acceptance criteria:

- The external differentiation is clear: PowerContext is not another memory system, but a context runtime layer for human-agent work handoff.
- The internal self-evolving capability base remains present: memory, experience, routine, and skill can be generated, updated, retired, and reused as artifacts.
- Users can complete the personal mounting scenario and receive handoff context when taking over later.
- In enterprise and team scenarios, the relationship among personal state, team assets, and team policy is clear. Teams are not required to start with strong unification.
- New functionality must return to work handoff and should not be explained as isolated low-level capability.

## Phase 1 and Phase 2 priorities

P0:

- Runtime manifest.
- Artifact registry.
- Handoff context.
- Codex dogfood.
- Trigger / feedback.
- Minimal MCP / skill / hook mounting.

P1:

- Source provider expansion.
- Artifact lifecycle.
- Generation and retirement of experience / routine / skill.
- Basic profile and dashboard.
- Internal evaluation loop.

# Drawbacks

Choosing work handoff as the product center has several costs:

- The product story no longer centers only on agents becoming stronger, which may make some existing memory or self-evolving system narratives less direct.
- The Source, Artifact, and Trigger abstraction collapses several capabilities into fewer top-level concepts, so early users may think underlying capabilities are hidden or weakened.
- Handoff context quality is hard to evaluate with static metrics alone. It needs real tasks and later takeover experience.
- The personal-first, team-aggregation path adds governance complexity and requires a clear boundary between personal flexibility and team consistency.

# Rationale and alternatives

## Why work handoff

PowerContext needs a product center broader than memory, but should not directly enter homogeneous competition around "everything evolves automatically." Work handoff can explain human judgment, agent traces, context artifacts, and feedback loops together, while bringing existing PowerMem capabilities into a broader product scope.

Core benefits:

- Clear external differentiation: PowerContext is not another memory system, but a context runtime layer for human-agent work handoff.
- Reusable internal capabilities: memory, experience, routine, and skill can continue to evolve as artifacts.
- More natural user path: users start from existing source systems instead of being forced to start by adding memory.
- More complete team scenario: it can express the relationship among personal state, team assets, and team policy.

## Alternatives

Alternative 1: keep memory as the product center.

- Pros: mature concept, easy to understand, and aligned with the existing PowerMem story.
- Cons: high risk of homogeneous competition, and hard to explain product objects beyond memory, such as trace, routine, workflow, and handoff view.

Alternative 2: use self-evolving organization as the product center.

- Pros: covers long-term capabilities such as experience distillation, skill evolution, and workflow automation.
- Cons: higher explanation cost, and it can obscure human judgment and responsibility behind automation.

Alternative 3: use AgentOps or observability as the product center.

- Pros: directly related to trace, eval, and execution quality.
- Cons: pulls PowerContext toward debugging and monitoring, and cannot fully express work takeover, preference inheritance, or team asset maintenance.

If this RFC is not adopted, PowerContext's source, memory, artifact, trigger, skill, and related capabilities may be discussed and implemented separately. The product center would remain unstable, making it harder to decide which capabilities are on the main path and which are supporting infrastructure.

# Prior art

Related products and directions include:

- PowerMem: memory-centered, addressing long-term memory, hybrid retrieval, intelligent extraction, agent integration, and skill distillation.
- ContextSeek: exploration around skills and context, providing reference for PowerContext artifact capabilities.
- Mem0 / OpenMemory: organize the user path around adding, searching, and sharing memory.
- LangMem / Letta / Zep / Graphiti: extract, organize, and retrieve memory or graph context.
- Continue / Claude Code: mount external material into agents through context providers, MCP, skills, and hooks.

PowerContext borrows source connection, memory extraction, agent mounting, feedback loops, and skill distillation from these directions, but shifts the product center to human-agent work handoff.

# Unresolved questions

Questions to resolve before merging this RFC:

- Whether routine must be included in the first usable Phase 1 version, or only kept as a registry type and future evolution point.
- How to define the minimal quality bar for handoff context: beyond task timeline, key judgments, and next state, should it also require risks, evidence, and responsibility boundaries?
- Whether artifact lifecycle stages should be fixed in Phase 1 or converged in Phase 2 based on internal dogfood feedback.
- Whether team asset confirmation should start from explicit human approval or allow recommendation based on reuse signals.

Intentionally out of scope:

- Concrete service architecture, data model, indexing plan, and storage choices.
- Concrete protocols for MCP server, skills pack, and hooks adapter.
- External launch materials, pricing, commercial packaging, and long-term scenario planning.
- Concrete eval benchmark design and weighting.

Follow-up decisions that may need separate RFCs:

- Runtime manifest and mount semantics.
- Artifact registry and lifecycle.
- Handoff context schema and quality evaluation.
- Trigger / feedback model.
- Multi-tenant governance model for personal state, team assets, and team policy.

# Future possibilities

Natural extensions include:

- Expand handoff context from a one-time handoff view into continuously maintained work state.
- Expand artifacts from memory, experience, routine, and skill into workflows, runbooks, and human-facing small tools.
- Build artifact projection across agents, tools, and teams.
- Maintain team-level reusable work assets based on real usage feedback.
- Govern PowerContext's own trace so the context system itself can be audited, evaluated, and improved.

All of these extensions must return to the same test: whether they make work easier for the next human or agent to take over.
