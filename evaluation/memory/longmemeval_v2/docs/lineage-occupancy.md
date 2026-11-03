# Scope-local lineage and near-duplicate occupancy

`longmemeval-v2 lineage-fixture` implements the evaluation-only fixture mode for
issue #1977. It uses real native Atomic Memory FTS, exact scoped revision/evidence
reads, and the in-process `PreparedContextBuilder`. It does not exercise HTTP/MCP
or a host's delivery workflow. No Runtime configuration, public schema, ranking,
write behavior, lifecycle policy, or Context Receipt is added.

## Prerequisites and execution

The loaded Runtime must include the deterministic FTS baseline from #1976 and
the exact historical Source-evidence API from #1975. A missing `evidence` API
refuses execution before fixture writes. It is never replaced with inferred
trust, Source names, current registration, or adapter audit correlations.

Install the evaluation suite and the pinned `powercontext[builtin]` source in
the same Python environment, then run:

```bash
longmemeval-v2 lineage-fixture \
  --backend sqlite \
  --runtime-revision EXACT_40_CHARACTER_RUNTIME_COMMIT \
  --dependency-state merged \
  --output-dir /path/outside/repository/new-lineage-report
```

Use `--dependency-state unmerged` when executing a pinned dependency snapshot
that has not merged. This state remains visible in the manifest, report and
decision. A snapshot run is useful local evidence, not merged-main acceptance.
The command records digests of the loaded Runtime and evaluation source trees;
the declared commit must identify the installed source. Existing report
directories are refused. The SQLite database is temporary, allocated beside the
report directory on the selected output filesystem, and removed on exit.

For OceanBase, configure `POWERCONTEXT_TEST_OCEANBASE_URL` and pass
`--backend oceanbase`. The account needs database creation/deletion privileges.
Only a newly created `pc_lineage_` database is populated and deleted; the
configured database is never populated or deleted. Credentials, connection
strings, machine paths, entry text, Source text, individual content digests and
exact entry/Source/Scope identifiers are not exported. Backend failures must not
be reported as successful or equivalent SQLite execution.

## Workload and measurement

`scope-local-lineage-fixtures-v2` fixes small, separate Scope partitions for:

- byte-identical concentration and a less-represented relevant fact;
- paraphrases grounded in shared evidence;
- distinct facts that share a Source and must all remain recallable;
- revised and historical entries with exact retained revision reads;
- recurring automation with distinct event facts;
- entries without Source evidence;
- held-out byte-identical entries without Sources citing different retained Artifact revisions;
- all six mandatory CJK forms from #1976, literal English and negative controls;
- held-out shared-source facts and held-out byte-budget pressure.

Oracles are declared synthetic fact markers, not model decisions or Source
authority claims. Held-out cases do not tune lexical thresholds. Each query
retrieves one immutable, post-admission RRF pool of at most 16 entries, with no
reranker or model. All alternatives reuse that pool and its recorded order.
Source and Artifact bodies/annotations are read under the same native Scope
boundary. Exact historical reads and before/after authority snapshots verify
that measurement does not alter retained revisions, lineage or state.

The versioned grouping keeps exact Artifact versions and declared Artifact
edges separate from exact Source materialization identity. A Source identity
includes its Scope, type, ID, digest and historical Definition manifest binding;
equal text or equal digests across different Sources do not merge identities.
Missing evidence stays unknown. Shared evidence measures concentration only;
it is not proof of factual duplication or a reason to discard an entry.

Three registered fixture arms are evaluated:

| Arm | Measurement |
| --- | --- |
| `lineage-baseline-v1` | Original pool and native final Context build. |
| `lineage-exact-collapse-measurement-v1` | First `(kind, exact UTF-8 text)` representative per equality class, projected only in memory into the same Builder. The original pool is unchanged. |
| `lineage-lexical-annotations-v1` | NFC/casefold/whitespace-normalized character-trigram Jaccard annotations at threshold 0.75. No suppression, reordering or different Context input. |

Equality projection is a counterfactual measurement, not a replacement pool or
a production deduper. No arm admits an entry from outside the original pool.
Jev is optional and absent here: manifests explicitly record `not_provided` and
no authority. No model call is made in this fixture suite.

Pool and delivered-origin aggregates include top-k Source/Artifact concentration,
exact redundant slots, lexical-neighbour occupancy, unique evidence/facts,
unknown evidence and neutral declarations. Delivered-origin fact counts describe
selected records; actual answer recall is measured against the rendered Context
bytes, so truncation cannot be mistaken for successful factual delivery.
For held-out fixtures, disjoint declared facts in a lexical-neighbour pair count
as a false relation annotation, independently of actual suppression.

Evidence accounting uses `scoped-source-artifact-evidence-v1`, pinned as
`evidence_version` in manifests and reports. `unique_evidence_count` is the
deduplicated union of direct Source materialization identities and exact scoped
Artifact citations, including family and revision. `unique_source_evidence_count`
and `unique_artifact_evidence_count` expose each part. Coverage and
`lost_baseline_evidence` use the same identity rules; Source and Artifact losses
are also reported separately. Artifact references are counted at the retained
cited version, without following current heads or recursively expanding lineage.
These are distinct direct references, not a claim of independent factual content.
Manifests without the evidence-accounting version are rejected for comparison.

Task outcomes separately record retrieval misses, final omitted facts, explicit
byte-budget drops/truncations, entry-limit omissions, baseline facts/evidence
lost by an alternative, task regressions, and negative-control false hits.
Latency separates retrieval, exact-evidence reads, and Builder work. Model calls
and model cost are zero. Counts sum across each Scope's queries; concentration,
occupancy and recall ratios are means over applicable samples. No per-entry
diagnostic or cross-Scope identifiers are exported.

## Artifacts and comparisons

One output directory contains the pinned baseline manifest, per-arm manifests,
aggregate `report.json` and `report.md`, `backend-matrix.json`, a path-free replay
command, `decision.md`, and artifact hashes. Fixture and source digests, backend
capabilities, dependency state, Python/library versions and all algorithm knobs
are comparison conditions. Timing is observed output, not a pinned input.

```bash
longmemeval-v2 lineage-compare \
  --baseline /reports/lineage-baseline-v1.manifest.json \
  --alternative /reports/lineage-exact-collapse-measurement-v1.manifest.json
```

Comparisons reject missing manifests, unknown arms, changed knobs, and every
manifest difference outside `experiment_arm`, including newly introduced fields.
SQLite and OceanBase are reported separately; their manifests cannot be compared
as if backend differences were an evaluation arm. An unexecuted backend is
explicitly `not_run`. The report's recommendation is **no product change**:
bounded synthetic results alone do not justify production suppression or ranking.

## Validation

```bash
python -m pytest -c evaluation/pyproject.toml evaluation/memory/longmemeval_v2/tests -q
ruff check evaluation/memory/longmemeval_v2/src evaluation/memory/longmemeval_v2/tests
```

Run native acceptance with the pinned evidence-capable Runtime. On an older
Runtime, the prerequisite-refusal test executes and the evidence-capable SQLite
acceptance is explicitly skipped. This does not establish backend acceptance.
Keep report directories and raw local replay evidence outside Git.
