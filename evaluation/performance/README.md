# Performance and capacity benchmarks

These suites measure runtime latency, throughput, storage costs, and capacity limits.
Run their commands from the repository root with the root Python environment.

| Suite | Purpose |
| --- | --- |
| [Memory capacity](memory_capacity/README.md) | Append latency, storage growth, compaction, and search identity preservation without model calls. |

Add load, stress, and capacity suites here, keeping each workload in its own directory with its setup and measurement
contract. Put labeled-dataset quality evaluation in [memory](../memory/README.md).
