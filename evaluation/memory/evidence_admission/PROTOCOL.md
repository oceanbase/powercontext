# Evidence admission protocol v1

## Frozen identity

`fixtures/synthetic-v1.json` stores deterministic, content-safe text recipes. `manifests/synthetic-v1.json` pins expanded
capture hashes and exact claim byte/character ranges. Required claims identify one exact occurrence. Repeated forbidden
claims use the first exact occurrence as the audit anchor; the forbidden semantic category applies to every occurrence.
`experiment-v1.json` pins both files and `decision-policy-v1.json`. Validation rejects edited inputs before persistence.

Run identity includes evaluator and production Python tree hashes, Git revision, effective instruction hash, estimator
identity, dependency versions, experiment hashes, model settings and provider identity. Physical Scope IDs, temporary paths,
timestamps and run IDs never enter generated evidence. The original projection is not normalized or stripped for hashing.
Code/configuration changes during a run prevent publication. Preparation hashes exclude timing; model output hashes are
recorded independently and are not promised to be deterministic, including at temperature zero.

## Source-to-generation boundary

Native fixtures use the existing Content capture API. Observation fixtures use the existing public Source store with the
built-in Content adapter's transport projection. This is local observation persistence, not remote-worker or HTTP acceptance.
There is no evaluation Source definition and no arm registration in the production registry. Missing materialization is
tested by an actual repository lookup failure. An invalid projection is stored as an observation through the local Source
store and fails at the real TextEvidence validation boundary. These faults do not claim that a validated HTTP endpoint accepts
malformed input. Every ordinary fixture is captured twice and reopened before projection to check idempotent materialization.

The runtime's built-in coding instructions are used unchanged. Counting uses
`character:weighted@1` on `instructions + "\n" + input.model_dump_json()`, matching the current processor budget boundary.
The bound is an estimate, not a guarantee about a provider's tokenizer or extra structured-output schema overhead.

The complete control rejects an over-budget input without generation. The window control selects exact contiguous journal
windows, persists a halved recovery limit on rejection, retries without consuming evidence, and stops at the one-Source floor.
Successful subwindows have separate input hashes and provider calls. Deferred windows are not confused with globally dropped
Sources. An overall fixture with a blocked remainder remains budget-rejected even if earlier windows reached generation.

## Fragment inventory and selection

The fragment policy traverses the full evidence content and metadata as JSON leaves. Object keys use Unicode lexical order;
arrays retain original order. Text leaves are split into at most 512 UTF-8 bytes at code-point boundaries. CRLF pairs remain
together. Grapheme clusters may cross fragments; concatenating contiguous fragments restores the exact original bytes.
Empty text and non-text JSON leaves remain typed atomic values. Their field names and values grant no authority or role tier.

Each inventory entry binds the SourceRef, persisted materialization SHA-256, full evidence projection SHA-256, JSON pointer,
half-open byte/character ranges and policy version. Text offsets refer to the original string value at the pointer, not to
JSON-escaped bytes or normalized text. Non-text leaves use canonical JSON bytes for accounting. Fragment identities hash
the complete immutable descriptor. Public range records hash Source IDs and pointers; private replay retains the original
field mapping. A hash alone is an integrity link, not a public dereference service or proof of model use.

Selection visits Source positions in order, then each Source's fields in order, taking one fragment per nonempty field each
round. Within a field, the order is head, tail, midpoint, then midpoint of the largest remaining gap; equal gaps prefer the
earlier interval and an even midpoint prefers the earlier position. Every tentative addition checks the complete wrapped
generation input. A non-fitting item is omitted and selection continues. Selected items are finally restored to Source,
field and byte-offset order. A complete projection that already fits bypasses fragmentation without changing its input.

The strategy receives only evidence, materialization hashes, budget, fragment size and instructions. It cannot read claim
labels. Its field fairness is mechanical and can spend budget on provenance or scaffolding; this risk is measured rather
than corrected with gold labels. There is no inference of user/tool/system ownership from free text.

## Metrics and support

`projected_bytes`, `admitted_bytes` and `omitted_bytes` count UTF-8 string values and canonical non-text leaf values, including
duplicated metadata carriers. They form an exact inventory partition; JSON keys, punctuation and instructions are excluded.
`serialized_input_bytes` counts complete prepared JSON input, including wrappers, across all successful windows.
`fragments`, `omitted_fragments` and `projected_fragments` count admitted, omitted and total inventory fragments, including
metadata leaves. Merged omission ranges do not change these counts.
`projected_tokens` and `omitted_tokens` are leaf estimates; `prepared_tokens` is the maximum complete request estimate.
The final per-request budget is always checked with the latter method. Category occupancy distinguishes body and metadata;
ownership tiers are unavailable. Inventory fragments are accounting partitions even for complete control inputs.

