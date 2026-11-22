# Topic Memory chunking compatibility experiment

This independent crate implements `markdown-v1` interval calculation. It does not select a production backend.
The Python adapter transmits UTF-32LE with `surrogatepass`, slices builtin strings and constructs the original models.
The only native direct dependency is PyO3 0.30.1; exact transitive versions are in the independent Cargo.lock.

## Environment and commands

Target acceptance uses CPython 3.14.5 / Unicode16.0.0, Rust1.95.0 and maturin1.16.0, Linux x86_64.
Set task-specific writable `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `CARGO_HOME`, `CARGO_TARGET_DIR`
and `PREK_HOME`; keep interpreter/venv/artifacts outside source control. Do not replace system Python.
For example:

```bash
export PC_TOPIC_RUN_ROOT=/tmp/topic-chunk-run
mkdir -p "$PC_TOPIC_RUN_ROOT"
export UV_CACHE_DIR="$PC_TOPIC_RUN_ROOT/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PC_TOPIC_RUN_ROOT/python"
export CARGO_HOME="$PC_TOPIC_RUN_ROOT/cargo-home"
export CARGO_TARGET_DIR="$PC_TOPIC_RUN_ROOT/cargo-target"
export PREK_HOME="$PC_TOPIC_RUN_ROOT/prek"
```

```bash
uv python install --no-bin 3.14.5
uv venv --python 3.14.5 /tmp/topic-build
uv pip install --python /tmp/topic-build/bin/python 'maturin==1.16.0'
cargo fmt --manifest-path rust/topic-chunk-rs/Cargo.toml --check
PYO3_PYTHON=/tmp/topic-build/bin/python cargo clippy --locked --manifest-path rust/topic-chunk-rs/Cargo.toml --all-targets -- -D warnings
PYO3_PYTHON=/tmp/topic-build/bin/python cargo test --locked --manifest-path rust/topic-chunk-rs/Cargo.toml
/tmp/topic-build/bin/maturin build --locked --release --manifest-path rust/topic-chunk-rs/Cargo.toml --interpreter /tmp/topic-build/bin/python --out /tmp/topic-wheels
```

Install dependencies from this checkout's locked root resolution in a separate target venv, then install the
explicit locally built core wheel followed by the experiment wheel with `--no-deps`. Do not resolve the core
from PyPI. Run acceptance from outside the checkout with an absolute interpreter path and no `PYTHONPATH`.
A `.pyi` describes the extension to type tools; it is not an executable Python fallback.

## First-slice evidence and decision

The S1 diagnostic compared 31 inputs, including model failures, across P0, P1a, P1b and R.
Every field/model identity and captured exception agreed in this diagnostic. Rust completion counter delta31.
This same-process preliminary check is not the later isolated reference recording or proof of zero fallback.
Raw medians/ranges (7 batches ×30 calls) are in `evidence/s1-results.json`.
P1a keeps the reference scanning strategy; P1b skips one regex full scan only when no LF exists.
No other dependencies or production optimization are introduced. Python diagnostic is about150 substantive lines.

| 8K structure | P0 µs | P1b µs | R full entry µs | P0 − R µs |
| --- | ---: | ---: | ---: | ---: |
| Plain | 255.6 | 79.6 | 33.7 | 221.9 |
| Markdown | 688.6 | 687.8 | 46.4 | 642.2 |
| Unicode | 289.4 | 97.9 | 46.3 | 243.1 |
| Inert LF | 244.9 | 244.2 | 36.3 | 208.6 |

At 1K, no-LF P1b (2.8–3.0µs) beats R (5.0–8.1µs); native is not uniformly preferable.
At125K, R was495–870µs versus P03.59–11.81ms. These are local diagnostic measurements, not service latency.
There is no known production call rate: 8K plain saves about0.000222 CPU seconds per call;
at10 calls/s that is about0.00222 CPU cores, at1000 calls/s about0.222, before system-level effects.
No-LF improvements include avoidable Python scanning costs; these figures do not isolate a universal language factor.
Proceed with mandatory compatibility acceptance; do not invest in speculative micro-optimization.

## Future adoption cost

All estimates below are preliminary engineering effort, excluding ongoing maintenance and review queues.

| Option | Benefits | Required work and risks |
| --- | --- | --- |
| Separate distribution | Keeps root Hatchling packaging | Coordinate two releases/versions, local-core dependency identity, platform wheel availability, failure/rollback selection, supply chain and ownership |
| Hatchling native build hook | One user-facing distribution | Design/review a platform build hook, binary inclusion and source distribution behavior, cross-build tooling and clean installation proof |
| Maturin main backend | Standard mixed PyO3 workflow | Migrate existing Hatchling/VCS/resource behavior, verify every package resource and supported installation route; affects main packaging owner |

A production proposal must budget a maintained Linux x86_64/aarch64, macOS and Windows matrix, supported
Python/ABI versions, cp314 changes, artifact provenance, upload ordering, retries/rollback, incident ownership
and each new interpreter release. Initial integration/matrix setup plausibly costs several person-days to weeks;
no measured release pipeline exists here to narrow that estimate. Only Linux x86_64 cp314 was exercised.
This operational cost can outweigh sub-millisecond savings. Keep Python as the default unless workload rate,
end-to-end significance and maintainership justify an explicitly reviewed follow-up.

No shared CI currently builds/tests this crate or wheel. Manual checks establish only the tested snapshot.
Official rust-anything3.1.0 build/record/diff/bench/gate pass through the lossless test-only tool_outcome boundary.
The tool compares67 business outcomes plus1 separately checked protocol rejection; see TOOL-ACCEPTANCE.md.
Sources and reproducible evidence stay available for review even if adoption is rejected; experimental package
publication and permanent support are separate maintainer decisions.

## Verified local acceptance

The frozen corpus contains67 unique calls:63 builtin-string calculations and4 invalid-input/missing-argument
calls. All67 match. Supported-string processing is N=R=63, F=E=0; invalid-input errors have a separate denominator.
The original12 chunking/model tests pass under explicit native selection (6 native invocations).
Real temporary SQLite publication/search and in-process HTTP API tests pass:92 tests,1196 native invocations,
F=E=0, zero skips. The25 installed adapter/protocol tests pass. These counts describe the selected tests, not repository coverage.
Synthetic embedding models verify local handling; no real model provider or remote host was exercised.

See `evidence/validation.json` and `evidence/performance.json` for machine-readable summaries.
Final8K warm medians were about33–47µs for R versus237–694µs for P0. The complete entry includes encoding,
validation, slicing and Pydantic construction. No-LF1K still favors P1b. The pilot budgets passed; they are not SLAs.
Warm-time rules were evaluated externally against the recorded samples, as shown in
`evidence/budget-evaluation.json`; the bench command itself only records samples.
Native/return timing includes decode/allocation/bridge return; it does not isolate pure Rust kernel time.
Independent measurements of components must not be summed into a causal decomposition.

Cold entry excludes import time; fresh-process wall time includes the selected backend's actual imports.
Resource workers import only the selected backend. RSS is a process high-water mark, not allocation or retained
memory; a zero increment means the workload did not exceed the import-time high-water mark. It does not mean
zero native allocation. Python live/peak tracing excludes Rust allocations and does not prove absence of leaks.
The GIL is retained. Parallel throughput was not measured because no concurrent consumer is part of this pilot.

### Reproduce the frozen result

`TASK` is this directory, `REPO` the repository root. Use separate `REFERENCE` and `NATIVE` venvs built from
exactly CPython3.14.5. The reference venv must contain the local core wheel and no experimental distribution.
The candidate venv must contain the same local core wheel plus the local native wheel. Do not install editable
copies of the main package; `pip check` and recorded installed module hashes are part of installation evidence.

A clean dependency installation can use:

```bash
uv export --locked --format requirements-txt --no-emit-project --no-emit-local --output-file /tmp/topic-requirements.txt
uv pip sync --python "$NATIVE/bin/python" /tmp/topic-requirements.txt
# Build the core with uv build --wheel --out-dir /tmp/topic-core-wheels.
# Supply exact local filenames, with dependencies already installed:
uv pip install --python "$NATIVE/bin/python" --no-deps /tmp/topic-core-wheels/EXACT_CORE.whl /tmp/topic-wheels/EXACT_NATIVE.whl
uv pip check --python "$NATIVE/bin/python"
```

The committed golden is authoritative. Recording is an explicit operation only when intentionally freezing a
new reference; a failed verification never rerecords it. Runtime, Unicode/Pydantic, source and golden hash drift
fail before comparison. Installed core and candidate sources are checked against the fixed local source.
The codec retains type, order, strings and exception type/text/args/Pydantic details. `str32` preserves surrogates;
`str-repeat` losslessly encodes an actually uniform string as char×count. JSONL is read only at LF separators.
No traceback or memory address is compared. Corpus content is synthetic and below2MiB.

```bash
cd /tmp
"$REFERENCE/bin/python" -B -I "$TASK/tools/generate_unicode_tables.py" --check
# Explicit reference recording, only when intentionally establishing the frozen oracle:
# "$REFERENCE/bin/python" -B -I "$TASK/tools/harness.py" record
"$NATIVE/bin/python" -B -I "$TASK/tools/harness.py" diff
"$NATIVE/bin/python" -B -I "$TASK/tools/harness.py" gate --output /tmp/topic-gate
"$NATIVE/bin/python" -B -I "$TASK/tools/harness.py" bench > /tmp/topic-bench.json
"$NATIVE/bin/python" -B -I "$TASK/tools/harness.py" resources --output /tmp/topic-resources > /tmp/topic-resources.json
PC_TOPIC_STATS=/tmp/topic-focused-stats.json "$NATIVE/bin/python" -B -I -m pytest \
  -p powercontext_topic_chunk_rs._pytest_plugin \
  "$REPO/tests/builtin/artifacts/topic_memory/test_chunking.py" \
  "$REPO/tests/builtin/artifacts/topic_memory/test_models.py" -q -rs
