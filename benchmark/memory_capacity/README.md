# Memory capacity benchmark

This provider-free benchmark appends one entry per Revision at 200, 1,000 and 5,000 entries, then retires 80% of the
entries, advances the tombstone recovery window, previews and commits compaction, and appends once more. It measures
canonical bytes, database bytes, mean append latency, the final 100 appends, affected projection rows, and preservation
of FTS hit identities across compaction. It retains historical manifests and entry bodies.

```bash
uv run python -m benchmark.memory_capacity --output .artifacts/memory-capacity/run-01/sqlite.json
uv run python -m benchmark.memory_capacity --backend oceanbase --output .artifacts/memory-capacity/run-01/oceanbase.json
```

Choose a new run directory for each measurement. Raw JSON, logs, and databases are local acceptance artifacts under the
Git-ignored `.artifacts/` directory. Attach reviewed results to the relevant PR or CI run and keep reusable methodology
and concise measurement summaries in this README.

SQLite creates a fresh database beside the output JSON, retained for inspection. Samples checkpoint and truncate the
WAL; the compaction report also records file bytes after `VACUUM`.
The full run requires several GB of disk because every historical manifest remains authoritative.

OceanBase requires `POWERCONTEXT_TEST_OCEANBASE_URL` pointing at a disposable test database. It creates a unique Scope;
reported database bytes cover the entire database, so use an otherwise idle database. The default observation delay is
30 seconds, adjustable with `--reclamation-delay`. No storage-engine compaction is forced. An unavailable database is
recorded as `not_run`, never represented as a measured result. No model calls are made on either backend.

OceanBase database bytes sum `OCCUPY_SIZE` from `oceanbase.DBA_OB_TABLE_SPACE_USAGE` for the selected database, including
its index tables. This measures reported SSTable occupancy, excluding memtables, transaction logs, and preallocated
cluster files; early samples can be zero before a flush. `information_schema.tables` statistics can remain zero even
after SSTables occupy space and are not used for this measurement.

`reclaimed_bytes` compares full canonical contents, including the new compaction audit records. The follow-up Revision
shows the continuing cost of the reduced manifest. Projection rows are counted using the database cursor's affected
row count; zero-row deletes do not count. FTS and active-head rows are included; immutable entry bodies are separate.

The latency sample is a local calibration, not an SLA. Default budgets remain 5,000 active entries, 10,000 manifest
entries, and 4 MiB pending deployment-specific latency requirements.


## Recorded local SQLite run

Windows, Python 3.11.9, one append per Revision; other repository validation ran concurrently, so these latencies are
indicative and must not be used as an isolated performance baseline. Byte counts are exact.

| Entries | Canonical bytes | Checkpointed database bytes | Mean append (ms) | Final 100 mean (ms) | Projection rows/append |
| --- | --- | --- | --- | --- | --- |
| 200 | 44,866 | 5,607,424 | 14.42 | 15.85 | 2 |
| 1,000 | 223,266 | 114,847,744 | 29.29 | 46.09 | 2 |
| 5,000 | 1,115,266 | 2,804,195,328 | 116.19 | 217.74 | 2 |

Compaction removed 4,000 tombstones with zero projection writes and preserved the same sentinel search hit. Complete
canonical content fell from 1,123,307 to 939,091 bytes in the compaction Revision, then to 223,489 bytes on the next
append. The audit delta accounts for the difference. Database bytes after that append were 2,815,942,656 and after
`VACUUM` were 2,815,492,096: old manifests still occupy live pages.

## OceanBase validation status

On 2026-09-26, all 13 OceanBase capacity behavior cases passed against OceanBase CE 4.3.5.6 without skips. Another
67 SQLite, HTTP/API contract, and projection regression checks passed. The complete 5,000-entry scale run and
compaction cycle also passed locally: 4,000 tombstones were removed, zero projection rows were written by compaction,
and the sentinel FTS hit identity was preserved.

| Entries | Canonical bytes | Observed database bytes | Mean append (ms) | Final 100 mean (ms) | Projection rows/append |
| --- | ---: | ---: | ---: | ---: | ---: |
| 200 | 44,866 | 73,706 | 24.69 | 25.94 | 1 |
| 1,000 | 223,266 | 73,706 | 36.14 | 47.70 | 1 |
| 5,000 | 1,115,266 | 1,679,697,682 | 99.85 | 180.02 | 1 |

The compaction revision reduced manifest entries from 5,000 to 1,000 and canonical bytes from 1,123,307 to 939,091;
the follow-up append produced 1,001 entries and 223,489 bytes. Database bytes stayed at 1,770,500,275 during the
compaction observation window, as historical revisions remain retained.
