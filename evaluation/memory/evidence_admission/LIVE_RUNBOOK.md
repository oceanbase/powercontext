# Generic live evaluation: execution and cost bounds

This runbook applies to the generic CLI's explicitly selected v3, five-arm workflow. It is separate from the frozen
v18–v20 three-arm execution and its historical cumulative CNY 20 approval. See the
[Chinese command sequence](LIVE_RUNBOOK.zh.md) for preparation, smoke, audit, formal execution, and comparison.
Do not pool these protocols or treat an old approval as authorization for new requests.

## Prepare before sending

1. Commit the intended fixture, policy, runtime, and code freeze; the CLI rejects uncommitted freezes.
2. Run model-free preparation for `synthetic-v1`, `validation-v2`, and `validation-v3`, compare the runs, and use
   `plan-live` to enumerate every arm's requests. Write new outputs under `.artifacts/evidence-admission/`, never
   over the [extracted historical result archive](RESULTS_ARTIFACT.md).
3. Choose the provider, exact model identifier, credential-free HTTPS endpoint, and credential environment variable.
   Keep the key out of commands, manifests, and reports. The private audit directory must be outside the repository.
4. Establish the cost bound below and the applicable approval before sending smoke or formal requests.

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
The Chinese examples total `8 + 561 + 300 + 216 = 1085` possible requests; costing smoke alone does not bound the run.

## Smoke, audit, and formal execution

Run `run-smoke --experiment-version v3` with its own request allowance and new private/public directories. The fixed
scope is two cases, five arms, a 4K budget, and one repetition. Review its outputs and run `audit` against that exact
smoke. An empty review file cannot clear unresolved semantics or failures; the audited result must satisfy
`ready_for_formal`.

Every formal v3 command needs both `--smoke-run-dir` and `--smoke-audited-run-dir`, pointing to the original and audited
smoke. Run the three datasets serially with the same provider/model/endpoint and the freshly calculated request caps.
The settings are one provider request per generation, zero SDK retries, a 2,048-token output cap, temperature zero,
and a 60-second timeout. Do not adjust frozen thresholds or replay selection after inspecting outputs.

Audit each completed dataset against its original outputs and the matching private review file. Keep original
sessions immutable. `compare-live` requires all three preparation runs, original live runs, and audited runs with
matching identities. A comparison cannot count budget-rejected, unavailable, or unresolved outputs as successful.

## Cancellation and report regeneration

Press Ctrl-C to cancel. The private journal retains captured outputs and known usage; local cancellation does not
prove remote cancellation or refund. An interrupted or capped generic session publishes no completed aggregate and
does not support automatic resume. Do not resend unknown attempts to repair the same historical result.

For a completed current v3 run, regenerate its report under the same frozen code, dependencies, and dataset:

```bash
uv run --project evaluation evidence-admission report --experiment-version v3 \
  --run-dir .artifacts/evidence-admission/live-primary
```

For the other datasets, pass the matching `--dataset`. The command verifies saved identities and scores rather than
issuing provider requests. Historical receipts and private response replay require their original code and audit
inputs; see [historical code availability](HISTORY_REPRODUCTION.md).
