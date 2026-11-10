---
title: "RFC 2528: DSH plugin UI, embedded dashboard, and cross-agent handoff"
description: A plugin-owned design for DSH connection status, existing dashboard embedding, and exact handoff to another agent.
---

- Proposal Name: `dsh_plugin_ui_and_agent_handoff`
- Start Date: 2026-11-06
- Status: Draft
- RFC PR: [oceanbase/powercontext#2528](https://github.com/oceanbase/powercontext/pull/2528)
- Tracking Issue: [oceanbase/powercontext#2514](https://github.com/oceanbase/powercontext/issues/2514)
- Related RFCs: [RFC 0048](0048_handoff_artifact.md), [RFC 1223](1223_human_agent_work_continuity.md), [RFC 1345](1345_scope_organization_and_agent_integration.md), [RFC 1783](1783-handoff-session-rollover.md), and [RFC 1629](1629_dashboard_profile_and_handoff_markdown_export.md)

# Summary

Extend the PowerContext DSH plugin with a connection-status entry, the existing dashboard embedded in DSH, and a latest-response action that hands work to another agent. Installation continues through `powercontext setup`. The plugin uses existing DSH Client extension points and a plugin-owned Host HTTP bridge. On a qualified local deployment, its Node Host code opens a destination through an allowlisted system opener and the destination's supported deep link.

The transfer uses PowerContext’s existing Handoff operations to prepare and preserve a complete Handoff, opens a new session in the intended directory, and prefills a short continuation prompt. The user sends that prompt. The destination verifies live conditions and acknowledges the exact Handoff. Opening an application is not delivery confirmation. Initial delivery includes three required destinations: Codex Desktop, Claude Desktop Code sessions, and Claude Code CLI. Only the owner’s local DSH deployment is supported.

The design requires no changes to DSH source, Electron IPC, external-link policy, or built-in application catalog. It introduces no PowerContext Task/Transfer domain, automatic milestone commit, or managed agent runtime. This is a design proposal, not evidence that these UI or launch capabilities have shipped.

# Motivation

Users currently have to connect separate actions: inspect plugin diagnostics, open the dashboard, summarize work, preserve the right Handoff, select a destination directory, and explain how to continue. This creates opportunities to lose unfinished work, choose the wrong Scope, or mistake a launched application for a completed transfer.

A useful integration should answer three questions in place: is PowerContext connected, what is happening for this project, and how can another agent continue this particular task? It must distinguish host capability from model choice, temporary transfer from a committed milestone, and receiver acknowledgement from eventual task completion.

DSH changes are outside the delivery boundary. All implementation PRs belong to PowerContext. A feature that requires an unavailable DSH extension must remain unavailable on that host version rather than require a patched DSH distribution.

# Guide-level explanation

## Connection and dashboard

A small PowerContext icon appears above the user area in the lower-left sidebar. Color is accompanied by text or an accessible label:

| State | Meaning |
| --- | --- |
| Gray | Not configured or disabled |
| Yellow | Connecting or retrying |
| Green | A successful response under the configured access mode verified connectivity and required readiness |
| Red | Connection, authentication, or a required capability failed |

An expired observation cannot remain silently green. Scope resolution and recent recall, Source acceptance, Memory production, and context injection are shown separately. Plugin installation is not Server startup, and Source acceptance is not Memory production.

Clicking the entry opens the existing PowerContext dashboard in the right panel. Prefer direct URL embedding, with a compatibility/authentication adapter only where needed. A local Server with Dashboard enabled and access mode disabled does not require login; an enforced-access Server retains its token requirement. Changing conversations updates the selected Scope. Without a conversation, use an existing overlay or external entry, never create an empty conversation. Viewing runtime details is the initial acceptance scope; embedding does not grant new permissions or remove existing confirmations. A separate reduced-privilege dashboard is not a prerequisite.

## Continue with another agent

The initial workflow is a deliberate choice to continue current work in another agent. It does not add automatic saving, per-response Handoff generation, or automatic checkpoints. Recovery applies to deliveries already persisted after an explicit request; there is no guarantee of a ready-to-use Handoff if the source session ends before that request or before its payload is saved. Existing capture behavior remains separate and is not proof that a complete recoverable Handoff exists. Automatic saving is outside this RFC's design and acceptance scope.

Only the latest eligible completed assistant response in the entire conversation receives the action. The action opens an agent submenu with names, bundled icons, availability, and actionable limitations. Hover, click, and keyboard interaction are supported.

The normal path is:

```text
Choose target -> check the application -> check its PowerContext integration
              -> if missing: ask to install; No cancels, Yes installs and verifies
              -> verify directory / Server / Scope
              -> use PowerContext Handoff -> persist the returned payload
              -> open new session and prefill -> user sends
              -> PowerContext Continue and acknowledgement
```

If the application is absent, stop and show “Install [agent] before handing off”; do not install the agent automatically or prepare a Handoff. If its PowerContext integration is absent, ask “Install PowerContext in [agent] and continue?” Refusal cancels; acceptance installs only that integration and continues only after verification. A broken or disabled installation is distinguished from a missing one.

The plugin preserves the source conversation and existing destination drafts. The destination may require first-use directory confirmation. It uses its own model settings and credentials; PowerContext does not select or borrow a subscription or model account.

For an existing local directory that the destination has never opened, use the supported new-session entry point. Do not create private application project records. If the directory is missing, ask the user to choose a valid directory; clone/create operations are outside the automatic transfer path.

Targets without qualified prefill support offer copy/export or configuration guidance. Frameworks are developer integrations rather than applications to launch. A transfer package can be inspected before launch. Unconfirmed and pinned packages are not automatically deleted.

DSH shows a waiting animation and the actual stage: checking, installing PowerContext, preparing Handoff, opening the target, or waiting for the receiver. When user action is required, explain it, for example “Send the prefilled prompt in Claude Code CLI.” Do not show invented percentages or imply that an application is already working merely because it opened.

## Example

A user finishes a DSH response about an unfinished test failure and selects Codex. The plugin checks that local launch, Codex, and Codex’s PowerContext integration are available; if the integration is missing it obtains installation consent and verifies installation first. It then fixes the source message boundary and calls the existing PowerContext Handoff workflow with the current-work inputs. It stores the full Prepared Handoff, checks the destination directory and Scope, and opens a new Codex session with a short prompt referring to the package.

The user sends the prompt. Codex reads the exact package, verifies the current repository and authorization, calls Continue, and records an acknowledgement with the allocated receipt source ID. DSH displays that acknowledgement only after reading and validating it. If Codex merely opens, the display remains “Awaiting receiver confirmation.”

# Reference-level explanation

## 1. Baseline and ownership

The inspected source baseline is PowerContext `961b1aa2574f410a6412aff4cecd56d7a5a07288` and DSH `ce7073d51254c26cd63b22a60835b8f948deb447` (`0.2.2-alpha.2`). The PowerContext DSH package is `0.0.2`. These are source-checkout facts, not a supported-release declaration.

The qualification baseline is the latest officially published DSH release, not the inspected checkout. As checked on 2026-11-06, it is [v0.2.2-rc.1](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.2-rc.1), published on 2026-11-04 and marked as a prerelease/release candidate. At implementation start and release qualification, check the official release list again and pin the actual version/package. Older-version compatibility is not an initial requirement. Do not silently upgrade DSH or advertise a moving “latest” range as tested.

Qualification results apply only to the recorded release/package and environment. If the pin changes, rerun the affected acceptance cases against the new installed package before declaring it supported; retain earlier results as historical evidence. In particular, passing on a release candidate does not qualify a later stable release. A destination, Server, plugin, OS, or access-mode change also requires requalification of the affected paths.

Relevant DSH mechanisms already exist in that checkout:

| Mechanism | Source location |
| --- | --- |
| Sidebar footer actions | `packages/client/ui-sidebar/src/client/SidebarRoot.tsx` |
| Assistant response actions | `packages/client/ui-chat/src/client/chat/TurnTailNodeView.tsx` |
| Right sidebar tabs | `packages/client/ui-sidebar-browser/src/client/index.ts` |
| External Client discovery through `dsh.client` and `./client` | `packages/client/modules/src/index.ts` |
| Authenticated Host routes | `packages/host/open-in-app/src/index.ts` |
| Host process launch through Node | `packages/host/open-in-app/src/resolver.ts`, `apps/desktop/src/host-process.ts` |
| Application-origin forwarding to Host | `apps/desktop/src/main.ts`, `apps/desktop/src/web-document.ts` |
| Scoped agent creation and tool policies | `packages/core/agent/src/index.ts`, `packages/core/tools/src/index.ts` |

DSH's Desktop shell forwards non-shell application paths to its Host with the shell-owned authentication cookie, but strips `Set-Cookie` from returned responses. The existing Browser plugin uses an isolated Desktop webview; this proposal does not assume that it shares the main application's identity.

PowerContext owns the Client bundle, Host bridge, target adapters, delivery records, and dashboard embedding support. Handoff validation and authorization stay in the owning PowerContext services. DSH and destination applications remain unmodified dependencies.

## 2. Packaging and setup

Extend `integrations/dsh/plugins/powercontext/` with a distinct browser entry, exported as `./client`, the required DSH Client declaration, and packaged UI assets. Keep the existing `src/client.ts` Server HTTP client separate. Update the build, release archive, and plugin configuration together; development-only module resolution is insufficient.

UI services are optional for headless hosts. Existing tools and lifecycle behavior continue to work without the Client. Uninstall disposes routes, views, subscriptions, and components. `powercontext setup` continues to select the correct Web/Desktop profile and Desktop's bundled runtime. It reports installation and capability results separately from Server connectivity; it does not silently start a Server.

Do not advertise a broad DSH version range until installation and UI routing have been tested against actual release packages. Missing Client or route support disables the new UI for that version without breaking existing tools.

## 3. Host bridge and authority

Use plugin-owned HTTP routes under `/powercontext/`, not new DSH central RPC descriptors. These are internal plugin interfaces, not new PowerContext business APIs:

| Method and proposed route | Purpose and effect |
| --- | --- |
| `GET /powercontext/status` | Read connection and current Scope observations |
| `GET /powercontext/targets` | Read capabilities; never install or launch |
| `POST /powercontext/preflights` | Detect the selected application, its integration, and configuration; return a scoped preflight ID |
| `POST /powercontext/preflights/{id}/install` | After explicit consent, install the selected target’s PowerContext integration |
| `GET /powercontext/preflights/{id}` | Read detection/install/verification status and required user action |
| `POST /powercontext/preflights/{id}/cancel` | Cancel preflight/pending installation continuation; preserve completed configuration changes |
| `POST /powercontext/deliveries` | Require a ready, revalidated preflight and prepare a PowerContext Handoff |
| `GET /powercontext/deliveries/{id}` | Read authorized delivery state and preview |
| `GET /powercontext/deliveries/{id}/export` | Download an existing package; no prepare or commit |
| `POST /powercontext/deliveries/{id}/launch` | Explicitly attempt the selected allowlisted launch |
| `POST /powercontext/deliveries/{id}/cancel` | Cancel pending work; cannot undo completed installation or launch |
| `GET /powercontext/deliveries/{id}/receipt` | Read and validate the allocated PowerContext receipt |

Every bridge request passes DSH’s existing connection/origin checks and operation-specific validation. Preflight/delivery IDs are selectors, not credentials. Bind them to the local owner, source session, target, and configured connection. Reject arbitrary programs, shell commands, alternate credentials, and arbitrary Server URLs supplied by the browser. Installation and launch are explicit POST operations, never GET side effects or model-generated links.

Only the owner’s local Desktop or explicitly configured local Web Host is supported. Remote/shared DSH is outside this RFC; do not build multi-user authority mapping as a prerequisite. Local-only does not mean anonymous websites can invoke the bridge: retain DSH authentication/origin checks even when the PC Server itself is configured without a token. Never change the Server’s access mode to make embedding work.

The dashboard is ordinary existing web content with its existing permissions; it receives no native launcher or installer capability. Prefer a separate-origin iframe/isolated existing browser surface where supported. Sanitize Source/model content and retain browser isolation. If a fallback proxy is needed, restrict it to the configured PC origin and intended dashboard routes, keep credentials server-side, and explicitly qualify the resulting frame/bridge boundary. Do not expose a generic URL proxy or disable web security.

## 4. Connection status and embedded dashboard

Reuse connection/readiness and runtime observations; do not poll `/pc doctor`, send diagnostic prompts, or run generation to display status. Track timestamps, connection generation, Scope, and stable errors. Connection changes invalidate old observations.

The existing Dashboard is optional and disabled by default. Its current local modes, documented in [Install and run](../docs/get-started/install-and-run.md), are:

| Server configuration | Existing Dashboard behavior |
| --- | --- |
| Dashboard disabled | No usable dashboard page; show enable/configuration guidance |
| Dashboard enabled, `POWERCONTEXT_SERVER_ACCESS_MODE=disabled`, bound to loopback | Direct viewing without password or token |
| Dashboard enabled, `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` | Uses the existing static Server token/login |
| Dashboard enabled with an injected custom authentication provider or access-control implementation | Server application creation is rejected, including on a personal local deployment; unsupported by this Dashboard integration |

`create_server_app` rejects the last combination with `ValueError("Dashboard supports only the built-in static Bearer profile")`. This is a Server configuration limitation reached before DSH embedding, not evidence of a plugin defect. Personal-local deployment does not imply built-in authentication. The supported acceptance paths remain loopback access with access mode disabled and the built-in static token profile. Do not disable authentication, discard a custom provider, or change Server configuration to make an embedding test pass. Confirm the startup cause from configuration and Server diagnostics; an unreachable Server alone does not identify this particular error.

Opening a page and starting the Server are separate actions. The plugin does not silently restart the Server, enable Dashboard, or weaken authentication. A healthy PC API with Dashboard disabled must not be reported as a broken PC connection.

Load the existing `/dashboard/...` URL in a DSH right-panel browser surface first. Web uses a suitable iframe; Desktop first evaluates its existing isolated Browser webview or a plugin frame supported by the release. Use the existing Scope query/navigation behavior. No new dashboard implementation or mandatory reverse proxy is required for a functioning direct embed.

For token-enabled configurations, try the existing dashboard login in the embedded surface. Host-side Bearer configuration does not automatically log an iframe in; cookie isolation, SameSite, origin, protocol, CSP, and HTTPS-to-HTTP embedding can require adaptation. The `dsh-app://app` forwarding path strips `Set-Cookie`, but that does not mean an independently loaded browser surface cannot log in. Test the actual container instead of assuming direct embedding fails.

If direct loading or login fails, add only the necessary compatibility/authentication adapter. A fixed-upstream credential bridge and explicit embedded base prefix are fallbacks; credentials never appear in a URL, page script, or logs. Do not disable authentication, rewrite arbitrary HTML, or relax global CSP/web security. An external-browser option remains available, but does not pass in-panel acceptance.

Viewing existing runtime, usage, Memory, Source and Handoff pages is the required scope. Preserve existing page permissions and mutation confirmations; no new write capability is granted. Strictly disabling all existing write controls would be a separate product/access requirement, not something iframe embedding automatically provides. Validate loading, assets, navigation, Scope changes, locale/theme and narrow layouts in both passwordless and token-enabled modes. Dashboard settings change only through an explicit configuration action.

## 5. Use the existing PowerContext Handoff workflow

PowerContext owns Handoff content, evidence validation, preparation, continuation and acknowledgement. The DSH plugin supplies the current task inputs and orchestrates target opening. It does not invent another Handoff schema, independent summarization product, or parallel transfer domain.

Existing operations serve different inputs:

| Available input | Existing PowerContext path |
| --- | --- |
| Current-work fields supplied through the source Agent’s existing Handoff guidance or an inspected editable snapshot | `handoff_current_work` captures the boundary and returns a complete Prepared Handoff |
| Bounded current-task evidence with a configured PowerContext Handoff generation pipeline | Capture missing material through the existing Source API, then `prepare_handoff` → inspect/validate the draft → `finalize_handoff` |
| A validated Prepared Handoff already selected for this same source boundary | Reuse that exact value without generating it again |
| User explicitly requests a durable milestone | `commit_handoff` and preserve the returned exact Revision |
| Destination receives the payload | `continue_handoff`, live checks, then `acknowledge_handoff` |

`prepare_handoff` already has a generation pipeline. `handoff_current_work` accepts current-work fields and deterministically prepares the carrier; it does not itself infer those fields from arbitrary DSH history. The Server cannot automatically see uncaptured DSH replies, tool results, or the current workspace. Supplying those inputs is host integration work, not reimplementing Handoff.

Choose a supported path during preflight and record it. Reuse the existing source-Agent Handoff guidance when current-work input needs host assistance; a second independent summarizer service/session is not mandatory. A configured PC generation path can do the drafting itself, so do not run both model paths for one transfer. If neither automatic path is ready, provide an editable current-work snapshot and call `handoff_current_work`, or explain the missing generation configuration. Do not silently configure a new model provider.

Detection and any consented installation finish before model calls or task-content Source writes. Record the selected source session/message at the click. If installation takes long enough for the source to change, ask whether to use the originally selected boundary or select the new response; do not silently switch tasks. Once accepted, fix `sessionId`, `messageId`, `throughSeq`, effective directory and `workspaceObservedAt`.

Read bounded material retaining the active objective, user constraints, selected completed response and relevant observations: up to 512 events / 256 KiB, including at most 32 KiB of workspace observations, subject to lower model/token budgets. Explicitly record omissions. Do not reinterpret an uncaptured full transcript as available evidence.

Use existing Handoff fields and evidence references to preserve the following when available; these are input requirements, not a new carrier schema or a requirement to rerun tests during handoff:

| Evidence | Required meaning |
| --- | --- |
| Tests and checks | The command/check, observed outcome, exit status when available, observation time, and exact available evidence reference; distinguish passed, failed, interrupted, and not run |
| Open failures | The unresolved error, attempted investigation or fix, remaining blocker, and next check; distinguish observed failures from unverified hypotheses |
| Workspace state | The revision and relevant observed working-tree state to which the result applies, including uncommitted changes when observed; do not imply that a result covers later edits |
| Evidence limits | Missing, uncaptured, truncated, or stale material and the effect of the input budget; unavailable details stay explicitly unknown |

A summary cannot turn an unexecuted test into a pass. If the workspace changed after a check, identify the result as evidence for the earlier observed state and require the receiver to reassess it. Preserve available references rather than silently dropping a known failure to fit the snapshot. Keep these details in authorized Handoff content, not operational logs.

Any source-Agent assistance is a logged preparation action under existing host guidance. Permit only the necessary Handoff calls and fixed read-only observations; prohibit unrelated project-changing tools. DSH global tool restrictions do not alone cover scoped registrations or every executor, so verify the actual execution boundary. If safe automatic assistance is unavailable, use the editable path. PC generation does not gain DSH filesystem tools. No broad autonomous task run is introduced.

Validate the result through the existing PC models/services. Unsupported claims remain `basis: declared`; exact evidence is required for `verified`. Preserve the returned Prepared value unchanged. One explicit request makes at most one automatic drafting attempt, with a 90-second generation deadline; install/login/user-action waits use separate states and budgets. Failures do not trigger a second model route silently. Corrections after preparation create a new delivery version. Default transfer does not commit a milestone.

## 6. Delivery records and payload

A delivery is plugin transport bookkeeping, not a new PowerContext domain entity. Persist a versioned envelope in a Host-owned user-data directory. Proposed envelope version: `powercontext.dsh-delivery.v1`.

Store one record per delivery ID, associated with its Server/Scope, source conversation, and exact response/event boundary. A Scope groups related context; it is not a single package slot, and a conversation can have multiple deliveries at different boundaries. Two conversations sharing a Scope must keep separate payloads, launch attempts, and receipts. For example, handing off a failing test from conversation A must not overwrite or acknowledge an API-design delivery from conversation B. A “latest” UI shortcut may select a record, but launch, recovery, and acknowledgement must resolve the exact saved delivery and payload. Repeated transport requests for the same active operation reuse its record under the existing deduplication rules; they do not create a new package merely because the request was retried.

| Field group | Required meaning |
| --- | --- |
| Identity | Delivery ID, owning DSH identity/session, creation time, schema version |
| Source boundary | Message ID, inclusive event cutoff, workspace observation time, original and effective directory |
| Connection | Non-secret Server identity/configuration reference, connection generation, Scope, validated target preflight/install result |
| Payload | Complete Prepared Handoff plus available exact Source receipts, or full exact Artifact Revision reference; never `latest` |
| Target | Catalog ID, qualified adapter/version, deployment facts, chosen directory, optional verified receiver identity |
| Attempts | Preparation result, launch attempt IDs/states, cancellation and unknown-outcome markers |
| Receipt | Preallocated ACK source ID per receiving attempt; observed exact SourceRef and validated attribution |
| Files | Package location, file-integrity digest, expiry/pin state; no embedded credentials |

For `handoff_current_work`, preserve the complete `PreparedWorkHandoff.handoff` and returned boundary receipt; for `finalize_handoff`, preserve its returned Prepared value and the actual input Source references without inventing a boundary receipt. Keep required nulls, `base`, and `generation`. `base` is the commit concurrency precondition, not the new handoff's Revision. The boundary Source is not a replacement for the complete Prepared value. Use canonical contract serialization; file checksum and Prepared content digest serve different purposes.

Before any model or Server request, persist the delivery identity and snapshot metadata. After prepare succeeds, atomically persist the full validated result before launch is permitted. Use per-delivery locking and request deduplication. A crash after the Server prepared a value but before local persistence leaves an unknown preparation result; rereading input/boundary Sources or re-finalizing against today's head cannot recover that original value. Offer explicit regeneration as a new version if no exact recovery path exists.

Default transfer does not commit a milestone. An optional explicit “Save milestone and hand off” action uses existing commit/CAS behavior and stores the returned exact reference. It is not a hidden prerequisite for local recovery or transfer.

Create owner-only directories/files, use atomic writes, reject path traversal and unsafe symlink/overwrite behavior, and never place a package in a repository by default. A Host-readable path is not proof that the destination sandbox can read it. Use a qualified shared location, an explicit export location, or an exact committed reference the receiver can access. Without a proven path, offer download/import rather than claim automatic delivery.

Unconfirmed and pinned payloads are not automatically deleted, and their recovery metadata stays with them. Confirmed, unpinned payloads are eligible for cleanup after seven days; ended-delivery metadata is retained for 30 days. Show retention and allow pin/export/explicit deletion. Cleanup never deletes Server records. Use configurable initial limits of 100 payload records or 50 MiB; at capacity, ask for cleanup or a deliberate limit increase rather than silently evicting pending/pinned work.

Unsupported future envelope versions are rejected without rewriting them. Uninstall stops activity and retains saved deliveries until explicit deletion or the disclosed cleanup policy; it does not silently erase pending work.

## 7. Directory, Scope, and local launch

Directory, application project entry, application session, and PowerContext Scope are distinct. Resolve DSH's effective working directory, not only its initial session header. Validate canonical path identity, existence, access, worktree/subdirectory behavior, and current state again immediately before launch.

The same directory can resolve differently in DSH, Codex, and Claude because their integration keys and session/workspace binding precedence differ. Preflight must establish the destination Server and effective Scope route before its startup hook injects context. A prompt cannot repair a hook that already loaded the wrong Scope. Conflicts enter an explicit binding/configuration flow; do not rewrite global bindings or the carrier's Scope silently. If the future session route cannot be established, downgrade to assisted transfer rather than claim automatic scope continuity.

| Deployment | Initial launch policy |
| --- | --- |
| Owner’s locally deployed DSH Desktop Host | Permit the three qualified initial adapters |
| Explicitly configured local DSH Web Host | Permit qualified local adapters; display the launch machine |
| Remote, SSH, shared, container, or unknown Host | Unsupported deployment for this feature; do not run target detection/install/launch there |
| Unqualified platform/application version | Preserve preparation and manual transfer; disable one-click prefill |

Loopback URLs, client claims, and absence of `SSH_*` are not proof of locality. Default native launch to disabled unless trusted deployment facts or explicit local-owner configuration establish the supported mode. Only the owner’s local deployment is in scope; no shared-user or remote-DSH launch mode is added. No new companion daemon is introduced.

The Host accepts only a stored delivery ID and target selection, revalidates them, and generates the official URI itself. On macOS, a fixed `/usr/bin/open` and structured argv can open allowlisted Codex/Claude URI schemes. Do not accept shell fragments, arbitrary programs, user-supplied URI schemes, or credential-bearing links. Scrub the child environment and bound early-error observation. Do not import DSH's private resolver or patch its fixed app catalog.

Use a short prompt referring to the exact package/reference and ACK source ID. URL-encode fields and enforce adapter-specific limits. Never truncate a URI silently or place the full transcript, carrier, or long-lived token in it. Existing sessions and unsent drafts are not reused by default.

### Target detection and consented installation

Treat Codex Desktop, Claude Desktop and Claude Code CLI as distinct targets. Selecting one begins this ordered gate:

1. Detect that exact application/runtime using supported package/application metadata and bounded version probes. An executable missing from `PATH` is not sufficient proof that a desktop application is absent; distinguish missing, unsupported and detection-unknown.
2. If absent, stop and show an agent-not-installed dialog. Offer installation guidance, but do not install the agent, capture task content, call a model, or create a Handoff.
3. If present, inspect the PowerContext integration in the target’s effective environment. Verify installation, enabled state, required Handoff/Continue/ACK tools, Server access mode and connection, and Scope routing. Files existing on disk are not enough.
4. If PowerContext is missing, show “Install PowerContext in [target] and continue?” with Yes/No. No closes this handoff as cancelled. Yes authorizes this integration installation/configuration only; show stage progress, then rerun verification before preparing the Handoff.
5. If installed but disabled, broken or misconfigured, show the specific repair/configuration action with explicit consent for changes. Do not blindly reinstall or silently replace settings.
6. Continue only after the selected target is ready. If a restart/login/manual step is required, show it and resume after verification. Failed, cancelled or unknown installation never counts as ready.

Reuse PowerContext’s existing setup/installer and doctor capabilities. The current Codex installer and `ClaudeInstaller` in `src/powercontext/cli/system.py` are implementation starting points, not proof of Desktop/CLI equivalence. `ClaudeInstaller` currently requires the `claude` executable; verify a supported installation path for Claude Desktop-only environments instead of assuming the CLI is present or silently installing it. Likewise detect a Codex app that uses a bundled runtime rather than equating application presence with `codex` on `PATH`.

Reuse existing supported connection configuration and credential storage; never pass secrets through URL parameters or log output. Source and package identity are pinned/trusted, not supplied as arbitrary commands by the browser. Serialize/deduplicate installation per actual target configuration, preserve unrelated settings, and never discard an unknown partial result. Cancellation stops the handoff and further work; it does not automatically uninstall a successfully installed integration or undo unrelated configuration. Restarting a running target must not lose unsent work.

Both Claude surfaces may share some files, but each must be checked in its actual runtime. A successful CLI installation is not proof that the Desktop Code session loaded the plugin. If the latest release lacks a supported path, show guided setup and keep that target’s acceptance open; a copy-only fallback does not complete the required three-target delivery.

## 8. Target catalog

The catalog distinguishes `kind`, PC tool support, launch mode, prefill mode, supported platform/version, installation state, required configuration, and last qualification evidence. Runtime probing does not execute a model prompt. Reuse approved local brand assets or a neutral fallback; do not fetch README images at runtime.

The baseline README lists 11 hosts, Bub, and three frameworks:

| Target | Initial treatment |
| --- | --- |
| Codex | Required initial target: Codex Desktop; detect its actual runtime/integration and qualify directory/new-session prefill |
| Claude Code | Two required initial targets: Claude Desktop Code session and Claude Code CLI; separately detect, install/verify PC, and qualify unsent prefill |
| DeepSeek Harness | Menu entry with manual guidance; in-host new-session continuation may follow separately |
| MiniMax Code | Configuration/copy/export; CLI prompt execution is not prefill |
| ZCode | Configuration/copy/export until a supported external entry is qualified |
| Kimi Code | Configuration/copy/export; version-specific CLI execution remains separate |
| Hermes Agent | Configuration/copy/export; do not present initial-query execution as a draft |
| Pi Coding Agent | Copy/export; extension-based editor prefill is a follow-up |
| OpenClaw | Copy/export; respect its agent/workspace/session model in later launch work |
| OpenCode | Copy/export; later bind a dedicated TUI/session before appending a prompt |
| WorkBuddy | Copy/export; platform approval, signature, directory behavior, and draft preservation need separate work |
| Bub | Evaluation entry; no claim of production handoff qualification |
| Pydantic AI | Developer integration; requires an explicit runner |
| LangChain | Developer integration; requires an explicit runner |
| LangGraph | Developer integration; requires an explicit runner |

The README still has 15 integration families; Claude exposes two destination choices, not an additional integration family. Distinct target IDs are `codex-desktop`, `claude-desktop`, and `claude-code-cli`. The catalog follows README membership while qualification remains version-specific. Actual installed tools must be checked separately from manifests or generated matrices. An unsupported target remains visible with a truthful action, not a disabled item with no explanation.

## 9. State, retries, and acknowledgement

In user terms, the receiving Agent uses PowerContext to report “I have read this handoff and can continue.” That is the acknowledgement. Detecting the application and installing PowerContext are prerequisites, not proof that it read the task.

Persist preflight/install, preparation, launch attempts, and receiver state separately; one flat success flag loses important facts.

| Dimension | States and transitions |
| --- | --- |
| Preflight/install | `checking_agent -> checking_integration -> ready`, or `agent_missing`, `needs_install_consent`, `installing`, `verifying`, `needs_user_action`, `failed`, `cancelled`, `unknown` |
| Preparation | `preparing -> prepared`; or `needs_input`, `failed`, `cancelled`, `unknown` |
| Each launch attempt | `requested -> opened`, `failed`, or `unknown` |
| Receiver | `awaiting -> accepted`, `needs_clarification`, or `declined`; separate `read_unavailable` diagnostic |
| Retention | Available, pinned, expired, or explicitly deleted |

`opened` means the OS accepted the launch request, not that a draft was visible or sent. `awaiting` does not establish whether the user has sent. An acknowledgement may arrive after a launch result became unknown and can still be validated. Cancel stops preparation/future activity but cannot recall a launched application or completed Server write.

Concurrent duplicate requests reuse the same delivery and active attempt. A crash between OS acceptance and local result persistence makes exactly-once launch impossible: retain `unknown` and require explicit retry. A new launch attempt does not regenerate or commit the payload. Semantically changed receiver acknowledgement uses a new receiving-attempt source ID while preserving history.

The receiving instructions identify the expected ACK source ID. The receiver reads the complete payload, verifies Server/Scope, calls `continue_handoff`, checks evidence, live state, capabilities and current authority, then calls `acknowledge_handoff`. Its `receiver` is the authenticated Principal where required, not the catalog's agent name.

Read the receipt through existing `get_source` at its exact Scope/source type/source ID. Validate the typed receipt, `prepared_digest` or exact Revision, acceptance checks, and committed receipt identity when enforced access is used. Source type must follow the existing capture/read contract; `metadata.kind="handoff-receipt"` is not automatically the source type. Match the allocated attempt, not an arbitrary ACK in the Scope. Hashing follows the existing Python model serialization contract, including nulls and field order, with cross-language fixtures; arbitrary JSON key sorting is not equivalent.

When the receiving Principal is known, require that identity. Otherwise display the actual verified identity; do not imply the receipt proves which application window received the work. An ordinary Source that resembles a receipt is insufficient. In a deployment without authenticated receipt attribution, label the result as a receiver-reported acknowledgement. If the receiver used another source ID, remain unconfirmed until an exact SourceRef is supplied and fully checked.

Missing, unreadable, permission-denied, and temporarily unavailable receipts are distinct. Poll only an active view with bounded backoff; keep a manual refresh action. Do not use the current full-journal WorkContinuity projection for UI polling. Validate point-read cost against a large unrelated history; add a focused storage index only if necessary.

### Waiting animation and recovery UX

Display an inline progress panel at the originating response and a recoverable delivery detail entry. Use a spinner/pulse and stage text during detection, installation, preparation and opening. While waiting for the user/receiver, use a subtle waiting animation plus explicit instructions; respect reduced-motion preferences with an equivalent static status. Do not show fake percentages, block normal DSH conversations, or imply background work when a user decision is required.

Waiting is a delivery state: the sidebar connection indicator continues to reflect actual connectivity and must not turn red merely because receipt is pending.

Disable duplicate actions for the same active attempt. Provide Cancel, View Handoff, and appropriate Retry/Refresh controls. User-action waits show “Send the prefilled prompt in [target]” or the required restart/login step. After five minutes without an ACK, stop continuous polling/animation and show “Not yet confirmed” with manual refresh; do not delete the payload or report task failure. Closing the panel does not cancel a transfer; reopening restores its real stage. Cancellation cannot undo a completed OS launch or suppress an already written ACK; show later exact confirmation if received.

A temporary Prepared Handoff may be acknowledged, but current Outcome receipt association requires an accepted exact committed Handoff. This RFC does not broaden that contract or automatically commit to obtain it.

## 10. Proposed budgets and diagnostics

| Operation | Draft default |
| --- | --- |
| Status refresh while visible | Every 10 seconds; bounded retry, pause when hidden, explicit stale state |
| Readiness | Reuse existing transport semantics, including cold-start allowance |
| Control request body | 64 KiB; clearly reject oversized editable input |
| Model snapshot | At most 512 events / 256 KiB total, with workspace observations capped at 32 KiB |
| Handoff drafting/model step | One attempt, 90-second deadline; uses one existing PC/source-Agent path |
| Installation / external user action | Separate visible stages and target-specific timeout; do not apply the model’s 90-second deadline |
| Export package | 2 MiB limit; reject without silently dropping fields |
| Receipt polling | Backoff from 2 to 30 seconds; pause after five minutes or when hidden |
| Optional authenticated embed bridge | 15-minute view lease if needed; reauthorize, no lease required for direct URL loading |
| Delivery retention/storage | Confirmed unpinned payload: seven days; ended metadata: 30 days; pending/pinned retained; 100 records / 50 MiB configurable limit |

These are initial configurable host budgets, not changes to Core schema limits or measured performance guarantees. A stricter owning contract wins. Capacity failures preserve existing records. Unknown model usage is unknown, not zero; record measured preparation latency and model usage before claiming cost improvements.

Operational diagnostics follow the [observability guide](../development/observability.md): stable codes and safe counts/durations, no prompts, responses, full URIs, credentials, or sensitive paths in logs. User-authorized local previews may show the necessary content, but are distinct from operational telemetry. Logging failure must not change business results or suppress cancellation.

## 11. Implementation and acceptance

| PR | Scope | Dependencies | Required evidence |
| --- | --- | --- | --- |
| 1 | Client export/assets, Host bridge, setup checks | RFC | Actual tarball installs on the current latest official DSH Web/Desktop release, with pinned release identity; owner-local mode, bridge checks, upgrade/uninstall/headless behavior verified |
| 2 | Sidebar status and diagnostics | 1 | Unconfigured, connecting, success, 401/403, offline, stale, recovery and Scope change; accessible collapsed/dark/light UI |
| 3 | Direct existing Dashboard embed and only necessary compatibility/auth adapter | 2 | Server/Dashboard prerequisites checked first; custom-auth startup rejection distinguished from embedding failure; latest DSH Web/Desktop local passwordless and token-login paths, disabled guidance, resources/navigation/Scope/theme; original auth and confirmations preserved, no token exposure |
| 4 | Existing PC Handoff workflow, current-work inputs, returned-carrier persistence/export | 1 | Existing operations generate/validate/continue the payload; test/open-failure evidence and omissions preserved; same-Scope conversations remain separate; bounded inputs, no unrelated source-Agent tool writes, no implicit commit or automatic saving; exact restart/unknown-result handling |
| 5 | Three target adapters, detection, consented PC installation, directory/Scope preflight | 4 | Codex Desktop + Claude Desktop + Claude Code CLI each pass real install/readiness/open/prefill; missing app stops, No cancels, Yes installs then verifies, failures/restart handled, no silent agent installation |
| 6 | Response submenu, preflight/install dialogs and waiting animation | 2, 4, 5 | Real UI, latest boundary, keyboard/reduced motion, stage text, no fake percentage, No/Cancel, deduplication, restore after closing, five-minute waiting timeout |
| 7 | Exact receipt reads, recovery UI, qualification and bilingual guides | 4, 5, 6 | Actual receiving model invokes Continue and ACK; wrong payload/identity and another conversation's receipt rejected; restart restores the exact saved delivery; interruption limits disclosed; no full-history polling; affected cases rerun when qualification versions change |

### Dashboard acceptance prerequisites

1. Record the Server access mode and whether custom authentication or access control was injected. Confirm successful Server startup and explicit Dashboard enablement before testing the panel.
2. Treat Dashboard plus an injected custom provider as an unsupported startup configuration. Verify the startup rejection and its diagnostic separately; it does not reach embedding acceptance. Do not infer its cause from a connection error alone or change authentication to bypass it.
3. In each supported mode, first confirm Dashboard access in a normal browser, including the existing token login when enabled. Then test the actual DSH Web/Desktop panel with its own cookie/container behavior. External-browser success is a prerequisite, not a substitute for in-panel acceptance.
4. Record Server startup/configuration failure, Dashboard disabled, authentication failure, and embedding failure separately. Preserve a healthy API connection status when only Dashboard is disabled or unavailable.

### Handoff evidence and recovery acceptance

- Exercise passed, failed, interrupted, and not-run checks, an unresolved failure, missing/truncated evidence, and a workspace edited after a check. The exact Handoff must retain the observed distinctions or disclose omissions; unavailable results cannot become verified success.
- Prepare deliveries from two conversations in one Scope and from different boundaries in one conversation. Verify that preparing, retrying, opening, recovering, or acknowledging one does not overwrite or confirm the others. A mismatched exact payload or attempt remains unconfirmed.
- Restart after a payload has been durably saved and restore that exact delivery and status. If preparation completed remotely but its payload was not saved, retain the unknown outcome and offer explicit creation of a new version; do not claim recovery of the original. If interruption preceded any request, show no saved delivery rather than claiming an automatic checkpoint. None of these cases adds per-response saving or generation.

### Qualification evidence

For each scenario and required destination, record the test date, DSH release tag and prerelease status, installed package identity, plugin archive identity, PC Server version, OS version, destination version, access mode/provider configuration, observed result, and evidence location. Do not include credentials. A changed pin or environment invalidates any unqualified extrapolation: rerun the affected cases on the new combination, retain earlier evidence, and leave the new combination unqualified until it passes.

The status/dashboard release may precede cross-agent handoff. Complete delivery requires PR 3 and PR 7 acceptance plus all three initial targets; Claude Code CLI is not an optional release add-on. A source build or test double is not a release qualification. Record exact DSH, plugin, PC Server, OS, destination version, package identity, access mode, and observed result for each scenario. Windows/Linux results cannot be inferred from macOS; remote/shared DSH deployment is out of scope.

Each PR includes focused tests. Use existing plugin `test`, `build`, `pack:release`, `test:e2e`, and `test:e2e:runtime` scripts as applicable, plus PC-owned UI acceptance against unmodified DSH. Setup changes run the existing DSH CLI/profile/transport tests. Dashboard and receipt changes run affected behavior and authorization tests. Follow repository checks and `make docs-test` for bilingual documentation. A public API change, if justified during review, requires `openapi/powercontext.yaml`, generated clients, and contract tests together; do not edit generated sources by hand.

# Drawbacks

The plugin gains packaging, authenticated UI, local storage, and platform-adapter responsibilities. Deep links and DSH extension behavior require ongoing version qualification. Existing Handoff drafting can cost model time and omit necessary state; the editable fallback and explicit omissions remain essential.

Local file transport is constrained by destination sandboxes. Exact receipt correlation depends on receiver cooperation and access to the receipt. Server Principal identity may not identify an application or session. Remote/shared DSH deployments are not supported by this feature. These limits must appear in the product and support matrix.

# Rationale and alternatives

| Alternative | Decision |
| --- | --- |
| Add DSH Electron IPC or extend its application catalog | Outside the delivery boundary; use the existing Node Host plugin mechanism |
| Put unrestricted deep links in model Markdown | Not a trusted product action; cannot establish directory, payload, or capability preflight |
| Browser-only deep links | Possible later convenience, but do not solve Host file locality, local detection, or remote payload transfer |
| New local companion daemon | Deferred; introduces pairing, installation, credentials, and another lifecycle before local Host capability is exhausted |
| Copy-only for every target | Retained as fallback; does not meet the qualified local one-click prefill experience |
| Automatically send through CLI/SDK | Separate execution product with different permission, cancellation, and cost semantics |
| Always commit a Handoff | Rejected; transport persistence does not imply a durable milestone |
| Read latest Handoff or latest ACK | Rejected for this workflow; concurrent work requires exact payload and receipt selection |
| Edit private destination databases or automate keystrokes | Rejected; fragile and risks user sessions/drafts |
| New Core Transfer entity and generic continuity query API | Not required initially; reuse Handoff contracts, plugin records, and exact Source reads |
| Mandatory new dashboard/proxy or independent summarizer | Not required; embed the existing page first and use existing PC Handoff operations |

# Prior art

PowerContext's [Handoff RFC](0048_handoff_artifact.md), [work-continuity RFC](1223_human_agent_work_continuity.md), and [rollover RFC](1783-handoff-session-rollover.md) define the semantic boundaries. The [DSH integration](../docs/integrations/dsh.md), [dashboard design](../development/dashboard.md), and [Codex handoff guide](../docs/workflows/handoff-with-codex.md) provide existing integration contracts.

The inspected DSH source demonstrates external Client discovery, authenticated plugin routes, and Node-based application opening. That precedent supports feasibility; it does not establish this plugin's shipped compatibility.

Destination entry references are [Codex application deep links](https://learn.chatgpt.com/docs/reference/commands#deep-links), [Claude Desktop links](https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link), and [Claude CLI links](https://code.claude.com/docs/en/deep-links). Candidate follow-ups include [OpenCode's server/TUI API](https://opencode.ai/docs/server/) and [Pi extensions](https://pi.dev/docs/latest/extensions). Qualification must recheck supported parameters, protocol registration, length limits, and draft behavior for the selected version; documented entry points alone do not prove end-to-end receipt.

# Unresolved questions

Product decisions are fixed below. Remaining questions concern exact implementation and qualification, not permission to change the agreed scope.

| ID | Decision | Remaining verification | Gate |
| --- | --- | --- | --- |
| Q1 | Use the current latest official DSH release; recorded baseline `v0.2.2-rc.1` | Recheck/pin latest at implementation and qualify its actual Web/Desktop packages; historical checkout is not acceptance | PR 1 |
| Q2 | Only the owner’s local deployment | Verify owner/locality signals or explicit local configuration; reject remote/shared/unknown execution | PR 1/5 |
| Q3 | Existing Dashboard supports passwordless local access; prefer direct embed and preserve configured auth | Prove both passwordless and token-enabled in-panel loading; use only necessary auth/container adaptation | PR 3 |
| Q4 | Existing PC Handoff is the single work-transfer mechanism | Verify current-work inputs, existing generation/high-level paths and exact returned carrier; no duplicate summarizer or unrelated project actions | PR 4 |
| Q5 | Validate directory, Server and Scope before target startup | Verify each target’s actual startup binding precedence; explicit resolution of conflicts | PR 5 |
| Q6 | User-data package, readable export if needed, no implicit milestone | Prove package readability in all three actual target environments | PR 5 |
| Q7 | Bounded waiting with DSH animation; retain pending/pinned payloads | Verify stage transitions, reduced motion, timeout, retention and capacity behavior; measure initial budgets | PR 4/6/7 |
| Q8 | PowerContext content and canonical digest are authoritative | Validate cross-language preservation and exact receipt matching, including null/numeric/Unicode cases | PR 4/7 |
| Q9 | Receiver confirmation means the target reports reading this Handoff through PC; check Agent and PC installation first | Test missing Agent stop, Yes/No install consent, target-specific installation/readiness and exact ACK; actual session identity remains unknown without evidence | PR 5/7 |
| Q10 | Codex Desktop, Claude Desktop and Claude Code CLI are all initial targets | Qualify all three; confirm Desktop-only installer support, CLI protocol registration and no automatic sending | PR 5/7 |

The practical open points are the latest DSH embed behavior, the supported Claude Desktop-only PC installation path, each target’s effective startup configuration and file access, and any required narrow dashboard authentication adapter. A failed experiment keeps its acceptance item open; it does not justify modifying DSH, silently installing an extra agent, weakening authentication, or dropping Claude CLI from the required scope. Existing copy/export and external-browser fallbacks remain truthful recovery options, not substitutes for the required automatic/embedded acceptance.

# Future possibilities

Add individually qualified Pi/OpenCode and other agent adapters, in-DSH continuation, Windows/Linux support, remote directory mapping and authenticated payload transfer, and optional receiver-provided session attribution. Automatic submission, managed multi-agent execution, and extending Outcome association to temporary handoffs require separate designs. None is needed to accept the local prefill and exact-acknowledgement boundary in this proposal.
