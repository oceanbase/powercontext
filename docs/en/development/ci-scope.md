---
title: "GitHub CI design and operation"
description: "How GitHub CI selects checks for a change, shares workflow ownership, and limits duplicate work and runner demand."
---

# GitHub CI design and operation

CI must validate affected behavior while controlling duplicate work, wasted work and bursts of runner demand. The upstream trunk branch is `master`, and automatic trunk validation listens only to that branch. Workflow policy, path ownership and required outcomes are reviewed with code. Release acceptance remains independent.

## Entry points and admission

Ordinary PRs targeting `master` and pushes to `master` start only `Main`. Its `CI preflight` job checks out the repository once and runs scope selection, license validation, quality checks and API-contract validation as separate steps on one runner. It then calls existing reusable workflows with the complete change scope. Called workflows reuse this selection without allocating another detector runner; directly invoked manual workflows retain their own scope detection. The Desktop `codex/desktop-preview` branch and workflow-specific manual diagnostics retain independent entry points.

```mermaid
flowchart TD
  E[PR / trunk push / daily / manual] --> Q[CI preflight: scope, license, quality and API contract]
  Q --> T[Selected Python tests]
  Q --> W[Selected website and documentation checks]
  T --> H[Selected backend, upgrade, platform and host acceptance]
  H --> U[Upgrade release gate]
  Q --> R[CI result]
  T --> R
  W --> R
  H --> R
  U --> R
```

Preflight failure prevents downstream tests and heavy work from starting. Python-test failure prevents dependent backend, upgrade, platform and host acceptance from expanding. Website and documentation checks can run alongside Python tests after preflight succeeds. Skipping downstream work never turns the original failure green. Grouping the four preflight checks in one job removes two runner allocations per ordinary validation run and avoids a separate runner wait between license and quality checks; each check retains its own step result and logs.

Draft PRs allocate no automatic CI runners. Marking a PR ready validates its complete diff; conversion back to draft cancels the old run. Only the latest automatic run survives for each PR. Schedule, push and manual entry points use separate concurrency groups so diagnostics do not cancel trunk or daily validation. These groups replace obsolete work for the same change; they are not cross-PR resource queues.

## Validation profiles

| Entry point or input | Python validation | Other acceptance |
| --- | --- | --- |
| Ordinary ready PR | 3.11/3.14 unit tests and 3.12 E2E: 3 combinations | Selected SQLite, installation-role, platform and host acceptance; seekDB/OceanBase are deferred to nightly |
| PR changes dependencies, shared fixtures, CI policy or unclassified files | 3.11–3.14 × unit/E2E: 8 combinations | Conservative affected or full selection within the SQLite-only database policy |
| PR update/reopen already carrying `ci:full` | 8 combinations | Complete PR scope with SQLite-only database execution |
| `master` push | 8 combinations | Compare with a successful trunk baseline; database execution remains SQLite-only |
| Daily at 18:00 UTC (02:00 the following day in Asia/Shanghai) | 8 combinations | Full SQLite, seekDB and OceanBase acceptance, including schema capture and short lock probes |
| Manual Main | 8 combinations | Full scope including native databases; upgrade selection defaults to `full`, with an explicit diagnostic subset available |
| Published release | Independent installed-package and publication checks | Complete upgrade evidence and validated publication bytes; PR scope cannot bypass acceptance |

Ordinary PR Python combinations drop from eight to three, a 62.5% reduction in combination count. This is not a measured reduction in runner minutes or queue latency. Dependency and CI-policy changes keep the full matrix, so a PR editing CI itself may still run eight combinations.

