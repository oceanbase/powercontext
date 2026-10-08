# Experience, managed Skill and candidates

Use `pc_experience_generate(source_refs,artifact_refs,...)` with exact retained evidence. Use `pc_skill_generate(origin,source_refs,artifact_refs,...)` with an origin supported by its tool declaration, such as approved Experience evidence. The combined evidence budget is 1–32 references.

Inspect the generation response: it can report a pending candidate, skipped work, a failure, or an uncertain outcome. A candidate is not an approved Experience/Skill artifact. Use `pc_review_list` and `pc_review_get(candidate_id)` to inspect it. An explicit family filter is experience/skill; an omitted filter retains the Server's unfiltered behavior. Preserve pagination and the full candidate evidence/version.

An authorized administrator reviews and approves outside this plugin. With the resulting exact Artifact reference, use `pc_experience_get(artifact)` or `pc_skill_get(artifact)`. Do not use a candidate ID in place of an Artifact reference. Managed Skills here are Server artifacts; these tools do not import or execute external host Skills.

Generation depends on the Server's configured generator and providers. A 503 or model outage is a tool failure, not an empty candidate success. Complete structured error receipts are preserved only for known resource fields; inspect them before retrying an uncertain write.