Required retention checks the exact labeled source/field range, joining only contiguous admitted fragments. The completeness
ceiling is a scorer-only reference input containing all required claims together, each with the candidate fragment's full
provenance/offset envelope. It measures joint feasibility under this declared reference encoding; it is not a semantic
compressibility oracle and is never supplied to admission. The impossible fixture's reference input exceeds every budget.
Exact-literal retention is independent of whole-claim retention. Each marked literal is anchored to its first occurrence
inside its labeled claim, at the original field's UTF-8 byte offset. An identical literal elsewhere cannot satisfy it.
One retained literal does not imply that the enclosing claim survived.

Generation scoring validates known exact statements and frozen accepted formulations, exact literals, forbidden categories,
candidate support ranges and duplicate occupancy. Unknown paraphrases are unresolved, not automatically unsupported or
supported. Exact duplicates compare candidate text; near duplicates use case-sensitive character-bigram Jaccard similarity
at 0.85, count each candidate once, and do not change output. Human review can resolve unknown semantics but cannot override
omitted support, known forbidden claims or exact-literal loss. A cited Source/fragment only establishes a supplied reference.

Generation request totals are provider-reported successful-call usage; failed-call usage can be incomplete and is marked
`usage_complete=false`. `generation_calls` separately records attempted extractor calls. Never interpret missing failure
usage as proof that no request or cost occurred. Materialization timing includes capture, reopen, read and projection;
admission timing includes inventory, selection and window preparation; generation timing includes extraction and validation.
For private audit runs, generation timing also includes synchronous journal flushes for successful responses.
Unavailable/cancelled materialization records include preparation cleanup cost; cancelled generation timing ends after local cleanup.
These are evaluator costs, not isolated production benchmarks. Selection scans the materialized evidence and checks each
candidate against the prepared input; no incremental-cost or throughput improvement is claimed.

Aggregate generation recall counts distinct supported required claims per scored sample against all required claims in that
sample, including omitted and impossible claims. Partial successful window generations are included even if the full fixture
remains budget-rejected. Missing or failed generations are not silently scored as zero recall: scored-sample counts and the
coverage gate disclose them separately. Aggregate candidate, duplicate, support-failure and token totals remain descriptive;
no aggregate average can clear a per-sample failure. Token totals sum known successful-call usage only. A missing total is
null, a partial total has `usage_complete=false`, and no currency cost is inferred from token counts.

## Failure, cancellation and publication

Completeness and execution status are separate fields. Bounded output always includes content-free omission reasons and
ranges. Unavailable materialization/projection never becomes an empty successful evidence input. Cancellation fixtures request
real task cancellation before read, while preparing a projection result, and while awaiting a controlled extraction generator.
Each cancellation verifies the actual database has no Atomic Memory mutation or production cursor advancement. Caller
cancellation propagates. Existing captured Sources are legitimate raw evidence, not completed evaluation or Memory output.

Publication stages files, writes them into a reserved new output directory, and writes the hashed completion marker last.
An incomplete directory cannot be consumed. Cancellation before publication produces no completed run. Local task tests
do not certify cancellation of a blocking third-party adapter, a spawned production worker or a remote provider.

Explicit live audit directories are outside the repository, with directory mode 0700 and file mode 0600. The private manifest
is saved before execution. Each returned structured response is appended to `events.jsonl` with its sample/call identity,
input/output hashes and reported usage, flushed and fsynced before the next generation call. Sample completion is journaled
separately, so interruption inside a multi-window sample preserves earlier responses even when no Sample can be returned.
`STATE.json` distinguishes running, cancelled, failed and complete. Abrupt process termination may leave running state and a
partial final line; neither is a completed run. This is not an automatic resume or a machine/power-loss recovery protocol.
Successful runs stream the journal into `outputs.json` for audit. Incomplete journals remain private investigation evidence;
they cannot be passed off as completed aggregates or replayed by the audit command without a completed original run.

## Decision

The full committed synthetic suite and repeated live coverage in every arm are required for a production decision. A control
budget rejection is an expected measured result; if earlier subwindows fit, their generation must still complete. A failed
or preparation-only ordinary sample in any arm makes coverage insufficient. Fault fixtures must actually produce unavailable
outcomes, and unresolved semantic reviews in any arm prevent a decision. A candidate must retain
all jointly feasible required claims and exact literals, produce no critical unsupported/false-attribution candidates, resolve
every support reference, preserve deterministic preparation and disclose omissions. Cancellation must publish no completed
result or Memory mutation. At least one reproducible one-Source floor rejection must reach generation under the candidate.
Critical failures and unresolved audit labels cannot be averaged away. Private runs cannot authorize this synthetic gate.

The report selects exactly one of `no_production_change`, `bounded_projection_follow_up_required`, or `insufficient_evidence`.
Only the follow-up result emits a separate implementation contract. Fragment citation, historical compatibility, authorization,
rebuild, migration/rollback, operational limits and #2069 coordination remain production design requirements.
