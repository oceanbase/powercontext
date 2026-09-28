---
title: Save and recall Memory
description: Save project decisions, retrieve relevant history, and correct outdated Memory.
---

# Save and recall Memory

Memory keeps durable decisions, constraints, and facts. PreparedContext selects relevant history for one request;
it is temporary and does not create another Memory entry.

## Save, recall, and correct

1. Complete the [Quick Start](../get-started/quickstart.md) and keep the resolved Scope consistent across sessions.
2. Explicitly ask the Agent to save the information. A direct `remember_memory` write does not require a model.
3. Search with `search_memory`, or inspect entries with `list_memory_entries` and `get_memory_entry` where the host
   exposes them. Keep the returned citation when referring to a result.
4. Use the current citation to revise incorrect information or retire information that should no longer be recalled.
   Retirement removes it from active recall while preserving history.

Host tool names differ; see [Connect Agents](../integrations/index.md). The
[HTTP API](../develop/http-api.md) exposes the complete request schemas and concurrency requirements.

FTS uses the same query terms for candidate retrieval and admission. Query normalization excludes common English
function words in longer queries and recognizes standalone execution instructions accompanying a question, such as
"Use only supplied context" and "Do not call tools, read files, inspect old sessions, or delegate." Those instructions
cannot supply evidence for an unrelated fact. Explicitly quoted terms remain searchable, including function words
used as identifiers such as `"AND" "OR" precedence`. Domain-specific directives and unrecognized wording remain searchable;
this is a conservative lexical rule, not a general intent classifier. The default threshold requires 25% of all
remaining distinct terms, with a minimum of two matches; one- or two-term queries require one match. Direct search and
prepared context share this rule, including when the optional recall gate relaxes the threshold. Stored text and
vector queries are unchanged, and matching words alone do not establish semantic relevance.

## Automatic context and extraction

Recall hooks ask the Server for bounded PreparedContext. An `empty` result is valid when no relevant information is
available. Returned history does not override current instructions or live project state.

Use [Prepare standard context text](prepare-context-text.md) to choose Memory, Experience, Profile, and Topic Memory
sections, set their order and limits, and configure the total entry limit.

Capturing a prompt creates Source evidence. It does not guarantee Memory extraction. Enable a generation model and
Source processing through [model configuration](../get-started/configure-models.md); configure embeddings only
when [vector or hybrid search](configure-vector-search.md) is needed. Review the host's prompt-capture switch before
recording project input.

Topic Memory has separate processing and retrieval surfaces (`search_topic_memory` and `get_topic_memory` on MCP).
It is not enabled by a basic explicit Memory write; inspect the Topic Memory settings and enabled runtime capabilities
in [Configuration](../operate/configuration.md).

Use [Handoff](memory-and-handoff.md) to transfer the current task boundary, and [Sources](sources.md) to preserve raw evidence.
