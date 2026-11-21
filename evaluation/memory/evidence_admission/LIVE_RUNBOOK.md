# Generic live evaluation: execution and cost bounds

This runbook applies to the generic CLI's explicitly selected v5, five-arm workflow. It is separate from the frozen
v18–v20 three-arm execution and its historical cumulative CNY 20 approval. See the
[Chinese command sequence](LIVE_RUNBOOK.zh.md) for preparation, smoke, audit, formal execution, and comparison.
Do not pool these protocols or treat an old approval as authorization for new requests.
The candidate arm remains `bounded-fragments-v3`; its name is separate from experiment protocol v5.

## Prepare before sending

1. Commit the intended fixture, policy, runtime, and code freeze; the CLI rejects uncommitted freezes.
2. Run model-free preparation for `synthetic-v1`, `validation-v2`, and `validation-v3`, compare the runs, and use
   `plan-live` to enumerate every arm's requests. Write new outputs under `.artifacts/evidence-admission/`, never
   over the [extracted historical result archive](RESULTS_ARTIFACT.md).
3. Choose the provider, exact model identifier, credential-free HTTPS endpoint, and credential environment variable.
   Keep the key out of commands, manifests, and reports. The private audit directory must be outside the repository.
4. Establish the cost bound below and the applicable approval before sending smoke or formal requests.

```bash
uv run --project evaluation evidence-admission prepare --experiment-version v5 \
  --output-dir .artifacts/evidence-admission/v5-live/live-ready-primary
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v2 \
  --output-dir .artifacts/evidence-admission/v5-live/live-ready-validation
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v3 \
  --output-dir .artifacts/evidence-admission/v5-live/live-ready-additional
uv run --project evaluation evidence-admission compare-preparation --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live/live-ready-primary \
  --run-dir .artifacts/evidence-admission/v5-live/live-ready-validation \
  --run-dir .artifacts/evidence-admission/v5-live/live-ready-additional \
  --output-file .artifacts/evidence-admission/v5-live/live-ready-comparison.json
uv run --project evaluation evidence-admission plan-live --experiment-version v5 \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-primary \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-validation \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-additional \
  --output-file .artifacts/evidence-admission/v5-live/live-request-plan.json
```

`plan-live` records estimated inputs, maximum output settings, and request counts; it does not select a price or
calculate a currency ceiling. Candidate-only counts such as 327 do not bound the full all-arm execution.

## Calculate the maximum amount

Generic `run-live` and `run-smoke` enforce `--max-requests`, not a currency ceiling. There is no `--limit-cny` option
or automatic money ledger in this path. The following is an operator calculation; the CLI does not enforce the
resulting amount. If an automatic money cap is required, use a separately verified executor or a provider-confirmed
hard spending control. Balance notifications and ordinary cancellation are not hard spending limits.

Record a pre-execution cost worksheet with these fields:

| Field | Unit and required evidence |
| --- | --- |
| `N` | The command's `--max-requests`; sum all commands, including smoke, for a shared allowance |
| `Imax` | Maximum billed input tokens per request, including instructions, schema, evidence, wrappers, and billed overhead |
| `Omax` | Maximum billed output tokens per request, including any billed hidden reasoning; verify the provider's cap semantics |
| `Pin` / `Pout` | Highest applicable input/output price per million tokens in one currency; record official pricing URL, verification date, model, and billing tier |
| `Fmax` | Additional per-request fee upper bound; explicitly record zero if none applies |
| `R` | Outstanding charges reserved from prior calls sharing the same approved allowance |
| Approved allowance | Cumulative amount, currency, scope, and conservative exchange/tax assumptions where applicable |

```text
per_request_bound = Imax * Pin / 1_000_000 + Omax * Pout / 1_000_000 + Fmax
new_run_bound = sum(N_for_command * corresponding_per_request_bound)
protected_total = known_prior_charges + R + new_run_bound
```

Round each component upward according to the provider's billing granularity. The protected total must fit the
applicable approved allowance. Use the highest input rate without assumed cache discounts. Failed, timed-out, and
cancelled attempts may be billed: retain a full reservation for each, and never treat unknown usage as zero.
An added command, manual retry, or increased request cap needs a new calculation. Zero SDK retries and the 60-second
timeout do not themselves bound money.

