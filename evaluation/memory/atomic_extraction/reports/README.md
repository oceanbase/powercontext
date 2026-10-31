# Pinned live-run reports

Each accepted report directory contains only `run-manifest.json`, `report.json`, and `report.md` from one explicit
real-provider run. Candidate text, evidence, case identifiers, provider responses, errors, and credentials must remain
in the ignored `results/` directory.

Use the package's `pin` command after reviewing a passing run. The command validates the run identity, re-scores the
private observations, requires a complete pass, and regenerates only the safe artifacts; it never copies
`observations.private.jsonl`.

Top-level report directories form the current baseline. Automated checks require at least one and verify every
current report against the exact dataset and Prompt contract, including instruction and compiled digests.

When the contract changes, move the complete previous directory unchanged into `archive/` and pin a new passing
real-provider run at the top level. Historical reports retain their original versions, identities and scores.
Their reports must match their own run manifests, but they do not certify the current contract. Never update an
old manifest or score to make it appear to come from a new run.