When packaging is selected, installation validation uses two jobs, one each for Python 3.11 and 3.14. Each job builds its wheel once and verifies five roles: `core` without extras, plus `builtin`, `cli`, `builtin,cli` and `server,cli`. Each role gets its own virtual environment, application state and temporary directory, and runs against the installed wheel with checkout imports excluded. The core role checks public imports without optional dependencies. The CLI role also verifies that adding unrelated SQLAlchemy and FastAPI packages does not enable optional roles. Every role produces a result; any failed role fails its job. All ten Python/role combinations remain covered while eight runner allocations and eight duplicate wheel builds are removed. The job-count reduction does not establish a queue-latency or throughput improvement without measurements.

`ci:full` is read from opened/synchronize/reopened/ready PR events. Adding the label to a running PR does not dispatch another workflow: update the branch or reopen the PR afterward. Manual Main uses the repository ref selected in Actions. For a fork PR, select its branch in the source repository; a successful default-branch run is not evidence for the PR head.

## Database execution schedule

Regular PRs and `master` pushes run selected SQLite checks. seekDB and OceanBase regression jobs run through Main's daily schedule at 18:00 UTC (02:00 the following day in Asia/Shanghai), or through an explicit manual run. The schedule validates the default branch, so a nightly result does not certify an unmerged PR head. The policy takes effect after the workflow reaches the default branch.

| Workload | PR / `master` push | Nightly Main | Manual Main |
| --- | --- | --- | --- |
| Harness replay | SQLite when selected | SQLite and OceanBase | SQLite and OceanBase |
| Native migrations, recovery, full-text search and acceptance | Deferred | seekDB and OceanBase | seekDB and OceanBase |
| Installed-release upgrade acceptance | All 4 SQLite configurations when selected | All 12 configurations | All 12 by default; `seekdb-code` is diagnostic only |
| Candidate schema replay | SQLite when selected | All 3 backends | All 3 backends |
| Historical schema capture | SQLite when selected | All 3 backends | All 3 backends |
| Short lock probes | Deferred | seekDB | Explicit diagnostics |

`ci:full`, shared inputs, unknown paths and unavailable change baselines retain broad ordinary coverage but never enable native database jobs on a PR or push. Native scopes and their required jobs are removed from the ordinary run plan together. Native workflow fallbacks and replay matrices also respect the event boundary.

The `sqlite` upgrade selection requires every selected case and records the deferred seekDB/OceanBase cases. Its result is partial acceptance: it cannot produce publication artifacts or authorize a release. Nightly acceptance and release publication retain the full matrix, including failures, cleanup and evidence checks. Manual runs can validate a chosen PR branch before merge when native evidence is needed.

## Manual runs and daily reports

Open **Actions → Main → Run workflow**, select the branch, and leave `upgrade_selection` at `full` for complete acceptance, including SQLite, seekDB and OceanBase. The equivalent CLI command is:

```bash
gh workflow run master.yml --repo oceanbase/powercontext --ref master -f upgrade_selection=full
```

Replace `master` with a repository branch to validate that branch. `automatic` also selects all 12 upgrade configurations; `seekdb-code` selects only the diagnostic upgrade subset and does not establish full upgrade acceptance. This input changes only the upgrade matrix, not the other Main suites. Manual execution does not change the daily 02:00 Beijing schedule.

After scheduled or manually dispatched **Main** finishes, **Nightly CI report** generates a report even if Main failed or was cancelled. Find it under **Actions → Nightly CI report**, open the corresponding run, and read the job summary or download `nightly-ci-report-<run-id>-attempt-<attempt>-report-<report-attempt>`. The artifact contains UTF-8 `report.md` and machine-readable `report.json`, retained for 30 days.

The report records the source run/attempt, commit, branch, Beijing timestamps, Main's conclusion, job results and execution durations, abnormal steps, and links to logs and retained artifacts. Job counts are not test-case counts. Skipped/cancelled jobs remain visible; incomplete API collection produces an explicitly incomplete report and fails the report job. A successful report job means reporting succeeded, not that Main passed. Rerun jobs are scoped to the exact source attempt; the artifact inventory is run-wide and can include earlier attempts. Original evidence retains its own expiry dates.