The native estimator's 2,048/4,096/8,192 budgets and `plan-live` estimates are not provider-billed token caps and must
not be substituted for `Imax`. A full model context input ceiling can be a conservative `Imax` only when the provider
confirms it bounds all billed input. The configured output cap is 2,048; use `Omax=2048` only if the provider confirms
that this includes all billed output, including reasoning. If input, reasoning, or additional charges cannot be
bounded, this method does not establish a maximum amount: obtain those bounds or a hard spending control first.

Arithmetic example only: `N=8`, `Imax=128000`, `Omax=2048`, `Pin=2`, `Pout=3`, and `Fmax=0` give
`8 * (0.256 + 0.006144) = 2.097152` in the assumed currency, with no extra billing-rounding charges.
These are hypothetical figures, not DeepSeek or another provider's current prices, context guarantee, or an approval.
The total request allowance is the smoke cap plus all three formal dataset caps from the current v5 plan;
costing smoke alone or reusing historical constants does not bound the run.

## Read request caps from passing preparation

After all three preparation commands succeed, run `compare-preparation` and `plan-live` as shown above.
Continue only when `offline_preparation_passed=true` and `plan-live` has successfully produced the plan file.
If preparation fails, stop without smoke or formal requests. Preserve the failure and keep frozen thresholds unchanged.

```bash
EVIDENCE_REQUEST_PLAN=.artifacts/evidence-admission/v5-live/live-request-plan.json
SMOKE_REQUEST_CAP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["smoke"]["provider_request_cap"])' "$EVIDENCE_REQUEST_PLAN")
PRIMARY_REQUEST_CAP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["datasets"]["synthetic-v1"]["provider_request_cap"])' "$EVIDENCE_REQUEST_PLAN")
VALIDATION_REQUEST_CAP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["datasets"]["validation-v2"]["provider_request_cap"])' "$EVIDENCE_REQUEST_PLAN")
ADDITIONAL_REQUEST_CAP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["datasets"]["validation-v3"]["provider_request_cap"])' "$EVIDENCE_REQUEST_PLAN")
```

Stop if any command fails. Use these caps in the cost worksheet and obtain the applicable authorization before sending.
A request plan does not authorize provider calls. Public and private output directories must not already exist;
parent directories may exist. If an example output path exists, choose new paths for the entire workflow and update
every subsequent reference.

## Smoke, audit, and formal execution

Run `run-smoke --experiment-version v5` with its own request allowance and new private/public directories. The fixed
scope is two cases, five arms, a 4K budget, and one repetition; its request cap comes from the current plan. Review its outputs and run `audit` against that exact
smoke. An empty review file cannot clear unresolved semantics or failures; the audited result must satisfy
`ready_for_formal`.

```bash
uv run --project evaluation evidence-admission run-smoke --experiment-version v5 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests "$SMOKE_REQUEST_CAP" \
  --audit-dir "$EVIDENCE_AUDIT_ROOT/v5/smoke" \
  --output-dir .artifacts/evidence-admission/v5-live/live-smoke
```

Create the private smoke review file using the format below, then audit that exact run:

```bash
uv run --project evaluation evidence-admission audit --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live/live-smoke \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/v5/smoke/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/v5/smoke/reviews.json" \
  --output-dir .artifacts/evidence-admission/v5-live/audited-smoke
```

Rebuild and inspect the audited smoke report:

```bash
uv run --project evaluation evidence-admission report --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live/audited-smoke
```

Require `ready_for_formal=true`. Unaudited or failing smoke, a different experiment version, or mismatched
provider/model/endpoint cannot authorize formal calls.

Every formal v5 command needs both `--smoke-run-dir` and `--smoke-audited-run-dir`, pointing to the original and audited
smoke. Run the three datasets serially with the same provider/model/endpoint and the freshly calculated request caps.
The settings are one provider request per generation, zero SDK retries, a 2,048-token output cap, temperature zero,
and a 60-second timeout. Do not adjust frozen thresholds or replay selection after inspecting outputs.

```bash
uv run --project evaluation evidence-admission run-live --experiment-version v5 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/v5-live/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/v5-live/audited-smoke \
  --max-requests "$PRIMARY_REQUEST_CAP" --audit-dir "$EVIDENCE_AUDIT_ROOT/v5/primary" \
  --output-dir .artifacts/evidence-admission/v5-live/live-primary
uv run --project evaluation evidence-admission run-live --experiment-version v5 --dataset validation-v2 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/v5-live/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/v5-live/audited-smoke \
  --max-requests "$VALIDATION_REQUEST_CAP" --audit-dir "$EVIDENCE_AUDIT_ROOT/v5/validation" \
  --output-dir .artifacts/evidence-admission/v5-live/live-validation
uv run --project evaluation evidence-admission run-live --experiment-version v5 --dataset validation-v3 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/v5-live/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/v5-live/audited-smoke \
  --max-requests "$ADDITIONAL_REQUEST_CAP" --audit-dir "$EVIDENCE_AUDIT_ROOT/v5/additional" \
  --output-dir .artifacts/evidence-admission/v5-live/live-additional
```

