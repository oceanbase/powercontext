# Integrating the Core Protocol

This guide is for developers who implement PowerContext components. It follows a context system as it grows and
explains where the Core Protocol ends and the integration layer begins.

The types and functions below illustrate the design. They are not a proposed builtin API. Database tables, codecs,
workers, and model clients are omitted so that the protocol remains visible. Focus on how objects move through the
system and which component owns each operation.

`tests/test_context_system_e2e.py` is the executable companion to this guide. It covers session-partitioned Trigger
State, query- and owner-scoped Memory retrieval, heterogeneous Sources, zero-to-many Memory writes from one Action,
and scheduled Handoff generation. The test uses SQLite as its local database example and APScheduler as its scheduler
example. Review changes to this guide's object flow, protocol boundaries, or recommended call order against that test.
Update the test when the expected behavior changes. Its concrete codecs and worker are test fixtures, not proposed
Core APIs.

## Core boundaries

Core defines three groups of domain contracts and one composition object:

| Concept | Core types | Purpose |
| --- | --- | --- |
| Source | `Source`, `SourceAdapter`, `SourceCatalog`, `SourceStore` | Bring external working material into the system and preserve readable evidence |
| Artifact | `ArtifactDraft`, `Artifact`, `ArtifactCatalog`, `ArtifactStore` | Write and read context products with revisions and lineage |
| Trigger | `Trigger`, `PolicyTransition` | Map a Signal and State to the next State and zero or more Actions |
| Composition | `Sources`, `Artifacts`, `PowerContext` | Bind the concrete components selected by an application |

Memory generation, Handoff queries, Trigger State persistence, Action execution, and scheduling remain outside Core.
An integration may provide them without making Core depend on a database, scheduler, or model SDK.

## Stage 1: generate Memory from agent turns

After a model call completes, the integration records that turn as a Source. The Source is evidence used to generate
Memory. It is not Memory itself.

### Define the Agent Source

Concrete implementations inherit their protocols explicitly. The class definition then records the relationship
between the input, stored Source, and value returned by `read()`.

```python
from dataclasses import dataclass

from powercontext import Source, SourceAdapter, SourceMaterialization


@dataclass(frozen=True, slots=True)
class AgentTurnInput:
    session_id: str
    round_number: int
    user: str
    assistant: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTurnSource(Source):
    session_id: str
    round_number: int
    user: str
    assistant: str


class AgentTurnAdapter(
    SourceAdapter[AgentTurnInput, AgentTurnSource, AgentTurnInput]
):
    input_class = AgentTurnInput
    name = "agent-turn"
    source_class = AgentTurnSource

    async def resolve(self, value: AgentTurnInput, /) -> AgentTurnSource:
        return AgentTurnSource(
            name=f"{value.session_id}/round-{value.round_number}",
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            round_number=value.round_number,
            user=value.user,
            assistant=value.assistant,
        )

    async def read(self, source: AgentTurnSource, /) -> AgentTurnInput:
        return AgentTurnInput(
            session_id=source.session_id,
            round_number=source.round_number,
            user=source.user,
            assistant=source.assistant,
        )
```

`SourceAdapter.name` identifies one adapter registration; `Source.name` identifies one value within a concrete Source
class. Core routes reads by the adapter's exact `source_class`. The adapter name is not copied onto Source values or
used as their persistence discriminator.

This adapter uses captured materialization, so the Source contains the turn as it existed when the call completed. An
adapter backed by an external trace store could use referenced materialization and materialize the content in
`read()`. The adapter owns that choice.

### Define the Memory Artifact

Memory is an Artifact family. The family defines its content instead of adding retrieval fields or business dates to
the generic Artifact contract.

```python
from dataclasses import dataclass
from typing import ClassVar

from powercontext import Artifact, ArtifactDraft


@dataclass(frozen=True, slots=True)
class MemoryContent:
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = "memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class Memory(Artifact[MemoryContent]):
    family: ClassVar[str] = "memory"
```

`MemoryDraft.sources` records the Agent Sources actually used during generation. After the Store commits the draft,
it returns a `Memory` with an identity, revision, and lineage.

### Define a round-based Trigger

A Trigger does not read the database or call a model. It only computes a transition.

