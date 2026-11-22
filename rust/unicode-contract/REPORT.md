# Python / Unicode compatibility for native text boundaries

Contract: `PC-RUST-2739` / `2026-11-22.1`. Owner: DoubleCore. Parent: #2736.
This is a **read-only research deliverable**. It does not change production code, does not
select a production strategy, and does not claim native coverage.

## What was actually executed

| Interpreter | UCD | Real CPython run | Fixtures | Existing tests | Generator (disposable copy) | Negative controls |
|---|---|---|---|---|---|---|
| CPython 3.11.14 | 14.0.0 | PASS | 26 | 13 passed | PASS | PASS |
| CPython 3.12.16 | 15.0.0 | PASS | 26 | 13 passed | PASS | PASS |
| CPython 3.13.3 | 15.1.0 | PASS | 26 | 13 passed | PASS | PASS |
| CPython 3.14 | 16.0.0 | **BLOCKED** — no interpreter on this host, and this investigation is not permitted to download one | — | — | — | — |

Native/Rust execution: **NOT_RUN** for every cell (no Rust extension is built or loaded by this
task; R/F/E are therefore `N/A`, not `0`).

## Reproduce

```bash
# 1. three real interpreters + locked dependencies + full matrix; ~1 min after sync
export PYTHONDONTWRITEBYTECODE=1
python rust/unicode-contract/probes/run_matrix.py \
  --python "3.11=C:/Users/AORUS/AppData/Roaming/uv/python/cpython-3.11-windows-x86_64-none/python.exe" \
  --python "3.12=C:/Users/AORUS/AppData/Roaming/uv/python/cpython-3.12-windows-x86_64-none/python.exe" \
  --python "3.13=C:/Python313/python.exe" \
  --missing 3.14 \
  --temp-root "$LOCALAPPDATA/Temp/pc2739-matrix" \
  --output rust/unicode-contract/evidence/matrix.json

# 2. single-interpreter recording / replay check
python rust/unicode-contract/probes/contract_probe.py \
  --fixtures rust/unicode-contract/fixtures/cases.json \
  --output rust/unicode-contract/evidence/cpython-3.13.json
python rust/unicode-contract/probes/contract_probe.py \
  --fixtures rust/unicode-contract/fixtures/cases.json \
  --output rust/unicode-contract/evidence/cpython-3.13-replay.json \
  --check rust/unicode-contract/evidence/cpython-3.13.json

# 3. red test: merge a surrogate pair and poison a UCD expectation, in disposable copies only
python rust/unicode-contract/probes/negative_controls.py \
  --fixtures rust/unicode-contract/fixtures/cases.json \
  --reference rust/unicode-contract/evidence/cpython-3.13.json \
  --output rust/unicode-contract/evidence/negative-3.13.json \
  --temp-root "$LOCALAPPDATA/Temp/pc2739-matrix"
```

Evidence written to `rust/unicode-contract/evidence/`:
`matrix.json`, `cpython-{3.11,3.12,3.13}.json`, `cpython-3.13-replay.json`,
`generator-{3.11,3.12,3.13}.json`, `negative-{3.11,3.12,3.13}.json`,
`candidate-inventory.json`, `public-metadata.json`.

## Result: where versions actually diverge

26 lossless fixtures, executed against the real `search.analyze_text`,
`search.lexical_terms_with_spans`, `analyze_text_with_spans`, `chunk_topic_memory_detail` and
`prepare_topic_memory_projection` entries. All 26 cases behave identically on 3.12 and 3.13.
**Exactly one case differs on 3.11**, and it is a genuine UCD assignment gap:

| Fixture | Input | 3.11 (UCD 14.0.0) | 3.12 / 3.13 (UCD 15.0/15.1) |
|---|---|---|---|
| `ucd_1e4ec_combining` | `a` + U+1E4EC + U+0300 + `b` | U+1E4EC is `Cn` / unassigned, `combining=0`, NFC keeps 4 codepoints; `analyze_text` → `pcu61 pcu62` | U+1E4EC is `Mn` "NAG MUNDARI SIGN MUHOR", `combining=232`, NFC **composes** the mark cluster → `à` + U+1E4EC + `b`; `analyze_text` → `pcuc3a0 pcu62` |

That is the concrete failure mode a native port must not paper over: a table generated from one
UCD applied under another interpreter silently changes normalization, term boundaries and
therefore the caller's codepoint spans.

Everything else is version-stable, including the cases most likely to break a Rust rewrite:

