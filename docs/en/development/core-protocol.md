# Core protocol and composition

This page explains the stable Core boundary for developers who extend PowerContext or assemble it into another
application. For ready-to-run storage and remote access, use the Builtin profiles and Server described in the other
development guides.

## The composition root

`PowerContext` is the public composition root. It binds the selected Source, Artifact, and Trigger services:

```python
from powercontext import PowerContext

context = PowerContext(
    sources=source_services,
    artifacts=artifact_services,
    triggers=trigger_services,
)
```

The object does not discover implementations, open databases, read environment variables, or start schedulers. Those
are lifecycle decisions for the application entry point or a Builtin profile. Keeping that work outside Core lets the
same contracts run in a local process, a Server process, or an application with its own resource lifecycle.

## Domain roles

### Source

A `Source` describes evidence that an adapter can resolve or read. Source subtypes use Pydantic models, so validation
and serialization stay with the model:

```python
from typing import Literal

from powercontext import Source, SourceMaterialization


class IssueSource(Source):
    provider: Literal["github"]
    repository: str
    number: int


issue = IssueSource(
    name="oceanbase/powercontext#42",
    materialization=SourceMaterialization.REFERENCED,
    provider="github",
    repository="oceanbase/powercontext",
    number=42,
)
```

An adapter owns the mapping between its native input, its Source subtype, and the value returned by `read()`. Register
the adapter with `SourceCatalog`; callers should not reproduce adapter identity rules.

The Source catalog derives `SourceRef` values:

```python
source_ref = source_catalog.as_ref(issue)
```

This is preferable to repeating `source_type` and `source_id` at each call site.

### Artifact

An `Artifact` is one immutable revision of reusable output. An Artifact family declares its family name as a class
value and uses a `BaseModel` for structured content:

```python
from typing import ClassVar

from pydantic import BaseModel

from powercontext import Artifact


class NoteContent(BaseModel):
    text: str


class Note(Artifact[NoteContent]):
    family: ClassVar[str] = "note"
```

Persisted Artifacts already carry their identity and revision. Use `artifact.as_ref()` when another value needs an
exact reference:

```python
note_ref = note.as_ref()
```

Lineage contains Source and Artifact references used to produce that revision. The store is responsible for revision
conflicts and persistence; the family service is responsible for its domain behavior.

### Trigger

A `Trigger` is a policy over a signal and prior state. It returns a `PolicyTransition` containing the next state and
zero or more actions. A Trigger should not open storage, schedule itself, or perform the action it selects.

APScheduler belongs to the Builtin runtime lifecycle. It decides when to evaluate a policy. The Trigger decides what
the observed signal means.

## Ownership boundaries

| Concern | Owner |
| --- | --- |
| Domain models, references, protocols, composition | Core |
| Builtin Memory, relational persistence, indexes, runtime policy | `powercontext.builtin` |
| Environment-backed process configuration | `powercontext.client.settings`, `powercontext.server.settings` |
| HTTP lifecycle and optional MCP transport | `powercontext.server` |
| Provider-specific generation and embedding | Inference integration |
| Database and scheduler resource lifetime | Application entry point or Builtin runtime instance |

Core models use Pydantic `BaseModel`. Add a validator when the value has a real domain constraint. Do not add wrapper
properties for stored fields, custom JSON value hierarchies, or a second definition object when the model or protocol
already expresses the boundary.

## Choosing an integration path

Use the smallest public layer that owns the behavior you need:

- Use Core protocols when providing a new Source adapter, Artifact family, Trigger policy, or persistence adapter.
- Use `open_builtin_runtime()` for the standard Source and Memory services with either supported database.
- Use `create_server_app()` when running the standard HTTP service with optional MCP.

The [Memory guide](memory-layer.md) covers the Builtin Artifact family. The
[remote access guide](remote-access-implementation.md) covers process configuration and transports.

## Extension review

Before adding a new abstraction, check that it represents one of these:

- a stable domain value with validation;
- behavior that has more than one practical implementation;
- a resource or lifecycle boundary;
- a user-visible capability.

An alias around one implementation, a duplicate configuration model, or a property that only returns a stored field
does not create a useful boundary.
