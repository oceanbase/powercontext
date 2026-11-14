# budget-v1 definitions

This document is part of the `budget-v1` preregistration together with
[`budget-v1.json`](budget-v1.json). It fixes how the manifest becomes production inputs and the exact
procedure of the evaluation-only `waterfill-v1` arm. The weights, the decision threshold, the top-item bound,
the critical cases and the case list are defined only by `budget-v1.json`. Changing anything here or there
requires a new manifest version; nothing is adjusted after results are inspected.

## Materialized inputs

Every case becomes one call to the production
`PreparedContextBuilder.build_scopes_result(request=..., current_scope_id=..., memory_candidates=...,
topic_memory_hits=..., experience_candidates=..., profile_candidates=...)`.

- **Request.** `PrepareContextRequest(query="synthetic prepared-context budget fixture", max_bytes=<case
  max_bytes>, include_receipt=True)`, plus `assembly=<case assembly>` only for `path: "assembly"` cases. The
  Builder never reads the query; it is fixed so that no case varies or records a query. `include_code` and
  `include_reuse` keep their defaults (`false`).
- **Bodies.** `{"text": s}` is the literal `s`. `{"head", "fill", "tail", "bytes"}` is `head`, then the
  characters of `fill` repeated cyclically while the next character still fits, then `.` repeated until the
  body has exactly `bytes` UTF-8 bytes, then `tail` (default empty). Padding with `.` happens only when a
  multibyte `fill` cannot reach the size exactly. A size that cannot hold `head` and `tail`, an empty `fill`, a
  blank memory body, or a declared fact that does not occur in its body is a manifest error.
- **Memory.** Each hit is `AtomicMemorySearchHit(hit=AtomicMemoryIndexHit(artifact_ref=ArtifactRef(family=
  "atomic-memory", artifact_id=<id>, revision=1), state_version=0, kind="fact", text=<body>, score=1.0),
  matched_by=("fts",))`, grouped as `PreparedMemoryCandidates(scope_id=<group scope_id>, hits=...)` in manifest
  order.
- **Topic memory.** `TopicMemorySearchHit(artifact_ref=ArtifactRef(family="topic-memory", artifact_id=<id>,
  revision=1), title=<title>, summary=<summary body>, snippet=<snippet or None>, score=1.0,
  matched_by=("topic_fts",))`.
- **Experience.** `ExperienceSearchHit(artifact_ref=ArtifactRef(family="experience", artifact_id=<id>,
  revision=1), content=ExperienceContent(situation, action, outcome, lesson))`, grouped as
  `PreparedExperienceCandidates(scope_id=<group scope_id>, hits=...)`.
- **Profile.** `PreparedProfileCandidate(scope_id=<scope_id>, profile=Profile(artifact_id=<id>,
  revision=<revision>, content=ProfileContent(content=<body>, generation=ProfileGeneration(mode=
  "manual_replace", created_at=2026-11-06T00:00:00Z))))`.

Each report lists, per case, the SHA-256 of the canonical JSON of these materialized inputs
(`input_sha256`), so a report pins the exact bytes both arms received.

## Frozen candidates

