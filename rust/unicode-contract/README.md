# Python and Unicode compatibility probes

This directory contains the isolated research deliverable for [PowerContext #2739](https://github.com/oceanbase/powercontext/issues/2739). It records Python reference behavior and inspects the fixed #2735 candidate without building, loading, selecting, or enabling a native backend.

- Python reference: `ca935e10daa68e5302cc0a5b1392618f49265932`.
- Read-only candidate: `a774fd0b8b2ac1729f5dd9121c34e968dd980905`.
- Contract: `2026-11-22.1`; report: [`REPORT.md`](REPORT.md).
- Product migration, native execution, performance, default integration, and R/F/E: **NOT_RUN / N/A**.

## Safety and fixture format

Only synthetic inputs are used. `fixtures/cases.json` stores strings as arrays of integer codepoints with explicit type tags. `fixture_codec.py` constructs them with `chr()` and joining; it never performs a UTF-16 or UTF-8 round-trip that could merge an explicit surrogate pair. Result containers retain `str`/`str` subclass, `bytes`, `tuple`, `list`, `bool`, and `int` distinctions. UTF-8 strict and `surrogatepass` results are separate observations, not the input encoding.

The matrix sets `UV_PROJECT_ENVIRONMENT` to a temporary directory and uses `uv sync --locked`; it does not install an interpreter, alter the root lockfile, or use the repository `.venv`. The fixed candidate generator and both negative controls run only in temporary directories. Raw bytes, LF-normalized bytes, and semantic JSON are compared separately.

## Reproduce the recorded Windows x64 matrix

Run PowerShell from the repository root. The three paths below are the interpreters used for the recorded evidence; substitute other **already installed** CPython paths when reproducing elsewhere. Python 3.14 is deliberately looked up, not downloaded.

```powershell
$Task = Join-Path (Resolve-Path .).Path "rust\unicode-contract"
$Evidence = Join-Path $Task "evidence"
$Fixtures = Join-Path $Task "fixtures\cases.json"
$Temp = Join-Path $env:LOCALAPPDATA "Temp\opencode\pc2739-matrix"
$Py311 = "C:\Users\AORUS\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe"
$Py312 = "C:\Users\AORUS\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe"
$Py313 = "C:\Python313\python.exe"

python -B (Join-Path $Task "probes\run_matrix.py") `
  --python "3.11=$Py311" `
  --python "3.12=$Py312" `
  --python "3.13=$Py313" `
  --missing 3.14 `
  --temp-root $Temp `
  --output (Join-Path $Evidence "matrix.json")
```

Expected recorded outcome: `recorded 26 commands; unexpected_nonzero=0; native=NOT_RUN`. The `uv python find 3.14` command is expected to exit 2 in this environment and is recorded as `BLOCKED_NO_EXISTING_INTERPRETER`; it is not counted as an unexpected failure. If an existing CPython 3.14 is available, replace `--missing 3.14` with `--python "3.14=<existing-python-path>"`; do not infer its result from UCD16 tables.

The matrix performs, per available interpreter: locked sync, identity, complete installed-package resolution, public-entry recording, same-input replay comparison, fixed-generator comparison, two targeted negative controls, and the two focused existing test files. It writes all command lines, exit codes, stdout/stderr, and durations to `evidence/matrix.json`.

## Individual probe commands

The following commands assume the variables above and the temporary environments created by the matrix. They overwrite only their named research evidence outputs.

### Interpreter identity

```powershell
$EnvPython = Join-Path $Temp "env-313\Scripts\python.exe"
& $EnvPython -B (Join-Path $Task "probes\pyver.py")
```

### Candidate inventory (read-only Git blobs)

```powershell
python -B (Join-Path $Task "probes\candidate_inventory.py") `
  --candidate a774fd0b8b2ac1729f5dd9121c34e968dd980905 `
  --output (Join-Path $Evidence "candidate-inventory.json")
```

### Real public-entry recording and replay

```powershell
& $EnvPython -B (Join-Path $Task "probes\contract_probe.py") `
  --fixtures $Fixtures `
  --output (Join-Path $Evidence "cpython-3.13.json")

& $EnvPython -B (Join-Path $Task "probes\contract_probe.py") `
  --fixtures $Fixtures `
  --output (Join-Path $Evidence "cpython-3.13-replay.json") `
  --check (Join-Path $Evidence "cpython-3.13.json")
```

The replay compares `cases` and `default_projection`, including values, types, exceptions, and offsets. It is a determinism check against a fixed Python recording, not a native comparison or an independent semantic oracle.

### Fixed generator in a disposable directory

```powershell
& $EnvPython -B (Join-Path $Task "probes\generator_probe.py") `
  --candidate a774fd0b8b2ac1729f5dd9121c34e968dd980905 `
  --output (Join-Path $Evidence "generator-3.13.json") `
  --temp-root $Temp
```

A runtime UCD outside 14.0.0, 15.0.0, 15.1.0, and 16.0.0 is explicitly `BLOCKED`; no nearest-version fallback is used.

### Targeted negative controls

```powershell
& $EnvPython -B (Join-Path $Task "probes\negative_controls.py") `
  --fixtures $Fixtures `
  --reference (Join-Path $Evidence "cpython-3.13.json") `
  --output (Join-Path $Evidence "negative-3.13.json") `
  --temp-root $Temp
```

Both mutations must make `contract_probe.py --check` exit 1 at the intended assertion: explicit-pair merging and a wrong U+105D2 assignment expectation. The wrapper then requires the original fixture and reference SHA-256 identities to be unchanged. An import failure or unrelated error does not satisfy a control.

### Existing focused tests

```powershell
& $EnvPython -B -m pytest `
  tests/builtin/artifacts/test_text_search_admission.py `
  tests/builtin/artifacts/topic_memory/test_chunking.py `
  -q -rs
```

`evidence/cpython-*.json` records the actual `powercontext.__file__`, source hashes, Jieba version/dictionary hash/total, analyzer version, and `HMM=False`. `matrix.json` records all 81 installed packages per interpreter and the test/skip counts.

## Probe lint and formatting

Use Ruff from the locked temporary environment:

```powershell
& $EnvPython -B -m ruff check (Join-Path $Task "probes")
& $EnvPython -B -m ruff format --check (Join-Path $Task "probes")
```

## Snapshot manifest

Not included in this delivery: `probes/snapshot.py` is not part of the committed change, so no
manifest is generated or referenced. Reproduce steps above are the complete, executed command set.

No command in this README builds native code, forces a Rust backend, benchmarks it, changes default routing, or establishes wheel/platform support.