Reporting uses a separate `workflow_run: completed` listener with read-only permissions, checks out the default branch's report implementation, and does not execute or download code from the source run. PR and push completions do not allocate a report runner. This listener becomes active after it is merged into the default branch. It does not send email or chat notifications.

## Ownership and comparison baseline

`.github/ci-paths.toml` declares independent scopes in `[suites]`, shared inputs in `[shared.*]`, and successful Main root jobs required for each selected scope in `[checks]`. `nightly_suites` identifies native-only scopes; `[nightly_checks]` adds their prerequisites to scheduled/manual result gates. A new scope must declare its required terminal jobs.

- PRs compare their merge base to the complete head, covering all commits and excluding unrelated base changes.
- Trunk pushes compare with a successful ancestor from the same Main workflow and branch, examining up to 100 successful pushes. Failed or cancelled commits never become baselines; their unverified changes remain in the next comparison.
- Missing history, unavailable APIs or failed diffs select all scopes allowed by the event and the full Python matrix. Unknown paths also fall back conservatively.
- Git supplies the full diff, without native path-filter truncation. Disabled rename detection includes both removed and added paths.
- Runtime, API and root dependencies conservatively cover consumers. Websites and isolated integrations retain their ownership rules. Extended lock observation is manual. Historical schema capture runs SQLite for its changed inputs and all backends nightly or manually. Short seekDB lock probes run nightly or manually; extended lock observation remains manual.

For example, documentation selects website and documentation-contract checks; Cindy changes select Python regression and Cindy-package checks; migrations and shared runtime changes retain SQLite regression and upgrade acceptance on PRs. Native database acceptance runs nightly or manually against real engines.

Helm charts select deployment-contract checks. Managed resource producers select the Windows x86-64, macOS arm64 and macOS x86-64 candidate builds through Main, after quality and any selected Python tests succeed. Their native build matrix has no independent PR trigger. Shared runtime and dependency changes retain these candidate builds; unrelated website or Desktop UI changes do not start them. Candidate artifacts remain unqualified until the separate resource qualification process accepts them.

Shared helpers select their actual consumers: the clean-migration runner also selects upgrade acceptance, with native managed-startup required on nightly/manual runs; the pnpm cache action also selects the Python test setup and Cindy package. A path filter must follow dependencies, not only directory names.

## Harness replay and OceanBase acceptance

The reusable E2E workflow selects replay (`harness`) separately from native snapshot, HTTP, metrics and archive regressions (`oceanbase_acceptance`).

| Changed input | PR / push replay workers | Native OceanBase acceptance |
| --- | --- | --- |
| Bub adapter or replay scenarios | SQLite | Nightly/manual |
| Native HTTP, metrics or archive test files | None | Nightly/manual |
| Shared Compose definitions or harness startup script | SQLite | Nightly/manual |
| Shared runtime, API or root dependencies | SQLite | Nightly/manual, alongside the other affected suites |

On nightly/manual runs, when both scopes are selected, their OceanBase work shares one worker and container. Database startup and capacity configuration run for either scope. Native-only changes do not allocate a SQLite replay worker or install the Bub validation environment. Selected tests and evidence must succeed; missing reports and unexpected skips fail the acceptance gate. Native JUnit results are retained independently of replay artifacts.

The OceanBase acceptance job allows 125 minutes: 15 for shared startup, 60 for native regressions, 40 for replay, and 10 for diagnostics, evidence and cleanup. Replay includes image builds and a separate full Server schema migration. SQLite replay keeps the same 40-minute step and reserves ten additional minutes for setup, evidence and cleanup. These are ceilings; unselected work is skipped. The native process deadline starts in its own step, and the setup step passes the container ID explicitly. Native report validation and upload use the same JUnit file.

Main's preflight covers repository-wide lint and formatting. Its harness validation runs `make harness-test`, retaining harness type checks, scenario tests and CLI validation, plus Compose validation. Standalone runs retain formatting through `make harness-lint`; local `make harness-check` runs both targets.

