# Topic Memory recovery v1

`schema.json` freezes the recognized budget/attempt layouts, target budget/block layout,
and conversion SQL for `powercontext.topic-memory-recovery.v1`. The bundle checksum binds
these bytes to the reviewed plan and recovery point. Released resources are immutable;
future formats require another revision and explicit compatibility rules.

The offline runner reuses database-maintenance locks and backup providers. It verifies
consumed allowance and converted terminal evidence before removing the attempts table.
No current Runtime models or repositories reinterpret historical records. Ordinary
startup only verifies this format or initializes empty recovery storage from the same DDL.
