# LoCoMo-Plus smoke dataset

`locomo_plus_smoke10.json` contains ten cognitive cases with their complete timestamped histories, questions,
cue evidence, and upstream annotations. It contains benchmark inputs only. Gold metadata is stored separately from
conversation content and is not passed to normal extraction or answer generation.

The snapshot uses the same cases and history assignment as `smoke --limit 10` at seed `42`. Its 253 sessions are
identical to the corresponding histories constructed from the pinned full dataset; no sessions or turns are truncated.

| Case ID | Relation | Host conversation | Sessions |
| --- | --- | --- | ---: |
| `cognitive:0000` | causal | conv-30 | 20 |
| `cognitive:0101` | state | conv-49 | 26 |
| `cognitive:0188` | goal | conv-30 | 20 |
| `cognitive:0301` | value | conv-26 | 20 |
| `cognitive:0001` | causal | conv-26 | 20 |
| `cognitive:0102` | state | conv-49 | 26 |
| `cognitive:0202` | goal | conv-42 | 30 |
| `cognitive:0302` | value | conv-50 | 31 |
| `cognitive:0002` | causal | conv-43 | 30 |
| `cognitive:0103` | state | conv-43 | 30 |

Source: [xjtuleeyf/Locomo-Plus](https://github.com/xjtuleeyf/Locomo-Plus/tree/059f4e3d38f7f1f96765e8e2cb7de3097551bffb),
commit `059f4e3d38f7f1f96765e8e2cb7de3097551bffb`.
The snapshot manifest records the source revision, seed, original sample identities, data exclusions, and
history coverage. [`../dataset.py`](../dataset.py) contains the download revision and default sample selection.
Loading validates the JSON structure without comparing file hashes.
The upstream data retains its original provenance; the pinned upstream revision supplies no explicit dataset license.

From the repository root, inspect the ten-case workload without network or model calls:

```bash
uv run python -m benchmark.locomo_plus run \
  --dataset-file benchmark/locomo_plus/dataset/locomo_plus_smoke10.json \
  --limit 10 --dry-run
```

For a real run, omit `--dry-run` and pass `--env-file`, `--judge-model`, and `--run-id` as described in the
[benchmark README](../README.md). The default smoke selection remains four cases; `--limit 10` selects the entire
snapshot. Use the upstream dataset workflow for full runs, more than ten smoke cases, or a different assignment seed.
