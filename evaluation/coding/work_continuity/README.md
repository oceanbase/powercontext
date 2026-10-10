# Work-continuity evaluation

Compare full transcripts, compacted transcripts, informal summaries, and Rollover
Handoff as ways to resume coding and documentation tasks. The suite validates pinned
task inputs, assembles continuation contexts, scores recorded attempts, and compares
compatible runs.

This deterministic harness does not call a model or database. Its checked-in attempts
are synthetic fixtures for harness validation, not real-host benchmark results.

Run from the repository root:

```bash
uv sync --project evaluation --frozen
uv run --project evaluation work-continuity validate \
  --task-lock evaluation/coding/work_continuity/locks/work-continuity-v1.tasks.json \
  --attempts evaluation/coding/work_continuity/locks/work-continuity-v1.attempts-fixture.json
uv run --project evaluation work-continuity --help
```

See the [benchmark guide](docs/work-continuity-benchmark.md) for the task set,
recording protocol, metrics, report commands, and comparison requirements.

```bash
uv run --project evaluation pytest -c evaluation/pyproject.toml \
  evaluation/coding/work_continuity/tests -q
```
