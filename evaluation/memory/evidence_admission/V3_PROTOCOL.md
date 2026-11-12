# Bounded evidence admission v3

This evaluation-only policy adds one arm to the unchanged four-arm v2 experiment. No production API, Source projection,
Memory publication or production cursor behavior changes. The default experiment remains v1. V1/v2 policies, inputs,
thresholds and saved results remain frozen. V3 uses the unchanged v1 claim-fragment feasibility reference and exact labels.

## Selection contract

Use v2's 512-byte record/UTF-8/CRLF-safe inventory and positional gap order. A v3 fragment and bundle bind policy v3 plus
exact SourceRef, materialization, projection, field and original byte/character ranges. Complete windows that fit remain
unchanged. Overflow windows reduce as in v2; a one-Source overflow prepares exactly one bundle. No paging is introduced.
Each request stays within its budget; samples retain 16-call and 8-times-input cumulative limits.

Classify paths mechanically: /content/metadata and its descendants are metadata; other /content paths are content;
/source_metadata paths are source_metadata. This describes the actual projection structure, not trust, ownership or role.
Every leaf remains eligible; source identity and range provenance stay in the shared bundle envelope.

Within each Source and field, visit head, tail, then v2 largest-index-gap midpoints. Stable-partition this order into the
first representative of every exactly equal JSON leaf value and deferred copies. Equality uses original strings/typed JSON
with no normalization, fuzzy comparison, keywords or fact labels. The equality domain never crosses Source, materialization,
projection or field. Each representative refers only to its own actual range; other copies remain omitted until admitted.

Fields rotate within three families. Families repeat a fixed content, content, metadata, source_metadata turn sequence,
skipping exhausted queues. Thus the body's first two turns cover its head and tail before many metadata keys can consume
turns. Source queues rotate one fragment at a time. These are allocation opportunities, not fixed byte percentages. Every
candidate checks the final serialized, ordered input including instructions and all wrappers. Non-fitting fragments are
skipped and the rest remain eligible. Return selected ranges in original Source/field/byte order.

## Costs and references

Publish calls, total token estimates, actual admitted value bytes by family, and weighted-character estimator units by
family, instructions and envelope. Four units make one estimated token, with rounding once per request. These are preparation
estimates, not provider usage. Measure whole-run elapsed time and peak RSS separately; no throughput improvement is assumed.
The complete materialized input is still scanned. Exact-content grouping and gap ordering add local work; repeated bundle
serialization remains. Duplicate scheduling changes no omission or citation semantics and establishes no semantic extraction
quality. A shared bundle citation cannot supply an omitted range or a duplicate occurrence at a different offset.

## Frozen evaluation and decision

Required datasets are synthetic-v1 (20 cases), validation-v2 (8 cases), and validation-v3 (8 additional synthetic stress
cases fixed before v3 implementation and execution). Five arms, three budgets and three repetitions give 1260 existing-case
samples plus 360 additional samples. New cases cover shifted middle facts, 64-field pressure, metadata ordering, a fact in
user metadata, near repetitions differing in numbers or negation, Unicode/CRLF, and a fact crossing a fragment boundary.
They are development stress data authored by the implementer, not a blinded or independent benchmark.

Pair v3 separately with both v1 and v2 and require containment of retained fact/literal identities, including impossible
fixtures, plus unchanged complete evidence and cancellation/unavailable behavior. All three datasets must have complete
coverage, deterministic inputs, no mutation/publication, no budget breach, no known jointly feasible fact/literal loss and
no regression before declaring the offline comparison passed. The primary report alone cannot satisfy this gate. Missing
runs or altered freezes block the combined gate. Synthetic offline success still leaves provider generation, attribution,
real host behavior and actual usage unmeasured; the production decision remains insufficient_evidence.

Arbitrary bounded subsets cannot guarantee arbitrary fact retention. Any failure stays in results. Do not modify labels,
repetitions, budgets, denominators or thresholds in response to results. A known loss remains a failure even if budget-rejected.