Audit each completed dataset against its original outputs and the matching private review file. Keep original
sessions immutable. `compare-live` requires all three preparation runs, original live runs, and audited runs with
matching identities. A comparison cannot count budget-rejected, unavailable, or unresolved outputs as successful.

Private `outputs.json` stores the actual outputs; `events.jsonl` retains captured outputs and known usage.
Require private `STATE.json` to be complete and public `COMPLETE.json` to be valid. Unknown failed-request usage
is not zero cost. Create a separate review file for each run, including a run-bound empty review when nothing needs review:

```json
{
  "manifest_hash": "digest of the original run manifest",
  "reviews": {
    "sample_id": {
      "candidate_hash": {
        "verdict": "supported",
        "claim_hashes": ["hash of a frozen annotated claim"]
      }
    }
  }
}
```

Allowed verdicts are `supported`, `unsupported`, and `false_attribution`; the latter two use empty `claim_hashes`.
A supported judgment must match the meaning, actual citations and admitted ranges. It cannot override missing support,
known forbidden facts or byte-literal errors. Never alter outputs, input identities, usage or original failure states.

```bash
uv run --project evaluation evidence-admission audit --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live/live-primary \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/v5/primary/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/v5/primary/reviews.json" \
  --output-dir .artifacts/evidence-admission/v5-live/audited-primary
uv run --project evaluation evidence-admission audit --experiment-version v5 --dataset validation-v2 \
  --run-dir .artifacts/evidence-admission/v5-live/live-validation \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/v5/validation/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/v5/validation/reviews.json" \
  --output-dir .artifacts/evidence-admission/v5-live/audited-validation
uv run --project evaluation evidence-admission audit --experiment-version v5 --dataset validation-v3 \
  --run-dir .artifacts/evidence-admission/v5-live/live-additional \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/v5/additional/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/v5/additional/reviews.json" \
  --output-dir .artifacts/evidence-admission/v5-live/audited-additional
```

Compare the three preparations, original live runs and audited runs:

```bash
uv run --project evaluation evidence-admission compare-live --experiment-version v5 \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-primary \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-validation \
  --preparation-run-dir .artifacts/evidence-admission/v5-live/live-ready-additional \
  --original-run-dir .artifacts/evidence-admission/v5-live/live-primary \
  --original-run-dir .artifacts/evidence-admission/v5-live/live-validation \
  --original-run-dir .artifacts/evidence-admission/v5-live/live-additional \
  --audited-run-dir .artifacts/evidence-admission/v5-live/audited-primary \
  --audited-run-dir .artifacts/evidence-admission/v5-live/audited-validation \
  --audited-run-dir .artifacts/evidence-admission/v5-live/audited-additional \
  --output-file .artifacts/evidence-admission/v5-live/combined-live.json
```

The combined gate verifies provider/model/endpoint, parent and review hashes, prepared inputs, generation coverage
for every arm and reported usage. Jointly fitting required facts and byte literals must reach 100% in supported outputs.
Impossible-completeness cases remain bounded; generation identity losses are disclosed without changing denominators.
Missing critical evidence yields `insufficient_evidence`; complete evidence with known candidate quality or preparation
regressions yields `no_production_change`. Only a full pass with a reproduced single-Source benefit can yield
`bounded_projection_follow_up_required`, which requires a separate production design and does not authorize production changes.

## Cancellation and report regeneration

Press Ctrl-C to cancel. The private journal retains captured outputs and known usage; local cancellation does not
prove remote cancellation or refund. An interrupted or capped generic session publishes no completed aggregate and
does not support automatic resume. Do not resend unknown attempts to repair the same historical result.

For a completed current v5 run, regenerate its report under the same frozen code, dependencies, and dataset:

```bash
uv run --project evaluation evidence-admission report --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live/live-primary
```

For the other datasets, pass the matching `--dataset`. The command verifies saved identities and scores rather than
issuing provider requests. Historical receipts and private response replay require their original code and audit
inputs; see [historical code availability](HISTORY_REPRODUCTION.md).