- The frozen candidate set of a case is the production budget trace of the baseline build: one record per
  candidate that reached budget fitting, in production visit order. Candidates removed before fitting
  (duplicates, blank bodies, the default path's experience and topic-memory family caps) are not candidates
  and are not scored.
- **Position** is the 1-based index of a record in that trace, across all families. The item at position 1
  is the top item, whatever its family.
- Records with a cap disposition (`entry_limit`, `family_limit`) stay in the candidate set and in the
  coverage denominator.
- Each record is bound to the production entry with the same `(kind, origin)`, built by the production
  entry constructors. Both arms deliver bodies only from that entry.

## Deliverable forms

A *form* is one body the production fitting code can deliver for a candidate; its size is its UTF-8 byte
count, including the 3-byte ellipsis `…` of a truncated form.

- **Display body.** On the default path the display body is the raw entry content: production truncation,
  the 2000-byte quota and the 64-byte floor all operate on raw bytes, even though the renderer later
  JSON-encodes the body and escapes line boundaries. On the text-assembly path the display body is the
  normalized and escaped body (`_body_chunks`), which is what that path truncates and renders. Budget
  records, retained fractions and fact checks all use these display bodies.
- **Default path.** The complete form is the raw content when it has at most 2000 bytes, otherwise the
  quota form `_truncate_utf8(content, 2000)` (marked truncated). A truncated form is
  `_truncate_utf8(content, b)` for an integer `b` in `[64, min(2000, source_bytes - 1)]` whose result has at
  least 64 bytes.
- **Text-assembly path.** The complete form is the display body when it has at most 2000 bytes; otherwise
  there is none. A truncated form is `"".join(chunks[:k]) + "…"` for `0 <= k <= len(chunks) - 1` whose size is
  in `[64, 2000]`.
- **`at_most(L)`** is the complete form when it exists and its size is at most `L`; otherwise the largest
  truncated form with size at most `L` (default: `_truncate_utf8(content, min(L, 2000, source_bytes - 1))`;
  text assembly: the largest `k` with `bytes(chunks[:k]) + 3 <= min(L, 2000)`), or nothing when that form is
  missing or smaller than 64 bytes.
- **Minimal form** `minimal_i`: the smallest truncated form when it exists and is strictly smaller than the
  complete form; otherwise the complete form. When no truncated form of at least 64 bytes exists (for
  example a 64-byte default body, whose largest truncation budget is 63), the minimal form is the complete
  form. A candidate with neither form has no minimal form.
- **Largest form** `largest_i`: the complete form when it exists, otherwise `at_most(2000)`.
- **Form at level** `form_i(t) = at_most(max(size(minimal_i), t))`, falling back to `minimal_i` if that is
  empty.

## Rendering and fit test

Both arms render delivered forms in frozen order with the production renderer of the case's path: the
default JSON envelope (`_render`) or `render_context_text` with the case assembly. Text-assembly items carry
the frozen family-local `recall_rank` from their budget record, so gaps left by dropped candidates are
rendered exactly as production renders them. `fits(S)` holds when the complete rendering of the forms `S`
has at most `max_bytes` UTF-8 bytes; an empty selection fits.

## waterfill-v1 procedure

1. **Admission.** Visit candidates in frozen order. A candidate past its cap gets the production cap
   disposition: on the default path `entry_limit` when 8 candidates are already admitted (the combined
   `entry_limit` over the interleaved order); on the text-assembly path `family_limit` when its section
   already holds `limit` admitted candidates. Otherwise the candidate is admitted at `minimal_i` when
   `fits(admitted ∪ {minimal_i})`, and is `dropped_minimum_infeasible` when it has no minimal form or the
   minimal form does not fit. Admission is never revisited.
2. **Uniform level.** `t` is an integer byte size searched over `[0, max_i size(largest_i)]` over admitted
   candidates, with the predicate `P(t) = fits({form_i(t)})`. `P(0)` holds because `form_i(0) = minimal_i`.
   The rendering is not monotone in `t` at the complete-form boundary (a complete form drops the ellipsis
   and changes `Truncated: yes` to `no`), so the level is defined by this exact search:
   `best = lo = 0; hi = max size; while lo <= hi: mid = (lo + hi) // 2; if P(mid): best = mid; lo = mid + 1
   else: hi = mid - 1`. Every admitted candidate takes `form_i(best)`.
3. **Top-up.** Visit admitted candidates in frozen order. For candidate `i` with current size `s_i`, run the
   same search over `[s_i, size(largest_i)]` with the predicate `Q(u) = fits(current selection with i at
   form_i(u))`, and set `i` to `form_i(best)`. Later candidates see the grown earlier ones.
4. **Dispositions.** An admitted candidate is `full` when its final form is not truncated, otherwise
   `truncated`.

`waterfill-v1` has no tunable parameter. It never changes candidates, order, caps, the truncation primitive,
the body quota, the 64-byte floor or the renderer.

## Validity checks

Both arms must pass, in every case: rendering within `max_bytes`; caps respected; every delivered body is a
production form; dispositions agree with the delivered forms; the reported rendering equals a fresh
rendering of the forms. The baseline must also pass: the recorded build equals the unrecorded build; the
trace describes the same build (path, `max_bytes`, envelope bytes); the forms rebuilt from the trace render to
exactly the production content; delivered origins, omission counters, Receipt omission counters and
fragments reconcile with the records; record byte fields match the frozen entries. A second complete run
of each case must produce identical results.
