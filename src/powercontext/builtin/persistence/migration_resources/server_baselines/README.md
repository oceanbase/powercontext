# Complete Server source baselines

Each resource records the complete backend schema inventory produced by a
specific installed public wheel. Inventories include indexes, constraints,
triggers, virtual tables, and SQLite shadow tables where applicable. They contain schema
definitions and artifact provenance, with no business rows or deployment paths.

Recognition identifies a source schema. It does not validate its business data,
adopt a schema revision, authorize writes, establish Server readiness, or promise
an executable upgrade path. The maintenance runner keeps an identified source
blocked until the registered migration chain covers that complete source and its
domain preconditions.

## SQLite 1.3.0

`1.3.0/sqlite.json` was captured from the public
`powercontext-1.3.0-py3-none-any.whl`, SHA-256
`8da7983763f3a6ab80b413c770a4973a46c557a54e1a1fa675099e58d47305e5`.
The wheel was installed with its `cli,server` extras in an isolated environment.
A real Server initialized a new file with enforced Access, all Processing roles,
and no model or embedding provider. Public HTTP operations created Scopes,
Atomic Memory, a Source, pending and approved Candidates, and an Access binding
with idempotency receipts. After stopping the Server, SQLite's backup API
produced the database used for schema capture.

The resource records the Python, SQLite, and SQLAlchemy capture versions. Its
94 explicit objects comprise 81 tables, 10 indexes, and 3 triggers. The complete
schema fingerprint is
`11e8770068a5fe32dfb25681564dbbe586ba0fd0238ed01adf1b09dde8a49f22`.
This shape differs from the historical four-table migration prototype.

The recognizer uses exact definitions. A different dependency version, optional
feature, missing FTS helper, manually altered constraint, or extra persistent
object may produce an unregistered shape. Such a shape remains unknown; a
semantically similar subset is never sufficient for adoption. Additional source
shapes require their own installed-release capture and review.

## seekDB 1.3.0

`1.3.0/seekdb.json` comes from the same public wheel with its `cli,server,seekdb`
extras, `pylibseekdb==1.4.0.post1`, and engine
`5.7.25-OceanBase seekdb-v1.4.0.0`. A real installed Server initialized the
database with enforced Access, all Processing roles, and no model or embedding
provider. The same public HTTP fixture populated historical records. After the
Server exited, a maintenance connection captured all 56 tables. Their raw
`SHOW CREATE TABLE` definitions and row counts remained unchanged during the
corrected reflection.

The normalized inventory includes all four FULLTEXT indexes, their `space`
parser, and `min_token_size=3,max_token_size=84` properties. Its fingerprint is
`041ee0d1cbf114ddafab1df096f9e7debbff1edf5d3a459cf7db847b640ff76c`.
Generated foreign-key names and physical storage options are normalized by the
MySQL reflector; index identity and search semantics are retained. Recognition
requires the complete matching inventory. It does not cover optional embedding
profiles or establish an OceanBase baseline.

## OceanBase 1.3.0

`1.3.0/oceanbase.json` was captured independently from an installed public
1.3.0 Server using a direct MySQL tenant connection to
`5.7.25-OceanBase_CE-v4.3.5.6`. The container image digest is
`sha256:31086a6900c21c479c2bcd942b6a28c53b17a51f4e9b9eb8eafcc596adfcd2e3`.
The source wheel, enforced Access, all Processing roles, disabled model and
embedding providers, and public HTTP fixture match the other source captures.

All 56 tables and four FULLTEXT indexes participate in its fingerprint
`049414cd6208742a92fdbc1a2965bf37c397ce7e7f1823e7d89ab6445040fedb`.
The native audit table's `AUTO_INCREMENT_MODE='ORDER'` is retained as a schema
property, distinct from the changing auto-increment counter. Unknown or
duplicate mode declarations reject. This source differs from the seekDB
inventory and is registered only for OceanBase.

The stopped Server's table definitions, counts and data hashes were unchanged
after reflection and `plan/verify/apply` rejection. This is complete source
recognition with `server_migration_unavailable`, not a successful upgrade,
runtime-readiness check, ODP test or multi-node acceptance.
