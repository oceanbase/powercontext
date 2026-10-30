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

Source writes and reads are subject to the selected [Scope and access policy](scopes-and-access.md).

## Cite a position within a Source

A Source reference can carry an optional `locator`. The Source identity remains `(source_type, source_id)` within
its Scope; a locator describes evidence position and never selects another Source or Artifact Revision.
HTTP inputs that use `name` instead of `source_type` accept the same locator.

```json
{
  "source_type": "document",
  "source_id": "design-note",
  "locator": {
    "kind": "line_range",
    "start_line": 40,
    "end_line": 55,
    "heading_path": "Architecture / Constraints"
  }
}
```

- `kind: sequence` carries a strict nonnegative integer `sequence`, the Source's position in the Scope journal.
  The stored journal currently starts at 1. A supplied position must match that Source's recorded position;
  a mismatched value is rejected, not used to redirect the reference. A serialized zero is distinct from no locator.
- `kind: line_range` carries strict positive integers `start_line` and `end_line`. Both endpoints are included;
  the range must contain between 1 and 200 lines. Invalid ranges are rejected with `invalid_request` on HTTP,
  with `source_locator_range` in validation details for reversed or excessive spans; they are never truncated.
- `heading_path` is optional on a line range and contains at most 1024 Unicode characters, not UTF-8 bytes.
  Lines refer to the exact decoded text supplied by the adapter. These locators introduce no byte offsets.

Atomic Memory extraction derives a journal locator from the persisted capture position for built-in captured
Content Sources and other Sources without a finer position. An adapter may report a line range in
`TextEvidence.locator`; worker observations preserve it in the text-evidence projection, and extraction keeps it
instead of guessing an offset. Adapters must report a location in their exact evidence text, not in a subsequently
edited file. Opaque sources and ordinary caller-supplied references may omit the locator. Other generation APIs
preserve explicitly supplied locators; they do not infer line numbers or heading paths. No read-time text scan or
historical backfill is performed. Existing worker manifests retain their exact fingerprints; a worker adopting
the expanded text-evidence schema must increment its Source Definition version before registering it.
Built-in Content and OpenDAL version-1 definitions keep the frozen `TextEvidenceV1` shape; their journal locators
are attached during extraction rather than changing the registered projection schema.

Locators survive candidate payloads, Artifact lineage, Handoff citations, HTTP/MCP reads and portable bundle
export/restore. Distinct positions in the same Source remain distinct citations. A missing locator stays omitted
in serialized references, preserving existing records and canonical encodings. Exact Artifact Revision and Source
eligibility checks still apply.

Server database revision `v1_4_1_006` adds the optional `pc_artifact_source_locators` relation keyed by the existing
Artifact Revision and Source-edge ordinal. Upgrade existing managed databases through the normal
[database migration workflow](../operate/database-migrations.md) before Server startup; existing lineage rows and
historical payloads are not rewritten. Atomic Memory embeddings are bound to its content, not this lineage metadata,
so this upgrade does not invalidate existing vectors or require re-embedding. A Handoff that includes a locator in
its content has a corresponding content digest, as for any other content change. Older binaries reject portable
bundles containing the new `artifact_source_locator` record kind; bundles without it remain supported.
