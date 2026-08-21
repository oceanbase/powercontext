- Proposal Name: `development_context_and_scope_model`
- Start Date: 2026-08-12
- Status: Draft
- Tracking Issue: [oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219)
- Related RFCs: [RFC 0002](0002_core_sdk_product_model.md), [RFC 0019](0019_local_source_memory_runtime.md),
  [RFC 0028](0028_context_pack.md), [RFC 0048](0048_handoff_artifact.md),
  [RFC 0072](0072_scoped_statistics_and_usage.md), and [RFC 0082](0082_handoff_report.md)

# Summary

PowerContext derives a `scope_id` from a project and uses it to share durable context across Sessions. Handoff later
reused that same scope as the identity of one linear Workstream. As a result `scope_id` now carries three conflated
meanings at once: the **project** (the Codex integration derives it from the Git remote or path), the **memory
isolation unit** (the Runtime keys one Source journal, one Memory head, and one Trigger cursor on it), and the
**Workstream** (Handoff keeps one linear history per scope). This conflation cannot express parallel work inside one
project or development that spans several projects.

Three approaches were weighed: **A** — a three-layer model with a new `workstream_id` (cleanest, but reverses two
RFC 0082 decisions and forces a backfill migration); **B** — keep `scope_id` as the Workstream identity and add a
separate, opt-in Project layer to carry the *shared* meaning; **C** — documentation-only clarification (no new
capability, does not actually solve the problem). **This RFC adopts B.** The full side-by-side comparison is in
*Rationale and alternatives*; the rest of this document specifies B.

Concretely, this RFC fixes the identity boundaries relating **Project**, **Workstream**, **Session**, and **scope**. It
decides:

- **`scope_id` stays the Workstream identity and the Runtime partition key** — this version introduces no `workstream_id`.
- **Project** becomes an explicit, application-layer grouping that carries *shared* project context while each Workstream keeps an *isolated* history.
- **Session** is a transient participant boundary, never an identity.
- **`project_id`** stays a separate, server-owned identity that lives only in the Builtin Runtime application layer and never enters the Core Protocol.
- Existing `scope_id`-keyed API, CLI, integrations, and data keep their contracts; the new sharing is additive and opt-in.

This RFC decides the model and invariants. It does not fix detailed user-flow orchestration, migration tooling, or the
concrete API/CLI/Dashboard shapes; those are follow-up work created after acceptance.

# Motivation

The current model works for a single linear project but breaks in two common situations:

- **Parallel work in one project.** Two features, or a long refactor alongside ongoing bug fixes, have different
  objectives and next actions and are continued by different Agents. Today the only way to isolate their histories is
  to derive different `scope_id` values — which also fragments the shared project context they should see.
- **Work spanning multiple projects.** A cross-repository change has no first-class relationship, only a weak
  `external_refs` tag.

Both stem from one cause: `scope_id` is overloaded with three meanings, and the *sharing* meaning wants scopes to be the
**same** while the *isolation* meaning wants them **different** — one key cannot be both.

| Meaning | Who relies on it | What it keys | Wants scopes to be… |
| --- | --- | --- | --- |
| **Project** (sharing unit) | Codex integration, README | context derived from the repo's Git remote (or path) | the **same** (to share) |
| **Memory isolation unit** (Runtime partition key) | Core Runtime | one Source journal + one Memory head + one Trigger cursor | — (mechanical) |
| **Workstream** (isolation unit) | Handoff, RFC 0048/0082 | one linear Handoff history | **different** (to isolate) |

This RFC resolves rows 1 and 3 by moving the *sharing* meaning up to a Project layer and leaving *isolation* on
`scope_id`. (The system already runs these as two unreconciled layers — a flat `scope_id` in Core/Runtime/Memory and a
`Project → Workstream(≡scope_id)` catalog in Handoff Report; see Prior art.) The tracking issue's load-bearing
invariants — preserve shared context across Sessions, keep Workstream histories isolated, never equate Session with
Workstream, don't assume the current `project_id` is final, treat migration as follow-up — are each preserved below.

