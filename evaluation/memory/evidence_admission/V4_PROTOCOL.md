# Evidence admission citation protocol

Experiment `v4` retains the existing five selection arms and frozen three datasets. It changes the model-facing citation encoding and uniformly appends `evidence-id-only-v2` instructions. The candidate remains `bounded-fragments-v3`; this is not a new selection algorithm.

## Identity and encoding

The v2 and v3 bundle encodings use `source-range-bundle-citation-v2`. Each evidence object retains its exact outer `evidence_id`. Model-facing ranges contain original field/value/byte/character data, without an internal range ID. Internal fragment identity and provenance stay in selection and support records. Bundle hashes include the new encoding identity. Legacy experiment configurations retain their original encoding and instructions.

The same citation instructions apply to all five arms and are included in actual prepared-input budget accounting. Selection does not receive labels. Joint feasibility uses the unchanged v1 claim-fragment reference and original extraction instructions; it must not silently drop previously eligible facts when new instructions consume more budget.

## Records and errors

New runs, samples and reports use v2 schemas. An inference failure remains failed. A returned output citing unavailable evidence is classified as output_validation/invalid_evidence_reference, including errors rejected before generation scoring. No citation is rewritten, inferred or aliased into validity. Known request/token usage remains reportable even when returned output fails semantic validation. Missing return/usage data remains unknown. Legacy samples remain readable with explicit v1 defaults, without changing their on-disk artifacts.

## Smoke and review

The smoke scope remains two cases, five arms, 4096 estimator units and one replay: 10 samples, expected 8 calls. Original and audited smoke are distinct modes and directories. Review binds the exact original manifest and candidates, preserving output/input identities, support, status and usage. A review may resolve a supported paraphrase, but cannot repair a failed generation, invalid citation, omitted support or missing exact literal. Both smoke modes remain excluded from formal comparisons.

`ready_for_formal` requires a complete audited smoke, all expected calls and returned usage, no exceeded request cap, no unsupported/false-attributed/invalid-support/unresolved candidates in any smoke arm, and 100% candidate required fact/literal retention. A smoke report always remains insufficient evidence for issue acceptance. New formal CLI runs require original and audited smoke proofs with matching frozen code, provider, model and endpoint. Formal runs record the proof hashes.

## Validation and resource limits

Run the complete 1620-sample offline matrix in new directories. The unchanged reference denominator is 495 eligible candidate fact occurrences. Require 495/495 retention, exact-literal retention, deterministic preparation, all ordinary/fault/cancellation outcomes, and no observed v1/v2-to-v3 preparation regression. Compare old/new per-arm fact and literal identities, status and completeness, independently disclosing changed input identities and measured resource costs. Keep old artifacts and the first real failed smoke immutable.

Budgets remain 2048/4096/8192 estimator units, fragments remain at most 512 bytes, max calls per sample 16 and cumulative prepared input cap 8 times the budget for hybrid arms. Provider calls remain serial, zero retry, one request per extraction, max output 2048 tokens and timeout 60 seconds. Recompute all-arm request plans after encoding/instruction changes. Offline passing is not real model evidence; a new full smoke must pass before formal generation is considered. No production projection, persistence, API or publication behavior changes.
