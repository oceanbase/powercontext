# Cross-host Work Continuity acceptance

This suite qualifies one manifest-selected pair: Pi 0.82.1 RPC -> ZCode 3.14.3 / CLI 0.16.9.
Both integrations are `agent_host`, `master_only`, and Recommended. Real host processes load their PowerContext plugins
and call a real HTTP Server with a disposable SQLite database. Only model responses are controlled. This qualifies the
native integration and persistence journey, not live-model reasoning, released hosts, other pairs, desktops or OceanBase.

ZCode source is pinned to `29628c9acdb81b703bbd4080c207a0e7ce5e276e`. Build it using the instructions in
[ZCode acceptance](../../../integrations/zcode/acceptance/README.md), then install Pi's frozen dependencies:

```powershell
pnpm --dir integrations/pi/plugins/powercontext install --frozen-lockfile --ignore-scripts
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
uv run --locked pytest tests/e2e/test_cross_host_work_continuity.py -m cross_host_acceptance `
  --cross-host-acceptance-output 'C:\short\cross-host-acceptance' -q
```

Daily pytest runs deselect this suite. Explicit selection fails missing prerequisites; deselection or an unavailable run
is not qualification. The ZCode CI job builds the fixed host and runs all four cases. Its artifact retains only
`summary.json` and selected `evidence/*.json`, including completed evidence from failed runs. Profiles, databases,
workspaces and transcripts are not uploaded. SQLite is the first deterministic slice; actual OceanBase qualification
follows the existing E2E backend policy and requires its own run.

The controlled model advances only after the host returns the matching tool result. Replayed model requests return
the pending call, and each plan uses distinct call IDs even when the host resumes a previous session. The receiver's
native Bash tool runs `verify.py` with the acceptance runner's Python executable, quoted for the host shell. A missing
verification result fails with `receiver_action_result_missing`; a failed argument prerequisite returns a terminal
model error rather than a retryable HTTP 500. These failures remain acceptance failures.

## Scenario and evidence

Pi creates a Work Contract, implements `task.py`, captures its exact file digest, prepares a Handoff and commits the
unchanged carrier. Its native RPC confirmation requests are approved only for the four named durable operations in this
disposable task. Pi never runs verification. Its private session marker is not included in evidence, and its ephemeral
session is never saved. ZCode uses a separate profile and receives only the current request, live workspace,
PowerContext access and exact Revision. Evidence resolution and a native Read precede acknowledgement. A selected
`latest` Revision is frozen and checked against the transferred Revision before an exact acknowledgement.

| Case | Required observation |
| --- | --- |
| accepted | Exact receipt enters `awaiting_outcome`; an unlinked result leaves it there; native verification runs, and only the linked result changes coverage to `covered` |
| diverged | A changed live file produces `mismatch` and `needs_clarification`; the old action does not execute |
| unavailable | Losing the cited Source in the disposable database makes evidence unavailable; attempted acceptance is rejected and clarification preserves a reason |
| unauthorized | The current request prohibits verification; the receiver declares insufficient authorization and declines with a reason |

The controlled receiver decision uses the actual native Read result and evidence resolution, rather than the case name.
Removing the live-state comparison makes the divergence case fail. Each run verifies that the sender's private marker
never appears in receiver model requests or PowerContext Sources. Acknowledgement itself must not execute the next
action. Coverage is inspected through the Server-owned public Runtime read projection on its own event loop; there is
currently no HTTP Work Continuity read operation. This examiner read is not counted as a native host capability.

`covered` means a result was recorded, not that every check passed. The Outcome preserves successful native verification
and skipped live-model qualification separately. An examiner proposes the successful Outcome as Experience evidence
through the existing public API. The Candidate must leave `result_artifact` empty. Review, approval, publication,
installation and execution require separate authorization and are not performed by this scenario.

Receiver live-state, capability and execution authorization are **untrusted self-attestations**. PowerContext validates
exact selection, evidence availability and schema invariants. Server authentication and API access control are separate
from host execution authority. A Receipt does not grant execution authority, prove host credentials or permissions
exist, or replace current instructions and live permission checks.

Reports record the exact PowerContext commit and dirty state, integration revisions and source digests, actual host
versions, ZCode executable digest, manifest qualification, database profile, model mode and per-step evidence. They claim
only this master-only pair. Ordinary temporary handoffs remain temporary: this scenario explicitly requests a durable
milestone and does not introduce automatic commits.