PC_TOPIC_STATS=/tmp/topic-consumer-stats.json "$NATIVE/bin/python" -B -I -m pytest \
  -p powercontext_topic_chunk_rs._pytest_plugin \
  "$REPO/tests/builtin/persistence/test_topic_memory.py" \
  "$REPO/tests/e2e/test_topic_memory_generic_api.py" -q -rs
# Collect adapter tests from the installed wheel, avoiding source-package shadowing:
"$NATIVE/bin/python" -B -I -c 'from pathlib import Path; import powercontext_topic_chunk_rs as p; import pytest; raise SystemExit(pytest.main([str(Path(p.__file__).parent / "tests"), "-q"]))'
```

Run SQLite/API tests on a host permitting local thread/event-loop wakeups; the restricted execution sandbox
stalled the unmodified reference too. No production service or credential is necessary.
The selector requires a single process. A zero-call collection is a failure, not native acceptance.
The launcher never inserts the source Python package on `sys.path`; candidates come from installed wheels.

The task-owned gate runs positive diff, serialized-ordinal mutation, actual native-range mutation with a normal
native completion count, swallowed reference trap, removed profile hook, spoofed native binding/counter,
missing extension in a disposable package copy, and restored positive diff. Mutation failure must identify the
intended comparison or path check. All changes are confined to disposable processes/copies; the delivery is untouched.
The profile hook has sticky violations and final health checks. It detects accidental fallback, including captured
private helper aliases; it is not a sandbox against malicious Python code. Source import checks supplement it.

For new-file repository gates, use an explicit non-ignored `git ls-files --others --exclude-standard` inventory;
`prek -a` omits untracked files. Local Ruff keeps root rules and adds only narrow test/subprocess exceptions.
The root ty check is required independently of target3.14 acceptance. Apache headers were checked locally;
license-eye CI and a maintained native CI matrix have not run.

`tools/snapshot.py --output /tmp/topic-snapshot.json --input <explicit-artifact> ...` records the contract,
HEAD, raw porcelain status, staged/unstaged diff hashes, sorted untracked file modes/types/hashes, submodules,
whitelisted build variables and explicitly selected artifact/tool/dependency evidence. The snapshot ID is SHA256
of UTF-8 sorted-key compact JSON with NaN forbidden. Keep output outside the checkout to avoid self-reference.
Raw local logs, wheels, environments and snapshots are stored outside the repository and must be archived before
cleaning that evidence directory. Do not commit binaries, caches, virtualenvs or raw logs.

A-08 local build/install/tool evidence is complete through the explicit lossless test wrapper and native receipt.
This local evidence does not authorize closing the issue. CI, package release,
default integration and publication remain separate decisions; this workspace contains no new commit or push.

The final independent technical review passed the local implementation and exercised acceptance on the frozen
source/artifact snapshot. See task status and `evidence/validation.json` for the current reviewed snapshot and
report digest. Local technical acceptance does not imply default integration, CI or maintainer acceptance.

## Official tool acceptance

The checked-in tool configuration, package export and library name support the actual official workflow.
A lossless test wrapper preserves all67 frozen business inputs/outcomes, including surrogates and detailed
errors, while the existing D1 algorithm/adapter/models remain authoritative. Native receipts and actual
mutation controls supplement the official tool's weaker raw codec and exception comparator.

See [TOOL-ACCEPTANCE.md](TOOL-ACCEPTANCE.md) for exact commands, the additional malformed-protocol case,
strict native receipt, distinct tool/wheel binaries, negative controls and packaging precautions.
The tool wrapper benchmark includes test serialization and receipt I/O; use the unwrapped D1 benchmark
for performance decisions. The experimental production default remains Python.