```python
from dataclasses import dataclass

from powercontext import Trigger
from powercontext.triggers import PolicyTransition


@dataclass(frozen=True, slots=True)
class SourceStored:
    source: AgentTurnSource


@dataclass(frozen=True, slots=True)
class PendingTurns:
    sources: tuple[AgentTurnSource, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerateMemory:
    sources: tuple[AgentTurnSource, ...]


class EveryAgentRounds(
    Trigger[SourceStored, PendingTurns, GenerateMemory]
):
    def __init__(self, rounds: int) -> None:
        if rounds < 1:
            raise ValueError("rounds must be positive")
        self._rounds = rounds

    def initial_state(self) -> PendingTurns:
        return PendingTurns()

    def activate(
        self,
        signal: SourceStored,
        state: PendingTurns,
        /,
    ) -> PolicyTransition[PendingTurns, GenerateMemory]:
        sources = (*state.sources, signal.source)
        if len(sources) < self._rounds:
            return PolicyTransition(state=PendingTurns(sources))
        return PolicyTransition(
            state=PendingTurns(),
            actions=(GenerateMemory(sources),),
        )
```

`PolicyTransition.actions` is a tuple. A policy may return no Action or several Actions. Action count and Artifact
count are separate concerns: one `GenerateMemory` Action may produce no Memory, one Memory, or several Memories.

### Compose the components

Concrete repositories should also inherit `SourceCatalogBackend`, `SourceStore`, `ArtifactCatalog`, or
`ArtifactStore` explicitly. This example assumes the application already provides `source_repository` and
`artifact_repository`.

```python
from dataclasses import dataclass

from powercontext import Artifacts, PowerContext, SourceCatalog, Sources, Trigger


@dataclass(frozen=True, slots=True)
class ContextTriggers:
    agent_memory: Trigger[SourceStored, PendingTurns, GenerateMemory]


context = PowerContext(
    sources=Sources(
        catalog=SourceCatalog(
            backend=source_repository,
            adapters=(AgentTurnAdapter(),),
        ),
        store=source_repository,
    ),
    artifacts=Artifacts(
        catalog=artifact_repository,
        store=artifact_repository,
    ),
    triggers=ContextTriggers(
        agent_memory=EveryAgentRounds(10),
    ),
)
```

`PowerContext` does not start workers or take ownership of repositories. It is the result of explicit composition.

### Apply transitions in the integration layer

After a Source has been stored, the integration publishes a Signal, loads the corresponding State, and calls the
Trigger. The Action handler reads Sources, calls the model, and writes the resulting Artifact.

```python
from collections.abc import Awaitable, Callable


async def record_agent_turn(
    context: PowerContext[ContextTriggers],
    state: PendingTurns,
    value: AgentTurnInput,
    extract_memories: Callable[
        [tuple[AgentTurnInput, ...]],
        Awaitable[tuple[MemoryContent, ...]],
    ],
) -> PendingTurns:
    resolved = await context.sources.resolve(value)
    source = await context.sources.add(resolved)
    if not isinstance(source, AgentTurnSource):
        raise TypeError

    transition = context.triggers.agent_memory.activate(
        SourceStored(source),
        state,
    )
    for action in transition.actions:
        turns: list[AgentTurnInput] = []
        for item in action.sources:
            turn = await context.sources.read(item)
            if not isinstance(turn, AgentTurnInput):
                raise TypeError
            turns.append(turn)

        contents = await extract_memories(tuple(turns))
        for content in contents:
            await context.artifacts.add(
                MemoryDraft(
                    content=content,
                    sources=action.sources,
                )
            )
    return transition.state
```

The function illustrates control flow. A production integration will usually append the Signal to a durable queue and
let a worker process the transition and Actions. `Sources.add()` remains a Source write. It does not imply a model call
or Artifact generation.

Passing `state` directly keeps the example short. A runtime must persist Trigger State under an application-defined
partition, such as a session, owner, or project scope. Core does not define that partition key. Keeping one global State
per Trigger can mix Sources from unrelated conversations.

### Inject Memory before the model call

Committed Memory is read while building the model request. The current turn can only be captured after the model has
returned, so Memory generated from that turn affects later requests.

```python
memories = await memory_queries.search(
    query=user_message,
    scope=context_scope,
)
request = build_agent_request(
    user_message=user_message,
    memories=memories,
)
assistant = await model(request)

state = await record_agent_turn(
    context,
    state,
    AgentTurnInput(
        session_id=session_id,
        round_number=round_number,
        user=user_message,
        assistant=assistant,
    ),
    extract_memories,
)
```

`memory_queries` belongs to the Memory family. The generic `ArtifactCatalog` does not offer `search`, because recall,
ranking, filtering, and scope rules differ between families. `context_scope` is an application-defined object; Core
does not add owner or session fields to every Artifact.

## Stage 2: add a Git Source

Adding a Source does not require changes to `Sources` or `PowerContext`. Implement the Source, its input, and its
adapter, then register the adapter with `SourceCatalog` during application startup.

