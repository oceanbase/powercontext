# PowerContext tool argument reference

This API reference is identical in both evaluation configurations. It supplies the top-level JSON argument
names and types omitted by the built-in mock tool schemas. It does not prescribe which operations to select.

Source: `openapi/powercontext.yaml`; validated by `validate_suite.py`.

Fields listed as required must be present in the tool's argument object. Optional fields may be omitted.
Defaults are shown after `=`. Named types refer to OpenAPI component schemas; this concise reference does
not replace those schemas or enforce their constraints. It contains no scenario values or expected results.

## mcp__powercontext__acknowledge_handoff

- Required: `receiver: string`, `scope_id: string`, `selection: "prepared" | "exact"`, `source_id: string`, `status: "accepted" | "needs_clarification" | "declined"`.
- Optional: `message: string | null`, `prepared: PreparedHandoff | null`, `receiver_checks: ReceiverChecks | null`, `revision: ArtifactReference | null`.

## mcp__powercontext__activate_handoff

- Required: `boundary_source: SourceReference`, `objective: string`, `scope_id: string`.
- Optional: `evidence: array<HandoffCitation> = []`, `max_bytes: integer = 8000`.

## mcp__powercontext__approve_candidate

- Required: `candidate_id: string`, `expected_version: integer`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__capture_content_source

- Required: `content: string`, `scope_id: string`, `source_id: string`.
- Optional: `metadata: object | null`.

## mcp__powercontext__clear_scope_binding

- Required: `key: ScopeBindingKey`.
- Optional: (none).

## mcp__powercontext__commit_handoff

- Required: `handoff: PreparedHandoff`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__continue_handoff

- Required: `scope_id: string`, `selection: "prepared" | "exact" | "latest"`.
- Optional: `prepared: PreparedHandoff | null`, `revision: ArtifactReference | null`.

## mcp__powercontext__create_dream_run

- Required: `idempotency_key: string`, `operation: "refine_experience" | "derive_skill" | "revise_skill" | "revise_profile" | "revise_memory" | "revise_topic_memory" | "refresh_handoff" | "revise_prompt" | "revise_tags"`, `scope_id: string`.
- Optional: `artifacts: array<ArtifactReference> = []`, `memory_citations: array<MemoryCitation> = []`, `sources: array<DreamSourceReference> = []`, `tag_target: TagDreamTarget | null`, `target: ArtifactReference | null`.

## mcp__powercontext__create_scope

- Required: `idempotency_key: string`, `summary: string`, `title: string`.
- Optional: `context_references: array<string> = []`, `external_references: array<ScopeExternalReference> = []`, `parent_scope_id: string | null`.

## mcp__powercontext__create_work_contract

- Required: `contract: WorkContract`, `scope_id: string`, `source_id: string`.
- Optional: (none).

## mcp__powercontext__finalize_handoff

- Required: `draft: HandoffDraft`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__get_candidate

- Required: `candidate_id: string`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__get_dream_run

- Required: `run_id: string`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__get_handoff_report

- Required: `selection: ScopeSelection`.
- Optional: `download: boolean = false`, `format: "json" | "markdown" = "json"`.

## mcp__powercontext__get_memory_entry

- Required: `citation: MemoryCitation`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__get_scope

- Required: `scope_id: string`.
- Optional: (none).

## mcp__powercontext__get_topic_memory

- Required: `artifact: ArtifactReference`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__handoff_current_work

- Required: `handoff: CurrentWorkHandoff`, `scope_id: string`, `source_id: string`.
- Optional: (none).

## mcp__powercontext__list_candidates

- Required: `scope_id: string`.
- Optional: `candidate_kind: "artifact" | "tag" | null`, `cursor: string | null`, `family: "experience" | "skill" | "profile" | "memory" | "topic-memory" | "handoff" | "prompt" | null`, `limit: integer = 50`, `status: "pending" | "approved" | "rejected" = "pending"`.

## mcp__powercontext__list_dream_runs

- Required: `scope_id: string`.
- Optional: `cursor: string`, `limit: integer = 20`, `operation: "refine_experience" | "derive_skill"`, `status: "queued" | "running" | "succeeded" | "failed"`.

## mcp__powercontext__list_memory_entries

- Required: `scope_id: string`.
- Optional: `include_inactive: boolean = false`, `tag_filter: TagFilter`.

## mcp__powercontext__list_scopes

- Required: (none).
- Optional: `binding_integration: string`, `binding_kind: string`, `cursor: string`, `external_reference_kind: string`, `limit: integer = 50`, `parent_scope_id: string`, `query: string`, `query_field: "scope_id" | "title" | "summary" | "external_reference_value" | "binding_external_id"`.

## mcp__powercontext__publish_artifact

- Required: `idempotency_key: string`, `source: ArtifactAddress`, `target_scope_id: string`.
- Optional: (none).

## mcp__powercontext__query_code

- Required: `operation: CodeStatusOperation | CodeChangesOperation | CodeMapOperation | CodeSearchOperation | CodeRelationOperation | CodeTestsOperation | CodeReadOperation`, `scope_id: string`.
- Optional: `before_fingerprint: string | null`, `expected_fingerprint: string | null`, `max_bytes: integer = 16000`.

## mcp__powercontext__record_task_outcome

- Required: `outcome: TaskOutcome`, `scope_id: string`, `source_id: string`.
- Optional: (none).

## mcp__powercontext__reject_candidate

- Required: `candidate_id: string`, `expected_version: integer`, `reason: string`, `scope_id: string`.
- Optional: (none).

## mcp__powercontext__remember_memory

- Required: `kind: string`, `scope_id: string`, `text: string`.
- Optional: `expected_revision: integer | null`, `reason: string | null`.

## mcp__powercontext__resolve_scope_binding

- Required: (none).
- Optional: `allow_default: boolean = true`, `binding_keys: array<ScopeBindingKey> = []`, `explicit_scope_id: string | null`.

## mcp__powercontext__retire_memory_entry

- Required: `citation: MemoryCitation`, `scope_id: string`.
- Optional: `reason: string | null`.

## mcp__powercontext__revise_candidate

- Required: `candidate_id: string`, `expected_version: integer`, `proposal: ExperienceProposal | SkillProposal | ProfileWriteContent | MemoryDreamCandidateProposal | TopicMemoryDreamProposal | HandoffContent | PromptContent | CatalogChangeProposal`, `scope_id: string`.
- Optional: `artifact_refs: array<ArtifactReference> = []`, `artifacts: array<ArtifactReference> | null`, `memory_citations: array<MemoryCitation> | null`, `reason: string | null`, `source_refs: array<SourceReference> = []`, `sources: array<DreamSourceReference> | null`, `target: ArtifactReference | null`.

## mcp__powercontext__revise_memory_entry

- Required: `citation: MemoryCitation`, `kind: string`, `scope_id: string`, `text: string`.
- Optional: `reason: string | null`.

## mcp__powercontext__search_memory

- Required: `query: string`, `scope_id: string`.
- Optional: `limit: integer = 10`, `mode: "auto" | "fts" | "vector" | "hybrid" = "auto"`, `tag_filter: TagFilter`.

## mcp__powercontext__search_topic_memory

- Required: `query: string`, `scope_id: string`.
- Optional: `limit: integer = 10`.

## mcp__powercontext__set_scope_binding

- Required: `key: ScopeBindingKey`, `scope_id: string`.
- Optional: (none).
