# Artifact-evolution evaluation

This suite owns the evaluation-only recording and faithful replay contract for
Experience Dream evolution. It does not extend Runtime, add database tables, or
create Candidates. The bundle stores exact references, digests, realized
results, deterministic validation results, nomination state, and saved cost.

The versioned bundle is `powercontext.artifact-evolution/v1`. Controlled capture
is explicit; ordinary production capture is not enabled by this suite. Prompt,
task, and evidence bodies are not part of the contract. Optional output payloads
require either the deterministic `redacted` policy (sensitive keys and
credential-shaped strings are replaced before persistence) or an `approved`
policy with an explicit `approval_ref`. The recorder stores a structured
`scope_id`/`run_id` reference to the terminal Dream run; it does not copy the
Runtime row or create a Candidate.

## Commands

Validate a bundle without calling a model or writing Runtime state:

```bash
uv run --project evaluation artifact-evolution validate path/to/bundle.json
```

Replay one exact action using the saved result only:

```bash
uv run --project evaluation artifact-evolution replay path/to/bundle.json \
  --decision-id decision-1 \
  --trajectory-id trajectory-1 \
  --observations-digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
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
observation and output digests.

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
