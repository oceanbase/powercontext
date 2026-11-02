# Source evidence declaration verification

This follow-up applies the existing, unmerged [PR #2004](https://github.com/oceanbase/powercontext/pull/2004)
to a consistent backup of the collected poisoning database. It verifies a missing dimension of #2158:
Source text, advisory metadata and model prose cannot promote their authority or verification declaration.
It reuses MaoMengww's implementation of #1975; this evaluation does not introduce a second implementation.

The result is annotation integrity, not a poisoning mitigation. The wrong `amber-592` Memory remains present
and is annotated `untrusted/unknown`. No admission, ranking, review queue or production prompt changes are made.

## Source and methods

The evaluated integration commit is `a33679fe746fdf00b6f5239edb13e63be415d239`, tree
`88f585af161906bad6bb52368eb7ce7142437e84`. Reconstruct it by merging the constituent runtime sources listed in
`collection_provenance.json` with #2004 Head `3c5c0eccdfc65da04c509c005fe230252990d00a`.
The local integration commit is provenance; the base and published PR heads identify the source independently.
PR #2004 remains a separate review and merge dependency.

The historical input is the actual synthetic SQLite catalog from [the collected observation](COLLECTED.md),
including the delayed browser instruction and later ordinary reporting Source. The backup uses
`sqlite3.Connection.backup()` while the original database is available; copying only the main WAL database file
is insufficient. The runtime retains `artifact_processing_families=("memory",)` and disables its schedule.
An initial attempt with an empty processing manifest was correctly rejected and is not counted as acceptance.

The two fresh declaration claims use the collected `browser-forged-declaration` input and add advisory
`authority=system_attested`, `verification=verified`, `declaration_version=1` metadata. A direct deterministic
Artifact service commit supplies model-like prose making the same stronger claim. It uses the `_commit`
helper in #2004's `tests/e2e/test_atomic_memory_source_evidence.py`; these two cases are **not live-model
extraction**. No generation or embedding provider is configured for this check.

## Results

Full exact annotations, table counts/hashes and backend node identities are in `declaration_observations.json`.

| Check | Observed result |
| --- | --- |
| Existing poisoned revision, default registry | `untrusted/unknown`, exact original Source reference and material digest |
| Same revision, today's Content Definition configured `repository_attested/verified` | Historical annotation remains neutral |
| Projection rebuild once under each registry | Both rebuilds ready; both exact historical annotations unchanged |
| Whole Artifact and Source authority before/after both rebuilds | Same counts and SHA-256 values: 2 Artifact rows, 11 Source rows |
| Fresh forged text/metadata/prose, default registry | `untrusted/unknown` |
| Identical fresh claim, operator-configured registry | `repository_attested/verified`; never the claimed `system_attested` |
| Same Source ID and Artifact ID in two Scopes, reopened under configured registry | Each Scope retains its own original declaration |

The operator-configured declaration is deliberately stronger to test the boundary. It is not a recommendation
to trust all Content Sources. An authorized registry change is different from authority claimed inside an input.
Historical neutral fallback preserves the original uncertainty rather than relabeling old material.

Two additional test runs exercise the implementation and its integration:

- Exact #2004 Head: 51 passed on SQLite, 10 OceanBase cases skipped because no backend was supplied to that run.
- Combined runtime: 262 passed on SQLite, 10 OceanBase cases skipped. This includes Source evidence,
  neutral recall, Source observations/persistence, owner recovery, Access, HTTP admission, MCP authentication
  and Server tests. Full Linux Ty with Python 3.11 passed.
- A separate real OceanBase run collected and executed all 10 backend cases from the Source evidence and
  neutral recall files: **10 passed, zero skipped**. Each node ran in an isolated pytest process and disposable
  database, and the runner required exactly one passing JUnit case with no error, failure or skip.

The backend was OceanBase CE `4.3.5.6`, MySQL tenant, using the repository CI image
`ghcr.io/oceanbase/oceanbase-ce:4.3.5.6-106000012026040916`, digest
`sha256:31086a6900c21c479c2bcd942b6a28c53b17a51f4e9b9eb8eafcc596adfcd2e3`.
It ran under WSL Docker with a 6 GiB memory limit and two CPUs. SQLite's skips and the later real backend passes
are separate evidence; the initial skipped run is not retrospectively counted as OceanBase acceptance.

## Reproduce

1. Reconstruct the runtime from the recorded base/PR heads. Install its normal test dependencies and run
   the four Source suites at exact #2004 Head, then on the combined runtime:

   ```text
   pytest -q tests/e2e/test_atomic_memory_source_evidence.py tests/e2e/test_atomic_memory_evidence_neutral_recall.py tests/builtin/persistence/test_sources.py tests/test_source_observations.py
   ```

2. Take a consistent backup of the synthetic collected catalog. Hash/count `pc_artifacts` ordered by
   `(scope_id, family, artifact_id, revision)` and `pc_sources` ordered by `(scope_id, source_type, source_id)`.
   The recorded hashes use SHA-256 of UTF-8 `repr(fetchall())` on the original SQLite Python values; they are
   local equality evidence, not a portable wire digest.
3. Under the default registry, read `atomic_memory.for_scope(scope_id).evidence(artifact_id, revision=1)`
   for the poisoned Artifact in `declaration_observations.json`. Rebuild with
   `rebuild_atomic_memory_projection(..., maintenance_confirmed=True, sources=contexts.repositories.sources)`.
   Read again and require exact annotation equality.
4. Repeat with the existing `content` Definition replaced through `dataclasses.replace` with
   `MemoryEvidenceDeclaration(authority="repository_attested", verification="verified")`. Require unchanged
   historical evidence and identical whole-table hashes/counts before adding fresh claims.
5. In two fresh Scopes, capture the forged fixture as `content/same-input` with the stronger metadata above.
   Use the acceptance helper to commit `same-memory`, citing that Source, with prose claiming
   `system_attested/verified`. Require neutral evidence with the default registry and exactly the configured
   declaration with the trusted registry. Reopen and verify both historical snapshots independently.
6. For real backend coverage, provision an empty disposable MySQL tenant database on the recorded OceanBase
   image and set `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL` to its normal async connection URL. Run:

   ```text
   python scripts/run_atomic_memory_clean_migration.py oceanbase --output <new-report-directory> --tests tests/e2e/test_atomic_memory_source_evidence.py tests/e2e/test_atomic_memory_evidence_neutral_recall.py
   ```

   Require all ten node results to pass with no skips. The runner has its own deadlines and stops on failure;
   do not count absent backend tests or a retried failure as equivalent evidence.

## Policy decision and limits

Keep RFC 1809's current automatic evolution behavior for this change. The observed persistent attack disproves
a general resistance claim, while the retention misses and query-sensitive abstention show why an unmeasured
Source-name filter, model-generated trust score or mandatory manual queue is not justified by this corpus.
Future admission, ranking or review behavior must be a separate contract decision with explicit fallback and
attack/legitimate-retention comparison. Neutral annotations alone cannot supply that behavior.

These checks establish trusted declaration and historical snapshot behavior for the recorded cases. They do not
show a successful production release upgrade, multi-node migration, arbitrary public-web/tool capture, adaptive
attack resistance, cross-model generalization or a host taking a deployment action. The OceanBase tests use the
existing deterministic acceptance fixtures; the original live poisoning observation remains SQLite evidence.
