# Layered Skill evaluation — 2026-09-16

## Scope

Evaluation evidence for PR #1622 / issue #1620. Native host discovery, deterministic evaluator regressions and
live-model behavior are separate evidence. This report does not qualify every native product or model.

## Initial observations

Baseline master `0ed20c54` combined the domains in most entries, shipped no OpenClaw Skill, and registered only the
DSH router. MiniMax was already layered. This is source/packaging evidence, not a matched before/after model benchmark.

The [2026-09-16 raw record](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-layered-20260916.jsonl)
contains 400 observations from `step-3.7-flash`, temperature 0, 6,000 output tokens, automatic tool choice, ten host
catalogs, and English/Chinese prompts. The main 320-case matrix covers eight intents with optional reads and Skills
unavailable. Its original automatic verdict passed 289 cases; transcript review accepted **262/320**. All 80 ordinary
coding/sufficient-context cases avoided Skill and data calls. The 40 save calls selected the write, but one changed Scope
and one fabricated a citation in reporting. Missing-tool substitution, inactive inventory, invalid evidence claims,
response wrappers, and strengthened facts remain failed observations.

Two additional 40-case probes explicitly requested Skill reading before search/Handoff. They recorded actual resource
reads in 24 and 21 cases, respectively; automatic checks passed 24 and 15. These probes are not semantically qualified
acceptance results. The second uses strict standalone-carrier validation. Early combined read-error messages could hide
whether a batch mixed reading with an operation; the final evaluator records rejected calls and reports distinct missing
reader, mixed-operation, budget, and missing-path errors. Scope changes are now rejected on non-Handoff calls as well.
Regression tests protect these evaluation corrections; historical verdicts are preserved rather than relabeled.

The raw record separates complete routing/reporting acceptance from a selected tool or a successful file read. It does
not show that every model always follows Skill instructions, and does not qualify automatic discovery in every native
product. Model failures and D's earlier observations remain visible under #1450. E's implementation PR closes its child
issue only; the parent stays open for aggregate acceptance and disposition of residual model behavior.

## Domain-read audit

The initial evaluator checked that some resource was read, not that the requested domain was read. Replaying the
recorded successful reads found 10 of the 24 routing passes in `requested-reading` and 5 of the 15 routing passes
in `strict-carrier-reading` omitted the requested domain. All 15 had read only the router. Their original
`acceptance_passed` values were already false. The original JSONL is immutable historical evidence, not the result
of the corrected evaluator. Removing this specific false-pass category leaves at most 14 and 10 routing passes;
these are not newly qualified acceptance scores or a new model run.

The `layered-skill-workflows-v2` evaluator requires the named workflow before the first data operation, supports
both packaged references and DSH runtime domains, and reports expected and observed resource names. Regression
scenarios reject router-only, wrong-domain and late reads while retaining direct-tool behavior when no read is
required. Specific read failures and their attempted calls remain in the report.

## Workflow-aware run

[Raw workflow-aware observations](results/step37-layered-workflows-20260916.jsonl) contain a new 80-case run of
`step-3.7-flash` over the ten freshly exported host catalogs, English/Chinese prompts, and optional Skill reads.
Temperature is 0, the output budget is 6,000 tokens, and tool choice is automatic. The record includes the shipped
resource text and evaluator source hashes. It uses `layered-skill-workflows-v2` and the unified Skill names.

The automated routing result is **53/80**. All 40 ordinary/sufficient-context cases avoided Skill and data calls.
Of the 40 requested-reading probes, **13/40** passed routing. No passing probe omitted its required domain before
the operation. Remaining failures include mixed read/operation batches, missing domain reads, incorrect carriers
and invalid arguments. They remain failures; improving the checker does not establish reliable model compliance.
All 80 observations remain unreviewed for semantic reporting, with `acceptance_passed: false`. This run is not a
claim of complete acceptance and does not replace the original observations or justify closing #1450.

Reproduce after exporting the catalogs with the registration/runtime tests:

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --layered-skills --env-file .env \
  --model step-3.7-flash --concurrency 4 --max-tokens 6000 \
  --cases skill_search skill_handoff ordinary sufficient_context --skill-modes unloaded \
  --output /tmp/pc-guidance/workflows.json
```

Repeat `--catalog` for the other nine hosts. Use Node 24.15+ for the OpenClaw adapter. A live run intentionally
returns a nonzero exit code until transcript-bound reporting review qualifies every observation.

## Handoff guidance consistency

Source review identified a conflict between DSH system guidance (capture, prepare, finalize) and its domain Skill
(defaulting to activation). The domain now uses prepare for ordinary transfers and reserves activation for an explicit
boundary-trigger request. DSH and OpenCode preview instructions explicitly require text only, with no Source/Handoff
calls or claims of an existing carrier. DSH's 265 package/E2E tests and 6 native runtime scenarios, OpenCode's 53 tests,
and 12 resource/packaging checks passed after this clarification.

A separate [16-observation follow-up](results/step37-layered-handoff-20260916.jsonl) uses freshly exported DSH and
OpenCode catalogs with `skill_handoff`, `handoff`, `preview` and `ordinary`, both languages, and optional reads.
The automated result is **15/16**; the remaining DSH English Handoff exhausted the 6,000-token model budget and is
recorded as truncated, not qualified. All four requested-reading probes read their domain before the operation, and
all eight ordinary/preview cases avoided Skill and data calls. Reporting remains unreviewed for all observations.
This small, separate run does not replace failures in the earlier matrix or establish a causal benchmark improvement.

## Hermes discovery qualification

The earlier Hermes catalogs read `SKILL.md` directly. They did not exercise native discovery, where Python
registration still supplied an older English-only description. Hermes observations in the initial and workflow-aware
records therefore do **not** qualify the native discovery description or namespace. Their historical scores remain
unchanged; they must not be presented as evidence that the host exposed the bilingual routing cues.

The registration now reads the installed frontmatter with Hermes' parser. Native verification uses the public
`load_memory_provider` entry point and `PluginManager.list_plugin_skill_metadata()` at Hermes v2026.8.18
(CLI 0.20.4, commit `e624e9fde561e1add9388384012b295fde669ade`). It checks inactive-provider isolation, active
provider discovery, the host-qualified name, the complete installed description, and native resource resolution.
A second case changes bilingual frontmatter and verifies that discovery follows it without a Python registration edit.
The old registration reproduces the stale-description failure on this host.

Hermes catalog export now uses that native test. The layered evaluator preserves the exported name and description,
including an empty description, and rejects older catalogs without native metadata. Deterministic regressions verify
that model-visible discovery and requested domain reads use the native namespace. These checks qualify metadata
propagation and evaluator fidelity, not universal model routing or semantic acceptance of the historical transcripts.
