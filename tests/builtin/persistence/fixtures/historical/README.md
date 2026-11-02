# Historical Server storage fixtures

These fixtures contain synthetic data written through the HTTP APIs of unmodified
PowerContext release tags. They are test inputs, not runtime source registrations.

| Fixture | Origin | Stored facts |
| --- | --- | --- |
| `first-release.json` | SQLite, `v0.0.1` | Four manual Memory entries |
| `memory-release.json` | SQLite, `powercontext-v1.0.0` | Four manual Memory entries |
| `evolved-atomic.json` | SQLite, 1.0.0 → 1.1.0 → 1.2.0 → 1.3.0 | Four original entries, official Atomic conversion, three new entries |
| `seekdb-memory-release.json` | Native embedded seekDB, `powercontext-v0.1.0` | Four manual Memory entries |
| `oceanbase-first-release.json` | Native OceanBase 4.6.3.1, `v0.0.1`, inherited GBK heap tables | Four manual Memory entries |

SQLite captures include `sqlite_schema` and relational rows. Virtual-table shadow
rows are omitted: restoring the ordinary tables and triggers rebuilds the search
projection. Native captures contain `SHOW CREATE TABLE` output and relational
rows. Binary values use an explicit hexadecimal representation; native timestamps
use ISO text. No provider credentials or user data are included.

The regression tests exercise the ordinary migration runner: a read-only plan,
backup selection, historical normalization, the registered revision chain, final
verification, search and repeat execution. Native tests also interrupt execution
between a committed DDL operation and its durable completion record. The original
intent must authorize recovery; the retry must retain the original backup.

The OceanBase GBK case uses the native 0.0.1 capture to test the inherited character
set on heap-organized tables, including an interruption after a committed table
rename. Database qualifiers in this capture are removed so the same definitions
can be restored in isolated test databases. Real release installation and HTTP
acceptance remain separate evidence from restoring a captured fixture.
The loader omits the explicit default `FTS_INDEX_TYPE = PHRASE_MATCH` display
option so older supported OceanBase servers can load the same logical fixture.

Future-revision tests append two simulated future feature migrations (9.8.0 and 9.9.0 test identifiers) to the
installed bundle. They cover both historical and managed sources, with and without
vectors. They verify extension of the mechanism; they do not certify an unreleased
PowerContext version or replace that version's feature-specific migration tests.
