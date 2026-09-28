# PowerContext benchmarks

Benchmarks in this directory exercise public PowerContext behavior against fixed datasets. They are kept outside
`tests/` because they use real databases and model providers, create durable benchmark namespaces, and may take a
long time or incur inference cost.

- [`locomo/`](locomo/README.md): conversation-memory retrieval and end-to-end question-answer accuracy.
- [`locomo_plus/`](locomo_plus/README.md): pinned LoCoMo-Plus factual and cognitive memory evaluation, with a
  four-case smoke profile, a bundled ten-case dataset with complete histories, and an explicit full profile.
- [`memory_capacity/`](memory_capacity/README.md): manifest capacity, append cost, and tombstone compaction without model calls.
