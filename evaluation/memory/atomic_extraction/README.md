# Atomic Memory extraction evaluation

This focused evaluation checks that Atomic Memory extraction retains durable facts while refusing to turn historical
instructions and task-specific procedures into commands for a future Agent. Its synthetic UTF-8 fixture contains no
private user content and covers both built-in extraction profiles in English and Chinese.

The three dispositions belong only to this evaluation:

- `atomic_memory`: at least one subject-bound declarative candidate is required;
- `experience_or_skill`: the evidence is procedural and must produce no Atomic Memory candidate;
- `reject`: the evidence has no durable memory content and must produce no candidate.

No disposition or routing field is added to the production `AtomicMemoryCandidate` schema.

## Offline validation and tests

From the repository root:

```bash
uv run python -m evaluation.memory.atomic_extraction validate
uv run pytest evaluation/memory/atomic_extraction/tests
```

The deterministic tests validate manifest coverage, case-declared expected literals and evidence IDs, independent
factual-retention and instruction-leakage scoring, content-free reports, CLI behavior, and the all-empty failure
control. Only expected literals declared by a case are exempted from instruction-leakage scanning. The tests make no
provider calls.

The deterministic leakage heuristic is safety-first for ambiguous sentence-initial Chinese verb-object phrases such
as `设置值` or `配置文件名`: they are treated as imperative. Declarative candidates should retain an explicit subject or
state, for example `项目的配置文件名已记录`.

## Opt-in live run

Configure an existing PowerContext Server environment file with a real generation provider. Then run:

```bash
uv run python -m evaluation.memory.atomic_extraction run \
  --env-file /absolute/path/to/provider.env \
  --run-id atomic-extraction-YYYYMMDD \
  --output-directory evaluation/memory/atomic_extraction/results/atomic-extraction-YYYYMMDD
```

The runner records the exact built-in Prompt versions, instruction SHA-256 values, compiled digests, dataset manifest
SHA-256, provider/model identity, safe model settings, timeout, request limit, candidate kind/text/evidence IDs, and
portable usage. Provider credentials and raw provider responses are never recorded.

Candidate descriptions are validated against the production content contract. Private observations retain the
kind, text and evidence IDs used by the factual-retention and instruction-leakage scorer.

`results/` is ignored because `observations.private.jsonl` contains generated candidate text. `report.json` and
`report.md` contain aggregate counts only. A provider failure is `incomplete`, not an instruction-safety pass.

Rebuild a report without making provider calls:

```bash
uv run python -m evaluation.memory.atomic_extraction replay \
  --run-directory evaluation/memory/atomic_extraction/results/atomic-extraction-YYYYMMDD
```

After reviewing a passing run, regenerate and pin only its content-free evidence for a PR:

```bash
uv run python -m evaluation.memory.atomic_extraction pin \
  --run-directory evaluation/memory/atomic_extraction/results/atomic-extraction-YYYYMMDD \
  --destination evaluation/memory/atomic_extraction/reports/atomic-extraction-YYYYMMDD
```

The `pin` command validates the strict run manifest, re-scores the private observations, requires a complete passing
run, and regenerates the public report rather than copying it. The PR description should link the pinned directory and
summarize the provider/model/settings and two quality gates. Do not copy or commit `observations.private.jsonl`.

Top-level directories under `reports/` are the current evaluation baseline and must match the current dataset and
exact Prompt identities. When either contract changes, archive the previous report directory unchanged under
`reports/archive/` and pin a new passing real-provider run. Archived reports remain bound to their original run
manifests; they do not establish acceptance of the current contract. A changed version string or digest without a
new run is not evaluation evidence.
