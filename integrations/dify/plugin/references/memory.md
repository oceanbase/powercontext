# Memory and context

Use `pc_prepare_context(query)` when a task depends on prior work. Its Server-produced context is bounded by the administrator's UTF-8 byte budget. Successful empty context is `status=empty,content=null,content_bytes=0`. An error is not evidence that no Memory exists.

Use `pc_search(query,limit,mode)` for up to eight hits; fts/vector/hybrid/auto availability belongs to the Server. Use `pc_memory_list(include_inactive)` to enumerate entries and `pc_memory_get(citation)` for exact detail. Preserve the complete `memory_ref`, `entry_id` and `entry_version_id`.

For an authorized explicit save, use `pc_remember(kind,text,...)`. For a correction or retirement, use `pc_memory_revise(citation,kind,text,...)` or `pc_memory_retire(citation,...)` with the exact inspected citation; conflicts require rereading the current state. Supported kinds and limits are in the provider README. No prompt phrase replaces application write controls or Server permissions.

`pc_capture_source(source_id,content,metadata)` stores explicit evidence with a stable caller-selected Source ID. Remove credentials and unrelated sensitive content before capture. Structured metadata remains an object; `origin=dify` is supplied when absent. Capture does not imply Memory extraction or immediate searchability. Keep its exact Source reference for later Handoff or generation. A write timeout has an unknown outcome and must not cause a blind retry.
