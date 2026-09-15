# Review and publication

## Inspect artifacts and candidates

- Use `pc_experience_get` or `pc_skill_get` only with an exact Artifact reference returned by PowerContext.
- Use `pc_topic_search` for a focused query over current Topic Memory heads, then use `pc_topic_get` only with the exact
  Topic Memory Artifact reference returned by search.
- Use `pc_review_list` to inspect the candidate queue and `pc_review_get` for one exact candidate.
- Candidate inspection does not authorize approval, rejection, revision, installation, publication, or execution.
