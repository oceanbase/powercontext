# Experience and Skill applicability with Jev / Laya

[中文说明](applicability.zh.md) · [Issue #1647](https://github.com/oceanbase/powercontext/issues/1647)

This opt-in example answers a specific question: which approved Experience and available Skill
revisions apply to the current task? Changing an HTTP contract needs generation and validation;
asking what an endpoint does should not load either procedure.

The implementation provides a reusable `ApplicabilitySelector` interface and a
`DecisionApplicabilitySelector` implementation. It uses the existing `DecisionModel` and the
System One adapter from #1797. Selection lives in the example layer. It adds no HTTP, MCP,
PreparedContext, database, or default Runtime behavior.

## Selection contract

1. `ServerCandidateCatalog` reads through the caller's authenticated `PowerContextClient`.
   Ordinary Experience retrieval supplies approved active exact revisions. Managed Skill
   metadata supplies a bounded active shortlist. Pending, rejected and retired candidates
   cannot enter through these read paths.
2. Each shortlisted standard Skill package is downloaded and checked against its approved
   package reference. The existing static compatibility assessor evaluates the host target.
   Incompatible, unknown and manual-review-required results are omitted before any model call.
   Complete textual evidence includes JSON and YAML supporting files outside `scripts/`,
   preserved verbatim alongside the entrypoint. Legacy Skills without a standard package and
   non-UTF-8 supporting text are omitted with reasons.
3. Absolute applicability asks `yes`, `no` or `abstain` separately for each candidate. Topic
   similarity is insufficient. A missing prerequisite means unknown; a known contradiction
   means no. All affirmative complementary Experiences are retained.
4. Only affirmative Skills enter pairwise preference. One Skill is recommended; ties retain
   retrieval order. Confidence is recorded as metadata and never used to rank candidates.
5. Results pin scope, family, artifact ID and revision, along with content/package digests,
   candidate order, environment, task and question versions. `revalidate` rejects changed
   or ineligible recommendations before loading; it never substitutes a newer revision.

No affirmative candidates yields `none`; a pool with unknown applicability and no affirmative
candidates yields `uncertain`. These are different from backend failure. Disabled selection
makes no model requests. Missing/unavailable backends and total deadline expiry return the same
declared retrieval baseline with `used_fallback=True`. Hosts must apply their existing selection
policy to a fallback rather than interpreting it as a positive model judgment.

The baseline for this experiment retains all retrieved Experiences and the first eligible Skill.
It is an explicit retrieval-order comparison, not a measurement of a native host's autonomous
Skill selection. PreparedContext still does not include Skills.

## Reuse the interface

```python
from examples.systemone.applicability import (
    ApplicabilitySelector,
    DecisionApplicabilitySelector,
    SelectionRequest,
)
from examples.systemone.applicability_catalog import ServerCandidateCatalog


async def recommend(client, target, model, scope_id, task):
    catalog = ServerCandidateCatalog(client, target, skill_limit=8)
    pool = await catalog.retrieve(scope_id, task)
    selector: ApplicabilitySelector = DecisionApplicabilitySelector(model, enabled=True)
    environment = () if target.environment is None else (target.environment.model_dump_json(),)
    request = SelectionRequest(task=task, candidates=pool.candidates, environment=environment)
    result = await selector.select(request)
    selected = tuple(item for item in pool.candidates if item.address in result.recommendations)
    await catalog.revalidate(scope_id, task, selected)
    return result  # The host owns loading, publication, approval and execution.
```

Constructing `ApplicabilityCandidate` does not authorize access. Supply the caller's authenticated
SDK and an observed, secret-free `AgentSkillTarget`; do not accept a user-supplied candidate pool
as proof of eligibility. Revalidation is a read-time check, not an execution lease.

The default catalog requests at most two Experiences and eight Skills. The selector accepts at
most 16 candidates and performs at most N absolute decisions plus N−1 Skill preferences, with
a 60-second total deadline and a 24,000-byte complete-evidence budget per decision. Oversized
evidence is explicitly unknown, never silently clipped. The existing provider/Laya preflight
also applies. Catalog revalidation repeats the bounded reads and package downloads. The existing
server's retrieval and library search own their indexing costs; this example does not establish
whole-library performance. No programs in Skill packages are executed by the selector/catalog.

## Run live provider evaluation

From the checkout, install locked development dependencies and copy the provider configuration:

```text
uv sync --locked
```

Copy `examples/systemone/jev.env.example` to `.env_jev` (or `laya.env.example` to `.env_laya`).
These `.env_*` files are ignored by Git. Configure:

| Setting | Value |
| --- | --- |
| `SYSTEMONE_PROVIDER` | `jev` or `laya` |
| `SYSTEMONE_ENDPOINT` | Complete decision URL: OpenRouter `/api/alpha/decisions`, or Laya `/v1/systemone` |
| `SYSTEMONE_MODEL` | Served model ID; prefer a provider-verified fixed version |
| `SYSTEMONE_API_KEY` | Provider credential; leave empty only for a local unauthenticated Laya server |

For Jev, the template selects OpenRouter and a versioned model ID. Fill in only the key in
your local `.env_jev`; never commit it:

```dotenv
SYSTEMONE_PROVIDER=jev
SYSTEMONE_ENDPOINT=https://openrouter.ai/api/alpha/decisions
SYSTEMONE_MODEL=typesafe/jev-1.13
SYSTEMONE_API_KEY=
```

The adapter posts `model`, `state` and a typed `choice` question with Bearer authentication.
The three answers remain `yes`, `no` and `abstain`; a `noul` probability alone does not express
the explicit unknown outcome required by this selector. OpenRouter's optional site attribution
headers are unnecessary. See the [official Jev guide](https://openrouter.ai/blog/insights/what-is-jev/).

```text
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_jev
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_laya --checkpoint /path/to/laya-checkpoint
```

Laya needs the matching checkpoint tokenizer and the optional dependencies described in the
[System One README](README.md). The evaluator uses a fresh local SQLite database and Scope,
seeds synthetic records through proposal and explicit review APIs, and communicates with the
actual Server using ASGITransport. Access-control tests separately exercise enforced Server
authorization with synthetic identities. Neither route reads production project Memory.

The ten fixed English/Chinese cases cover contract changes, neighboring general/specific Skills,
complementary Experiences, explanation-only requests, unrelated tasks, unknown approval and
explicitly denied approval. Each comparison uses exactly the same task, environment and candidate
pool. Version, operating-system and unparseable-version rejection are covered by Server tests.

Reports are saved under `.powercontext/applicability/<run-id>/`. They contain exact references,
full synthetic input, model policy IDs, question/fixture versions, source hashes, decisions,
preferences, omissions, timing and usage. Completed cases are checkpointed before optional host
execution; an interrupted run's individual case files remain usable. Rerunning creates a fresh
run and makes fresh provider calls; this evaluator does not replay/resume paid requests.

The configured model string is recorded. `model_version_verified=False` means this example
cannot attest that the provider's model alias is immutable; verify a fixed model ID with your
provider. It does not infer a pinned version from the name. Reports omit the endpoint and key.

## Optional real Codex flow

With an installed, authenticated Codex CLI:

```text
uv run --locked python -m examples.systemone.applicability_eval --env-file .env_jev --codex-host
```

This runs both arms of `change-en` in separate disposable workspaces. Each host receives the same
task and starting files, explicitly reads the selected exact text, updates a small authoritative
HTTP schema, generates a Python client and runs contract tests. The harness independently checks
the response field and generated annotations. It also checks that the generator, tests and
selected context have not changed. Existing Codex authentication/configuration and approval rules
apply; no bypass flags are used. `--codex-timeout` bounds each CLI process (default 180 seconds).

The runner finds `codex` on `PATH`. Check `codex --version` if a model that works in the desktop
app is reported as unsupported by the CLI; the two installations can have different versions.
The CLI subprocess uses its own tool connection and session identity while retaining the
existing authentication, proxy, configuration and approval settings.

This is real CLI/tool execution with explicit context loading, not native Skill discovery or
publication. The runner never installs a package or executes package scripts. The existing
`record_skill_usage` API records each selected exact Skill revision and package digest with an
outcome Source. `invoked=true` requires observed context reading, generation and successful
contract-test commands for an exact known generation/maintenance fixture package.
Commands may run the fixture's `.py` file or its equivalent `python -m` module; observed Python
main targets, successful exit codes, output markers and independent workspace checks provide
the evidence. Quoted names in unrelated commands do not count as fixture execution. Other selected
Skills retain unknown invocation/outcome: generating a client cannot establish deployment.
An empty Skill selection cannot produce a Skill invocation observation. Selection or a host's
final prose alone leaves invocation unknown.
The JSONL host trace and independent checks support the observation.

Skill usage is a registered adapter Source and can be read through the existing typed
`context.sources` catalog/read API. The current generic HTTP Source read and Review text
projection do not expose this Source type; the example does not expand those contracts.

## Read the metrics

| Field | Meaning |
| --- | --- |
| `applicable_candidate_recall` | Fraction of known applicable candidates in this retrieved pool admitted by the absolute filter; preference is measured separately |
| `unretrieved_applicable_candidates` | Known applicable fixture candidates absent from retrieval |
| `wrong_recommendations` | Recommended candidates outside known applicability labels |
| `unnecessary_recommendations` | Recommendation count on a known no-fit task |
| `selection_exact` | Expected recommendations and outcome, without fallback |
| `wrong_loads`, `unnecessary_loads` | Actual counts only when a real host context read is observed; otherwise unknown |
| `task_success` | Independent real host task result on `change-en`; unknown on selection-only cases |
| `added_latency_ms` | Selector time minus retrieval-order baseline time, excluding shared retrieval and host execution |
| `estimated_added_cost_usd` | Provider tokens × supplied per-million prices; unknown without complete usage or prices, including interrupted/fallback requests |

Supply optional `--input-price-per-million` and `--output-price-per-million` from your provider's
current pricing. Codex host duration and usage are reported separately, without an invented
host dollar estimate. Low recommendation error does not establish task success. Mock provider
tests establish protocol/report behavior, not Jev or Laya accuracy. This small synthetic suite
separates abstention from preference; it is not a production calibration or a general quality
claim. Real-provider and host results must be reported from actual runs, including failures.
The CLI exits with status 1 if an assisted selection misses a known answer, falls back, or an
enabled host arm fails. The report is preserved for inspection.

## Offline validation

```text
uv run --locked python -m pytest tests/examples/systemone tests/e2e/test_applicability_selection.py tests/e2e/test_systemone_example.py
uv run --locked ruff check examples/systemone tests/examples/systemone tests/e2e/test_applicability_selection.py
uv run --locked ty check examples/systemone tests/examples/systemone tests/e2e/test_applicability_selection.py
```
