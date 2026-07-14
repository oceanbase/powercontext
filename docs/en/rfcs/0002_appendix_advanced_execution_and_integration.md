- Proposal Name: `advanced_execution_and_integration`
- Start Date: 2026-07-10
- RFC PR: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/pull/2)
- Tracking Issue: [oceanbase/powercontext#2](https://github.com/oceanbase/powercontext/issues/2)
- Parent RFC: [RFC 0002: Core SDK Product Model](0002_core_sdk_product_model.md)
- Related Appendix: [Types and Interfaces](0002_appendix_types_and_interfaces.md)

# Status

This integration guideline is not normative. It explains how PowerContext fits into an agent harness and host execution
environment. Schedulers, workflows, Graphs, Triggers, and agent frameworks continue to use their own protocols.

# Summary

PowerContext owns only persisted Sources, Artifact Revisions, and explicit lineage. Other runtime state remains with its
native owner:

| State or capability | Owner |
| --- | --- |
| Current-session messages, tool state, and immediate context | Agent harness |
| Original material and framework-native run state | Source provider or host system |
| Source identity, Artifact Revisions, and lineage | PowerContext Catalog |
| In-process background derivation | Internal Core policies |
| Durable scheduling, retry, recovery, and approval | Host scheduler or workflow runtime |
| File content and backend I/O | fsspec and the concrete file-backed Family |

# Recommended integration path

## Before a model call

An adapter may use best-effort retrieval of Memory or other Artifacts for the current task and inject the result through
the framework's native middleware, hook, or context provider:

```python
hits = await pc.memory.search(query, limit=5)
```

PowerContext does not choose the projection's priority, placement, or token budget in the prompt. The adapter owns
authorization, redaction, delimiters, and prompt-injection isolation. Retrieved context is reference material, not a
higher-priority policy.

## After a run completes

The adapter passes the framework-native run value to its typed Source provider and only needs to commit the Source:

```python
source_input = await pc.sources.resolve(run)
source = await pc.sources.add(source_input)
```

This step does not require the adapter to call `memory.remember()` immediately. The agent harness retains the current
session's basic content. PowerContext background policies may later group Sources and derive Artifacts.

## When generation must complete now

If the current workflow must use a newly generated Artifact, the host may explicitly await the semantic operation:

```python
memory = await pc.memory.remember(sources=(source,))
```

This is an advanced path with an explicit ordering requirement, not the default step for every agent integration.

# Background processing boundary

`Sources.add()` guarantees only that the Source has been persisted. Internal policies may initiate later processing
after an accumulation or periodic condition, but they do not provide:

- Source-to-Artifact read-after-write.
- A durable counter or schedule.
- Cross-process exactly-once execution.

Exact thresholds, batch selection, target Artifact identities, failure retry, and observability remain internal
implementation policies. Cross-process recovery, long waits, human approval, and auditable retries belong to the host
scheduler or durable workflow runtime.

# Lineage across execution systems

Artifact lineage records only the Sources and upstream Artifact Revisions actually used by one Revision. Before a
semantic operation, callers reconstruct complete objects with `get()` and pass the objects actually used into Core.

Execution-system metadata may support correlation, but it does not automatically become Artifact lineage.

Lineage commits with the Revision. Changes to code, workflows, or indexes do not rewrite an old Revision.

# Operational guidelines

- Source commit is idempotent on `(source_type, uri)`. The host still owns authentication, deduplication, and
  acknowledgement for external delivery.
- Artifact revision uses an exact base Revision. After a stale conflict, reload and decide whether to recompute.
- One explicit and bounded owner should control retries of nondeterministic generation.
- Engines, filesystems, model clients, and agent objects are constructed in the execution process rather than passed as
  durable job payloads.
- Retention and redaction of framework-native run material belong to the provider or host.
