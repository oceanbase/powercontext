# Controlled admission for live evaluation

This is an opt-in evaluator interface, not an authorization service. Product
approval to perform the experiment is already given; the remaining security
preconditions must be established by the controlled runner/operator. Unit-test
approvals and simulated workers are never acceptable production attestations.
No live database/model run is claimed by this patch.

## Trust and transport boundary

The trusted evaluator owns plans, receipts, oracle, runtime, approval anchor and
single-run ledger. The Agent only receives one question, the frozen common/Skill
inputs, explicit public configuration, public CA bytes and task-scoped secret
values through the supervisor's private stdin pipe. It cannot read approval,
oracle, other cases or prior traces. The worker permit is an internal consistency
check, **not** a signature or independent credential: invoking Python helpers or
feeding a self-authored worker request is not an alternative authorization route.
The host must prevent untrusted access to that pipe and evaluator Python APIs.

Both `public.model.tls` and `public.database.tls` are exactly:

```json
{"ca_file": "/evaluation/verified-ca.pem", "ca_sha256": "64 lowercase hexadecimal characters"}
```

The operator must verify this public CA material out of band; hashing an arbitrary
certificate does not establish trust. The file must be regular, non-symlink,
absolute and at most 1 MiB. Its exact bytes are hashed before loading via
`SSLContext.load_verify_locations(cadata=...)`; no second path lookup or default
root-store fallback occurs. Contexts use CERT_REQUIRED, hostname verification
(SAN, no CN fallback), TLS >=1.2. TLS 1.3 remains allowed. Certificate expiry,
chain or hostname failure terminates that connection before application auth.
Admission failure precedes **all** secret resolution and worker dispatch.

The MySQL creator enforces the pinned PyMySQL distribution version 1.2.0 and
`_ssl_required`. A server without CLIENT.SSL receives no auth packet. A server
advertising SSL receives only the credential-free SSLRequest before certificate
verification on the same socket; only successful verification allows the auth
packet. There is no STARTTLS probe on a different socket and no plaintext retry.
The native MySQL connector retains its query methods and pool policy, including
observable online PING/reconnect/rollback attempts. Engine creation/recreation
and every physical reconnect use the same verified context; foreign engines are
rejected. Connection/read/write timeouts are 10/30/30 seconds. Secrets are never
put in the connector URL/config, which use a credential-free creator.

Worker-owned HTTPX sync/async clients use that model context, no ambient proxy or
CA overrides, no custom transport/mounts, and no redirects (including per-send
overrides). Existing capture enforces the model origin. Patches are process-owned
and restored on exit; concurrent unrelated HTTP clients in that process are not
supported. No guarantee of zero *other already verified connections'* auth is
implied if a later peer fails; each peer verifies before its own auth is sent.

## Approval schema v1

Every live invocation takes `--approval-sha256` (or Python keyword
`approval_sha256`) from a trusted runner's out-of-plan allowlist. Never populate
this argument by simply extracting it from an untrusted plan. The admission JSON
contains `safety_approval: {file, sha256}`; its hash must equal the separately
supplied anchor. Missing/mismatched/expired approval fails before reading secrets.
The approval JSON has exactly these fields:

| Field | Contract |
| --- | --- |
| version | integer 1 |
| issue_id | `01a068bf-a292-7d75-9c20-4018e69a6e3f` |
| phase | exact `native_smoke`, `independent_learning`, `independent_development` or `independent_formal` |
| grant_id, runner_id, approved_by, subject | accountable nonempty operator/runner identifiers; not secret values |
| issued_at, expires_at | integer Unix seconds, current time inside interval, duration <=24h |
| evaluation_sha256 | `admission.evaluation_identity(plan)` computed before adding safety_approval |
| conditions | exact six keys below, all literal true |
| receipts | exact six keys below, each a JSON `{file, sha256}` reference |
| single_run_ledger | **formal only**: unused absolute file path in private owned 0700 directory |

The six receipt/condition keys are `verified_transport`, `read_only_grants`,
`immutable_data_version`, `protected_runner`, `rotation_resolved`,
`model_authorized`. The trusted operator checks their contents and authority:
verified TLS trust/host, task-specific read-only grants and common data version,
dedicated runner and secret exposure/rotation decision, authorized HTTPS model
endpoint and exact model identity/quota. The library verifies schema, binding,
hashes and expiry; it **cannot independently attest that a DBA/issuer assertion
is true**. A hash or self-asserted true is not authorization. Receipts must be
bounded regular JSON documents containing non-secret evidence/references only.