```python
@dataclass(frozen=True, slots=True)
class GitCommitInput:
    repository: str
    commit: str
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GitCommitSource(Source):
    repository: str
    commit: str
    summary: str


class GitCommitAdapter(
    SourceAdapter[GitCommitInput, GitCommitSource, GitCommitInput]
):
    input_class = GitCommitInput
    name = "git-commit"
    source_class = GitCommitSource

    async def resolve(self, value: GitCommitInput, /) -> GitCommitSource: ...

    async def read(self, source: GitCommitSource, /) -> GitCommitInput: ...
```

Register both adapters when composing the application:

```python
source_catalog = SourceCatalog(
    backend=source_repository,
    adapters=(AgentTurnAdapter(), GitCommitAdapter()),
)
```

Git commits may feed a separate Trigger or contribute to Memory together with agent turns. The integration decides
which Sources contribute to an Artifact. Core only requires the Artifact lineage to contain the Source objects that
were actually used.

## Stage 3: replace a Trigger

The application selects Triggers through a typed bundle. When another implementation keeps the same Signal, State,
and Action contract, only the object in the bundle changes.

```python
triggers = ContextTriggers(
    agent_memory=EveryAgentRounds(20),
)

context = PowerContext(
    sources=sources,
    artifacts=artifacts,
    triggers=triggers,
)
```

Another `Trigger[SourceStored, PendingTurns, GenerateMemory]` could activate after session completion, manual approval,
or an evaluation result. The runtime contract stays unchanged.

Changing the State or Action type widens the replacement boundary. The owning runtime, state codec, and Action handler
must change with it. Core does not promise arbitrary interchangeability between these internal parts.

## Stage 4: connect a local database and scheduler

This section uses SQLite and APScheduler as concrete examples of a local database and scheduler. Neither choice is part
of the Core Protocol.

```text
Agent hook or Git hook
    -> write Source
    -> append Signal

APScheduler job
    -> append timer Signal

worker
    -> load Trigger State
    -> Trigger.activate(Signal, State)
    -> commit next State and pending Actions
    -> execute Action
    -> add or revise Artifact
```

A local database integration will usually persist the following data. The companion E2E implements this role with
SQLite.

| Data | Writer | Reader |
| --- | --- | --- |
| Source | Agent hook, Git hook, or another adapter integration | Action handler, SourceCatalog |
| Artifact revision | Memory or Handoff handler | Family query, ArtifactCatalog |
| Pending Signal | Hook, external event, or scheduler callback | Trigger worker |
| Trigger State by application partition | Trigger worker | Trigger worker |
| Pending Action | Trigger worker | Action handler |

Before activation, the runtime selects the State partition for the Signal. When processing it, the runtime should
commit the next State and pending Actions in one transaction. The runtime also owns Action claim, acknowledgement,
retry, and idempotency behavior.

APScheduler owns time-based wakeups. A cron job should append a Signal such as `DailyReviewDue(scope, day)`. The active
runtime then evaluates the Trigger supplied by the application. The job should not reconstruct a default Trigger,
because that would bypass the application's replacement.

A daily Handoff handler can read that scope's Memory for the day, build `HandoffDraft(artifacts=memories)`, and add a
new Artifact or revise an existing Handoff. Memory and Handoff share `Artifacts`, while their family queries remain
separate.

## End-to-end call order

One agent turn follows this order:

```text
read relevant committed Memory for the current scope
    -> build model request
    -> call model
    -> resolve and add AgentTurnSource
    -> append SourceStored Signal
    -> evaluate Trigger with persisted partitioned State
    -> execute GenerateMemory Action
    -> add zero or more Memory Artifacts with Source lineage
```

Daily Handoff uses a separate Signal and Action path while sharing the Source catalog and Artifact lifecycle.

## Boundaries to review

Review an integration against these questions:

1. Does `PowerContext` only compose explicitly configured `Sources`, `Artifacts`, and a typed Trigger bundle?
2. Do public interfaces pass Source and Artifact objects instead of backend IDs or row references?
3. Do `Sources.add()` and `Artifacts.add()` retain clear persistence semantics without implied model calls?
4. Does each Trigger remain sans-I/O and allow a transition to return zero or more Actions?
5. Does the runtime partition Trigger State without turning the partition key into a Core concern?
6. Do family queries apply application scope while workers, schedulers, codecs, and transactions remain in the
   concrete integration?

If an implementation needs to change these boundaries, discuss the Core Protocol first instead of adding an implicit
runtime convention.
