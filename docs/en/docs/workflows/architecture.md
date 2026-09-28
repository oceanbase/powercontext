---
title: Context management architecture and responsibilities
description: Understand the boundaries between Scopes, Sources, Artifacts, the Runtime, and hosts.
---

# Context management architecture and responsibilities

This page explains the responsibility of each component in context management and how evidence becomes reusable
artifacts. It is a cross-workflow architecture guide; use the individual artifact pages for concrete write, review, and
retrieval procedures.

## How context is created and used

### Where data belongs

A Scope defines data ownership and access control for its Sources, Artifacts, Candidates, and processing state.
The outer box represents one Scope; the arrows inside it describe how content is formed.

### How content is formed

![Two artifact creation paths within a Scope: process Sources, or explicitly create and update; use Candidate review when required](/docs-diagrams/context-formation-en.svg)

Sources preserve evidence. Processing evidence and committing an artifact are separate actions: capture does not mean
generation has finished, and generation does not necessarily mean approval. Types that support explicit creation or
updates can also accept content without automatic extraction.

- Generated Experience and Skill proposals require Candidate review before commit. Direct Artifact Create/Replace can
  commit a validated payload as a formal Revision immediately; these endpoints do not create a Candidate automatically.
- Profile policy selects automatic commit or review; manual creation and replacement are also supported.
- Memory and Topic Memory commit according to their processing rules; Topic Memory does not support manual creation or updates.
- Handoff commits through the work continuity workflow; Prompt is managed through configuration APIs.

A successful commit creates an immutable Artifact Revision. See each type's guide for permissions, version checks, and
generation requirements.

### How artifacts are used

| Purpose | Content and operation |
| --- | --- |
| Provide context for the current task | Memory, Experience, Profile, Topic Memory → select under recall and assembly rules → PreparedContext |
| Continue work across sessions | Handoff → read the transfer → continue the task |
| Make a Skill discoverable by a host | Skill → export a specified approved Revision → host-local Skill |
| Adjust generation behavior | Prompt → prompt configuration used by the corresponding operation |
| Find and trace content | Filter content that supports tags; read history by exact Revision |

These are parallel uses. Handoff is itself an Artifact family; PreparedContext is temporary output for one request.
Not every family enters PreparedContext automatically: Profile requires explicit selection, while Skill and Handoff
have their own consumption paths.

## Where components run

![Agent hosts access PowerContext Server through integrations; application and background processing use storage and call model services when needed](/docs-diagrams/context-deployment-en.svg)

The Agent and integrations run on the host and connect to PowerContext Server over HTTP. The Server owns application
APIs, authorization, and background processing. Workers are logical responsibilities in this diagram, not a requirement
to deploy another service. The Server calls configured generation or embedding models when needed. SQLite uses a local
data file; OceanBase / SeekDB uses a configured database connection. The storage box represents alternative backends.

See [deployment and operations](../operate/index.md) for deployment settings, service checks, and recovery procedures.

## Component and responsibility boundaries

| Component or role | Owns | Does not own |
| --- | --- | --- |
| Agent / Host | Submits requests and decides when to save, hand off, or use context | Extra execution authority because content was recalled |
| HTTP, MCP, Python Client, and CLI | Projects different integrations onto one Server contract | An assumption that every integration exposes the same capabilities |
| PowerContext Server | Resolves Scopes, enforces access, and serves Source, Artifact, and PreparedContext APIs | User authorization decisions for the Agent |
| Runtime / background workers | Generates Memory, Experience, Skill, Profile, or Topic Memory from eligible Sources | Bypassing Candidate review or treating generated content as an execution command |
| Review / projection | Reviews Candidates or exports an exact approved Skill Revision to a host | Rewriting historical Revisions, installing, or executing a Skill automatically |
| Database and persistence layer | Stores Scopes, Sources, Artifact Revisions, and processing state | The deployment layer's backup and recovery policy |

Model generation, human review, and execution authority are separate boundaries. Proposals requiring review commit after
approval; direct Create/Replace commits a validated Revision under the caller's governance policy. Export creates a
host-local copy without granting execution authority. `PreparedContext` is temporary for one Agent turn, not a new Artifact.

## Content types currently supported

| Type | Purpose and current lifecycle | What happens next |
| --- | --- | --- |
| Scope | Isolates Sources, artifacts, Candidates, and runtime state while carrying access policy | Resolve a Scope before every content operation |
| Source | Stores captured evidence or references to external material | Read explicitly, or process asynchronously through a configured pipeline |
| Memory | Stores facts, decisions, constraints, state, and next steps; `retire` removes active recall but preserves history | Write explicitly or extract from Sources, then retrieve |
| Experience | Records a reusable situation, action, outcome, and lesson | Generated proposal: proposal → Candidate → review → approved Revision → PreparedContext recall; direct write: Create/Replace → Revision |
| Skill | Stores an exportable name, description, instructions, validation checks, and lineage | Generated proposal: proposal → Candidate → review → exact export to a host; direct write: Create/Replace → Revision → exact export |
| Profile | Stores stable Scope background and subject information | Generate from subject Sources or policy, then review, replace, or roll back by appending a Revision |
| Topic Memory | Incrementally derives topic summaries from long-running Sources with progressive detail | Request `flush`, search current heads, then `get` an exact Revision |
| Handoff | Records task boundaries, transfers, and milestones | prepare → optional commit → receiver acknowledgement / outcome |
| Prompt | Scope-level operational prompt configuration, not factual evidence and not ordinary recall | Manage with `scope.admin` access |
| Tag | Filterable metadata for Memory, Experience, Skill, and Handoff | Manage through tag APIs; it is not an Artifact family |

The code enum for write-capable Artifact families is `memory`, `experience`, `skill`, `handoff`, `profile`, and `prompt`.
Artifact reads also expose the specialized `topic-memory` family. Topic Memory currently has no generic create, update,
delete, or retire API.

## Current data governance and recovery boundary

The implementation currently provides these consistency and boundary controls:

- Scope-level access control, plus `expected_version` and ETag / `If-Match` concurrency protection on writes;
- immutable Artifact Revisions and exact references, Memory `retire`, and Profile replacement or rollback by appending a Revision;
- isolation between pending, approved, and rejected Candidates so unreviewed content does not enter Artifact retrieval or
  `PreparedContext`.

There is no generic user-level Artifact delete, TTL/retention policy, export/import, or one-click Scope restore API today;
Topic Memory also has no manual delete endpoint. Operators can back up the database or data directory using the [Server
deployment backup principles](../operate/deploy-server.md), then restore or migrate it using [troubleshooting and
migration](../operate/troubleshoot.md). Those are operational responsibilities and should stay outside the context
management data lifecycle.
