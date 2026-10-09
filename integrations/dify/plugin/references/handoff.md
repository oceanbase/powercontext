# Handoff

Use `pc_handoff_activate(boundary_source,objective,evidence)` to activate from an exact boundary Source. Inspect its status and any returned complete draft or receipt. Use `pc_handoff_prepare(objective,evidence)` to prepare a draft from explicit evidence instead.

Inspect the draft, then JSON-serialize the complete object once for `pc_handoff_finalize(draft)`. Serialize the complete prepared object once for `pc_handoff_continue(selection=prepared,prepared=...)` or commit. Workflow Code-node serialization is shown in [README.md](../README.md). This does not persist a Handoff until commit.

To retain it, use `pc_handoff_commit(handoff=...)`. Keep the exact returned Artifact reference. Use `pc_handoff_continue(selection=exact,revision=...)` for that revision or `selection=latest` for the configured Scope's current Handoff.

Do not reconstruct objects from visible prose or rewrite nested Scope identities. Temporary validation, publication and exact reference access belong to the Server. A conflict requires inspecting the current state; an unknown commit outcome requires checking persisted state and any returned receipt before recovery. No automatic acknowledgement, Work Contract creation or approval is supplied by these tools.
