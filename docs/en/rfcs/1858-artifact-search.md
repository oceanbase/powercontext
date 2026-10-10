---
title: Unified Artifact Search and Capability Contracts
---

- Proposal Name: `artifact_search`
- Start Date: 2026-10-07
- RFC PR: [oceanbase/powercontext#1858](https://github.com/oceanbase/powercontext/pull/1858)

# Summary

Provide a common `search` endpoint for one Artifact Family within one Scope, returning an Artifact array ordered by
relevance and bounded by `limit`. The common layer defines calling conventions and baseline behavior. Each Family declares
its supported capabilities and owns the domain implementation. Existing specialized HTTP endpoints retain their request and
response contracts and default behavior; for integrated Families, they must call the new common search method internally.

# Motivation

`ListActiveEntries` lacks query relevance ordering, so budget truncation can discard the most relevant memories. Callers
need the top N relevant Artifacts for a query. Independent search implementations also increase integration cost; new Families
should join the common endpoint through registration.

# Guide-level explanation

Call `POST /v1/scopes/{scope_id}/artifacts/{family}/search`, for example with:

```json
{
  "query": "Experience resolving database connection timeouts",
  "limit": 5
}
```

The response contains a relevance-ordered Artifact array in `results`, with at most five items. Each Family defines its item
content, and no matches produce an empty array. `query` must be nonempty; omitting the retrieval mode uses the Family default.

Callers may also use modes, filters, relevance admission, fusion configuration, score thresholds, or reranking declared by
the Family. Unknown Families, unsupported search, and unsupported requested capabilities produce clear errors. Execution
failures are not returned as empty results.

Each request searches one Family, with a final `limit` and no pagination. Multi-Family orchestration and context budget
allocation remain with `prepare`.

# Reference-level explanation

- **Capability dispatch:** Families register search capabilities, parameter constraints, and domain implementations. The
  common entry point parses and dispatches through registration. Families own specialized parameters and default strategies;
  the framework does not mandate `auto`, and adding a Family requires no new branch in the public entry point.
- **Capability scope:** Full-text, vector, hybrid retrieval, filters, admission, score thresholds, and reranking are
  encouraged capabilities. Families choose support and publish available combinations. Unsupported explicit requests must
  fail rather than silently ignoring parameters or switching strategies.
- **Scoring contract:** Retrieval scores must be finite values in `[0, 1]`, with higher values indicating higher scores.
  When supported, `min_score` strictly filters normalized retrieval scores; reranking or filling the result count cannot
  bypass it. Normalization does not imply absolute comparability across Families, algorithms, or queries.
- **Fusion responsibility:** Fusion algorithms are shared implementations independent of Artifact types. Families supply
  retrieval channels, candidates, and weights. Algorithm-specific parameters belong to the selected algorithm's
  configuration. Switching algorithms preserves declared relevance admission.
- **Compatibility and adoption:** Existing specialized HTTP endpoints remain available with their request and response
  contracts and default behavior. For integrated Families, they must call the new common search method internally, sharing
  the Family domain implementation with the new HTTP endpoint, SDK, and `prepare`. They perform only necessary parameter and
  result adaptation, without independent retrieval logic. Initial coverage includes Topic Memory,
  Experience, and Skill; existing Memory entry retrieval retains its current entry points. Prompt is excluded; Profile and
  Handoff do not gain search solely for uniformity and return a clear error when search is unsupported.

Family contracts follow [RFC #1549](1549_artifact_family_unification.md), database migrations follow
[RFC #1771](https://github.com/oceanbase/powercontext/pull/1771). [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803)
is a related retrieval implementation proposal, not a prerequisite for the common interface. Atomic Memory integration
depends on and follows [RFC #1809](https://github.com/oceanbase/powercontext/pull/1809), joining after that proposal is adopted
and its Family is ready. This dependency does not block other Families.

# Drawbacks

- Family support and defaults still differ, requiring maintained capability documentation and awareness from callers.
- A common score range cannot eliminate algorithm differences; retrieval configuration changes can affect threshold
  behavior. Adapting existing entry points also carries compatibility costs.

# Rationale and alternatives

- Adding relevance retrieval to `list` mixes browsing with query search. A separate `search` operation expresses top-N
  relevant results more clearly.
- Continuing to add independently implemented specialized search endpoints repeats interface and retrieval work. The common
  method keeps domain differences within Families while supporting the retained endpoints.
- Requiring every capability from every Family introduces unnecessary index or model dependencies for simple retrieval;
  declared, optional support avoids this requirement.
- Searching all Families with mandatory model reranking adds cross-Family comparison and inference costs. Multi-Family
  orchestration remains with `prepare`.

# Prior art

PowerContext already exposes specialized endpoints such as `/v1/memory/search` and `/v1/topic-memory/search`. This proposal
retains these endpoints and their request and response contracts and default behavior. For integrated Families, their
retrieval must use the new common search method internally.

# Unresolved questions

The initial Family capability combinations, defaults, and compatibility mappings for retained entry points need to be
specified in the design. Complete request and response schemas, parameter models, scoring formulas, and backend execution
plans belong in the subsequent design work.

# Future possibilities

Capability discovery and additional fusion algorithms can reduce the effort needed to understand available capabilities.
Cross-Family score calibration can be explored separately and is not a prerequisite for this proposal.
