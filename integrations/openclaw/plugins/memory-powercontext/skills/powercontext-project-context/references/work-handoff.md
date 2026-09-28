# Work Handoff

These tools are available only in eligible private sessions and remain subject to actual host permissions.
Check the current tool catalog; this document does not grant access.

1. For a requested transfer, inspect the objective, progress, blockers, next action and omissions. A preview only needs
   the current information and makes no PowerContext call.
2. Call `powercontext_handoff_current_work` with `handoff` containing `schema: "powercontext.current-work-handoff.v1"`,
   `trust: "untrusted_input"`, objective, state, disposition, next_action and omissions. Each state item and non-null
   next_action is one `{text, basis, evidence}` claim. Use `declared` with `[]` unless exact prior PowerContext citations
   exist; a fact called verified by the user is not verified citation evidence. `next_action` is one object or null,
   and omissions is an array of strings. Report observed facts without strengthening their meaning.
3. This operation captures its own boundary. Omit optional top-level `source_id` to let the adapter generate it;
   never put it inside `handoff`. No preliminary capture or separate finalization is needed.
4. Return only the complete, unchanged `handoff` member, not the entire response, a Draft, or just content. Preserve
   schema, Scope, required nullable base, citations, and generation receipts. A temporary transfer does not commit.
5. Only for an explicitly requested durable milestone, pass that exact value to `powercontext_handoff_commit` and
   confirm the returned Revision. Preserve partial/unknown outcomes and do not blindly repeat Source capture.

Use `powercontext_handoff_continue` with the exact prepared value or Revision. Check current repository state,
evidence, capability and authorization before `powercontext_handoff_acknowledge`. Acknowledge an exact returned target,
never unchecked latest. Use accepted only when all receiver checks and evidence are confirmed; otherwise report
needs_clarification or declined. Acknowledgement is a Receipt, not execution or completion.

Use `powercontext_work_contract_create` only for an explicitly delegated work baseline that needs recording; it is not
a mandatory step before coding. Use `powercontext_task_outcome` at a real, requested completion/interruption boundary,
retaining failed, skipped, unavailable and unknown checks. Neither operation grants additional authority or approves artifacts.
