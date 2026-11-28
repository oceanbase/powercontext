# Status — migration harness (`PC-RUST-2737`)

Contract `2026-11-22.1`. Facility state: **complete for the startable scope**. Product replay:
**BLOCKED** (depends on #2735 merging into `master`).

| Item | Value |
|---|---|
| Reference master | `0b22a60f783e3d43613359ff9970a36192e1adc8` |
| Delivery `snapshot_id` | recorded in `rust/migration-harness/evidence/snapshot.json` (the manifest is the authority; copying the id here would make the described tree and the description differ) |
| Reference interpreter | CPython 3.11.14, UCD 14.0.0 (`win32`) |
| Selectors | `analyze_text`, `analyze_text_with_spans`, `lexical_terms_with_spans` |
| Corpus | `rust/migration-harness/fixtures/corpus.json`, 20 cases, sha256 `23ff7be9…` |
| Execution mode | `SELF_CHECK_ONLY` for this delivery; independent verification `NOT_RUN` |

## Verification matrix (real commands, this tree)

| ID | Item | Command / method | Result |
|---|---|---|---|
| A-01 | Real success chain | record → replay on the reference → *native* replay | Reference replay `PASS` 60/60 compared, 0 uncompared, 0 skipped. **Native replay `BLOCKED`** (#2735 OPEN) |
| A-02 | Numeric/record boundaries | `pytest …::test_codec_preserves_nan_and_signed_zero`, `::test_records_split_on_lf_only`, arbitrary-precision int cases | `PASS` |
| A-03 | Empty values and types | `::test_codec_round_trip_is_lossless`, `::test_codec_distinguishes_tuple_from_list_and_bool_from_int` | `PASS` |
| A-04 | Error propagation | `::test_codec_rejects_unsupported_types`, `::test_codec_rejects_malformed_documents`, missing-native control | `PASS` (rejects, never falls back) |
| A-05 | Unicode/object compatibility | `::test_codec_distinguishes_surrogate_pair_from_real_scalar`, `::test_corpus_keeps_the_explicit_pair_distinct_from_the_scalar`, `::test_recorded_inputs_are_not_folded_into_scalars` | `PASS` (the six surrogate fixtures are stored as `codepoints`, so the pair never collapses into a scalar) |
| A-06 | Option consumption | `::test_replay_refuses_empty_selection`, `::test_replay_refuses_unknown_selector`, `::test_replay_refuses_a_limit_below_the_selection`, `::test_replay_fails_when_a_selected_observation_was_not_recorded` | `PASS` (requested-but-uncompared coverage is `FAIL`) |
| A-07 | Redness | `negative_controls.py` — wrong value / mispaired records / wrong type / missing native; plus `::test_replay_rejects_a_truncated_recording`, `::test_replay_rejects_records_from_another_manifest`, `::test_recording_refuses_a_substituted_selector` | `PASS`, each fails on the target path; delivered files unchanged |
| A-08 | Dependencies/assembly | only stdlib + locked `pytest`; `ruff check` + `ruff format --check` clean; no production dependency added | `PASS` |
| A-09 | Resources/side effects | disposable temp copies only; delivered files fingerprinted before/after | `PASS` (`delivered_unchanged=true`) |
| A-10 | Scale | 20 cases × 3 selectors = 60 calls; measured `wall=7.535 s`, `peak=67.2 MiB` (tracemalloc, includes Jieba load) | `PASS` (harness cost only; product speed-up `N/A`) |

Counts for this run: 63 self-tests `PASS`; repository baseline
`pytest tests/builtin/artifacts/test_text_search_admission.py tests/builtin/artifacts/topic_memory/test_chunking.py -q`
→ 13 passed; `ruff check` and `ruff format --check` on the new directory → clean.

## Execution path accounting

`N = 60` recorded/replayed observations. `R = 0`, `F = 60`, `E = 0`, because no native component is
built or loaded: every call was answered by the Python reference
(`candidate.native = false`, `suffix = ".py"` in `evidence/replay-3.11.json`). This is **not** a claim
about native coverage; it is the honest state before #2735 merges.

## Evidence files

| File | Content |
|---|---|
| `rust/migration-harness/evidence/reference-3.11.ndjson` | 20 lossless records |
| `rust/migration-harness/evidence/reference-3.11.manifest.json` | CPython/UCD/source/dependency/Jieba-dictionary identity |
| `rust/migration-harness/evidence/replay-3.11.json` | 60/60 compared, candidate identity |
| `rust/migration-harness/evidence/negative-3.11.json` | 4 controls, delivered-file identity before/after |
| `rust/migration-harness/evidence/snapshot.json` | `snapshot_id`, HEAD, status/diff hashes, untracked hashes; the output path itself is excluded (`excluded_paths`) |

## Not run / blocked

- **Native product replay** — `BLOCKED`: #2735 is OPEN; no native module is built, loaded or claimed.
- Independent verification session (`FINAL_INDEPENDENT_REVIEW`) — `NOT_RUN`; this delivery is self-checked only.
- CI — `NOT_RUN` at delivery time; no CI claim is made here.
- Other interpreters (3.12/3.13/3.14), wheels, default backend selection, performance — `NOT_RUN` / `N/A`.
