# Window-first bounded admission v2

`manifests/experiment-v2.json` adds `bounded-fragments-v2` to the three unchanged v1 arms. It pins the original synthetic
fixture/catalog, the unchanged decision policy and an additional eight-case variant fixture/catalog. The default CLI remains
v1. Select v2 explicitly; select the same dataset for preparation, audit and report commands.

```bash
uv run --project evaluation evidence-admission validate --experiment-version v2
uv run --project evaluation evidence-admission prepare --experiment-version v2 \
  --output-dir .artifacts/evidence-admission/v2-primary
uv run --project evaluation evidence-admission prepare --experiment-version v2 --dataset validation-v2 \
  --output-dir .artifacts/evidence-admission/v2-validation
uv run --project evaluation evidence-admission report --experiment-version v2 \
  --run-dir .artifacts/evidence-admission/v2-primary
uv run --project evaluation evidence-admission report --experiment-version v2 --dataset validation-v2 \
  --run-dir .artifacts/evidence-admission/v2-validation
```

The primary matrix is 20 cases × 4 arms × 3 budgets × 3 repetitions = 720 samples. The variant matrix is
8 × 4 × 3 × 3 = 288 samples. Variants stress reordered Sources, CRLF and prefix offsets, off-center facts, repeated-history
length, metadata pressure, durable metadata, typed metadata and shifted exact literals. They are synthetic development
stress cases, not a blinded test or real host observations. Both reports must be considered; a primary improvement cannot
erase a known variant failure. Neither report alone establishes a production-ready admission policy.

## Window-first policy

Each sample uses real persisted/reopened Sources and their real projection. V2 uses the existing Source window trigger and
reduction repository in its disposable Scope, with an evaluation-private consumption position. Complete windows that fit
retain their original extraction input. Over-budget windows halve as in the control. Only an over-budget one-Source window
uses the v2 range bundle. After a bounded Source, evaluation continues to later Sources; it never advances a production cursor.

The experiment fixes 16 prepared calls per sample and a cumulative input-estimate cap of eight times the per-request budget.
Both bounds include complete windows and fragment bundles. No accepted request exceeds its own budget. Exhaustion leaves
remaining evidence omitted with `resource_budget`, marks the sample budget-rejected and bounded, and cannot authorize a
complete result. These are experiment cost limits, not product defaults. Actual provider requests and token usage are separate
from prepared-call counts and cumulative input estimates.

## Fragment boundaries and coverage

JSON object fields use the original Unicode lexical order; arrays keep their original order. All content and metadata leaves
remain eligible. String partitioning uses Python's `splitlines(keepends=True)` record boundaries, including its Unicode line
separators, and packs whole records up to 512 UTF-8 bytes. A record larger than this limit is subdivided at code-point-safe
boundaries, preserving CRLF pairs. Empty strings and typed JSON leaves remain represented. No bytes are normalized, rewritten
or discarded by partitioning. There is no role inference, keyword scoring, fact-label access or ownership upgrade.

Per field, selection visits head, tail, then the earlier midpoint of the largest unvisited index gap; equal gaps prefer the
earlier gap. This preserves the v1 positional convention over the new record-aligned inventory. A heap implements gap
ordering. Per Source, field queues rotate after every fragment. Source queues also rotate after every fragment: one Source
cannot consume a turn for every field before the next Source is visited. Every proposed addition checks the final ordered,
fully wrapped extraction input, not a payload-only estimate. Non-fitting fragments are omitted and selection continues.
Final ranges are ordered by Source, field and original byte offset.

Fragmentation still copies/scans the complete materialized input. Candidate checks repeatedly serialize bounded inputs and
compute fragment identities. No incremental-cost or throughput improvement is claimed. Reported latency includes this work.

## Bundle identity and citations

Every fragment identity hashes policy v2, SourceRef, exact materialization and projection hashes, JSON pointer, and original
half-open byte/character ranges. A Source bundle ID hashes the policy and its ordered fragment identities. The bundle shares
SourceRef, journal position, materialization and projection hashes once, instead of repeating a full evidence wrapper for
each short leaf. Its `ranges` retain each fragment's ID, field, original value and byte/character bounds.

The production input shape remains `AtomicMemoryExtractionInput`; each bundle is one `AtomicMemoryEvidence` containing
evaluation-only JSON. The real generation pipeline accepts citations only to supplied top-level evidence IDs. A bundle
citation resolves to its exact admitted range set, not to the whole Source and not to every omitted byte. Public support
records hash Source identities and pointers. The scorer selects cited fragments by immutable Source/materialization/
projection/field/range containment and joins only contiguous supplied ranges. Human review cannot invent omitted support.
No public API, Source registry, production projection or SourceRef citation contract is changed.

## Comparable scoring and non-regression

Joint feasibility continues to use the unchanged **v1 claim-fragment reference encoding**, for every arm. The v2 bundle's
smaller envelope does not change the denominator. The same raw fact and literal offsets are checked. Retained literal
identities additionally bind claim ID and literal index, so a higher count cannot hide replacement of an old literal.

Paired comparisons match case hash, budget and repetition. They check retained claim/literal set containment, complete
evidence status, expected unavailable/cancelled outcomes, and absence of Memory/cursor publication. Impossible fixtures also
participate in non-regression checks while remaining excluded from the jointly feasible retention denominator. The report
lists fixed v1 failure combinations and any regressions. V2 regressions are critical for its follow-up gate; mismatched
reference feasibility is insufficient evidence. Provider quality is not part of the model-free non-regression claim.

V1 admission code, fixture/catalog, decision file, experiment file and saved preparation artifacts are preserved. The local
ignored archive `.artifacts/evidence-admission/v1-before-v2.tar.gz` additionally preserves the pre-v2 evaluator snapshot.
V2 reports have their own current evaluator/code identities. The old saved v1 run must not be presented as a run of the
extended evaluator; the new comparison matrix reruns all three v1 arms alongside v2.

## Limits

Record boundaries and position coverage are heuristics. A fact may be outside selected positions, split inside a long record,
or starved by many metadata fields. No finite subset policy without fact labels can guarantee retention for arbitrary text.
All such failures remain visible and must not be corrected by changing labels or silently relaxing the frozen threshold.
No model requests occur in preparation. Live quality, false attribution, real-host provenance and remote cancellation remain
unmeasured. A successful primary input comparison alone is not permission to open a production implementation issue.
