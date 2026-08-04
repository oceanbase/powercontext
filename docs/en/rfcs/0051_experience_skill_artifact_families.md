- Proposal Name: `experience_skill_artifact_families`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#51](https://github.com/oceanbase/powercontext/pull/51)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md), [RFC 0014](0014_memory_layer_design.md),
  [RFC 0016](0016_pydantic_ai_inference_integration.md), and [RFC 0028](0028_context_pack.md)
- Depends on: [RFC 0031 Artifact Candidate and Review Inbox](https://github.com/oceanbase/powercontext/pull/50)

# Summary

This RFC defines the `experience` Artifact Family, the PowerContext-managed `skill` Artifact Family, and the governance
boundary for external Agent-native Skills that are locally available in the current Agent environment.

Four statements summarize the design:

1. Experience is a reusable judgment distilled from evidence of real work. It answers, "In what situation did an
   action produce an outcome, and what did we learn?"
2. A Skill is a capability package that an Agent can discover and use for a class of tasks. It answers, "When should
   this be used, where is its entry point, and how should its result be validated?"
3. A session or task is an evidence boundary and an evolution trigger, not the identity or lifetime boundary of an
   Experience or Skill.
4. Skills may be external or PowerContext-managed. They can share discovery in the current Agent environment, but they
   cannot share content authority by accident.

A PowerContext-managed Skill may be incubated from one or more approved Experiences, official documentation, human
input, and later usage feedback. Not every Skill must come from Experience. Skills already installed or supplied in the
current Agent environment remain owned by their original system. PowerContext discovers, indexes, associates, and
resolves them locally without silently copying or rewriting them or handing them to another Agent or host.

Experience and managed Skill reuse the existing Artifact identity, immutable Revision, lineage, and CAS contracts.
Their create and revise operations first enter the Review Inbox defined by RFC 0031. Discovering an external Skill
does not create a managed Artifact. Only an explicit import or fork proposes a new managed Skill Candidate.

Experience and managed Skill generation and evolution are advanced capabilities that require a configured generation
model. Without one, Runtime generates no Candidate and provides no rule-based fallback. Artifact persistence, Review,
exact reads, and local external Skill registration continue to work independently. A model cannot approve its own
Candidate, allocate final Artifact identity, or acquire execution authority.

# Motivation

## A task result is not Experience

A task usually leaves changed files, command results, risks, and next steps. That material helps with the immediate
handoff, but most of it only describes what happened once.

```text
"This task changed three files, and make contract-test passed."
```

That is a task result, not Experience. Experience must derive a bounded relationship from the evidence:

```text
"After changing openapi/powercontext.yaml, regenerate the Client and then run contract tests;
otherwise generated code may drift from the public contract."
```

One task may be enough to propose Experience, but Experience is not confined to one task. Later tasks may strengthen,
narrow, contradict, or split the original judgment through new Candidates and Revisions.

## A procedure is not a complete Skill

`preconditions -> steps -> validation -> failure handling` describes an executable procedure. It may be an important
part of a Skill, but it does not fully represent a Skill used by a coding Agent.

An Agent Skill commonly also needs:

- a discoverable name and description that tell an Agent when to load it;
- an instructions entry point that the Agent can read;
- a format and capabilities understood by the current Agent or host;
- optional scripts, templates, examples, and references;
- an installation location in a user, project, or plugin environment.

This RFC therefore no longer defines Skill as a fixed linear list of steps, and it does not turn any instruction with a
branch into a workflow. The first managed Skill format defines an instruction core. External Skills retain the native
package format understood by the current Agent. Whether Routine or Procedure becomes another Artifact Family remains a
future decision.

## Skills already exist outside PowerContext

Before adopting PowerContext, the current Agent environment may already contain:

- user-level or project-level Codex Skills;
- Claude Code Skills;
- repository Skills under `.agents/skills/`;
- Skills installed from plugins, Git repositories, or team tooling.

If PowerContext only knows Skills incubated from its own Experiences, it cannot discover capabilities already available
to the current Agent and may create redundant managed Skills.

PowerContext must govern the discovery of external Skills without taking over their ownership:

- the original package remains the content authority;
- a provider adapter scans only local directories and installation scopes configured for the current Agent;
- PowerContext maintains a rebuildable local catalog, content fingerprint, and binding;
- a binding is `available` only when the current Agent can resolve and read it and its fingerprint matches the latest
  scan;
- an invalid binding resolves as `unavailable` without querying a remote source, offering installation, or treating its
  locator as a cross-environment contract.

## Reusable assets must cross sessions and tasks

If every session produces isolated Experience or Skill content, the system merely creates another form of session
summary. The useful loop is:

```text
bounded task evidence
  -> propose or revise Experience
  -> Review
  -> reusable Experience Revision
  -> propose or revise managed Skill
  -> Review
  -> publish or bind to an Agent
  -> collect bounded usage evidence from later tasks
  -> propose the next Experience or Skill Revision
```

A session or task is an observable boundary and trigger. Artifact identity belongs to a durable scope such as a user,
project, or team; it must not be derived from a session ID or task ID.

# Guide-level explanation

## Understand the design on two axes

The first axis is content semantics:

| Object | Question it answers | Typical content |
| --- | --- | --- |
| Memory | What should be remembered directly for future work? | Facts, preferences, decisions, constraints |
| Experience | What action produced what outcome in which situation? | situation, action, outcome, lesson |
| Skill | When and how should an Agent use a capability? | name, description, instructions, validation |
| Procedure | What process does a capability follow internally? | Preconditions, steps, branches, failure handling |

The second axis is Skill content authority:

| Skill origin | Content authority | What PowerContext owns | What PowerContext does not do |
| --- | --- | --- | --- |
| External / Agent-native | Native package in the current Agent environment | Local discovery, indexing, association, and exact resolution | Silent rewrites, remote installation, or cross-Agent resolution |
| PowerContext-managed | Exact Skill Artifact Revision | Candidate, Review, Revision, lineage, and publication projection | Automatic execution or tool authority after approval |

Origin and content are independent axes. An external Skill may contain an excellent procedure. A managed Skill may be
written from official documentation or human input without first deriving an Experience.

## What exists and what is new

```text
[existing] SourceRef / ArtifactRef / immutable Artifact Revision / lineage / CAS
[existing] ArtifactLineage can cite multiple exact SourceRef and ArtifactRef values
[dependency] RFC 0031 Candidate / Review Inbox
[existing] Memory and PreparedContext v1

[new] Experience typed content and cross-task evolution rules
[new] PowerContext-managed Skill typed content and evolution rules
[new] External Skill registration, rebuildable index, and local binding semantics in the current Agent environment
[new] Rebuildable approved Experience head FTS projection and PreparedContext recall

[unchanged] Memory writes, flush, MCP, Codex Hook, and the PreparedContext v1 public envelope
[not included] Cross-Agent/host handoff, automatic installation, execution, format conversion, workflow engine, or
permission grants
```

"Locally available" is relative to the current Agent kind, host, and installation scope rather than a global property of
a Skill. This RFC does not serialize a local registration or binding into a cross-Agent contract. Approved Experience
recall uses the existing source-neutral PreparedContext v1 envelope; it does not make Skill availability portable.

## Example 1: build Experience across three tasks

Three independent tasks leave bounded evidence:

```text
Task A: changed OpenAPI without generating the Client; contract-test failed
Task B: changed OpenAPI, ran api-generate; contract-test passed
Task C: hand-edited generated code; api-generate-check failed
```

With a generation model configured, a generator may select all three exact SourceRef values and propose:

```yaml
family: experience
proposal:
  situation: changing PowerContext's public OpenAPI contract
  action: regenerate the Client after changing the source and inspect the generated diff
  outcome: generated code matches OpenAPI and contract checks pass
  lesson: OpenAPI is the authority for the public HTTP contract; generated code must not be maintained by hand
sources:
  - source:task-outcome/task_a
  - source:task-outcome/task_b
  - source:task-outcome/task_c
status: pending
```

The tasks do not need to belong to one session. Review checks that scope is consistent, evidence supports the
proposal, and contrary evidence has not been hidden.

## Example 2: revise Experience after a later task

Suppose `experience/exp_openapi_change@1` only requires `contract-test`. A later task shows that Client generation is
also required. The Runtime may propose:

```yaml
family: experience
target: artifact:experience/exp_openapi_change@1
proposal:
  situation: changing PowerContext's public OpenAPI contract
  action: run api-generate, inspect generated changes, and then run contract-test
  outcome: the generated Client matches OpenAPI and contract tests pass
  lesson: contract-test does not replace the generation step
sources:
  - source:task-outcome/task_d
artifacts:
  - artifact:experience/exp_openapi_change@1
status: pending
```

Approval creates Revision 2. Exact readers can still read Revision 1, while later work reads active Revision 2 by
default. If new evidence contradicts the old conclusion, the generator must narrow the situation, split the Experience,
or preserve an explicit conflict. Similarity alone cannot overwrite it.

## Example 3: discover a local Skill in the current Codex environment

The Codex provider discovers a Python Skill in the user-level Skill directory:

```yaml
skill:
  identity: external:codex:user/friendly-python
  origin: external
  content_ref: source:agent-skill/friendly-python@sha256:abc123
bindings:
  - agent: codex
    scope: user
    locator: user-skill:friendly-python
```

When the current Codex integration queries the Registry, it rechecks that the locator is readable and the fingerprint
still matches before returning the local entry point. A removed directory, changed content, or caller outside the same
scope resolves as `unavailable` and is excluded from available discovery results. Claude Code, another host, or another
user cannot reuse this binding; each environment performs its own local discovery.

## Example 4: incubate a managed Skill from multiple Experiences

With a generation model configured, Runtime selects two approved Experiences and one exact project command source, then
proposes:

```yaml
family: skill
proposal:
  name: powercontext-openapi-change
  description: Use when changing PowerContext's public HTTP contract; covers generation, diff review, and validation.
  instructions: |
    Edit openapi/powercontext.yaml, then run make api-generate.
    Inspect generated changes; never patch src/powercontext/http/_generated/ by hand.
    Run make contract-test last. Preserve failure output and do not claim completion when it fails.
  validation:
    - make api-generate-check passes
    - make contract-test passes
artifacts:
  - artifact:experience/exp_openapi_change@2
  - artifact:experience/exp_generated_code_edit@1
sources:
  - source:repository/makefile_contract_targets
status: pending
```

After approval, the Skill Artifact Revision is the content authority. Rendering it into the current Agent's Skill
directory creates only a host-local projection. A missing or stale projection can be rebuilt from the exact Revision.

## Example 5: evolve a Skill from usage feedback

A Skill may succeed, fail, or not be selected in later tasks. The owning integration may submit bounded usage evidence
at a task-end or an explicit Agent stop boundary:

```text
Skill@1 selected in Task E -> validation passed
Skill@1 selected in Task F -> packaging validation was missing and the task failed
```

These outcomes may generate new Experience or propose a replacement Candidate with `Skill@1` as an exact target.
They cannot modify the Skill directly, and high usage count alone cannot raise its trust level.

# Reference-level explanation

## Scope

This RFC defines:

- Experience typed content, evidence, and cross-session/task evolution;
- PowerContext-managed Skill typed content, evidence, Review, and Revision;
- authority boundaries between external and managed Skills;
- minimal semantics for external Skill registration, a rebuildable index, and local bindings in the current Agent
  environment;
- Review boundaries for create, revise, import, and fork;
- model capability gating, retrieval, publication, and execution boundaries.

This RFC does not redefine Candidate identity, state transitions, CAS, Review APIs, or Candidate persistence owned by
RFC 0031.

This RFC does not define:

- cross-Agent or cross-host Skill handoff, resolution, or portability contracts;
- automatic installation, update, or removal of external Skills;
- a hosting format for scripts, binaries, or large assets;
- a Routine, Procedure, workflow, or DAG Family;
- Skill execution sandboxes, tool grants, or secret policies;
- unbounded background distillation or full historical session scanning.

## Experience Family

`experience` content has four fields:

| Field | Meaning | Must not become |
| --- | --- | --- |
| `situation` | Context, trigger, and applicability boundary | An unbounded phrase such as "during development" |
| `action` | What was actually done in that situation | Advice or a guess that was never attempted |
| `outcome` | The observed result supported by evidence, including failure | "should work" or "probably passed" |
| `lesson` | A reusable judgment derived from action and outcome | A raw task summary, counter, or slogan |

Evidence is not duplicated in content. A Candidate uses the RFC 0031 `sources/artifacts` envelope, which becomes
`ArtifactLineage` after approval:

- at least one exact SourceRef or ArtifactRef must exist;
- evidence may come from one or more sessions or tasks;
- all evidence must remain within scope authorized by the caller and cannot leak across tenant or project boundaries;
- evidence must support situation, action, and outcome rather than merely discuss the same topic;
- one task may be enough to propose Experience, with no fixed minimum sample count;
- several tasks may jointly produce the first Revision or revise an existing one;
- both success and failure may form Experience, and failure cannot be rewritten as successful advice;
- contradictory evidence must narrow, split, or remain explicit in the Candidate.

An Experience Artifact ID is a durable identity inside a scope and contains no session ID or task ID. Revision lineage
stores the direct SourceRef and ArtifactRef values used for that proposal. Historical evidence remains reachable
through an exact previous ArtifactRef instead of copying an unbounded history into every Revision.

The first version adds no automatic `confidence`, `importance`, `decay`, or free-form metadata. Task count is not
confidence; Review still judges evidence quality and applicability.

## PowerContext-managed Skill Family

The first managed `skill` content has:

| Field | Meaning | Minimum requirement |
| --- | --- | --- |
| `name` | Display name recognized by people and Agents | Non-empty; not Artifact identity |
| `description` | Applicable tasks, triggers, and expected result | Sufficient for an Agent to decide when to load it |
| `instructions` | Complete instruction core readable by an Agent | Bounded content, not only a title or slogan |
| `validation` | How to determine whether use succeeded | At least one observable result |

`instructions` may include preconditions, steps, constraints, bounded branches, and failure handling. The first version
does not parse it into a workflow or DAG. Exact SourceRef values may cite scripts, templates, or reference material,
but the first version does not host arbitrary package assets.

A managed Skill Candidate no longer requires approved Experience in every case:

- automatic incubation from Experience cites at least one exact approved Experience ArtifactRef;
- complete human authorship or official documentation may cite exact SourceRef values instead;
- import or fork from an external Skill cites its exact snapshot or fingerprint;
- revision from usage feedback cites the exact target Skill Revision and the bounded evidence used directly.

After approval, these direct references become Skill Artifact lineage.

## External Skill registration and index

An external Skill is not a managed Skill Artifact. Its original package remains the content authority, while
PowerContext maintains a Registry projection. A minimal registration represents:

- a stable Skill identity within scope;
- external origin/provider and local locator;
- the observed content fingerprint or exact SourceRef;
- a discoverable name and description;
- the current Agent kind;
- at least one local binding that the current environment can resolve.

The Registry and index are rebuildable projections, not a second content authority:

- rescanning may update availability, fingerprint, and bindings;
- upstream changes cannot masquerade as the same exact version;
- disappearance removes the Skill from the current Registry/index; exact SourceRef values already written to Artifact
  lineage remain intact;
- discovery proves only that content was observed, not that it is safe, correct, or suitable for a task;
- only an explicit import or fork creates a managed Skill Candidate and new Artifact identity.

## Local Skill binding and resolution

A binding describes how the current Agent environment accesses a Skill. It distinguishes at least Agent kind, host,
installation scope, and locator. A binding is local environment state, not Skill content, and does not enter a managed
Skill Revision.

An external Skill is "locally available" only when:

- its registration is visible to the current caller scope;
- its binding belongs to the current Agent kind, host, and allowed installation scope;
- its locator currently exists and is readable;
- its current content fingerprint matches the registration.

Exact resolution returns a local entry point only when all conditions hold. Otherwise it returns `unavailable` without
falling back to another version, querying a remote source, or generating an installation hint.

A registration and binding are not a cross-Agent or cross-host contract. Another Agent, host, or user must rescan its
own local environment through its provider adapter. Successful local resolution still does not authorize the Agent to
load or execute the Skill.

## Cross-session generation and evolution

The first version does not depend on unbounded background scans. Only after a generation model is configured does the
owning integration select evidence and trigger generation at bounded events such as:

- task outcome creation;
- a session or turn stop boundary that the integration can interpret;
- a Git change or validation result that changes an existing judgment;
- a Skill use with an observable outcome;
- an explicit user request to preserve or revise reusable content.

Each evolution run has one of four results:

```text
no reusable change      -> no-op
new reusable judgment   -> create Candidate
correction or extension -> replacement Candidate targeting an exact active ArtifactRef
contradictory evidence  -> scoped split or explicit conflict Candidate
```

Retrieval may suggest related active Artifacts, but Runtime cannot choose a target and overwrite it from similarity
alone. The first implementation may require the owning integration or a person to select the target explicitly. Later
indexes remain Candidate discovery mechanisms.

## Generation and Review

A generator uses the configured generation model to propose typed content but does not allocate final Artifact identity
or write a Revision directly:

```text
bounded exact evidence
  -> generate typed proposal outside transaction
  -> validate shape, scope, target, and references
  -> persist pending Candidate
  -> human approve/revise/reject
  -> commit Artifact Revision on approval
```

Experience and managed Skill use the RFC 0031 `review` policy:

- create produces only a Candidate;
- revise targets an exact active ArtifactRef and proposes a complete replacement;
- importing or forking an external Skill proposes a new managed Skill without modifying registration;
- `approve` cannot edit proposal content at the same time;
- approval validates the Family and commits Candidate state and Artifact Revision in one transaction;
- a stale target returns conflict instead of an automatic three-way merge.

External Skill discovery does not enter the Review Inbox because it only records rebuildable observations. Recommending
it to a task, importing it, publishing it, or executing it remains a separate explicit decision.

## Model capability gate

Experience and managed Skill generation are advanced capabilities enabled only when a generation model is configured.
The underlying Artifact contract, Review, and local external Skill Registry do not depend on an LLM:

| Capability | Requires an LLM? |
| --- | --- |
| Typed validation, Revision, Review, and exact read | No |
| Local external Skill scan, fingerprint, binding, and exact resolution | No |
| Generate or revise an Experience Candidate | Yes |
| Generate, import, fork, or revise a managed Skill Candidate | Yes |
| Decide how contradictory evidence should narrow or split a situation | Yes; Review makes the final decision |

Without a configured generation model:

- Runtime does not expose an available Experience/managed Skill generation capability;
- task, session, or usage outcomes do not trigger Experience/managed Skill generation;
- explicit generate, revise, import, or fork requests return a typed capability error before persisting a Candidate;
- local external Skill discovery, indexing, binding, and exact resolution continue to work;
- Review of existing Candidates and exact reads of approved Artifacts continue to work;
- Runtime never wraps raw summaries or deterministic rule output in Experience or managed Skill content.

Rules still enforce input bounds, scope, exact references, typed shape, and lineage, but those checks do not replace
semantic generation. Even when available, an LLM cannot approve Candidates, choose execution authority, invent
evidence, install Skills, or commit final Revisions.

## Identity, Revision, persistence, and projections

Experience and managed Skill reuse the existing Artifact contract:

- Families are fixed as `experience` and `skill`;
- an Artifact ID is an opaque identity inside scope and is not computed from title, content, session, or task;
- every approved update creates an immutable Revision;
- lineage retains direct SourceRef and ArtifactRef values;
- ArtifactStore CAS prevents a write based on a stale head.

External Skill Registry, text/vector indexes, and Agent bindings are rebuildable projections rather than content
authorities. Approved Experience recall stores deterministic `searchable_text` on the existing generic
`pc_artifact_heads` row; SQLite adds only its FTS5 virtual index, while OceanBase indexes that field directly. Builtin
Runtime creates no parallel `pc_experience_heads`, `experiences`, or `skills` truth/projection table. A later
implementation RFC may choose persistence for the Registry by reusing general registry/projection capabilities.

The current Artifact contract has no retirement semantics, so this RFC adds no automatic retirement or time decay.
Reviewed Revisions correct managed content, while rescans refresh external registration. Usage counts, task counts, and
vector scores cannot silently delete or overwrite content.

## Retrieval, Context Pack, publication, and execution boundary

- pending and rejected Candidates never enter Artifact or Skill discovery;
- approved Experience and managed Skill can be read through an exact ArtifactRef;
- external registration resolves only when visible in the current scope, its local binding is available, and its
  fingerprint matches;
- approved current Experience heads have a rebuildable FTS projection; managed Skill has no automatic recall path;
- PreparedContext v1 selects active Memory plus at most two relevant approved Experiences under one total byte budget;
- publishing a managed Skill only creates a host-local projection or binding and does not change content authority.

Every Skill remains untrusted content. Review, discovery, or local resolution grants no authority to:

- load or execute inside an Agent;
- install, update, or remove a package automatically;
- publish as an MCP tool;
- access secrets, network, filesystem, or other tools;
- bypass host approval or sandbox policy.

## Implementation order

Implementation proceeds through three independently dogfoodable vertical slices:

1. Local External Skill Registry: integrate one provider for the current Agent and support local discovery, fingerprint,
   binding, and exact resolution;
2. Experience: with a generation model configured, support single-task and multi-task evidence, Review, exact read, and
   replacement Candidates driven by later tasks;
3. Managed Skill: with a generation model configured, support multi-origin Candidates, Review, Revision, host
   projection, and replacement Candidates from usage feedback.

None of the slices requires a generic distillation framework, complex ranking, or automatic publication first.

## Acceptance

| Scenario | Passing condition |
| --- | --- |
| Experience shape | situation/action/outcome/lesson exist and evidence resolves exactly |
| Cross-task Experience | One Candidate cites evidence from several sessions/tasks; Artifact identity is not session/task-bound |
| Experience evolution | A later task proposes a replacement against an exact target; the old Revision remains readable |
| Conflict handling | Contradictory evidence is narrowed, split, or made explicit rather than merged by similarity |
| Scope isolation | Cross-task aggregation cannot exceed the tenant/project scope authorized by the caller |
| Managed Skill shape | name/description/instructions/validation meet minimum requirements |
| Managed Skill provenance | Lineage follows its actual origin: Experience, Source, external snapshot, or usage evidence |
| External authority | The external package remains authoritative; Registry/index is rebuildable and does not rewrite it |
| Exact external version | Local content changes produce a new fingerprint; a stale registration no longer resolves as available |
| Local binding | Resolution returns only a local entry point that exists, is readable, and matches the fingerprint for the current Agent, host, and scope |
| Review gate | Experience/managed Skill create, revise, import, and fork produce only pending Candidates |
| LLM gate | Without a configured model, no Experience/managed Skill Candidate is generated and generation returns a typed capability error |
| No-LLM baseline | Local external discovery, existing Candidate Review, and approved Artifact exact read still work |
| Retrieval gate | Pending and rejected content never enters Artifact or Skill discovery |
| Experience recall | Only approved current Experience heads are eligible for scope-local FTS recall and PreparedContext |
| Execution boundary | Discovered, approved, or resolved Skills are not automatically installed, loaded, executed, or authorized |
| Compatibility | Memory item rendering, flush, MCP, Codex Hook, and the PreparedContext v1 public envelope remain unchanged |

# Drawbacks

- Separating external registration from managed Artifact adds a concept, but avoids two content authorities.
- An external Skill may move, disappear, or drift, so Registry must refresh local availability.
- Cross-task evidence creates real conflicts, and Review costs more than producing isolated summaries per task.
- Experience/managed Skill generation requires a configured model, increasing deployment and runtime cost.
- The first version supports only Skills locally available to the current Agent environment; bindings from another
  Agent, host, or user cannot be reused directly.
- An instruction-only managed Skill does not cover every scripts-and-assets package; richer packaging needs later work.

# Rationale and alternatives

| Alternative | Decision |
| --- | --- |
| Bind Experience to one task/session | Rejected; it degenerates into task summary and cannot form durable judgment |
| Require every Skill to be distilled from Experience | Rejected; it excludes Agent-native Skills, official docs, and human authorship |
| Treat procedure as the entire Skill | Rejected; it omits discovery, entry points, compatibility, resources, and installation location |
| Copy every external Skill into an Artifact | Rejected; it creates a second authority and hides upstream version drift |
| Treat a local binding as a cross-Agent contract | Rejected; a local locator is valid only for its Agent, host, and installation scope |
| Install or load an external Skill immediately after discovery | Rejected; discovery and reference do not grant execution authority |
| Merge highly similar Experience or Skill automatically | Rejected; similarity does not establish the same scope or content authority |
| Add a workflow/DAG and package runtime now | Rejected; first prove the Experience, local Registry, and managed Skill loop |
| Add graph/sparse/reranking now | Rejected; evaluate a baseline on real cross-task data first |

# Prior art

- RFC 0001 places Memory, Experience, Routine, and Skill on the Artifact registry and self-evolution foundation, and
  defines mount as a projection of governed content into an Agent. This RFC separates Experience, Procedure, and Agent
  Skill explicitly.
- RFC 0014 defines cross-session Artifact identity and exact evidence. This RFC applies those durable-asset principles.
- RFC 0016 defines a shared inference boundary. Experience/managed Skill generation requires a model through that
  boundary, while External Skill Registry depends on neither a model nor a provider SDK.
- RFC 0031 defines Candidate and Review Inbox. This RFC reuses its envelope, lifecycle, CAS, and approval transaction.
- Repository `skills-lock.json` already describes restorable external Skills with source, ref, skill path, and content
  hash. This RFC borrows its exact-content idea for a local Registry without turning a lock file into a runtime database.

# Unresolved questions

The following must be resolved before implementation:

- how the first provider for the current Agent derives stable identity within scope, a local locator, and a fingerprint
  for a multi-file package;
- how generation capability checks model configuration and maps provider/model failures to stable typed errors;
- exact bounds for text fields, evidence count, registration, and bindings;
- which task or usage outcome Source powers the first dogfood integration.

The following do not block this RFC:

- whether Routine or Procedure becomes another Artifact Family;
- a managed package format for scripts, templates, and assets;
- cross-Agent/host Skill handoff, automatic installation, publication, removal, and format conversion;
- retirement, ranking, and usage attribution for Experience and Skill;
- selection and budgeting rules for Skill and any later contributor in a multi-Artifact Context Profile.

# Future possibilities

After real cross-task data exists, PowerContext may:

- generate new Experience or managed Skill Candidates from Skill usage feedback;
- explicitly import or fork an external Skill into a governed managed Skill;
- build rebuildable search projections for managed Skill and external descriptors;
- decide whether Skill should join Memory and Experience in a multi-Artifact Context Profile;
- evaluate local availability, freshness, conflict, and useful-use rate on fixed tasks;
- consider graph, sparse retrieval, reranking, automatic recommendation, and automatic publication only after measured
  benefit.

Every extension preserves two boundaries: sessions and tasks bound evidence, not the evolution of durable assets; and
external Skill discovery describes only locally available content in the current Agent environment, while only a
governed managed Artifact Revision is Skill content that PowerContext owns.
