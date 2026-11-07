# Citation encoding with frozen selection costs

Experiment v5 keeps the five arms, all original fixtures/labels, v1 joint-feasibility reference, model settings and resource limits. The candidate remains bounded-fragments-v3. Its citation encoding is source-range-bundle-citation-v2 and its live policy is live-policy-v2. Original v3 and experiment-v4 artifacts remain immutable.

Selection uses frozen-v3-envelope-v1: original extraction instructions and original per-arm evidence encoding determine admission, window reductions, cumulative caps and selected ranges. After selection, model-facing bundles omit internal range IDs and receive encoding-versioned outer evidence IDs. Source/materialization/projection/field/range identities stay exact in support records. No fact labels or model outputs are used to select content.

The model-facing instructions replace the original citation sentence with: `Cite only full top-level evidence_id values in evidence_ids; use self-contained kind/text.` This natural instruction is shorter under the frozen character estimator. Removing internal IDs and keeping fixed-width outer hashes cannot increase serialized input size. Actual model-facing instruction/input costs are recomputed, checked against per-call and cumulative budgets, and reported separately from conservative selection costs. No padding, cost rescaling or larger budget is used.

Require exact selected provenance ranges, fact/literal identities, status and completeness preservation against the original v3 experiment for all 1620 offline samples. Input identities may change only through model-facing encoding; no oracle feasibility denominator changes are allowed. Candidate eligible retention must remain 495/495. Compare v4 separately and retain its known three repeated v2-control losses rather than overwriting it.

Use new run directories and recompute request plans. Freeze implementation before a full independent smoke. All reviewed-smoke guards and review/usage/error rules in V4_PROTOCOL.md apply. Smoke must pass before any formal matrix; neither offline nor smoke results authorize production changes.
