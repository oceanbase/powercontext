# PowerContext benchmarks

Benchmarks in this directory exercise public PowerContext behavior against fixed datasets. They are kept outside
`tests/` because they use real databases and model providers, create durable benchmark namespaces, and may take a
long time or incur inference cost.

- [`locomo/`](locomo/README.md): conversation-memory retrieval and end-to-end question-answer accuracy.
