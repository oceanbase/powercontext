---
title: "Dashboard design principles"
description: "Design decisions for reading saved content, showing Scope boundaries, and working with revision history in the Dashboard."
---

# Dashboard design principles

The Dashboard is a content viewer for personal use and demonstrations, disabled by default. It shares the Server access mode: local access can be anonymous, while enforced access requires a static token. This document helps developers and reviewers decide what a page should show, how to organize reading, and whether a change preserves the behavior users need. The API contract in `openapi/powercontext.yaml` and the service implementation define the available capabilities.

## What the Dashboard helps users do

A user may open the Dashboard to check an agreement, find an experience relevant to a similar problem, or resume interrupted work. The page needs to make the current scope clear, help them find the relevant record, and provide access to source material when they need to check it.

The core experience for 1.0 is reading saved content. Existing pages also list revision history, compare two revisions, and roll an exact stored revision forward through the same conditional replace the API already uses. Each Atomic Memory shows its own revisions and comparison read-only. Restoring an Atomic Memory belongs to its restoration flow, which previews every affected memory before confirmation. Memories, experiences, skills and handoffs can exist independently. A scope containing only memories should work normally, and an empty scope should still have a clear entry point and page structure. Existing tools and APIs handle generation, review and saving. Reading their results should not require generation configuration.

Scope names, summaries and saved content supply the business topic. A payment service, customer interviews and personal research can use the same interface without separate navigation for each domain. Before adding a section, explain which task it helps the user complete and whether the available data supports what it claims.

## What content and scopes mean

### What each content type tells the user

| Content | What the user learns |
| --- | --- |
| Memories | Saved agreements, preferences, facts and decisions |
| Experiences | What happened in a particular situation, what was learned, and the conditions and evidence behind that lesson |
| Skills | Reusable practices, steps and checks |
| Handoffs | The objective, progress and next action recorded for a piece of work |
| Usage | Preparation counts, context size estimates, and PowerContext's own model usage |

Experiences need their original context, skills need their conditions of use, and handoffs describe the state at the time of saving. Related records may support different conclusions. A record's claim that a fix is complete still needs to be checked against the relevant evidence.

Home and collection pages may shorten previews; detail pages preserve the full text. The presentation layer shows existing information rather than deriving new conclusions by splitting or rewriting sentences. Source references let users check the content. Link to source text when it is readable, and retain the reference when only its identity is available.

### Scopes determine what the user is viewing

A scope defines content membership and selects what the user reads. An entry point without an explicit scope uses the Server's configured default. Choosing another scope in the dropdown changes the browsing location, not the default save destination or tool bindings.

Users need to distinguish scopes with similar names. The selector shows each scope's name and recorded ancestry, using the Server's parent-child relationships. A child collection does not automatically include its parent's content. Explicit references to other scopes during context preparation are also separate from hierarchy.

Content collections and usage both read the current scope and update when the user switches scopes. Usage for a parent does not automatically include its children. For example, an empty child may have no memories even when its parent has some. That is a valid state; the page must not fill the child with the parent's content.

### What the data supports

No records, no comparable data, unreported values and read failures need distinct presentation. Show zero only when the API reports zero. If a scope or record cannot be read, explain the failure and offer an available recovery action instead of silently substituting other content.

Prepared-context estimates cover only comparable records. A reduction percentage describes a change in text size. It does not establish completeness, content quality or billing savings. Show estimated increases as increases. List model input, output and embedding usage separately, and leave missing values as unreported.

Home summaries, daily charts and detail tables use the same statistical definitions. The Server supplies the reporting period and daily attribution; the interface does not invent historical data.

## Topic Memory processing evidence

