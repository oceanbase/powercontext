# Historical lexical experiment measurements

These measurements describe the isolated experiment. Production integration evidence is recorded in [production results](production.md).

Measured on 2026-11-20, Intel Core Ultra 5 225H, Windows x64, CPython 3.14.3 / Unicode 16.0.0, Rust 1.97.1 MSVC, release profile. The Python reference is the unmodified `analyze_text` in `src/powercontext/builtin/artifacts/search.py`; its hash is in `reference.json`. The original reference commit `5b557788c33b57b8ef55a3f3446ee72d693270a4` has the same tracked tree as PR base `6763b07661846c19934044184995adf9183c28cc`.

## Warm measurements

One thread; verify exact outputs before timing; warm both implementations; alternate Python/Rust measurement order for nine rounds; report the median for each implementation. Times include the Python/native call boundary, argument conversion and output allocation. Document reads are outside timing. Each document contributes at most its first 16,000 characters. `reference.json` records all 24 public corpus paths; `../scripts/benchmark.py` records the fixed short query.

| Workload | Python median (ms) | Rust median (ms) | Python / Rust |
| --- | ---: | ---: | ---: |
| 12 Chinese documents (101,036 characters) | 133.866 | 15.118 | 8.85x |
| 12 English documents (140,040 characters) | 22.399 | 5.774 | 3.88x |
| One short query repeated 1,000 times | 36.630 | 3.431 | 10.67x |

`benchmark.json` contains every timing sample and the tested native binary hash. This is a desktop wall-clock microbenchmark: CPU affinity, clock rate and other system activity are not controlled. The initial trial on the same workloads measured 7.53x / 4.24x / 7.29x respectively; all those samples and its different binary identity are retained in `benchmark-initial.json`. The final table is the post-packaging-cleanup rerun, not a selection of the best sample. The rerun was performed after the pilot build and hook commands completed. Variation in absolute times and ratios limits precision; the evidence supports a local improvement, not a fixed production speedup.

These times do not measure HTTP, database access, inference, complete recall, a host integration or end-to-end PowerContext latency. The measured experiment did not integrate the extension into the normal PowerContext wheel.

## Behavior and packaging validation

- rust-anything diff: 2,033 / 2,033 exact recorded cases; all 12 gate checks passed, including build, fmt, clippy and Cargo tests.
- Strict comparison: 2,637 unique string cases match exactly, including Unicode casefold, NFC ordering, Han segmentation, reserved prefixes, UTF-8 length thresholds, apostrophes, public documents and lone/adjacent surrogates. Seven non-string inputs raise TypeError; one str subclass succeeds. Total unique inputs: 2,645; 2,648 executions include three repeated independent expected values. These are finite corpus counts, not a product coverage percentage.
- Negative controls reject an intentionally wrong result and a package with the native module absent. A wrong binary hash was also rejected during the trial. The strict checks supplement the tool's permissive exception comparison; exception messages are not part of this trial's promise.
- Existing behavior tests: 13 passed with 20 actual native calls through the test-only plugin. No existing assertions changed. Cargo: two algorithm tests passed; locked fmt/test/clippy checks passed with Rust 1.97.1 MSVC.
- A separate maturin release wheel was installed into a clean environment and verified with Python `-I`. The same strict checks passed without importing the PowerContext source package, Jieba or Pydantic. `validation.json` records the direct-extension and installed-wheel identities separately; they are different builds. The wheel and native binaries are not committed.
- Ruff check, Ruff format and `uv run --locked ty check evaluation/performance/lexical` passed. Prek was run on the changed files. Its file/content hooks passed; its whole-repository ty hook remains failing on Windows. Running ty with `--exclude evaluation/performance/lexical` reports the same 93 diagnostics in existing files, including unavailable POSIX APIs and a checked-out symlink represented as plain text. The pilot's own type check passes; this is not a claim that the full repository check is green.
- Final independent review has not completed. These are executor measurements and checks, not reviewer approval. Full pytest, tox, all `make check` targets, real backend/host acceptance and production rollout were not executed.

## Cold observations

`cold.json` retains the initial trial's three fresh-process observations per implementation, using the binary identified in `benchmark-initial.json`. First Chinese analysis took about 325-336 ms in Python and 61-77 ms in Rust; module imports were timed separately. This small exploratory sample is not a service-startup SLA and was not repeated for the final wheel.

## Reproduce

### Reading the recorded cases

The tool's `cases.jsonl` contains raw U+2028 and U+0085 inside a recorded JSON string. They are input characters, not record separators. Python `str.splitlines()` treats them as line boundaries and produces invalid JSON fragments; a consumer that catches decoding errors and skips fragments can lose cases. Split on LF explicitly and surface any JSON error:

```python
import json
from pathlib import Path

text = Path("evaluation/performance/lexical/cases.jsonl").read_text(encoding="utf-8")
cases = [json.loads(line) for line in text.split("\n") if line]
```

This reads all 2,033 committed cases without altering their inputs. The separately generated strict corpus uses `ensure_ascii=True`, so its Unicode separators are escaped and do not have this issue.

### Running the checks

Use CPython 3.14 with Unicode 16 and the root project's locked dependencies plus Jieba 0.42.1. Install rust-anything and maturin separately. On Windows explicitly select Rust `1.95.0-x86_64-pc-windows-msvc`, matching the gateway CI version, and put the intended Python environment first on PATH; rust-anything prefers `python3` over `python`. The timing samples above retain their original 1.97.1 build identities; they are not measurements of the 1.95.0 build.

The 1.95.0 MSVC build was separately validated on Windows CPython 3.14.3: fmt, locked Cargo tests (two passed), locked clippy with warnings denied, release wheel construction and clean-environment `-I` strict validation all passed. The installed extension SHA-256 was `070d051e50482ce3e00e1041d13055d8514071d57efe6696f639677f7b6d95d3`; all 2,637 recorded strings, type checks and negative controls passed. No performance measurements were taken for that build.

From the repository root:

```text
cargo fetch --locked --manifest-path evaluation/performance/lexical/Cargo.toml
python evaluation/performance/lexical/scripts/prepare.py
rust-anything build evaluation/performance/lexical
python evaluation/performance/lexical/scripts/verify.py
rust-anything diff evaluation/performance/lexical
rust-anything gate evaluation/performance/lexical
python evaluation/performance/lexical/scripts/benchmark.py
```

For existing tests set PYTHONPATH to the checkout's `src` and `evaluation/performance/lexical/scripts`, then run:

```text
python -m pytest -p native_pytest tests/builtin/artifacts/test_text_search_admission.py tests/builtin/artifacts/topic_memory/test_chunking.py -q
```

For packaging:

```text
python -m maturin build --locked --release --manifest-path evaluation/performance/lexical/Cargo.toml --out output/lexical-pilot/wheels
python -m venv output/lexical-pilot/clean-runtime
```

Install the resulting wheel with that new environment's pip (or `uv pip install --python <new-python> <wheel>`), then use its Python to run `-I evaluation/performance/lexical/scripts/verify.py --installed`. `prepare.py` must run first to generate the strict corpus. Generated evidence lives in ignored `output/lexical-pilot/`.

Only fixed-default-dictionary `analyze_text(str) -> str` on Windows CPython 3.14/Unicode 16 is covered. Other Python/Unicode versions, operating systems, free-threaded Python, custom dictionary mutation, spans/offsets and production backend selection require separate validation. `Requires-Python` is restricted to 3.14; an abi3 tag is not a broader support claim.
