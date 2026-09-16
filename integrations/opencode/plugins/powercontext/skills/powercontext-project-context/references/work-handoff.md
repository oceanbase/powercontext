# Work Handoff

## Hand off work

1. Call `pc_capture_source` with a concise, unique Source containing the objective, verified progress, blockers, and
   next action.
2. Call `pc_handoff_prepare` with the objective and `evidence: [{kind: "source", source_ref: capture.data.source}]`.
3. Inspect `prepare.data`, then call `pc_handoff_finalize` with that exact Draft as `draft`.
   `pc_handoff_activate` is an alternative for explicit boundary-trigger activation; do not call it after prepare.
4. The receiving task calls `pc_handoff_continue` with `selection: "prepared"` and the exact prepared value.

Call `pc_handoff_commit` only when the user explicitly requests a durable milestone.

For the lower-level Handoff flow, `pc_handoff_prepare` returns the Draft in `data`;
`pc_handoff_activate` returns it in `data.draft`. Pass only that Draft to `pc_handoff_finalize`,
never the `{ok, data}` wrapper. Return `finalize.data` unchanged, including `schema`, `scope_id`,
`base`, `content`, and `generation` when present. Do not return an unfinished Draft or only `content`.
For a preview, draft text from current inspected facts without calling any Handoff or Source tool. Do not claim that
a prepared carrier or durable milestone exists. For an actual transfer, preserve the complete returned carrier,
including required nullable fields and generation receipts.
