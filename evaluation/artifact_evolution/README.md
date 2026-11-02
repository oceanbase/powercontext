# Artifact-evolution evaluation

This package is the Dream execution adapter and recording/replay core for the
evaluation-owned harness defined by RFC #1750. It is not a second general
workload harness. It does not extend Runtime, add database tables, or create
Candidates. The bundle stores exact references, digests, realized results,
deterministic validation results, nomination state, and saved cost.

The versioned bundle is `powercontext.artifact-evolution/v1`. Controlled capture
is explicit; ordinary production capture is not enabled by this package. Prompt,
task, and evidence bodies are not part of the contract. The default
`payloads="none"` policy records output digests and exact Candidate references
without retaining output payloads. Optional output payloads require either the
deterministic `redacted` policy or an `approved` policy with an explicit
`approval_ref`. The redacted policy covers the contract's exact sensitive keys,
explicit sensitive suffixes, task/evidence/prompt content fields, and recognized
credential-shaped strings. Version, token-count, budget, type, identity, and
timestamp metadata remain available unless the complete key is itself marked
sensitive. It is deterministic field redaction, not a claim that arbitrary prose
has been classified. The recorder
stores a structured `scope_id`/`run_id` reference to the terminal Dream run; it
does not copy the Runtime row or create a Candidate.

Before accepting a run, the adapter verifies that its operation, Scope digest,
input-manifest digest, exact target/head binding, and top-level Artifact and
Source references match the declared workload. The same bindings are stored on
each attempt and revalidated whenever the bundle is loaded or written. One
bundle therefore represents one fixed Dream objective; it cannot mix Scopes,
input manifests, targets, heads, or evidence selections. Reused Candidates and
snapshots without the terminal outcome or model identity required for faithful
replay are rejected. Declared proposal-attempt, model-call, token, wall-time,
and concurrency budgets are enforced when the bundle is validated; a declared
optional token or wall-time limit also requires that cost to be present.

The recorder validates values that must already be present in the base bundle;
it does not initialize or rewrite the workload. Parse the run with
`DreamRunSnapshot` before constructing those values. The snapshot removes null
object members recursively from `input_manifest`, so object and JSON inputs have
the same observation identity. It also requires the Runtime manifest's transform
version, projection digest, projection byte count, typed Artifact and Source
references, evidence nodes, edges, and root groups; an empty or structurally
incomplete manifest is rejected.

| Base-bundle field | Required value |
| --- | --- |
| `workload.scope_digest` | `digest_json(snapshot.scope_id)` |
| `workload.fixture_digest` | `digest_json(snapshot.input_manifest)` |
| `requested_decisions[].observations_digest` | The same value as `fixture_digest` |
| `workload.target_artifact_ref` | Compact canonical JSON of `snapshot.target`, or `null` |
| `workload.expected_head` | The same exact target revision, or `null` |
| `workload.evidence_refs` | Compact canonical JSON of each top-level Artifact ref followed by each Source ref |

Runtime currently exposes the admitted exact target revision but no independent
CAS/head token. For v1, `expected_head` is therefore an explicit alias of the
exact target ref, not an independently observed value. Supporting a distinct
head identity requires a versioned adapter-contract change.

`canonical_json` is UTF-8 JSON with sorted object keys, no insignificant
whitespace, and non-ASCII text preserved. `digest_json` is the lowercase SHA-256
hex digest of those bytes, without a `sha256:` prefix. For a proposed Dream run,
`output_digest` identifies the unredacted typed result projection
`{"candidate_id": ..., "candidate_version": ...}` and is stable across capture
policies; that projection is retained structurally as `result.output`, allowing
the digest and nomination Candidate ref to be revalidated after load. It is not
a digest of Candidate content. When a payload is retained, `payload_digest`
identifies the actual redacted or approved bytes stored in `output_payload` and
may differ from `output_digest`. These representations are local to the v1
evaluation bundle and do not add a Runtime reference format.

## Commands

Validate a bundle without calling a model or writing Runtime state:

```bash
uv run --project evaluation artifact-evolution validate path/to/bundle.json
```

Replay one exact action using the saved result only. Read the observation digest
from the matching entry in `requested_decisions`; it is not an independently
chosen value.

```bash
OBSERVATIONS_DIGEST="$(jq -r '.requested_decisions[] | select(.decision_id == "dream-run-1") | .observations_digest' recorded-bundle.json)"

uv run --project evaluation artifact-evolution replay recorded-bundle.json \
  --decision-id dream-run-1 \
  --trajectory-id scope-1 \
  --observations-digest "$OBSERVATIONS_DIGEST" \
  --action-signature refine-experience:v1 \
  --ordering 0
```

Report exact replay coverage and saved generation cost:

```bash
uv run --project evaluation artifact-evolution coverage path/to/bundle.json
```

Record one controlled terminal `refine_experience` Dream snapshot into a new
bundle. The input is a JSON serialization of the existing Runtime `DreamRun`
shape; the evaluation package only reads the fields it needs and computes the
observation and output digests. Validation errors report field-level reasons
without echoing rejected payload content. Unless overridden, `record` uses the
run's `run_id` as `decision_id` and `scope_id` as `trajectory_id`; the base
bundle must declare those same keys.

```bash
uv run --project evaluation artifact-evolution record \
  base-bundle.json dream-run.json recorded-bundle.json \
  --action-signature refine-experience:v1 \
  --policy-id baseline-policy-v1 \
  --generator-id dream.refine_experience \
  --runtime-version runtime-v1
```

An absent or mismatched action returns `out_of_support`. Replay never runs a
generator or evaluator and never mutates Runtime, Candidate, Review, or Artifact
state. Exact replay support is identity-level: the trajectory, decision id,
observation digest, action signature, and ordering must all match. Coverage
therefore reports unsupported decision ids and is not a cross-policy comparison.
In P1, every requested decision in a bundle shares the workload's one fixed
observation digest. `TrajectoryRequest` groups ordered decision identities over
that observation; it does not encode a sequence of evolving observations.