The Topic Memory directory has a compact Processing status section and a details drawer for the selected Scope.
This covers the Source-window path in [#2308](https://github.com/oceanbase/powercontext/issues/2308), within
[#2130](https://github.com/oceanbase/powercontext/issues/2130). Saved Topic search, pagination and exact revision
reads remain independent; an inspection error leaves independently readable topics available.

| Family / path | Coverage of this section | Evidence and limitations |
| --- | --- | --- |
| Topic Memory / Source window | Selected-Scope progress, current allowance and applicable retained block | Durable records; current execution is unknown. |
| Memory, Profile, Experience / Source consumption | Not covered by this section | Each path needs its own progress and intent mapping. |
| Skill, Prompt and other explicit / Dream paths | Not covered by this section | Existing Dream records remain on the processing page; Source-window progress is not applicable. |
| Handoff | Not covered by this section | Existing corresponding operation outcomes remain separate from Source-window progress. |

| Displayed fact | Authoritative source | Meaning and limitation |
| --- | --- | --- |
| Consumed through position | `pc_source_cursors`, selected Scope and `topic-memory-source-window` binding | Input consumption; does not establish publication or a recorded no-change outcome. A missing cursor starts at zero. |
| Source head position | `pc_source_journal_heads`, selected Scope | Latest journal position; position differences are not artifact counts or completion percentages. A missing head starts at zero. |
| Retained target position | `pc_topic_memory_processing_targets`, same Scope and binding | Can remain after the cursor catches up; absence means no retained target, not execution success. |
| Current frontier allowance | `pc_topic_memory_work_budgets`, same Scope, binding and cursor position | Recorded attempts, request reservations and token capacity reservations; missing records remain unavailable. Reservations are conservative, not exact provider calls, usage or billing. |
| Exact attempt / allowance end positions | Current allowance or retained block | Attempt and cumulative allowance ranges can differ. Missing exact attempt positions remain unknown. |
| Current retained block | `pc_topic_memory_work_blocks`, matched to current allowance | Scope, binding, frontier, attempt and terminal reason must match; the block must be unretried and still apply to outstanding input. Historical or superseded blocks are not current failures. |
| Safe reason and last error | Applicable retained block | Retained codes only; no raw provider exceptions, Source bodies or model content. Missing last errors remain unknown. |
| Block first observed at | Applicable retained block's `blocked_at` | First observation time, not an invented failure time. |
| Snapshot read time | Scope read snapshot clock | Read time, not proof of a fresh Worker observation. |
| Current execution | No Scope-and-binding execution observation is used | Explicitly unknown, including external workers and after restart; family-wide counts cannot establish this Scope's state. |

The directory loads saved topics first, then inspects processing in a separate authorized, consistent read snapshot
with its own two-second timeout. Exact revision pages do not inspect processing. The projection is adopted only after
its transaction and required audit complete. Inspection timeout, connection failure or inspection-specific
authorization/audit failure produces a section error without discarding independently authorized topic reads.
Request cancellation still propagates. At most five keyed SELECTs return at most one current allowance and block. It does not scan Source content or block history, call
models, probe readiness, dispatch work, request retries, change allowances or write business/processing state.
Existing authorization and audit behavior stays at its owning boundary. No public HTTP contract is added.

The two-second budget applies to processing inspection, including its transaction and audit boundary; it is not a
two-second response-time guarantee for the entire page. Topic reads, cancellation cleanup and rendering also take
time. Directory browsing uses pages of 50 topics. Search uses the existing bounded result set and has no search
pagination; processing inspection does not change either behavior.

The supported recovery status operation can materialize legacy diagnostics and remains separate. An exhausted
allowance without an applicable retained block stays pending while input or a target remains; page access does not
create its missing diagnostic. The final allowed attempt might still publish. Explicit recovery retains the old
block for audit, but that retried block no longer appears as current. A caught-up cursor without a retained target
is shown as Inputs caught up, without claiming artifact completion, stopped execution or a no-change outcome.

Run the provider-free Dashboard acceptance scenarios with:

```bash
uv run --locked pytest -q tests/e2e/test_dashboard_topic_processing.py
```

The suite uses real Dashboard requests, SQLite and controlled persisted fixtures. It covers progress, targets,
current/obsolete blocks, recovery transitions, missing legacy diagnostics, a final attempt without a block, repeated
passive reads, Scope isolation, immediate errors, timeouts, invalidated inspection connections, inspection-specific
authorization/audit failures, independent saved-topic reads and a second Runtime opening the same
database without local execution history. These are local checks, not real-provider or OceanBase acceptance.
Additional scenarios cover search hits and empty results, multi-page browsing during inspection errors and timeouts,
exact historical revisions after replacement, return navigation, and distinct per-Scope progress, targets, allowances
and blocks. Provider-free repeated reads observe the actual worker-launch boundary and must launch no workers.
Existing Dashboard pagination and recovery suites cover their respective behavior separately.

For a manual check, enable the Dashboard using [Install and run](../docs/get-started/install-and-run.md), open
`/dashboard/topics?lang=en` and `/dashboard/topics?lang=zh`, switch Scope, and open View details. Check drawer
scrolling, close-button and Escape dismissal, focus return, narrow viewports and both themes. Search and open a saved
exact revision, then return to the directory. Also exercise directory → detail → browser Back → open the drawer →
browser Forward, including navigation during the opening transition. The new page must scroll normally, with no
remaining backdrop or focus trap; repeat the sequence to check cleanup. The drawer uses native dismissal before
instance disposal when HTMX replaces its element. Without a configured model, captured input can remain pending and
execution must stay unknown; opening or refreshing this page must not create a block or start processing.

## How pages organize reading

### Home provides an overview and entry points

Home starts with the selected Scope. Issues precede all data when processing is blocked, failed, unconfirmed or unreadable; ordinary pending work and review candidates are not faults. Four pale-blue overview cards precede Token usage, an optional saved handoff, recent Sources and saved revisions. Each preview contains at most three records with a full-directory link and no extra-record disclosure. Home and Token usage share the complete comparison component, including axes, grid, legend, statistics and notices.

The overview summarizes saved sources and active memories, then shows current inventory, pending candidates, and context preparation counts for the selected period. Counts come from the statistics contract rather than the number of home previews. Inventory and preparation counts use different time bases; they are not a conversion funnel for the same inputs and do not establish Agent adoption or task benefit. Pending counts include artifact and tag-change candidates, with a matching Review filter for all pending candidates. Family inventory counts include all lifecycle states of current heads; destination collections retain their existing filters.

The built-in Runtime reads inventory inside the page's authorized, audited snapshot without loading additional recurrence history. Inventory and usage have independent reads and error displays, so either can remain available alongside readable content when the other fails. Changing the reporting period does not change inventory; switching Scope updates all overview values.

Each section has a clear heading and a link to its collection or details. Excerpts are previews. Wide screens place recent processing beside recently saved content; narrow screens preserve the reading order in a single column. Titles use saved content fields rather than technical identities or serialized envelopes. When a content type has no records, its empty state stays in its own section.

Users can also open records directly from a collection, a bookmark or an exact reference.

### Collections support finding; details support reading

Collections help users identify and select entries. Pagination limits how much they need to scan at once, and search narrows the results. Search coverage and result limits must match the API. Filtering the current page must not appear to search the entire collection.

Memories use a list and reading pane. Experiences, skills and handoffs lead from a collection to a detail page containing the full record and related material. Summaries may be shortened when there are many entries, but users must still have a way to read the full text.

Exact Memory links retain the desktop list on the left and display the selected immutable revision on the right. Selecting a list entry replaces the memory browser in place, preserving lifecycle filters, period and pagination in the URL. Small screens retain the accordion. Artifact-only access keeps a standalone detail without disclosing a Scope directory. Source association checks, missing historical reasons and version comparisons retain their existing behavior. The Profile page uses the same outer content width as other main pages.

After opening a record, the user should be able to return to its collection. A new search starts on the first page. Switching scopes clears the previous scope's record selection and pagination position, returning detail pages to the corresponding collection. Changing only the language or theme preserves the scope, period and record being read.

Memory search follows the deployment's capabilities by default and allows users to choose a retrieval mode. Show the actual mode, each result's matching channels and retrieval score so users can inspect why it was recalled; the score is not a similarity percentage.

## Source processing

The processing page reads Content Sources from the existing Source journal and checks the existing Artifact lineage relation. A Source cited by exact Artifact revisions shows one tag per type, such as Memory, Topic Memory, Experience or Skill. Each tag opens the matching type group in the Source detail. Groups show the type first, followed by each result title and body, with links to exact revision details. Results use pages of at most 20 exact revisions, with previous and next links; selecting a type starts its own result page. The original Source section preserves the complete content, including every field of a structured JSON value. A Source without a lineage row shows None. A runtime without a lineage projection shows Unknown. Neither establishes that processing failed. The page shows the first server save time in UTC from the stored Source envelope. Historical envelopes without this metadata show Unknown and are not backfilled. Readers predating this envelope field cannot read newly written envelopes; do not roll back to such a reader after new writes. This view adds no table, no public JSON interface and no execution or feedback record.

Dream details show the persisted status, outcome, reason, attempt count and timestamps, plus saved input-manifest references and a link to the recorded candidate. Candidate publication and current review state are checked in Review. Input-manifest nodes can supply resolved upstream Source references; these execution inputs are not automatically direct evidence for every output. Per-attempt timings, errors and missing history are not invented. Dream records cover only recorded Dream executions, not all automatic Source processing.

### Automatic processing status

Runtime data → Automatic processing shows current-Scope evidence for Memory, Topic Memory, Profile and Experience above the Source directory. Input consumption and local-process execution observations are separate. Input positions and request acknowledgement do not prove publication; missing execution observations do not prove that workers stopped. Dream pending work is excluded; Dream records keep their separate entry.

Expand a type in place to see cursor / input head, the current bottleneck or stage, the latest recorded processing event and the blocked or unconsumed range. These facts remain visible in a compact panel. Cursor positions are not a generated-artifact count or completion percentage. The processing-event time uses a matching durable block or the latest exact-Scope/binding local failure observation, with its source labeled; it does not establish the last successful processing time. No record is shown explicitly when neither is available. Read time is never substituted for processing time. A matching persisted block may provide the last failure in that window, shown using bounded vocabulary; missing or unknown causes are not invented. Request generations and read/observation times stay in a collapsed technical-evidence section. Runtime configuration and the diagnostic guide have separate links. Historical errors do not imply a current failure. The status section offers no retry or budget reset. The Source table's Related artifacts column represents exact Artifact revision lineage. No lineage does not establish processing failure.

Inspection uses selected-Scope read authorization and completed audit, without model calls, work dispatch or business writes. One type's inspection failure preserves other types and independently authorized Source reads. A non-built-in Runtime without a public inspection operation reports unavailable status while Sources remain readable through existing public operations. This is not service-wide monitoring, per-Source execution history or an artifact-completion percentage.


### Context diagnostics

The built-in Server captures prepare diagnostics without an enable switch. Capture uses a separate
`pc_context_prepare_diagnostics` store for all Scopes and actual recall rounds. Standalone SDK use does not write
this Server store. Records have no expiry, retention limit or cleanup action.

Capture starts after initial request validation, Scope admission and authorization of referenced Scopes it will read. Calls rejected
before that boundary have no diagnostic record. Each observed execution attempts one best-effort write for its
ready, empty, failed or cancelled outcome. Storage failure, cancellation during persistence or process termination
can leave a missing record; this is not a lossless audit ledger.

A record contains UTC start/end times, elapsed milliseconds, existing principal/request/trace identities, the original
query and normalized digest, byte budget/use, selected count, optional Receipt, final prepared text and available
same-execution search, rerank and assembly evidence. Elapsed time runs from capture creation to record construction;
it excludes earlier validation/authorization, diagnostic persistence and response delivery. Stage timings can overlap:
Memory search includes reranking, so their sum is not total request time.

Capture does not repeat retrieval or model calls, add an unrequested Receipt to the response or change reuse.
Persistence uses an independent transaction with a two-second deadline; failures do not replace a normal prepare
result, and request cancellation propagates. Authentication headers, vectors and raw model responses are not copied
into the record as separate fields. Query and candidate/final text are stored without redaction; secrets present in
that text remain in the diagnostic copy. Public Receipt v1 remains body-free, with an 8 KiB limit and at most 32 selected
references. Ordinary logs, metrics and traces remain body-free.

The history page defaults to all dates. It reads query and summary columns without stage payloads, with at most 25
records per page ordered by finish time and record ID. Signed cursors bind the Scope, result/date filters and current
subject. Long queries expand in place. Selecting Details opens `/dashboard/context-diagnostic-detail` for one record.

Details show query → retrieval → reranking → budget assembly → final context. Stage tables combine counts, outcomes,
reasons and original-text drawers. Headings show available measured timings; Profile and Code durations come from
their recorded search calls. Retrieval counts use pre-rerank candidate snapshots; without a Memory snapshot that
count stays unknown. Public Receipt search-group counts remain call counts. Reranking shows separate input and output
text. Assembly text contains only Artifact candidates selected by the same-execution Receipt; without a valid Receipt
selection is unknown. Code has no supported exact-revision reader here, so its selected reference and assembly text
are unavailable; captured Code text can still appear in retrieval and final context.

Selected Artifact rows are grouped by family in first-appearance order. They use retained assembly text for the exact
revision, without filling missing evidence from current content. Omission counts describe excluded candidates;
`not_retrieved` is a family-level absence marker, not a candidate count. Empty retrieval, failed retrieval and missing
evidence stay distinct. Topic text contains only the returned title, summary and snippet. Original queries and stage
text missing from older records are not reconstructed. Final content remains readable when Receipt validation fails;
Receipt validation checks its shape, byte use and content digest. UTF-8 byte budgets are not character counts.

Reads complete existing authorization and audit. Releasing stored bodies requires current access to the recorded
Scopes and retained Artifact references. Exact revision links use ordinary authorized reads, with a shared ten-second
deadline; unavailable links are not rendered. Pages escape stored text and send `Cache-Control: no-store`. Inspection
does not prepare again, invoke models or write business data. Prepared text does not prove host delivery, model use
or task benefit.

Migration `000` adds the independent table and indexes, including nullable UTF-8 `query_text`, without rewriting
existing domain records or backfilling past calls. Existing managed databases require explicit migration before startup.
The table adds no Scope deletion cascade or new permission path to deleted-Scope content. Recovery follows the
verified-backup workflow; capture provides no table-drop or history-cleanup operation.

### Artifact operation evidence

Artifact pages expose a compact **Processing status** section and a keyboard-accessible **View details** drawer. It follows the selected Scope and preserves saved-content browsing, search, exact revisions and pagination. The operation selector covers each family's Dream paths and the separate `revise_tags` metadata path. Tag records can concern another family and remain labeled Scope records unless their exact content basis matches.

The card and the drawer both name the current operation, the number of records read and the fact that the complete operation history was not scanned; one operation's page is not a conclusion about the whole family. The methods list and the Handoff list select no saved revision, so every record there remains a Scope record. An exact association starts only after opening a specific Revision: the requested Experience, Skill or Handoff Revision, the Profile Revision on display, or the Memory, Topic Memory or Prompt record actually opened. Generation attribution is read only for that displayed Revision, never for a list's first record.

The drawer opens on the operation that matches the page: `revise_skill` for a Skill detail, `derive_skill` for the Skill list, and the Family's Dream operation elsewhere. An explicit `outcome_operation` overrides that default. Because the read covers a single operation, a missing association means the records read for the current operation type contain no evidence for this exact Revision; it does not prove the Artifact has no outcome.

The selected saved revision is independent of the Scope operation window. Relationships compare the complete `family/artifact_id/revision` within the authorized Scope: exact saved result, target baseline, input-only evidence, or Tag content basis. A latest run with a different target, revision or Prompt key is not the selected Artifact's status. Candidate decisions identify saved results with `result_artifact`; the result body is read under its own current authorization. Candidate kind, identity and version are preserved. If a Candidate was edited after the Run's recorded version, the drawer distinguishes the original reference from the current decision and its saved result. A saved Profile generation can link directly to its exact Dream run even outside the recent window. Retained Handoff generation attribution identifies the operational Prompt revision and whether the Draft was edited; it is not invocation history. Drawer links use ordinary navigation so HTMX replacement cannot leave an orphaned overlay or scroll lock. Publication does not establish installation, distribution, execution, receiver acceptance or quality improvement.

| Family/path | Retained evidence used or available | Interpretation and limits |
| --- | --- | --- |
| Memory/Atomic Memory explicit write | Selected immutable revision | Confirms saved content; does not reconstruct an invocation outcome. Source consumption is a separate path. |
| Memory Dream | `revise_memory` Run and Candidate | Exact target/result; does not advance or describe Source consumption. |
| Experience explicit generation | Existing Candidate; synchronous response when the caller receives it | Pending proposal or explicit no-change at call time; no Dream run or retained complete invocation history. Existing Candidates remain inspectable in Review. |
| Experience Dream | `refine_experience` Run and Candidate | May create or revise; target, input and output remain distinct. |
| Skill explicit generation | Existing Candidate; synchronous response when the caller receives it | Generation is not publication. Unretained no-change/failure results remain unavailable. |
| Skill Dream | `derive_skill` / `revise_skill` Run and Candidate | Derivation has no Skill target. Revision targets an exact baseline; approval is not host installation or distribution. |
| Profile direct write / Source-window generation | Saved revision and its existing generation metadata | Separate from Dream; cursors, policy pointers and acknowledgements do not establish Dream results. |
| Profile Dream | `revise_profile` Run and Candidate | Candidate review and saved revision are distinct from Source-window processing. |
| Topic Memory direct write / Source-window generation | Saved revision; progress/block evidence belongs to the Source-window view | This section does not call recovery status, retry or materialize diagnostics. |
| Topic Memory Dream | `revise_topic_memory` Run and Candidate | Dream outcome is separate from a retained Source-window block. |
| Prompt configuration / writes | Existing configuration and selected revision | `supported/disabled/unsupported` and `auto/custom` describe configuration, not invocation success. |
| Prompt demonstration generation | Synchronous suggestion response | Does not update the Prompt head; no retained asynchronous lifecycle. |
| Prompt Dream | `revise_prompt` Run and Candidate | Exact Prompt key and revision; publication is not proof of inference use or improved results. |
| Handoff prepare/finalize | Temporary Draft/PreparedHandoff carried by the caller | Preparation is not commit. Unretained call outcomes cannot be recovered by browsing saved revisions. |
| Handoff activation | Synchronous `generated/ignored` response; retained boundary cursor | `generated` reaches Draft only. The cursor is not a per-call result history. |
| Handoff commit | Exact committed revision | May return an existing revision; saved content alone proves neither a new commit nor a known no-change invocation. |
| Handoff Dream | `refresh_handoff` Run and Candidate | Approval publishes a revision; acceptance, Continue and actual task execution remain independent. |
| Direct imports or writes in any family | Existing saved revision/provenance | Do not infer generation or invocation history from content existence. |
| Tag Dream | `revise_tags`, tag Candidate and its decision result | Changes catalog metadata without creating an Artifact revision; a historical saved Tag set need not still be current. |

For every synchronous path, the asynchronous queued/running dimension is **not applicable**. A missing retained invocation result, missing exact match, failed read or unavailable capability is **unavailable**, not success, zero failures or proof that no operation occurred. This view has no exactly associated local invocation-history source: no local window, freshness or worker activity is fabricated. After restart, durable evidence remains readable while unavailable local history remains unavailable. Pending/running Run records are labeled recorded states and do not establish a currently observed worker or retry wait.

Each Dream result retains its own acceptance/start/completion timestamps. `succeeded/proposed` means a proposal, including a reused proposal, and does not mean publication. `no_change` is displayed only when recorded, with its retained reason and reuse flag; it can reference a previously rejected Candidate. `needs_evidence` is a completed execution with insufficient evidence, not an execution failure. Historical failures remain historical after later success; unrelated successes do not resolve them. Ordering by acceptance time does not imply completion order. Missing Candidate decision/publication timestamps remain unavailable; the drawer's read time is not a business-event timestamp. Unknown failure details are replaced with a safe category rather than raw provider error text.

Inspection uses existing authorized Dream list/get, Candidate get and exact Artifact revision reads, with the incoming credentials and Dream contract header. Each request reads at most one page of six Runs (or one explicitly selected Run), at most six Candidate decisions and six exact result bodies; it never follows a next cursor automatically. Pagination and operation selection preserve Scope, language and the opened revision. No match in a query window does not prove absence from all history. The shared unpublished migration `000` adds `(scope_id, operation, accepted_at, run_id)` for operation-filtered pages. The repository seeks with the composite `(accepted_at, run_id)` cursor instead of an OR predicate, including ties at the same timestamp. Existing managed databases need explicit migration; startup does not add the index. Controlled SQLite tests retain 12,000 Runs and bound read work for sparse operations, empty matches and deep cursors. This budget applies to the operation-only query used by this view; it is not a cost guarantee for arbitrary status filters. OceanBase query execution was not exercised, so native schema validation and SQL compatibility do not establish OceanBase performance acceptance.

Dependent Candidate and result reads run with a concurrency limit of three, while one Run's Candidate-then-result order is preserved. A failed dependent read marks only its own dimension, and the remaining Runs and already read facts stay visible. An eight-second inspection deadline bounds optional waiting; after the deadline or any partial failure the retained base records and details are shown, and the association summary is recomputed the same way for the card and the drawer. Identifiable structural errors in a Run, Candidate or result response are normalized to an invalid response rather than rendered. An approved Artifact Candidate must identify a valid published revision; an approved Tag Candidate must retain its Tag decision result. Missing results in either approved state are unavailable evidence, never proof of non-publication. Pending and rejected Candidates cannot carry published results. After an inspection deadline, each unfinished dependent dimension is also labeled unavailable while known references and base associations remain visible. Record-only Artifact authority does not disclose Scope operation history. Every later navigation/read rechecks current authority. This section does not generate, approve, retry, cancel, resume, probe readiness, reset budgets, inspect managed Skill files or write business/processing state; existing authorization/audit behavior remains at its owning boundary.

Controlled SQLite Dashboard tests are in `tests/test_dashboard_operation_outcomes.py`. They exercise full-page and HTMX reads, empty/missing evidence, durable states, Candidate decisions and edits, exact-version association, history, bounded pagination, Scope isolation, revocation, partial failure and passive reads. Fixtures seed existing records without production model calls; they do not establish real-provider, OceanBase or host acceptance. Validate the Topic Memory Source-window slice ([#2308](https://github.com/oceanbase/powercontext/issues/2308)) and Memory/Profile/Experience Source consumption ([#2309](https://github.com/oceanbase/powercontext/issues/2309)) alongside this explicit/Dream slice ([#2310](https://github.com/oceanbase/powercontext/issues/2310)) before declaring the cross-page parent ([#2130](https://github.com/oceanbase/powercontext/issues/2130)) complete.

### Review explains the proposal and the saved result

The inbox directory uses existing reasons and family labels; an opened candidate can use its saved content as its title. The built-in directory keeps its metadata-only reads rather than loading every proposal for a title. Details put readable content fields before raw data. Updates compare the candidate with its recorded target revision, which may no longer be current. An unavailable baseline is shown as an unavailable comparison, not as unchanged content or an empty base.

Evidence bodies and candidate history remain explicit, deferred reads. Full proposal data, raw diffs, package metadata and the complete versioned editor remain available in disclosures. Approval effects are separate from the candidate's reason and evidence. Decisions retain current permission checks, same-origin protection and candidate-version guards.

After a decision, Review opens the same candidate under its saved status and preserves the language, Scope and reporting period. Approved content links to the exact recorded result revision, even after a later head change. Approved tag changes show the tags saved by that decision and the recorded content basis; they do not create a content revision or establish that those tags remain current.

### Source material supports checking in context

Sources can be opened directly from a memory, experience, skill or handoff. They use Tabler's large modal, which fills the screen on smaller devices. A menu switches between sources. Closing the reader returns to the user's place in the record. The same actions are available from the keyboard.

Source text can be long. Size its reading area to the viewport, keep the heading and close control reachable, and let the text scroll within that area. Opening a source should not lengthen the whole page until the user loses their place. If one source fails to load, the open record remains readable.

## Scope, authority, and read results

Without an explicit Scope selection, the page uses the default Scope. Changing the default binding affects subsequent requests once the current request has selected its Scope. Viewing another Scope does not change the default. Default selection requires `server.observe`; reading its content still requires content authority. Observer authority alone does not grant content access.

Scope read authority permits browsing that Scope's collections and usage. An Artifact-only grant permits the selected record's exact revision and references; the page does not expose the Scope title, neighboring records, or Scope usage. Valid grants remain effective when an owner relation is missing, while owner-derived authority requires the actual relation. Retrying a page does not restore revoked authority; see [Artifact ownership](../rfcs/1396_handoff_access_control.md#artifact-ownership).

An exact link's body and references correspond to the requested Revision. The current revision and lifecycle state are shown separately; historical content must not be labeled as current. Content, state, and authorization in one response must agree. Subsequent requests evaluate the updated authority and content state.

Home's memories, handoffs, experiences, and skills remain independently readable. A missing or unreadable content type does not hide other readable sections. Unavailable usage preserves authorized Scope information and readable content. Read failures appear as errors rather than empty collections or zero usage. Content and Scope metadata are exposed only after their required authorization and audit succeed.

An empty browse or search result does not establish that an exact historical revision is absent. Existing exact references remain readable under current authority. Rebuilding browse or search data must preserve historical bodies, references, and revision identities. Page reads do not establish ownership or repair broken default bindings; recovery uses the corresponding maintenance operation.

The Scope selector supports paging and search. The selected Scope remains readable while other Scopes are unloaded. Review shows a candidate directory, opens the selected record, and exposes history and evidence bodies when the reader requests them.

Opening a record preserves its exact body and shows its paged revision history. Comparisons show the two requested revisions; an unavailable comparison does not replace the opened body. History, comparison, and rollback controls use current authority. A rollback creates a new revision with its source, reason, and actor; a concurrent head change requires the user to review the new current revision before retrying.

## Keeping layout and interaction consistent

### Content changes preserve page organization

Populated, empty and unmatched search states keep the same heading hierarchy, main sections and reading order. An empty memory collection does not turn into a different full-page view, and a period without records does not remove the usage regions.

A stable layout reserves useful reading space without requiring every state to have identical pixel dimensions. Allow for the excerpt length, page capacity and available viewport; longer text can grow naturally. Pagination should remain easy to find when the last page has fewer entries. Short pages should not push the start of the content downward.

### Choose columns and scrolling to suit the available space

Page containers, side margins and grid spacing follow Tabler's defaults. Use columns when both sides have enough width for reading. Stack them in order when space is limited, rather than reducing body text size to retain a split view.

On smaller screens, memories expand within the list. Opening another entry closes the previous one. Desktop screens use a directory beside the text. Search results follow the same reading pattern. Previous, current page and next controls remain visible, with unavailable directions disabled.

Lists usually scroll with the page, with the entries on each page directly visible. Local scrolling suits long text, source panels and wide tables where it helps preserve context. Add a scroll region when it helps the user keep their place, not merely to make cards look even.

### Copy and visual treatment establish hierarchy

Headings describe a page or section; buttons describe the action they perform. Preserve business content as written and use direct, specific interface copy. Explanatory text need not repeat what the navigation and content already make clear.

Prefer Tabler's default typography, font weights, buttons and form states. Use regular weight for memory, experience and skill entries so every item does not compete for attention. Cards at the same level use consistent borders and spacing. Arrows belong to actions with a directional meaning.

Chinese, English, light and dark settings apply across the Dashboard, including login and standalone source pages. Translations may wrap naturally, but should preserve navigation order, button groups and column proportions. Theme changes retain the information hierarchy and readability. Changing the interface language does not rewrite business content. Use existing brand assets for the full logo and only the graphic for the favicon.

## Enablement and access

Enable Dashboard with `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`. Local `ACCESS_MODE=disabled` opens directly without a token. With `ACCESS_MODE=enforced`, it requires `AUTH_TOKEN` and uses the built-in static Bearer identity, with the same permissions for every token holder. Team deployments that inject authentication or authorization Providers must disable it. Pages follow the Server access mode and current authority, and provide no member or role management. See [Install and run](../docs/get-started/install-and-run.md) for personal setup.

## Visible behavior acceptance

| Scenario | Acceptance focus |
| --- | --- |
| First use, only one content type, no search matches | Accurate states with recognizable layout and entry points |
| Default scopes, duplicate names, empty children and explicit references | Clear reading and statistical boundaries; switching does not change the default |
| Long lists, last pages, long sources and small viewports | Entries can be traversed, full text is readable, controls are reachable and reading position remains sensible |
| Changing language or theme, or returning through browser history | Scope, record identity and information hierarchy remain consistent |
| Unreported usage, incomparable data and partial read failures | Unknown values do not look like zero, and failures do not look like empty content |
| Credential expiry, network interruptions and service recovery | Errors are clear, and signing in or retrying restores reading |

## Appendix: Page APIs

The HTTP API specification defines request fields and limits.

### Pages and APIs

| Page capability | API | Boundary to preserve |
| --- | --- | --- |
| Scope selection | `GET /v1/scopes`, `GET /v1/scopes/default`, `GET /v1/scopes/{scope_id}` | Treat defaults, explicit selection, hierarchy and readable scopes separately |
| Memory collection and text | `POST /v1/atomic-memory/list`, `POST /v1/memory/search`, exact Atomic Memory revision and state reads | Preserve Atomic Memory identities, lifecycle filters, the selected search mode and hit evidence; return up to 50 matches |
| Handoff collection and text | `GET /v1/scopes/{scope_id}/artifacts/handoff` and exact revision reads | Retain cursors; list order does not imply chronological order |
| Experience collection and text | `GET /v1/scopes/{scope_id}/artifacts/experience`, `POST /v1/experience/get`, `POST /v1/scopes/{scope_id}/artifacts/experience/search` | Dashboard provides paged browsing and Family relevance search; search covers approved active revisions only, returns up to 200 matches, and suggests a narrower query at that limit |
| Skill collection and text | `POST /v1/skill/library`, `POST /v1/skill/get` | Library queries return up to 200 entries; suggest a narrower query at the limit and preserve source identities |
| Source material | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | Verify the source's relationship to the record; `content` is currently the source type with readable text |
| Usage | `POST /v1/stats` | Read the current scope with `exact` and use Server reporting periods and statistical definitions |

Home shows the selected Scope's memories, handoffs, experiences, skills, and usage summaries. Collections show readable entries and pagination without inferring an unreported total. Record and source references preserve their complete identities.

`/dashboard/runtime` requires the same Dashboard authentication as the other pages but does not require a Scope. It reads this instance's existing `/health/live` and `/health/ready` contracts. A readiness response with HTTP 503 is displayed as not ready; a failed probe read is displayed as unavailable. These checks do not establish health for every worker or for metrics, logs, and tracing pipelines. Access coverage is secondary detail, separate from dependency health.

### Manual Experience and Skill generation

Methods pages offer Generate experience and Generate Skill. Users choose exact evidence from the current Scope and submit the current page selection. Experience supports Sources, active Memory revisions, and approved Experiences. Skill supports Sources or approved Experiences with the matching provenance origin. Supplemental notes are candidate metadata.

The Dashboard posts to the existing generation API using the current credentials and requires a same-origin request. It never approves the candidate. Pending candidates open in the review inbox; a semantic no-op is reported explicitly. Calls have a bounded 120-second wait with no automatic retries. Direct Generate calls do not create Dream execution records.

### Source-consumption status

Memory, Profile, the Experience collection, and Experience detail pages show a passive **Processing status** section for the selected Scope. The section describes current Scope evidence, including when the reader opens a historical revision; it does not describe that revision's generation history. The Skill collection does not use this Source-consumption section. Explicit generation and Dream outcomes remain separate paths.

| Page / path | Processing family | Binding | Domain interpretation |
| --- | --- | --- | --- |
| Memory / Atomic Memory | `memory` | `memory-source-window` | The scheduling alias retains its cursor identity; the processor publishes `atomic-memory` Artifacts. Consumption can advance without publication, including a write-gate hold. |
| Profile Source window | `profile` | `profile-source-window` | The Scope Policy controls generation. A pending review candidate retains the cursor until the review decision; rejecting a candidate can consume its window without publication. |
| Experience incubation | `experience` | `experience-incubation` | The independent cursor tracks incubation. Creating a review candidate does not establish publication of an approved Experience. |

The evidence drawer maps each displayed fact to its owner:

| Displayed fact | Evidence source | Coverage and limits |
| --- | --- | --- |
| Consumed journal position / Source journal head | `pc_source_cursors`, keyed by Scope and binding / `pc_source_journal_heads`, keyed by Scope | An absent cursor or empty journal uses initial position `0`, following the processors. A failed read is unavailable rather than zero. A cursor ahead of the head is shown as unavailable input progress. Positions are not Artifact counts or completion percentages. |
| Domain dirty / clean generation; requested / acknowledged invocation generation | `pc_artifact_processing_intents`, keyed by Scope and binding | Show each counter independently. An absent intent remains unavailable. Dirty notifications are coarse domain notifications; acknowledging an invocation proves neither publication nor an explicit no-change result. |
| Profile policy and pending review | `pc_profile_policies`, keyed by Scope | Show whether Source-window generation is enabled, the activation policy, and whether a pending candidate pointer exists. An absent Policy does not enable generation. The section exposes no candidate body or identity. |
| Configured processing capability | Assembled Runtime declarations, registered processors, and injected generation pipelines | A declaration does not establish a live worker. API-only composition can declare externally executed capabilities. An injected Runtime without this declaration reports configuration unavailable. Disabling automatic scheduling does not itself disable explicit invocation or recovery of accepted requests. |
| Current execution, scheduling reason, running seconds, next-check seconds, retry failures | `observe_scope()` for the selected Scope, filtered to the exact processing family and binding | Accept only scheduler observations sampled within five seconds. Display their separate observation time and local origin. Missing, stale, failed, or external observations remain unavailable. Local admission blocking is not a durable recovery record. |
| Durable evidence observation time | Time captured by the passive inspector | The cursor, head, intent, Policy, and Scope authorization share a consistent read snapshot. The timestamp identifies inspection, not the historical time when a counter changed. Local scheduling is sampled separately. |
| Dedicated target / flush counters | Not applicable | Topic Memory pending watermarks and flush generations do not govern these three Source-consumption paths. They are not fabricated from intent generations. |
| Publication / explicit no-change outcome | Unavailable from these records | A saved Artifact list, cursor movement, acknowledged request, or candidate creation cannot establish the current invocation's publication or no-change outcome. |

The Server uses an internal `ScopeReadSnapshot.source_consumption()` projection with current Scope-read authorization and deferred audit completion. It reads a fixed set of metadata records for one Scope and binding; it does not scan Source bodies or Dream history. A separate authorized transaction isolates status failures from independently readable content. Only audited successful status projections are rendered. Readers with an exact Artifact share but no Scope-read permission receive no Scope status. Page refresh and Scope switching repeat authorization, retain no cross-request status cache, and do not change the default Scope binding.

`ProcessingStatusInspector.source_consumption()` and the composite `status()` share the cursor reader, family-to-binding mapping, and retained-intent projection. Their remaining responsibilities differ: only the Source-consumption projection reads Profile Policy, while the composite status also reads Topic blocking and Dream history. The Source-consumption projection accepts only fresh executor observations for the exact family and binding, and isolates observer failures from durable evidence. These executor guarantees do not describe the composite status contract.

External processing and unavailable built-in inspection are separate deployment properties. A supported API-only Runtime can read durable progress through the built-in snapshot while workers execute elsewhere; it reports local execution as unavailable without treating the capability as unconfigured. An injected Runtime without capability declarations reports configuration unavailable. When the Dashboard cannot use built-in snapshots, including with an access-control implementation that does not support them, existing public reads still serve saved content, and the section explicitly states that the deployment does not support reading Source-consumption status. A transient read failure uses the separate observations-unavailable message.

The status read has a five-second limit. It performs no model calls, readiness probes, processing activation, retry, budget reset, cursor/intent creation, or business/processing writes. Existing authorization and audit behavior stays at its owning boundary. The section does not call the full statistics overview or recovery status operations that can flush usage or materialize diagnostics. No storage schema or public HTTP contract is added.

Controlled SQLite Dashboard tests cover pending and caught-up inputs, a cursor ahead of the head, outstanding and acknowledged request generations, independent bindings, Profile review, configured/unconfigured/unknown capabilities, bilingual output, failed queries, authentication failure, timeout cancellation, public-read fallback, empty Experience collections and missing detail selections, fresh/stale observations, unavailable local execution, loss of local observation after restart, Scope switching, permission revocation, exact-record shares, and repeated passive refresh. An optional OceanBase test, enabled with `POWERCONTEXT_TEST_OCEANBASE_URL` pointing to a dedicated test database, checks durable progress through an API-only Runtime. The controlled fixtures and API-only test do not establish live provider calls or actual remote-worker execution; the optional backend must be run before claiming backend acceptance.
