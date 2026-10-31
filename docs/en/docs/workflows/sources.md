---
title: Sources and capture
description: Capture evidence, inspect Source records, and configure processing.
---

# Sources and capture

Sources preserve evidence before it becomes selected knowledge. A Content Source stores captured text; other Source
types can refer to material supplied by a connector. Work Contracts and Task Outcomes also preserve Source evidence.

## Capture and inspect

1. Choose an existing Scope. The [API Quick Start](../develop/api-quickstart.md) demonstrates content capture through
   `POST /v1/sources/content` and retains the returned Source reference.
2. Use `GET /v1/scopes/{scope_id}/sources` to browse the Source journal, then
   `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` to read the selected Source.
3. To process eligible evidence into Memory, configure a generation model and either scheduled processing or an
   explicit Memory flush. See [Enable extraction and vector search](../get-started/configure-models.md).

A successful capture means evidence was stored; it does not mean extraction succeeded or that the result was worth
keeping. Use Source references to trace a generated result back to its input. Do not retry a capture with changed
content under the same identity as if it were a new observation.

## Choose a capture path

- Agent prompt hooks: use the host-specific capture switch in [Connect Agents](../integrations/index.md).
- Pre-install Codex prompts: with the Server running, use `powercontext import-sessions --host codex` to import
  historical user prompts from `CODEX_HOME` or `~/.codex` on the CLI machine. The command can write to a remote Server,
  writes ordinary Content Sources only, binds its persistent checkpoint to that Server, and skips sessions whose
  workspace cannot resolve to a Scope. Extraction remains a separate operation: configured background processing can
  still process imported Sources. Preview the scan with `--dry-run`.
- Files and object stores: use the [OpenDAL connector](ingest-text-files-with-opendal.md).
- Subject evidence for a Scope profile: follow [Scope profiles](use-profiles.md).
- Application or evaluation events: use the application's adapter; [Bub capture](../integrations/evaluation.md) is opt-in.

## Declare source authority

A trusted adapter can declare how its material should be trusted. The optional `memory_evidence` field on a Source
Definition carries a versioned `MemoryEvidenceDeclaration` with `authority` (`untrusted`, `user_asserted`,
`repository_attested`, or `system_attested`) and `verification` (`unknown`, `verified`, or `not_verified`). Only
adapter or deployment code supplies it; Source text, references, model output, and retrieval rank cannot.

```python
from powercontext.sources import AdapterSourceDefinition, MemoryEvidenceDeclaration

definition = AdapterSourceDefinition(
    existing_adapter,
    memory_evidence=MemoryEvidenceDeclaration(
        authority="repository_attested",
        verification="not_verified",
    ),
)
```

Remote Source observations configure the same declaration in trusted deployment code, keyed by the exact Definition
name, version, and manifest fingerprint. A missing, invalid, or unavailable declaration resolves to
`untrusted`/`unknown`; it does not block capture, processing, or existing retrieval.

Observation manifests describe the serialized payload for every field the payload must carry. Validation aliases do
not require workers to rename serialized keys, including when a model forbids extra fields.
For unions, an alias-renamed branch may retain the validation manifest when another branch has an equivalent flat
primitive-property schema covering its serialized output. Unproven branch coverage uses the serialization schema, except
for a field whose default is `null`: it can reach the payload as `null`, so a renamed shape below it never replaces an
accepted manifest. Populating such a field with a renamed key requires the nested model to serialize by alias, which
keeps the validation keys authoritative. Changing what a registered Definition serializes requires a new Definition
version.

Each Memory revision records the declaration and exact Source materialization accepted with it. That retained evidence
is authoritative and versioned. The index projection beside it is derived: maintenance can discard and rebuild it
without consulting today's registry. Declarations add no ranking weight, score, or access right.

Read the retained facts back for one exact revision:

```python
from powercontext.builtin.runtime import AtomicMemoryEvidenceAnnotation

memory = runtime.atomic_memory.for_scope(scope_id)
annotation: AtomicMemoryEvidenceAnnotation = await memory.evidence(artifact_id, revision=3)
for source in annotation.sources:
    print(source.source_ref.source_type, source.declaration.authority, source.declaration.verification)
```

A declaration that can no longer be attested resolves to `untrusted`/`unknown`. A record that no longer matches its
exact Source material raises `AtomicMemoryEvidenceError`, which callers catch through the `PowerContextError` family.

Source writes and reads are subject to the selected [Scope and access policy](scopes-and-access.md).
