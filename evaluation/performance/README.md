# Performance and capacity benchmarks

These suites measure runtime latency, throughput, storage costs, and capacity limits.
Run their commands from the repository root with the root Python environment.

- [Experience input encoding](input_encoding/README.md): pinned public coding evidence, OFF/SHADOW/ACTIVE generation,
  real database readback, failure-inclusive usage, blinded grounding and downstream-use checks, and bounded load runs.

Add load, stress, and capacity suites here, keeping each workload in its own directory with its setup and measurement
contract. Put labeled-dataset quality evaluation in [memory](../memory/README.md).