# Guide-level explanation

Four concepts; the binding definitions are the invariants I1–I8 in the Reference-level section, so this is only the
mental model.

- **scope** — an opaque, integration-owned partition string (≤256 chars), unchanged from RFC 0019. One scope keys one
  Source journal + one Memory head + one Trigger cursor.
- **Workstream** — one continuable line of work (single objective, single linear Handoff history). **Its identity *is*
  its `scope_id`**; no separate `workstream_id`. Two pieces of work are different Workstreams — and use different
  scopes — exactly when they can be continued independently (RFC 0082's rule); branch switches/renames/rebases reuse
  one scope.
- **Project** — an explicit, application-layer grouping of Workstreams (a repo, service, or long effort) with an
  immutable `project_id`. It is the boundary for *aggregation* (RFC 0082, existing) and *shared context* (new), and is
  **never** a Handoff scope.
- **Session** — the transient participant boundary that reads/prepares/receives context in one interval. Never an
  entity, identity, or Workstream; appears only as optional `session_id` attribution.

Default behavior is **share, don't isolate**: `derive_scope_id` keys only on the Git remote/path and does not read the
branch, so switching branches resolves the *same* scope. To run work as an independent Workstream you **declare** a
distinct scope (e.g. a git worktree with its own `POWERCONTEXT_CODEX_SCOPE_ID`); branch is never a partition key. The
declaration surface is follow-up design.

**What does *not* start a new Workstream.** Opening another CLI, terminal, or Agent process is a new *Session*, not a
new Workstream (I6). Two CLIs pointed at the *same* scope are two Sessions continuing the *one* Workstream — they share
its single Memory head and single linear Handoff history, and concurrent commits are resolved by RFC 0048's CAS
conflict, not by forking a second history. A new Workstream appears only when a *distinct scope is declared*; the test
is "can these be continued independently" (separate objective, separate linear history), never "is this a different
process, branch, or CLI". So: two CLIs on one declared scope = one Workstream; two CLIs on two declared scopes = two
Workstreams — the CLI is never the deciding factor.

```
Project  (project_id — server-owned grouping; shared context + aggregation; never a Handoff scope)
  ├── Workstream A  (scope_id = git:host/repo#featureX)  ── isolated Sources / Memory / Handoff / Stats
  ├── Workstream B  (scope_id = git:host/repo#refactorY) ── isolated ...
  └── Workstream C  (scope_id = git:host/repo)           ── isolated ...
        ▲
        └─ shared Project context is readable from any Workstream's Session,
           but each Workstream's committed Handoff history stays private.

Session  (transient; attributes activity to a Workstream; never an identity)
```

`#featureX` / `#refactorY` are *explicitly declared* scopes, not branch-derived — default `derive_scope_id` yields only
`git:host/repo` (Workstream C). Isolation lives at the scope level (unchanged); sharing lives at the Project level (new,
additive, opt-in). An existing single-Workstream user is unaffected: an ungrouped scope behaves exactly as today.

# Reference-level explanation

Everything from here to *Drawbacks* specifies **Alternative B** — the chosen model. It is not a neutral survey: the
invariants, data model, and resolution below are B's. Where **Alternative A** would take a materially different path,
the difference is flagged inline as *Under A: …*; the full A-vs-B comparison lives in *Rationale and alternatives*.

## Model and invariants

These eight invariants (I1–I8) are the binding decisions of this RFC; everything else in the document follows from them.

- **I1 — scope is opaque and Runtime-owned.** Unchanged from RFC 0019: non-empty string ≤256 chars; the Runtime does
  not parse structure from it. (No change to `validate_scope_id`, `MAX_SCOPE_ID_LENGTH`, or the OpenAPI pattern.)
- **I2 — Workstream identity is `scope_id`.** No `workstream_id` is introduced in this version. A Workstream's Sources,
  Memory, Handoff, and Statistics are partitioned by its scope and isolated from other Workstreams. The one-linear-
  history-per-scope and CAS-conflict guarantees of RFC 0048/0082 are preserved verbatim. *Under A:* this is the one
  invariant that inverts — `scope_id` becomes a routing key and a new `workstream_id` carries identity; every invariant
  downstream of I2 is B's because B keeps identity on the scope.
- **I3 — Project is an application-layer grouping, not a Core concept.** `project_id` is server-owned and immutable; it
  lives only in the Builtin Runtime application layer. Core Protocol (RFC 0002; `core-protocol.md`) still knows nothing
  about scope, Project, Workstream, or Session. Project membership creates **no foreign key** into Core tables
  (Sources, Artifacts, Memory, Handoff), consistent with the existing `catalog_store` decision.
- **I4 — one scope belongs to at most one Project.** Registering a scope under a second Project is a conflict
  (`scope_already_grouped`), unchanged from RFC 0082. A scope may also belong to no Project (ungrouped), in which case
  it behaves exactly as today.
- **I5 — Project is not a Handoff scope.** A Project never owns Handoff history, never receives a committed Handoff,
  and never writes back into a Workstream. Handoff and Continue remain scope-bound (RFC 0048).
- **I6 — Session is transient and non-identity.** Session is never persisted as a domain identity and never determines
  a Workstream. It equals neither Workstream nor scope.
- **I7 — Branch is not an identity axis.** Unchanged from RFC 0082: branch metadata is a weak signal and untrusted
  activity attribution, never a Handoff boundary.
- **I8 — The Workstream boundary is declared and semantic, not derived from branch or task.** The determinant is
  independent continuability (I2 / RFC 0082), not the git branch or the number of tasks. The default is one scope per
  repository (its Git remote); an explicit declaration starts a distinct Workstream. Neither the branch nor a git
  worktree changes the derived scope — only an explicit `POWERCONTEXT_CODEX_SCOPE_ID` (or a different remote) does.

## Shared Project context (the new capability)

Today RFC 0019 persists a strict one-to-one `scope_id → Memory Artifact` binding via `MemoryBindingStore`, and RFC 0019
itself names the extension path: "supporting multiple instances requires an explicit extension of the application
mapping." This RFC uses exactly that seam. The mechanism below fixes the *shape* of the extension; the ranking/merge
policy and the write surface stay Unresolved (see below).

### Data model

- **Per-Workstream binding (unchanged).** `MemoryBindingStore` keeps its one-to-one `scope_id → Memory Artifact`
  mapping. Each Workstream continues to own exactly one Memory head, isolated from every other scope.
- **Project context binding (new).** A Project **may** own one additional, Project-level binding
  `project_id → Memory Artifact`, resolving to a Memory Artifact distinct from any Workstream's. This is the **Project
  context**. It is a *second row in the application-layer mapping, not a second head on a scope*: no `scope_id` gains a
  second binding, and the per-scope one-to-one invariant is untouched.
- Both bindings live only in the Builtin Runtime application layer. Neither introduces a foreign key into Core tables
  (I3); Core still resolves a Memory Artifact from an opaque key and knows nothing about Project.

The full model is three application-layer catalog records plus the unchanged per-scope head — only the last row is new:

| Record | Shape | Cardinality | Origin |
| --- | --- | --- | --- |
| Per-scope Memory head | `scope_id → Memory Artifact` | one per scope | RFC 0019 `MemoryBindingStore` (unchanged) |
| Project | `{ project_id, project_key, title }` | one per Project | RFC 0082 catalog |
| Membership | `scope_id → project_id` | ≤ 1 per scope (I4) | RFC 0082 Workstream catalog (`WorkstreamDescriptor`) |
| Project context | `project_id → Memory Artifact` | ≤ 1 per Project | **new (this RFC)** |

A scope's own head is resolved directly; its Project context is resolved indirectly, by following Membership to a
`project_id` and then the Project-context binding. The two Memory Artifacts are always distinct handles.

### Storage seam

The Project context binding is persisted alongside the existing Project catalog (RFC 0082's `catalog_store`),
keyed by `project_id`. Resolving it reuses the same Memory Artifact machinery the per-scope head already uses — the only
new thing is *which key selects which Artifact*, not how Memory Artifacts are stored, revised, or searched. No Core
Memory table, revision format, or search path changes.

### Resolution

Every read and write resolves deterministically from the request's `scope_id`, with no new required parameter on the
call surface:

1. **Locate the scope.** The application layer looks `scope_id` up in the catalog.
2. **Ungrouped → one binding.** If the scope belongs to no Project, resolution stops at its own `scope_id → Memory
   Artifact` head — byte-for-byte today's path. No Project context is consulted or created.
3. **Grouped → two bindings.** If the scope carries a Membership to a `project_id`, resolution additionally resolves
   that Project's context binding. If the Project has no context binding yet, resolution degrades to the ungrouped case
   (own head only).
4. **Reads vs writes.** A *read* hands both resolved heads to the composition step below. A *write* targets **only** the
   scope's own head (I2, I5); the Project context is never on a write path in this version.

Which keys select which Artifacts, and in what order they are resolved, is fully decided here. Only how two resolved
read-heads are *ranked and merged* is deferred — the next subsection.

### Read composition (shape fixed, policy deferred)

When a Session prepares context for a Workstream whose scope is grouped under a Project, the application layer resolves
**two** Memory heads — the Workstream's own scope-level head and the Project context — and composes them into the
prepared context. Two things are **decided** here:

- **Both are readable.** The Workstream's own head is always read; the Project context is *readable* from any
  constituent Workstream's Session.
- **Composition is a read-only overlay.** Composing never writes to either binding, never copies one Workstream's
  entries into another, and never mutates the Project context. Isolation is preserved *structurally* — the two heads
  remain two distinct Artifacts, so no Workstream's private history can end up in another's store.

What stays **Unresolved** (deferred to the Memory/Context follow-up): the precedence between the two heads, the
merge/dedup/ranking rule when both surface an entry, and how cross-Workstream leakage is bounded at read time. This RFC
fixes that composition *happens over two isolated Artifacts*; it does not fix *how* they rank.

### Write isolation

A Workstream's committed Handoff history is never written to the Project, and the Project never writes back into a
Workstream (I5). Whether a Session can *write* into Project context — and through which operation and trust markers — is
deferred; the default in this version is that **Project context is read-shared only**.

### Context Pack (RFC 0028)

Context Pack keeps its "one scope per request, no mixed scopes" contract unchanged. Surfacing Project-level reads
through `prepare_context` would require either a new contract version or an explicit Project parameter; this RFC does
not modify the existing single-scope contract and leaves the surface choice to the follow-up.

## Project entity and registration

A Project is a catalog record in the Builtin Runtime application layer (RFC 0082's `ProjectDescriptor` in the
`catalog_store`), not a Core entity (I3). It carries three fields:

- `project_id` — server-generated, immutable (`prj_<uuid>`), the durable identity. Never client-supplied, never
  reused after retirement.
- `project_key` — a catalog-unique human key (e.g. `acme/api`) used to look a Project up without knowing its
  `project_id`.
- `title` — a mutable display label; changing it never changes identity.

**Registration.** A scope becomes a Project member by binding its `scope_id` to a `project_id` in the Workstream
catalog (the existing `WorkstreamDescriptor` / `create_workstream` seam, keyed on `scope_id`). Binding a scope that is
already grouped under a *different* Project is the existing `scope_already_grouped` conflict (I4) — one scope, at most
one Project. An **ungrouped** scope is the default and needs
no registration: it owns its Sources/Memory/Handoff/Statistics exactly as today and reads no Project context.

**Grouping states.** A scope is in exactly one of two states, and this version defines exactly one transition between
them:

- **Ungrouped** — no Membership record; behaves exactly as today.
- **Grouped under P** — Membership `scope_id → P`; additionally reads P's Project context.

`ungrouped → grouped(P)` is the registration above. Re-homing (`grouped(P) → grouped(Q)`) and un-grouping
(`grouped → ungrouped`) are **not** defined here — a second registration under a different Project is *refused* with
`scope_already_grouped`, never silently re-homed (see Unresolved: moving scopes between Projects). Grouping a scope
never rewrites, moves, or merges its existing per-scope Memory or Handoff history; it only adds the ability to read P's
Project context.

**Lifecycle.** `title` is mutable; `project_id` is immutable for the life of the Project. A Project exists once at
least one scope is bound to it (or once it is explicitly created, if the follow-up adds an explicit create surface).
Moving a scope between Projects, and merging or splitting Projects, are **out of scope** for this RFC (listed under
Unresolved / future work) — this version fixes only that a scope belongs to at most one Project and that membership is
an application-layer binding.

## Worked example: two parallel Workstreams in one Project

A team develops the `acme/api` service. They register a Project:

- `project_id = prj_ac…`, `project_key = acme/api`, `title = "ACME API"`.

Two lines of work run in parallel and must not share a Handoff history:

- **W-main** — ongoing bug fixes on the default checkout. `derive_scope_id` reads only the Git remote, so its scope is
  `git:github.com/acme/api`. No branch is encoded.
- **W-refactor** — a long storage rewrite that must be independently continuable. A worktree of the same repo shares
  the *same* Git remote, so it would otherwise derive the *same* scope as W-main; the team therefore **explicitly
  declares** a distinct scope via `POWERCONTEXT_CODEX_SCOPE_ID = git:github.com/acme/api#storage-rewrite`, which
  `derive_scope_id` uses verbatim. The worktree only lets two checkouts coexist on disk — the *declared scope alone*
  creates the isolation, and the `#storage-rewrite` suffix is *declared*, not derived from the branch (I8).

Both scopes are bound to `prj_ac…`. Now:

- **Reads.** A Session on W-refactor prepares context and resolves **two** heads — W-refactor's own scope-level Memory
  head ⊕ the Project context bound to `prj_ac…` — composed as a read-only overlay. It can therefore see shared
  project-wide knowledge (build quirks, service conventions) **without** seeing W-main's in-flight bug-fix history. A
  Session on W-main symmetrically sees its own head ⊕ the same Project context.
- **Writes.** Each Session's committed Handoff appends only to its own scope's single linear history: W-refactor's
  Handoffs never enter `git:github.com/acme/api`, and neither Workstream writes back into the Project (I5). The Project
  context is read-shared only in this version.

Both invariants hold **simultaneously**: sharing (both Sessions read one Project context) and isolation (two separate
linear Handoff histories, two separate Memory heads) — which is exactly what a single overloaded `scope_id` could not
express. An ungrouped `acme/api` checkout (no Project bound) would simply read its own head and no Project context,
behaving exactly as today.

## Acceptance scenarios

These pin the model to observable behavior. Each asserts *structure*, not the deferred read-composition policy.

| # | Given | When | Then | Invariant |
| --- | --- | --- | --- | --- |
| 1 | scope bound to no Project | a Session prepares context | only the scope's own `scope_id → Artifact` head resolves; no Project context is read or created — byte-for-byte today | I4, Resolution ② |
| 2 | scope grouped under `P`, and `P` has a context binding | a Session prepares context | two **distinct** Artifact handles resolve (own head ⊕ `P`'s context), composed as a read-only overlay | Resolution ③ |
| 3 | scope grouped under `P`, but `P` has no context binding yet | a Session prepares context | resolution degrades to the ungrouped case (own head only) | Resolution ③ |
| 4 | two Workstreams under one `P` | one commits a Handoff | it appends only to its own scope's linear history; the sibling's history and `P`'s context are never written | I2, I5 |
| 5 | two Workstreams under one `P` | one's Session reads | it sees `P`'s shared context but **not** the sibling's in-flight head (two Artifacts stay distinct) | Read composition (structure) |
| 6 | scope already grouped under `P` | it is registered under `Q` | `scope_already_grouped` conflict; never silently re-homed | I4 |
| 7 | one repository checkout | the git branch is switched | the **same** scope resolves; no new Workstream is created | I7, I8 |
| 8 | any Project membership | Core tables are inspected | no foreign key into Core points at `project_id`; Core still resolves an Artifact from an opaque key | I3 |

Scenarios 2 and 5 deliberately assert only that two isolated Artifacts are resolved — the precedence/merge order
between them is Unresolved (see below) and must not be pinned by a test yet.

## Interaction with existing subsystems

- **Memory (RFC 0019).** Per-scope binding unchanged. Optional Project-level binding added via the documented
  application-mapping extension. No change to Core Memory, Revisions, or search.
- **Handoff (RFC 0048).** Fully unchanged. One linear history per scope; CAS conflict; Prepared vs committed;
  evidence/Continue `untrusted_history` semantics all preserved. Workstream stays ≡ scope.
- **Handoff Report (RFC 0082).** Reconciled, mostly reaffirmed. The Project → Workstream catalog, `WorkstreamDescriptor`,
  `WorkspaceBinding`, activity store, and `handoff-reports` API keep their current shape. The one clarification is that
  Project now also carries *shared context* in addition to *aggregation*; RFC 0082's "aggregation-only, never writes
  back" rule is preserved (Project context is a separate read binding, not a write-back of Handoffs).
- **Statistics (RFC 0072).** Per-scope statistics unchanged. Optional Project/Workstream roll-up is **deferred** to a
  follow-up (this RFC does not require cross-scope aggregation now, but records it as the natural place for it).
- **Core Protocol (RFC 0002).** Unchanged and explicitly out of bounds for Project/Workstream (I3).

## Compatibility

- **Existing data.** No rewrite required. Existing scopes keep their Sources, Artifacts, Memory, Handoff, and
  Statistics unchanged. Grouping and Project context are additive; an ungrouped scope reads no Project context and
  behaves exactly as today.
- **API.** Every existing `scope_id`-keyed endpoint (Memory, Handoff, stats, sources, context) keeps its contract. New
  Project-context reads are additive and opt-in. No existing request shape changes meaning.
- **CLI.** Existing `--scope-id` commands are unchanged.
- **Codex integration.** `derive_scope_id` (Git remote → path fallback) is unchanged. `POWERCONTEXT_CODEX_SCOPE_ID`
  keeps its meaning. Registering a derived scope under a Project is an additional, optional step.
- **Deprecations.** None in this RFC.

## Identity encoding (unchanged surfaces, for reference)

- `scope_id`: opaque string ≤256, client-supplied; derived from the Git remote, else a `local:` path hash;
  `POWERCONTEXT_CODEX_SCOPE_ID` overrides it verbatim.
- `project_id`: server-generated immutable (`prj_<uuid>`), catalog-owned.
- `project_key`: catalog-unique human key; `title`: mutable display.
- No `workstream_id` in this version.

# Drawbacks

- **Parallel work still requires deriving multiple scopes.** Because Workstream stays ≡ scope, expressing parallel work
  is a naming/derivation discipline (distinct scopes under one Project), not a first-class "one Workstream, many
  branches" identity. Teams that want a single Workstream to span branches or repositories are not served by this
  version.
- **Cross-Project work remains weakly modeled.** Development spanning multiple Projects still relies on `external_refs`
  rather than a first-class cross-Project relationship. This RFC does not close that gap.
- **Two Memory bindings introduce a composition question.** Adding Project context means a Session may read two Memory
  sources; the precedence/merge policy is real design work deferred to the follow-up, and getting it wrong risks
  leaking one Workstream's private context into another via the shared layer if the boundary is drawn incorrectly.
- **A second identity may still be wanted later.** By deliberately not introducing `workstream_id`, this RFC bets that
  the deferred extension is acceptable; if parallel-branch identity becomes a hard requirement, a follow-up RFC must
  reopen RFC 0082's central identity decision.

# Rationale and alternatives

Three directions were considered. **A and B are the two real contenders** (C is a no-new-capability baseline). Both
start from the same problem — `scope_id` carrying three meanings — but split it differently. Read the table
top-to-bottom: **A moves each meaning onto its own key** (three keys), while **B keeps isolation on `scope_id` and
lifts only the *sharing* meaning onto a new, additive Project layer** (one key, one opt-in layer).

| Design dimension | Today (`scope_id`) | **Alt A** — three-layer (deferred) | **Alt B** — Project layer (this RFC) |
| --- | --- | --- | --- |
| Sharing (project context) | `scope_id` | `project_id` (first-class) | new **Project layer** (additive) |
| Runtime isolation / partition key | `scope_id` | `scope_id` (routing only) | `scope_id` (unchanged) |
| Workstream identity | `scope_id` | new `workstream_id` | `scope_id` (unchanged) |
| `project_id` role | thin, app-layer | first-class identity | app-layer grouping (unchanged) |
| One Workstream across branches/repos | no | **yes** (first-class) | no (derive distinct scopes) |
| Migration of existing scopes | — | **required** (backfill every scope) | **none** |
| RFC 0082 identity decisions | — | **reversed** (two of them) | preserved |
| Core Protocol change | — | likely | none |

### Alternative A — Three-layer model with a distinct `workstream_id` (deferred)
**What it does.** Give each conflated meaning its own key. `project_id` becomes a first-class identity (sharing and
aggregation); `scope_id` is demoted to a pure Runtime partition/routing key that no longer *means* "Workstream" — one
`project_id` may map to many scopes, and a scope is just a storage partition; a new `workstream_id` becomes the
Handoff/history identity, so a single Workstream can span several scopes/branches/repositories because its identity no
longer rides on the partition key.

**What it buys.** Parallel and cross-repository work become first-class: one Workstream moves across branches or repos
without fragmenting its history; Branch and Session get natural non-identity positions; sharing and isolation never
contend because they live on different keys by construction. This is the cleanest expression of the three meanings.

**What it costs.** It reverses two central RFC 0082 decisions ("`scope_id` is the only Workstream identity"; "no
separate `workstream_id`"); it adds a resolution/indirection layer that every operation across Handoff, Handoff Report,
and Runtime must traverse (`workstream_id` → scope(s) → Artifacts); it forces a **backfill migration of every existing
scope** into the new three-key model; and it likely touches the Core Protocol boundary. That is a large blast radius
for a first version.

**Verdict — deferred, not discarded.** Preserved as a Future possibility, and reachable *on top of* B without
contradicting B's invariants — B is the smaller first step on the same path.

### Alternative B — Keep `scope_id` as Workstream identity; add a Project shared-context layer (this RFC, chosen)
**What it does.** Leave isolation exactly where it is — scope ≡ Workstream, `scope_id` unchanged as both identity and
partition key — and add *sharing* as a new, additive, opt-in Project-context binding (`project_id` → Memory Artifact).
No key is demoted or reassigned; `workstream_id` is not introduced.

**What it buys.** It satisfies both hard constraints from the issue (shared context *and* isolated histories) with
**zero migration and no Core change**; it lands inside seams the existing RFCs already left open (RFC 0048's "parallel
workstreams, derived scopes"; RFC 0019's "multiple instances via explicit application-mapping extension"; RFC 0082's
deferred cross-Project reporting) rather than overturning RFC 0048's linear-history model; and it keeps Alternative A
reachable as a later extension.

**What it costs (accepted).** Parallel work stays a scope-derivation discipline, not a first-class "one Workstream,
many branches" identity; cross-Project work remains weakly modeled; and the two-binding read-composition policy is real
work deferred to the follow-up. These are the Drawbacks above, accepted as the price of a small, reversible first step.

**Verdict — chosen.**

### Alternative C — Clarify semantics only, no new grouping capability
**What it does.** Only document the three conflated meanings of `scope_id`, standardize the derivation and parallel-work
naming conventions, and optionally add Project roll-up to Statistics. No new modeled capability.

**Verdict — rejected.** It does not actually solve the issue's core need: parallel work and shared-but-isolated context
would remain a convention rather than a modeled guarantee.

**Impact of not doing this.** `scope_id` keeps carrying three meanings; teams either fragment shared context to gain
isolation or pollute one Workstream's history to keep context shared. RFC 0019/0048/0082 continue to encode an
unstated model that new features must each rediscover.

# Prior art

- **RFC 0048** first asserts "one current workstream per scope" and explicitly parks *parallel workstreams* and
  *derived scopes* under Future possibilities — the seam this RFC builds on.
- **RFC 0082** specifies the current `Project → Workstream(≡scope_id)` catalog, the "Project aggregates, never writes
  back" rule, and the branch-is-not-identity rule; this RFC reaffirms those and adds shared context.
- **RFC 0019** documents the `MemoryBindingStore` one-to-one mapping and names the multiple-instance extension path
  used here.
- The RFC process itself (`docs/en/rfcs/README.md`): validate the problem with maintainers first, keep the initial
  scope narrow enough to review and implement.

# Unresolved questions

To resolve before merge:

- **Context composition and precedence.** When a Workstream's scope is grouped under a Project, exactly how are the
  Workstream Memory head and the Project context composed on read? What precedence, and how is cross-Workstream leakage
  prevented? (Blocks the Memory/Context follow-up.)
- **Writing Project context.** Can a Session write into Project context, through which operation, and with what trust
  markers? Default in this version is read-shared only.
- **Context Pack surface.** Should Project context be surfaced through `prepare_context` (new contract version) or a
  separate read path? (RFC 0028 stays single-scope for now.)
- **Workstream boundary declaration (I8).** Through what surface, and when, is the per-repository default overridden?
  This must reconcile RFC 0082's "parallel branches use distinct scopes" rule with the current `derive_scope_id`, which
  keys only on the repository.

Intentionally out of scope (follow-up decisions, may need their own RFC):

- Introducing a distinct `workstream_id` (Alternative A).
- First-class cross-Project relationships beyond `external_refs`; Portfolio/Program entities above Project.
- Statistics roll-up across a Project or Workstream group.
- Moving, merging, or splitting Workstreams across Projects.
- Migration tooling and detailed user-flow orchestration.

# Future possibilities

- **Alternative A as an extension.** If parallel-branch or cross-repository Workstream identity becomes a hard
  requirement, a follow-up RFC can introduce `workstream_id` on top of this model, with `scope_id` demoted to a routing
  key — reachable without contradicting the invariants set here.
- **Cross-Project aggregation.** Portfolio/Program-level reads and cross-Project Handoff Reports (already deferred by
  RFC 0082).
- **Project-scoped Statistics roll-up** (natural extension of RFC 0072).
- **Richer Session semantics** (concurrency signals, live-state hints) as long as Session remains non-identity —
  including how *concurrent Sessions on one scope* coordinate beyond RFC 0048's CAS conflict (the open residue of "does
  a different CLI start a new Workstream": no, but simultaneous CLIs on one scope may still want live-state signals).