The exact test paths in `[exclusions].database` use SQLite or in-memory behavior and retain ordinary Python coverage, plus any other matching scope. Their test-only edits do not start the broad database suite. Remove an exclusion if that file gains native database cases. Exclusions cannot suppress another changed file, shared fixtures, shared runtime selection or the full fallback. Selected upgrade acceptance runs all 4 SQLite configurations on PRs/pushes and all 12 configurations nightly.

These rules reduce unnecessary work for local changes. Shared-runtime and CI-policy changes retain broad coverage; runner minutes and queue latency require measurement after rollout.

The isolated background-listener and concurrent-startup cases have separate initialization, execution and cleanup deadlines. Full schema preparation runs in a fresh database fixture before the startup assertions; database deletion gets a new 60-second window after the test body, including after assertion failures. The per-case collection/import window is 60 seconds. A hard process deadline also covers the sum of these windows, with ten seconds of termination grace; a timeout, missing JUnit report or failed cleanup still fails the case.

| Isolated suite | Initialization | Startup and assertions | Cleanup |
| --- | --- | --- | --- |
| Background listener | 420 seconds | 120 seconds, including a 90-second CLI cold-start limit | 60 seconds |
| Concurrent managed startup | 300 seconds | 120 seconds | 60 seconds |

The native metrics CLI retains separate 180-second migration-plan, 360-second migration-apply and 90-second startup limits. Before an inner deadline, CI requests an opt-in Python stack dump and bounded container/session/lock/transaction snapshots while the process is still alive. Stack dumps use a signal rather than a background traceback watchdog. Snapshots omit SQL text, row keys and environment variables. CLI JSON stdout remains separate from diagnostic stderr. Timeout and cancellation terminate the owned CLI process group, including children; no test is retried. Phase reports and native CLI diagnostics are uploaded with the existing acceptance artifacts.

## Parallelism and cancellation budgets

| Matrix | Maximum parallel jobs per run | Failure behavior |
| --- | --- | --- |
| Python tests | 3 | Stop remaining combinations after the first failure |
| Isolated installations, one job per Python version | 2 | Attempt every role within a job; a failed job cancels the other Python job if still running |
| Installed-release upgrades | 3 | Preserve all cases and complete evidence collection |
| OceanBase clean-migration shards | 2 | Preserve all shards and completeness checks |
| Candidate replay | 2 | Preserve every backend result |
| Historical schema capture | 2 | Preserve every configuration result |
| Managed resource candidates | 2 | Preserve all three native targets |

`max-parallel` bounds one matrix run, not the repository or organization. Do not place unrelated PRs in a normal concurrency group as a global semaphore: the default group retains only one pending run and replaces other waiting work. Release publication must not share a cancellable PR group.

A concurrency cap does not remove work and can lengthen one PR's completion time. Queue reduction comes from skipping unrelated scopes, eliminating duplicate entries, grouping short checks on one runner, reusing installation builds, using the smaller PR compatibility matrix and cancelling superseded runs. Runtime and CI-policy changes still require broad acceptance, so their completion time remains sensitive to organization capacity.

Organization capacity should provide separate pools: reserve capacity for basic checks and Python tests, isolate database/upgrade workloads in a heavy pool, and budget Windows/macOS separately. GitHub larger-runner groups or appropriately isolated ephemeral runners can provide these pools. Create and verify actual group names and capacity before changing `runs-on`; nonexistent labels would only queue indefinitely. Account for other repositories sharing organization capacity.

These rollout tasks require organization administration. Workflow code does not change paid plans, budgets, runner groups or organization concurrency:

