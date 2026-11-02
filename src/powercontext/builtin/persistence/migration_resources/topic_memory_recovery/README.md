# Topic Memory recovery v1

`schema.json` freezes the recognized budget/attempt layouts, target budget/block layout,
and conversion SQL for `powercontext.topic-memory-recovery.v1`. The bundle checksum binds
these bytes to the reviewed plan and recovery point. Released resources are immutable;
future formats require another revision and explicit compatibility rules.

The offline runner reuses database-maintenance locks and backup providers. It verifies
consumed allowance and converted terminal evidence before removing the attempts table.
No current Runtime models or repositories reinterpret historical records. Ordinary
startup verifies this format or completes registered initialization states from the same DDL.

`atomic_maintenance_sqlite_v1` records the complete pristine SQLite output of
`server atomic-memory-migrate --action apply --maintenance-confirmed`, including
FTS5 shadow tables and triggers. Only empty Atomic projection/FTS/archive rows
qualify for this continuation. Ordinary startup adds Topic tables transactionally;
offline apply retains the distinct source identity and backs up the existing file.
This source is not a registered MySQL baseline.
