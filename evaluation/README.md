# PowerContext evaluation

Evaluation suites are grouped by what they measure. Each suite owns its dataset inputs, execution, grading,
reports, tests, command entry point, and any console or deployment tools it needs. This directory provides the shared
Python environment and build configuration.

| Directory | Purpose | Suites |
| --- | --- | --- |
| [`agent_tool_recovery/`](agent_tool_recovery/README.md) | Agent recovery after failed PowerContext MCP calls | [Agent tool recovery](agent_tool_recovery/README.md) |
| [`coding/`](coding/README.md) | Coding-task completion and continuation quality | [SWE-bench Pro](coding/swebench_pro/README.md), [Work continuity](coding/work_continuity/README.md) |
| [`memory/`](memory/README.md) | Memory extraction, retrieval, answer quality, and write-gate evidence | [Atomic extraction](memory/atomic_extraction/README.md), [LoCoMo](memory/locomo/README.md), [LoCoMo-Plus](memory/locomo_plus/README.md), [LongMemEval-V2](memory/longmemeval_v2/README.md), [Memory write gate](memory/write_gate/README.md), [Capture ownership](memory/capture_ownership/README.md) |
| [`performance/`](performance/README.md) | Load, latency, storage growth, and capacity limits | [Experience input encoding](performance/input_encoding/README.md) |
| [`skills/`](skills/README.md) | Agent instruction routing and authorization regressions | [Claude Code skill-up](skills/skill-up/README.md), [ZCode native CLI](zcode_guidance/README.md) |
| [`artifact_evolution/`](artifact_evolution/README.md) | Evaluation-owned Dream recording and faithful replay | [Artifact evolution](artifact_evolution/README.md) |

```text
evaluation/
├── pyproject.toml                 # Shared powercontext-eval environment and build
├── uv.lock
├── agent_tool_recovery/            # Agent recovery after failed MCP calls
├── artifact_evolution/             # Artifact-evolution recording and faithful replay
│   ├── src/powercontext_eval_artifact_evolution/
│   ├── tests/
│   └── README.md
├── coding/
│   ├── swebench_pro/
│   │   ├── src/powercontext_eval_swebench_pro/
│   │   ├── tests/
│   │   ├── web/
│   │   ├── deploy/
│   │   └── docs/
│   └── work_continuity/
│       ├── src/powercontext_eval_work_continuity/
│       ├── tests/
│       ├── locks/
│       └── docs/
├── memory/
│   ├── atomic_extraction/
│   ├── capture_ownership/
│   ├── locomo/
│   ├── locomo_plus/
│   └── longmemeval_v2/
│       ├── src/powercontext_eval_longmemeval_v2/
│       ├── locks/
│       ├── config/
│       ├── docs/
│       ├── scripts/
│       └── tests/
├── performance/
├── zcode_guidance/                 # Native ZCode Skill routing and authorization
└── skills/
    └── skill-up/
```

## Run a suite

Run these commands from the repository root. LoCoMo and LoCoMo-Plus modules use the root project's
Python dependencies. SWE-bench Pro, Work continuity, and LongMemEval-V2 share this directory's locked environment and
expose independent `swebench-pro`, `work-continuity`, and `longmemeval-v2` commands. Skill-up has its own validation
dependencies and pinned CLI. ZCode guidance uses the root project's dependencies and its pinned native CLI. Agent
tool recovery uses the root project's dependencies and the installed Claude Code and Codex CLIs.

```bash
# Install the shared environment, then choose a suite command.
uv sync --project evaluation --locked
uv run --project evaluation swebench-pro --help
uv run --project evaluation work-continuity --help
uv run --project evaluation longmemeval-v2 --help
uv run --project evaluation --extra capture-ownership capture-ownership --help

# Memory quality: inspect a bundled, provider-free smoke plan.
uv run python -m evaluation.memory.atomic_extraction validate
uv run python -m evaluation.memory.locomo --help
uv run python -m evaluation.memory.locomo_plus run \
  --dataset-file evaluation/memory/locomo_plus/dataset/locomo_plus_smoke10.json \
  --dry-run

# Skill regression: validate the pinned Skill and controlled fixtures locally.
uv run python evaluation/skills/skill-up/sync_skill.py --check
uv run python evaluation/skills/skill-up/validate_suite.py

# Native ZCode Skill regression: validate the pin and runner without calling a model.
uv run --locked python -m evaluation.zcode_guidance.pin --check
uv run --locked python -m evaluation.zcode_guidance.run --help

# Agent tool recovery: run the offline tests and inspect the real-host runner without calling a model.
uv run pytest evaluation/agent_tool_recovery/tests
uv run python -m evaluation.agent_tool_recovery.run --help
```

The [SWE-bench Pro console guide](coding/swebench_pro/docs/console.md) covers its Web and worker deployment.
LongMemEval-V2 uses its own suite CLI. Real dataset runs may call model providers, use databases or Docker,
create durable namespaces, and incur inference cost. Help, fixture validation, and dry-run plans do not establish
real-model or external-backend performance.

## Development and results

Run the installed suite checks together:

```bash
uv run --directory evaluation pytest -m "not live"
uv run --project evaluation ruff check evaluation/coding evaluation/memory/longmemeval_v2
uv run --project evaluation ruff format --check evaluation/coding evaluation/memory/longmemeval_v2
uv run --directory evaluation ty check
uv build --project evaluation
```

The `powercontext-eval` distribution contains five packages: `powercontext_eval_artifact_evolution`,
`powercontext_eval_swebench_pro`, `powercontext_eval_work_continuity`, `powercontext_eval_longmemeval_v2`, and
`powercontext_eval_capture_ownership`. Editable installs and wheels expose the independent `artifact-evolution`,
`swebench-pro`, `work-continuity`, `longmemeval-v2`, and `capture-ownership` commands and their suite modules.
Capture ownership requires the `capture-ownership` extra shown above.
LoCoMo behavior tests remain under [`tests/`](../tests/), including [`tests/evaluation/`](../tests/evaluation/).
Skill-up, ZCode guidance, and agent tool recovery keep their own validation tests. The [Bub replay harness](../e2e/bub/README.md) and
[product E2E tests](../tests/e2e/) cover cross-component acceptance.

Keep fixed datasets, locks, configuration examples, and scoring rules with their owning suite. Store local outputs in
a suite's ignored results directory or an explicit directory such as `.artifacts/`; keep credentials and unredacted
provider traces out of version control. Preserve dataset hashes and result schema identifiers so saved evidence
remains interpretable.

Add suites under the category describing what they measure. Keep suite-specific execution tools, storage, workers,
and Web interfaces inside that suite. Define each suite's command and errors in its own package.
Report different datasets, judges, hosts, and protocols as separate experiments.