1. Inspect organization Settings → Actions → Runners for active jobs, total/OS limits and other repositories' usage. Check billing, budgets and service availability as well.
2. Create and qualify runner groups before migrating workflows. A starting allocation of roughly 20% basic checks, 40% ordinary tests and 40% heavy acceptance can be evaluated against measured minutes; it is not a capacity configuration already applied by this change.
3. After merging and validating the workflows, require the stable `CI result` check while preserving existing review requirements. The quality job's displayed check name is `CI preflight`; license and scope validation are its steps, rather than separate status checks. Replace required-check rules that still name the old quality, license or scope checks. Reusable workflow check names acquire Main caller prefixes, so external status-check rules must be reconciled.

See GitHub's [concurrency semantics](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) and [organization job and capacity monitoring](https://docs.github.com/en/actions/how-tos/manage-runners/github-hosted-runners/view-current-jobs).

## Terminal gates

`CI result` depends on every Main root job. Any failure or cancellation blocks it. Root jobs required by selected scopes must succeed: unexpected skipping or missing results also block. Only jobs excluded by scope or by the database execution schedule may skip. A skipped nightly native prerequisite still fails the gate.

`Upgrade release gate` accepts skipped acceptance only after successful preflight explicitly excludes upgrades. Selected acceptance must succeed: SQLite on PRs/pushes, the full matrix nightly. Cancelled runs allocate no new summary-gate runners; necessary in-job cleanup and diagnostics retain `always()`. Publication still checks the complete upgrade matrix and evidence files independently of path selection.

The upgrade evidence collector runs after ordinary case failures so missing or failed evidence cannot pass; it stops on workflow cancellation. Harness acceptance explicitly checks successful validation with a cancellation-aware condition, so skipping the redundant child scope detector cannot silently skip its SQLite/OceanBase matrix.

## Clearing an existing backlog

Inspect jobs, rather than treating a workflow awaiting its final summary as an entirely unstarted matrix:

```sh
python3 scripts/ci_queue_report.py --repo oceanbase/powercontext \
  --max-runs 40 --output /data/codex-tmp/ci-queue.json
```

This bounded read-only report records sample coverage and actual job states. It never cancels, retries or changes a run. `workflow_age_minutes` is the workflow's age, not an individual job's queue latency. Incomplete or failed queries are not evidence of zero backlog.

Operational sequence:

1. Compare queued work with current PR heads and state. Remove only obsolete runs for closed PRs or superseded heads. Preserve current-head checks, latest trunk verification, daily full verification and releases. Updated concurrency policy does not establish that historical runs have already been cleared.
2. For summary jobs remaining after cancellation, confirm the parent run and current PR head before removing the old run. The new cancellation predicates prevent recurrence.
3. If jobs queue while no actual jobs run, investigate organization capacity, budgets and runner availability first. A smaller matrix cannot substitute for capacity troubleshooting.
4. Rerun only failures supported by evidence of nondeterminism. Fix deterministic assertions, contract drift and incompatible database schemas first. Retain failure logs and real-backend evidence; do not add unbounded automatic retries.

Observe at least one full working day and one daily full validation after rollout. Track ready-PR to basic-check completion, selected jobs' queue p50/p95, actual runner minutes, consumed minutes of cancelled jobs, OS usage and failure/rerun rate. Suggested targets are basic-check p95 under five minutes and ordinary PR test-start queue p95 under ten minutes. These require capacity and runtime measurements; they are not guarantees from a code change.

To roll back coverage policy, restore the full PR matrix while retaining orchestration, scope selection and cancellation fixes. Never simultaneously enable automatic child triggers and Main calls, which would duplicate executions.

## Validation

```sh
uv run pytest -q tests/test_ci_changes.py tests/test_ci_pipeline.py \
  tests/test_upgrade_release_gate.py tests/test_workflow_actions.py tests/test_e2e_workflow_contract.py
uv run python scripts/check_workflow_actions.py .github/workflows .github/actions
actionlint -shellcheck= -pyflakes=
```

Local checks use real Git histories, simulated Actions metadata and executable terminal-gate scripts. They validate selection, budgets and outcome contracts, not the GitHub scheduler, native operating systems or real-database acceptance. Measure throughput improvement after deployment.
