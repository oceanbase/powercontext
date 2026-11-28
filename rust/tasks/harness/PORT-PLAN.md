# Plan — migration harness (`PC-RUST-2737`)

Contract: `2026-11-22.1` (issue #2737, parent #2736). Target branch `master`. This file is the task's
exclusive state path; root `rust/*.md` stays untouched.

## Objective

Give one migration unit a reusable, versioned behavioural replay facility that can detect wrong
results and silent fallback — without changing production algorithms.

Observable outcome: the same Python input, after recording, saving and reading back, is still the same
input (explicit surrogate pair `"a\ud85d\udf41b"` must never equal scalar `"a\U00027741b"`). A correct
native candidate passes; a wrong result or a missing native fails explicitly.

## Stages

| Stage | Content | State |
|---|---|---|
| S0 | Contract intake: read issue, verify master/PR #2735 status, freeze scope | done |
| S1 | Lossless versioned codec (closed type set, LF-only framing, explicit rejection) | done |
| S2 | Recorder: fixed non-empty selector set, corpus, source/dependency/environment fingerprints, recording before import substitution | done |
| S3 | Replay comparator: compares every recorded observation, reports paths, fails closed on missing/unknown selector | done |
| S4 | Negative controls: wrong value / mispaired records / wrong type / missing native, on disposable copies, delivered files fingerprinted before and after | done |
| S5 | Delivery snapshot (`snapshot_id`) per contract recipe | done |
| S6 | Isolated self-test suite (no native build) + reference recording/replay/controls on a real interpreter | done |
| S7 | **Product replay against a built native component** | `BLOCKED` — needs #2735 merged; no stacked or unauthenticated implementation branch is created |

## Reuse, not a new framework

The contract forbids building a generic framework with no consumer. Concretely reused:

- `search.analyze_text`, `search.analyze_text_with_spans`, `search.lexical_terms_with_spans` — the real
  public entry points a native port must reproduce.
- The `#2735` candidate's own corpus/fixture conventions (fixed public/synthetic text, codepoint-level
  identity) were read as a reference only; no file from that branch is copied or modified.
- The repository's existing pytest and Ruff configuration; no new dependency.

## Boundaries

- Only `rust/migration-harness/**` and `rust/tasks/harness/**` are added.
- `pyproject.toml`, `uv.lock`, production modules, `#2735` branch and root `rust/*.md` are untouched.
- No production credential, backend or database is used; corpus is synthetic/public text only.
- Virtual environments, caches and logs are not committed (`.venv/` is git-ignored).

## Verification order

1. `ruff check` + `ruff format --check` on the new directory.
2. `pytest rust/migration-harness/tests -q` (isolated self-tests).
3. Repository baseline: `pytest tests/builtin/artifacts/test_text_search_admission.py tests/builtin/artifacts/topic_memory/test_chunking.py -q`.
4. Record → replay → negative controls on the reference interpreter; keep the JSON evidence.
5. `snapshot.py` for the `snapshot_id` bound to the delivered tree.
6. After #2735 merges: build/install the native candidate, replay the recorded corpus against it and
   report `R/F/E` separately from the harness state.
