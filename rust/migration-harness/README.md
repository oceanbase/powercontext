# Migration harness (`PC-RUST-2737`)

Reusable, versioned behavioural replay for one PowerContext migration unit: record the **Python
reference** through real public entry points, store observations losslessly, then replay them against
a candidate (a built native component or any alternative implementation) and fail on any difference.

This directory is a test/verification facility only. It does not change production algorithms, does
not switch a default backend, and does not add a production dependency.

Scope of this stage: **harness facility + isolated self-tests**. Product replay against a built native
component is `BLOCKED` until the lexical PR (#2735) is merged into `master`; see "Status" below.

## Layout

| Path | Role |
|---|---|
| `codec.py` | Lossless, LF-delimited record codec (closed type set, explicit rejection) |
| `recorder.py` | Records reference observations + source/dependency/environment identity |
| `replay.py` | Compares recorded observations against the candidate that is actually imported |
| `negative_controls.py` | Proves the comparator fails for the right reason (4 controls, disposable copies) |
| `fixtures/corpus.json` | Fixed synthetic corpus, 20 cases, public text only |
| `tests/test_harness.py` | Isolated self-tests (codec, framing, comparator, controls) |

## Commands

All commands run from the repository root. `python -m venv .venv` + `uv pip install -e ".[builtin]" pytest`
gives a reference environment; `.venv/` is git-ignored.

1. Record the Python reference (happens **before** any import substitution):

```bash
python rust/migration-harness/recorder.py \
  --corpus rust/migration-harness/fixtures/corpus.json \
  --output rust/migration-harness/evidence/reference-3.11.ndjson
```

2. Replay against the candidate (the module that is actually imported answers the calls):

```bash
python rust/migration-harness/replay.py \
  --records rust/migration-harness/evidence/reference-3.11.ndjson \
  --manifest rust/migration-harness/evidence/reference-3.11.manifest.json \
  --output rust/migration-harness/evidence/replay-3.11.json
```

Exit codes: `0` identical, `1` mismatch (paths are printed), `2` nothing comparable. A missing or
unknown selector is an error, never a fallback to the Python reference.

3. Negative controls on disposable copies:

```bash
python rust/migration-harness/negative_controls.py \
  --records rust/migration-harness/evidence/reference-3.11.ndjson \
  --manifest rust/migration-harness/evidence/reference-3.11.manifest.json \
  --output rust/migration-harness/evidence/negative-3.11.json \
  --temp-root /tmp/pc2737-negative
```

4. Harness self-tests (no native build required):

```bash
python -m pytest rust/migration-harness/tests -q
```

## Record format

One JSON object per line, `schema = powercontext.migration-harness.v1`. Values are tagged
(`$t`/`$v`), not inferred:

- `str` → codepoint array, so an explicit surrogate pair stays distinct from the real scalar
  (`"A\ud83d\ude00B"` vs `"A\U0001F600B"` cannot collide).
- `bool` before `int`; `int` as a decimal string (arbitrary precision).
- `float` keeps `-0.0`, `nan`, `inf`; `bytes` as hex; `tuple` vs `list` distinct; `None` explicit.
- structured exception → `{module, qualname, args}`; the failure identity is compared, not a message
  string.
- Anything else raises `UnsupportedType`; there is no `repr` fallback.

Lines are split on `\n` **only** — a record containing U+0085/U+2028/U+2029 is never cut by a
`splitlines()`-style reader. Unknown `$t`, extra fields and malformed payloads are rejected.

Every recorded case carries the reference fingerprint (`python`, `unicode`, `source_sha256`), and the
manifest additionally records the CPython build, Unicode database version, Jieba dictionary identity,
source tree fingerprint and resolved dependency versions.

## Status (this commit)

| Item | State |
|---|---|
| Codec round-trip, framing, rejection rules | `PASS` (53 self-tests) |
| Reference recording, 20 cases × 3 selectors | `PASS` on CPython 3.11.14 / UCD 14.0.0 |
| Reference replay (Python candidate) | `PASS`, 60/60 compared, 0 uncompared |
| Negative controls (wrong value / mispaired records / wrong type / missing native) | `PASS`, delivered files unchanged |
| **Real product replay against built native** | `BLOCKED` — depends on #2735 being merged; no native module is built or loaded here, so `R/F/E = N/A` |
| Performance / product speed-up | `N/A` (harness cost only, no product claim) |

The native figure is intentionally not guessed: without a built native component every call is
answered by the Python reference, which the replay result records as `candidate.native = false`.