The approval binds public configuration, exact tasks, oracle hash, shared common
and Skill content, timeout, runtime profile, every admission field except the
approval's self-reference, and secret **reference paths**. Secret contents and
their hashes are never part of evidence. The trusted runner computes
`digest_json(paired.runtime_profile(sandbox))` offline and places it in
`plan.runtime_profile_sha256` before approval: this inventories bridge/runtime/
interpreter bytes and the execution root, with no DB/model calls. Live entrypoints
verify this value before credentials. Referenced evidence and CA bytes are checked
transitively in frozen inputs, not merely the top-level admission file. The same
runtime/profile identity must appear in development and formal evidence.

Secret files must be task-specific, current-user-owned regular files with no
group/other access. Do not pass values on argv, store them in plans, copy ambient
dotenv files, or post them in comments/attachments. `native --db` needs only
`secret_refs.db_password`; paired/sample require exactly `model_api_key` and
`db_password`. References, not secret bytes, are approved. Freeze/sample/run
each read them once and reuse the values in memory. The worker receives no
secret-reference path. Approval is rechecked per dispatch and after each pair;
worker lifetime is bounded by both the plan timeout and approval expiry.

## Formal sequence and failure policy

1. Verify external conditions and provision separate phase-specific approvals.
   Execute native safety smoke, independently authored learning examples, PC
   Source/Candidate/approval/exact Skill delivery, then paired development.
2. Preserve existing provenance, exposure ledger, full-row oracle/data-version
   checks and exact Skill delivery receipts described in README. Formal admission
   adds `holdout_source_sha256` equal to the registered source hash
   `c4507c1cd1d167a4c2b05d4cfad7f48226b45e216f2826127070b562e89bda84`.
   `formal_roster_receipt` points to independently reviewed JSON exactly
   `{source_sha256, roster_sha256}`, binding that source to `digest_json(tasks)`.
   PC cannot establish that mapping from a self-authored receipt; the approving
   evaluator must verify the sealed source and its extraction.
3. `learning_roster` and `development_roster` each reference nonempty JSON arrays
   of `{task_id, question_sha256}`; hashes use
   `admission.question_fingerprint` (whitespace-folded, casefolded text via
   digest_json). IDs and hashes must be disjoint within/across both rosters and
   the 46 formal tasks. This catches exact normalized overlap, not semantic
   contamination; independent provenance review remains required.
4. `development_report` references a complete valid independent-development
   report from both arms, with matching `profile_sha256`, case IDs and complete
   native execution/trace coverage. The frozen safety approval must cover these
   receipts and formal oracle before formal freeze. No model/parameter/Skill/
   runtime tuning after this freeze. Freeze itself authenticates to initialize
   native DB sessions, but injects no formal question and makes no model request.
5. Run the same `freeze`/`run` CLI with phase `independent_formal` and its own
   approved digest. Require a new output directory. Before any formal credential
   read, atomically create (O_EXCL, 0600, fsync) the grant ledger, binding manifest,
   plan and run ID. Each task/arm is separately claimed once before dispatch.
   Reusing a grant or case fails closed; no restart/resume/reset/delete fallback.
   Crashes or even secret-file failure after claim consume the grant. An operator
   must retain aborted evidence and independently authorize any replacement run;
   silently editing/deleting ledger files is outside this trusted-runner contract.
6. Preserve all 92 arm/case slots, including unstarted/timeouts, and explicit
   failure categories. Report formal completed/aborted separately from accepted.
   Acceptance still requires >=42/46 jointly correct in 1–2 steps and total
   observed online steps <=92; missing coverage is never zero-filled. No feedback
   is written during the run. Raw result evidence stays in the protected evaluator
   area and needs a redaction review before export.

Per-case trust/expiry/runtime drift aborts subsequent dispatch and invalidates
the pair. JSON syntax/encoding and parser integer-size/nesting failures are
bounded integrity failures, including in transitive receipts and final validation.
After a worker returns, such a failure preserves its records, raw/partial stdout,
exit/timeout and timing metadata, writes the started case and all unstarted slots,
and emits an aborted report with the full denominator. Decoder payloads are not
included in diagnostics. Filesystem errors retain their existing classification;
unrelated programming exceptions and process cancellation are not swallowed.
The default fixture profile remains a clearly labeled synthetic path
and never claims a live score. Simulator tests may exercise the formal branch
under a forged **test-only** operator but are not deliverable benchmark results.

## Remaining external gates

REL-4/5/7 still need verifiable controlled-network/TLS, task-specific grants and
immutable snapshot, authorized model identity and protected-runner/exposure/
rotation references. This engineering change does not create those conditions.
Real OceanBase/MySQL TLS + readonly grants, live model, sample learning, PC
governance E2E and the 46-question paired experiment remain production/acceptance
gates. TLS tests use the real locked driver with in-memory packets and injected
verification failure; they do not constitute a real-server certificate smoke.
