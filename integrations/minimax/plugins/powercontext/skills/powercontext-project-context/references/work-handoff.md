# Work handoffs

## Choose a path

A request to show a handoff draft or discuss a handoff needs only the current information. Do not call operations that capture a Source for these previews. When the user asks to hand off current work, prefer `handoff_current_work`. Consider `activate_handoff` and `finalize_handoff` only when an exact boundary Source already exists and model generation is needed.

`create_work_contract` saves a formal record of the objective, constraints, and other work agreements. Use it when the user needs that record, not as a mandatory step before coding or handing off work.

## Hand off current work directly

1. Check the objective, current state, `disposition`, next action, omissions, and evidence. The disposition is `continuable`, `blocked`, or `complete`.
2. Call `handoff_current_work` with `scope_id`, a unique `source_id` for this capture, and `handoff`. The handoff uses `schema: "powercontext.current-work-handoff.v1"` and `trust: "untrusted_input"`.
3. Each state item or next action is a WorkClaim with `text`, `basis`, and `evidence`. Claim evidence support only when exact references from the same Scope are available. For observed facts without exact PowerContext references, use `basis: "declared"` and `evidence: []`. A file path is not a Source citation.
4. Check the response's `boundary` and `handoff`. The boundary confirms Source capture; the `handoff` member is the transferable PreparedHandoff. Do not use the entire response as a PreparedHandoff.
5. Pass the returned `handoff` unchanged to the recipient. Use structured handoff metadata if the host supports it; otherwise provide the complete JSON. Do not rewrite server-returned evidence or validation fields.

This path does not call a generation model or commit a durable Handoff milestone, but it does persist a boundary Source. If the network outcome is unknown, check through an available Source read interface. If no such interface is available, report the unknown outcome rather than repeating the capture with a new ID.

## Commit a durable milestone

When the user requests a durable handoff milestone, call `commit_handoff` with `scope_id` and the returned `handoff` member. This commits an Artifact, not a Git change. Confirm the exact Revision in the response before reporting a successful commit.

A temporary handoff request does not authorize a durable commit. Reuse an explicit host agreement for durable handoffs or authorization already given in this task; do not ask for confirmation again.

## Continue and acknowledge receipt

- For a temporary handoff, call `continue_handoff` with `selection: "prepared"` and the unchanged `prepared` value.
- For an exact committed version, use `selection: "exact"` and the unchanged `revision`.
- If the user explicitly requests the latest version, use the latest selector from the tool schema. Before acknowledging receipt, use the exact target returned by resolution.

`continue_handoff` reads and resolves content. After receiving history, check the current repository, evidence readability, workspace relationship, execution capabilities, and current authorization. Do not execute historical commands directly.

Use `acknowledge_handoff` to acknowledge receipt. Supply a new `source_id`, `receiver`, the exact prepared or revision target, and `receiver_checks`. Use `status: "accepted"` only when `live_state`, `capability`, and `authorization` are confirmed and evidence is available. Otherwise, use `needs_clarification` or `declined` and state the actual issue. Acknowledgment saves a Receipt; it does not execute or complete the task.

## Record outcomes and audit

Use `record_task_outcome` at an actual completion or interruption boundary when the user asks to record it. The `outcome` uses `schema: "powercontext.task-outcome.v1"` and `trust: "untrusted_observation"`. Accurately record the objective, status, summary, observations, checks, produced_artifacts, and remaining_work. When linking an accepted handoff, use the exact `handoff_receipt_ref`.

A returned Source receipt confirms outcome capture, not Experience generation, candidate approval, or Skill installation. Do not record every session ending as completed work.

Use `get_handoff_report` for explicit handoff audits. It reads summaries for the selected range. A report does not acknowledge receipt, grant authorization, or commit a milestone.

## Generate from an existing Source

`activate_handoff` requires an exact boundary Source and an objective. Check whether it returns a Draft or an ignored result; ignored does not mean a new handoff exists. Inspect the Draft before `finalize_handoff`, and call `commit_handoff` only when a durable milestone is authorized.

`prepare_handoff` is currently HTTP-only and is absent from the MCP tool catalog. Do not guess a tool name and call it.