- `nfc_j_caron` / `decomposed_j_caron`: NFC-then-casefold unifies U+01F0 and `j`+U+030C.
- `capital_i_with_dot`: `İ` → `i` + U+0307 (casefold expansion, not 1:1).
- `sharp_s` / `capital_sharp_s`: `ß` and `ẞ` both → `ss` (length-changing casefold).
- `non_ascii_numbers`, `han_and_extensions`, `apostrophes`, `non_ascii_whitespace`: `re` `\w`/`\d`/`\s`
  and Jieba run boundaries agree on all three versions.
- `explicit_surrogate_pair` (`A`+U+D83D+U+DE00+`B`) vs `pair_only` vs `scalar_only` (U+1F600):
  **the explicit pair is never merged with the real scalar.** Codepoint counts differ (4 vs 3),
  `utf-8 strict` raises while `utf-8 surrogatepass` succeeds (`41eda08042`), and both stay distinct
  through `analyze_text` and chunking.
- `lone_high_surrogate` / `lone_low_surrogate`: `analyze_text` returns a result for the string but
  chunking raises `ValidationError` (`string_unicode`) — raw string handling and the validated
  chunk boundary are different contracts and must be recorded separately.
- `crlf_emoji_combining`: CRLF and combining marks keep exact codepoint offsets; the UTF-8 byte
  column is derived separately with `surrogatepass`.

`None` / `0` / `False` reach `prepare_topic_memory_projection` as `AttributeError`
(`'NoneType' object has no attribute 'detail'` etc.), and the default-argument call is byte-identical
to the explicit `topic_embedding=None, chunk_embeddings=(), embedding_profile=None` call.

## Fixed candidate (#2735) table inventory — read-only

`a774fd0b8b2ac1729f5dd9121c34e968dd980905`, 4 tables, 337,951 bytes total:

| Table | Bytes | casefold entries | word ranges | assigned ranges | U+1E4EC assigned | U+105D2 assigned |
|---|---|---|---|---|---|---|
| unicode14.json | 83,210 | 1530 | 734 | 698 | false | false |
| unicode15.json | 84,112 | 1530 | 748 | 707 | true | false |
| unicode15_1.json | 84,154 | 1530 | 749 | 707 | true | false |
| unicode16.json | 86,475 | 1557 | 771 | 731 | true | true |

The generator `scripts/generate_lexical_unicode.py` (2,025 B,
`36e6dc3777c3fddaf7beb8bbce3fcb99d305e497bae400196ccaeb8c9cc0770f`) regenerated each table in a
disposable copy: semantic JSON match **True**, LF-normalized bytes match **True**, raw bytes match
**False** (Windows CRLF vs Git LF blobs) for all three interpreters. Generator blob unchanged after
every run. One-sample wall time: 0.236 s / 0.246 s / 0.207 s.

Cost note: the four tables are 337 KB and regenerate in ~0.2 s, so per-interpreter generation is
cheap. The expensive part is the *proof* obligation, not the artifact.

## Options for the maintainers (not selected here)

1. **Per-interpreter generated table** — cheapest to produce (0.2 s), exact per-interpreter match,
   but multiplies artifacts by supported interpreter count and needs a runtime UCD→table selector.
2. **Single multi-version table set with an explicit selector** — one build, explicit version pin;
   requires the selector to be observable (logged) or the same silent mismatch reappears.
3. **Restrict the supported UCD range** — declare a minimum UCD and reject interpreters below it
   at import; simplest correctness story, rejects some otherwise-working interpreters.

Whichever is chosen, the unknown-UCD policy must be explicit: the evidence above shows that
treating an unassigned codepoint as "ordinary character" is not stable across versions.

## Limitations / not run

- CPython 3.14 / UCD 16: no interpreter present; `uv python find 3.14` exits 2. Not downloaded by
  design. Every 3.14 cell is `BLOCKED`, not inferred from the presence of `unicode16.json`.
- No native/Rust path is built or loaded: native behavior, differential results, performance and
  R/F/E are `N/A`.
- GIL / free-threaded interpreters, other operating systems, wheels and default integration are
  unverified.
- Jieba dictionary identity is fixed per environment (0.42.1, dict 5,071,852 B,
  `7197c3211ddd9896…`, `total=60101967`); a different Jieba build would invalidate the term rows
  but not the Unicode rows.
- Windows x64 only.

## Scope audit

Modified/added only under `rust/unicode-contract/**` and the three task files
`rust/tasks/unicode-contract/{PORT-PLAN,DECISIONS,PORT-STATUS}.md`. No production module,
`pyproject.toml`, `uv.lock`, `#2735` branch or shared root `rust/*.md` file is touched.
