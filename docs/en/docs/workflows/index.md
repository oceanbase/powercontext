---
title: Manage context
description: Save knowledge, continue work, capture evidence, and control access.
---

# Manage context

Context management turns evidence in a Scope into reusable artifacts, then assembles the selected results into a
temporary context for one Agent turn. Use this order when learning or operating the system:

A Scope manages Sources, Artifacts, and Candidates. Sources preserve evidence. Artifacts can be produced by processing
evidence or by explicit creation and updates where supported. Proposals requiring review enter a Candidate and commit an
immutable Revision after approval; other paths follow their type's commit rules.

Committed artifacts support context recall, Handoff, Skill export, or Prompt configuration. See
[How context is created and used](architecture.md) for the individual paths.

This chain describes data and authorization flow. It does not include availability checks, troubleshooting, or recovery;
those belong to [deployment and operations](../operate/index.md), outside this directory's data lifecycle.

## Choose an entry point by content type

| What you need | Content type or concept | Start here |
| --- | --- | --- |
| Understand boundaries, roles, and deployment relationships first | Architecture and responsibilities | [Architecture and responsibilities](architecture.md) |
| Isolate projects and control access | Scope | [Scopes and access](scopes-and-access.md) |
| Capture raw evidence or references to external material | Source | [Sources and capture](sources.md), [Ingest text files](ingest-text-files-with-opendal.md) |
| Read revisions and choose a write workflow | Artifact | [Artifacts](artifacts.md) |
| Save and recall durable facts, decisions, and constraints | Memory | [Memory and context](memory-and-context.md) |
| Build a reusable lesson and review it | Experience | [Create and review Experience](create-and-review-experience.md), [Experience lifecycle](experience-and-skill-lifecycle.md) |
| Manage an exportable instruction set | Skill | [Create and export Skill](create-and-export-skill.md), [Configure Skill targets](configure-agent-skill-targets.md) |
| Maintain stable Scope background information | Profile | [Use Profiles](use-profiles.md) |
| Derive topic summaries from long-running Source processing | Topic Memory | [Use Topic Memory](topic-memory.md) |
| Transfer task boundaries and outcomes across sessions | Handoff | [Memory and Handoff](memory-and-handoff.md), [Handoff with Codex](handoff-with-codex.md), [Handoff Report](use-handoff-report.md) |
| Add filterable metadata to artifacts | Tags | [Manage Artifact tags](manage-artifact-tags.md) |
| Configure Scope-level operational prompts | Prompt | [Manage Prompts](manage-prompts.md) |
| Select and render context for one Agent turn | PreparedContext | [Prepare context text](prepare-context-text.md) |
| Enable optional vector or hybrid retrieval | Retrieval deployment option | [Configure vector search](configure-vector-search.md) |

The implementation currently writes these Artifact families: `memory`, `experience`, `skill`, `handoff`, `profile`, and
`prompt`. Artifact reads also expose the specialized `topic-memory` family. A Source is evidence, not an Artifact; a Tag
is metadata, not an Artifact family. Confirm the actual surface in [integration capabilities](../integrations/capabilities.md).

These guides describe the current implementation. Do not assume an Agent exposes every operation available through HTTP.
