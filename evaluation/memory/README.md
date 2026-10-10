# Memory quality evaluation

These suites measure conversation memory, retrieval, and answer quality against labeled datasets.
Run their commands from the repository root. LoCoMo suites use the root Python environment; LongMemEval-V2 uses
the shared `evaluation/` environment.

| Suite | Purpose |
| --- | --- |
| [LoCoMo](locomo/README.md) | End-to-end Source ingestion, Memory retrieval, answer generation, and judging on LoCoMo. |
| [LongMemEval-V2](longmemeval_v2/README.md) | Long-horizon memory retrieval, prepared context, reader answers, and official scoring. |
| [LoCoMo-Plus](locomo_plus/README.md) | Factual and cognitive memory evaluation with bounded smoke and full profiles. |

Add dataset-specific Memory quality suites here. Put latency, throughput, storage, and capacity measurements in
[performance](../performance/README.md). Keep each suite's execution and grading tools with the suite; see
[evaluation](../README.md) for the shared Python environment. LongMemEval-V2 provides its own `longmemeval-v2` command.
