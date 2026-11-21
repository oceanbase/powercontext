# V3 live evaluation and audit contract

The live gate is an evaluator extension, not a new admission strategy. Keep v1/v2/v3 selection implementations, fixtures,
reference feasibility, repetitions, budgets and historical results unchanged. The live-policy-v1 manifest is immutable;
all new runs bind its hash plus current evaluator/runtime/dependency/instruction identities. Rerun the offline matrix under
this identity before actual live execution. No commit is created automatically. Both smoke and formal live commands require
the exact evaluator/data freeze to be committed; local uncommitted preparation does not satisfy this condition.

Formal comparison requires three current preparation runs, three original live runs and their three audited derivatives.
Each set must contain all required datasets exactly once, all arms/budgets/repetitions and the same provider, exact model ID,
endpoint hash, instructions and settings. Per-dataset request caps may differ. Audit derivatives must bind the original
manifest and review hash, preserve all non-generation sample values, candidate/output hashes, supports, counts and usage.
Audits can resolve semantic labels but cannot create missing support, change literal text, overwrite failures or add calls.

Require full generation coverage in every arm, including admitted subwindows of budget-rejected controls. Expected zero-call
control rejections remain valid. Candidate samples with jointly feasible required content must retain every required claim
and literal in the actual supported output, not merely in the prepared input. Keep impossible samples bounded and include
them in descriptive generation identity comparisons. Missing ordinary output, unresolved reviews, incomplete/unknown usage,
uncommitted live freezes, changed inputs or missing runs mean insufficient_evidence. With complete evidence, known candidate
quality loss, critical support/attribution errors, preparation regression or non-deterministic input mean no_production_change.
A follow-up is possible only if every dataset satisfies the fixed critical/retention gates and a reproducible single-Source
improvement over a rejecting window control exists. Candidate generation regressions on impossible cases are reported, not
used to change the jointly feasible denominator. Offline and smoke results cannot authorize production work.

The request plan sums prepared calls and estimated input across all five arms, not just v3. Maximum output equals planned
calls times the frozen output cap. Report actual successful usage separately; failed/unknown usage is incomplete, not free.
SDK retries remain disabled, each generation call permits one provider request, output cap is 2048 tokens, timeout 60 seconds,
concurrency one and temperature zero. A request-cap exhaustion or interruption retains returned responses in the private
journal and does not publish a complete run. No automatic resume or selective repeat replacement is introduced.

Smoke is a separate scope: the two pinned synthetic cases, all five arms, budget 4096 and one replay. Keep the full frozen
experiment unchanged and bind the scope explicitly in its manifest. It checks provider access, structured extraction, exact
citations, returned usage and local journal behavior. It is neither a formal matrix nor evidence of remote cancellation.
Formal comparisons reject smoke runs. Raw output and human review stay in the explicitly selected private audit directory.

The public report contains hashes/counts/ranges and usage, never raw prompts, content, credentials or private paths. Provider
configuration is explicit; do not read unrelated application credentials. Missing configuration and the no-commit constraint
leave actual provider testing not_run. Local controlled tests of the full chain must be labeled as such.
