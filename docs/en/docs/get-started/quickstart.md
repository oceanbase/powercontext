---
title: Quick Start
description: Configure full memory, connect Codex, and verify Source capture, Topic Memory evolution, and cross-session recall.
---

# Quick Start

Start with installation, discuss a project in Codex, watch its input become Source evidence and an evolving topic,
then recover the decisions in a new session. These instructions use PowerContext 1.4.1,
with the Agent plugin from the matching `powercontext-v1.4.1` tag.

You need macOS or Linux, Python 3.11+, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and an installed Codex CLI. Full memory also needs working Generation and Embedding model APIs:
prepare their base URLs, model names, and API keys. Signing into a Codex or Claude subscription does not automatically
provide these APIs to the PowerContext Server. Without separate model APIs, select basic memory to test explicit saves
and recall; that does not enable automatic Topic Memory.

## 1. Install and open the wizard

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
mkdir -p ~/powercontext-demo
cd ~/powercontext-demo
powercontext config init --language en --output .env
```

For a first local installation:

1. **Storage**: SQLite works without another database dependency. To try embedded seekdb, select it and approve the background dependency installation.
2. **Usage scenario**: choose “Only on this machine” when your Agent, browser, and Server share a machine. For a remote Server, first read [Connect to a remote Server](../operate/connect-remote-server.md).
3. **Memory capabilities**: select full memory and enter Generation and Embedding API details. See [Configure models](configure-models.md) for protocols and dimensions.
4. **Dashboard**: enable it to inspect Sources and memories. Local authentication defaults to off; enable it if you want to require a token. See [optional authentication](configure-server-environment.md#local-dashboard-and-optional-authentication).
5. **Background processing**: start with the recommended schedule for each Artifact. An inspection interval is not a completion deadline; model processing takes additional time.
6. **Agent**: select Codex and plan a new isolated Scope. Select Claude Code next if needed, then choose “Finish Agent configuration”.
7. Review and save.

The wizard writes:

| File | Purpose |
| --- | --- |
| `.env` | Server, client, Agent, database, and model settings, including any configured credentials |
| `.env.next-steps.md` | Startup, Scope creation, plugin connection, and checks for your choices |

The final screen shows the Dashboard URL and the SSH command when selected. If authentication is enabled, it also
shows a newly generated Server token once; later, look up `POWERCONTEXT_SERVER_AUTH_TOKEN` in `.env`.
These files can contain credentials; do not commit them.
If seekdb is still installing, the wizard waits with an activity indicator. Complete any reported dependency recovery
before starting the Server. Saving files or installing dependencies does not start the Server.

## 2. Start the Server

In this terminal, run:

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

Keep the terminal running. Open the Dashboard URL printed by the wizard, using the port saved as
`POWERCONTEXT_SERVER_HTTP_PORT` in `.env`. With authentication disabled, the page opens directly. Otherwise, sign in
with the **Server token**, not a model API key.
An empty Dashboard is expected before you capture data. For operation after closing the terminal, stop the foreground
Server and install a [persistent personal service](../operate/deploy-server.md#run-a-persistent-personal-server)
with `powercontext service install --env-file .env` to reuse the same configuration.

Open another terminal, load the client settings, and check the running service:

```bash
cd ~/powercontext-demo
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Confirm readiness and the capabilities you selected. A `degraded` result means some dependencies still need attention;
it is not a successful full-memory check. `config validate` checks configuration, while these commands check the running service.

## 3. Create a Scope and install the Codex plugin

Open `.env.next-steps.md` and run the request under “Create the planned isolated Scopes”. The Server returns the real
`scope_id`. Add it to `.env`:

```dotenv
POWERCONTEXT_CODEX_SCOPE_ID=replace-with-returned-scope-id
```

The planned `codex-xxxxxxxx` value is a title, not an ID. For Claude Code, put the ID returned by its creation request
in `POWERCONTEXT_CLAUDE_SCOPE_ID`. Agents can have separate Scopes or explicitly share an existing one.
Changing directories does not create isolation. Use the same Scope in the Dashboard and Agent during this check.

Reload the client settings and install the matching plugin:

```bash
set -a
. ./.env
set +a
powercontext setup codex --ref powercontext-v1.4.1
powercontext doctor codex
codex
```

Before sending the first message in the new session:

1. Open `/hooks`, inspect the PowerContext hooks, and grant trust when prompted. `SessionStart` resolves the Session Scope;
   `UserPromptSubmit` automatically prepares relevant context and captures your input as a Source.
2. Confirm that PowerContext MCP tools are available. Tool discovery and Hook execution are separate checks.
3. If you skipped or denied trust, use the [Hook recovery steps](../integrations/codex.md#first-session-hook-trust)
   before testing capture. Without an enabled `UserPromptSubmit` hook, automatic capture and recall do not run.

For a non-default address, follow the Codex connection instructions in `.env.next-steps.md`: the installed plugin's
`.mcp.json` must use the same Server as the Hook, with credentials configured when authentication is enabled.
Installing the plugin does not start the Server. `doctor codex` checks installation, connection, and tool coverage;
it does not establish Hook trust, execution, or successful memory processing.

These commands use Codex CLI. A desktop app may not inherit terminal environment variables. Before testing in the
desktop app, verify that both its Hook and MCP receive the same URL and Scope, and credentials when authentication
is enabled. See [Codex](../integrations/codex.md) and [Claude Code](../integrations/claude-code.md) for host-specific behavior.

Full memory also needs a generation policy for this real Scope to use Profile. Follow the
[Profile policy steps](configure-models.md#enable-a-profile-policy-for-the-scope) to read its current version and update it;
saving the configuration does not perform this operation.

## 4. Verify memory in your selected mode

Both modes can capture Source evidence when the Hook is enabled. Basic memory needs no separate Server model API;
the Agent still uses its own model. Choose the check matching your configuration.

### Basic memory: explicitly save and retrieve

Ask in the bound Codex session:

```text
Save this decision to PowerContext: the Aurora acceptance project uses uv for Python dependencies.
Return the saved record reference.
```

Check the actual write result, then open a new session with the same Scope and ask:

```text
Search PowerContext for Aurora's Python dependency-management decision, with its citation.
```

Verify the saved content and citation. A conversational acknowledgement is not proof of a write; this check verifies
explicit save and retrieval, not automatic extraction or Topic Memory generation.

### Full memory: Source processing and Topic Memory

Send a concrete project decision in the new Codex session:

```text
We are designing the Aurora acceptance project. Use uv for Python dependencies and .env for configuration.
The first deployment runs on one Linux server, with a Mac connecting through an SSH tunnel.
Restate these constraints without changing any code.
```

Select the bound Scope in the Dashboard and check the following:

1. **Source** contains your input. Prompt capture does not imply that every Agent reply is captured.
2. **Topic Memory** contains a related topic after the configured inspection interval and model processing.
3. Send a related update: “Aurora deployment update: run the service with a user-level service manager. Keep the SSH tunnel.”
4. Confirm that the second Source arrived and that topic content or revision history reflects the update. Waiting one minute is not itself a check.
5. Open a new Codex session with the same Scope and ask: “Find Aurora's dependency-management and deployment decisions in PowerContext, with citations.”

Correct content and its citation complete the capture → evolution → cross-session recall check. A successful
`doctor codex` only checks installation and connectivity; it does not prove this workflow. The model may merge evidence
or decide that no update is needed, so not every message creates a new memory.

Full memory does not generate every Artifact from every message. Profile, Experience, and Skill have additional
triggers or review requirements; see [Capability behavior](configure-models.md#full-capabilities-do-not-generate-every-artifact-from-every-message).

| Check | Evidence of success |
| --- | --- |
| Installation and MCP discovery | `doctor codex` reports connection and tool coverage; it does not verify Hook execution |
| Prompt capture | The actual input appears as a Source in the bound Scope |
| Explicit Memory save | A real saved record reference, with matching content found in a new session |
| Automatic processing | A related Topic Memory and its Source evidence; check updates without assuming a fixed completion time |
| Cross-session retrieval | Correct content with a citation, using the same Server and Scope |

## 5. Choose your next workflow

The seven Artifact families have different inputs and consumption paths. Source is evidence and Tag is metadata;
neither is an Artifact family. The examples below are requests, not guaranteed keyword triggers. Use the actual tools
available in your connection; follow the linked HTTP/CLI workflow for operations without a dedicated MCP tool.
Read the [Artifact overview](faq.md#artifact-families-at-a-glance) for conceptual differences.

| Capability and purpose | Request or starting point | Requirements and result |
| --- | --- | --- |
| [Atomic Memory](../workflows/atomic-memory.md): individual decisions and constraints | “Remember in PowerContext that Aurora uses uv.” | `remember_memory` saves explicitly without Server generation; search with `search_memory` and verify the record citation. Automatic extraction needs Generation and scheduling. |
| [Topic Memory](../workflows/topic-memory.md): evolving topic summaries | “Search Aurora's deployment topics, then read the relevant revision.” | `search_topic_memory` / `get_topic_memory` read existing topics. Automatic generation needs configured Generation and scheduling; inspect the topic's Source references. |
| [Profile](../workflows/use-profiles.md): Scope background | Follow the Profile workflow to generate and inspect the Scope's profile. | Automatic generation needs Generation, a Scope policy, and scheduling; policy selects review or automatic commit. Recall requires explicit assembly selection. |
| [Handoff](../workflows/handoff-with-codex.md): continue unfinished work | “Hand off this work” for temporary transfer; “Commit a durable handoff” for a milestone; “Preview the handoff; do not commit” for a read-only preview. | The shipped Skill routes a transfer request to the Handoff workflow. Distinguish temporary prepared content from an exact committed Revision; no background extraction is required. |
| [Experience](../workflows/create-and-review-experience.md): reusable lessons | “Generate an Experience candidate from these exact Source references.” | `generate_experience` needs Generation; `propose_experience` accepts caller-authored content without generation. Inspect the pending Candidate or `no_op`; generated proposals need review before reuse. |
| [Skill](../workflows/create-and-export-skill.md): reusable procedures | “Generate a Skill candidate from these exact Source references.” | `generate_skill` needs Generation; `propose_skill` accepts complete content without generation. Review the candidate, then explicitly export the exact approved Revision to the Agent host. |
| [Prompt](../workflows/manage-prompts.md): operational generation instructions | Read and configure an operation's Prompt through the HTTP workflow. | This is not a captured user message. Check customization support and `scope.admin` when access is enforced; saving a Revision does not enable an operation or reprocess history. |
| [Source](../workflows/sources.md): original evidence | Send a distinctive ordinary message; for explicit material use `capture_content_source`. | Hook capture needs no generation model. Verify the Source; capture alone does not establish Memory generation. Do not infer capture of all Agent replies. |
| [Tag](../workflows/manage-artifact-tags.md): organize saved Artifacts | Use the Python Client or HTTP workflow to label an exact Artifact and query the label. | No model is required. Use the existing Artifact ID, permissions, and tag ETag; verify the saved label and query result. There is no dedicated MCP tag-management tool. |

### Plugin guidance and managed Skills

The installed plugin includes `powercontext-project-context`, which routes requests to the relevant workflow. It is
separate from a managed Skill stored in PowerContext. If the routing Skill is absent from the host catalog, check plugin
installation; a natural-language example does not add missing tools or grant permission.

For a generated managed Skill, inspect the Candidate and obtain an authorized review decision. Approval creates an
immutable Revision; it does not install or execute it. Follow [Create and export a Skill](../workflows/create-and-export-skill.md)
to export the exact approved Revision with `powercontext skill export --target codex` to the intended host-local
`.agents/skills/<skill-name>` directory. Confirm that Codex discovers the exported Skill; restart if it does not appear.
Direct Artifact writes have their own contract; the Candidate sequence here describes generation and proposal workflows.
Review authority depends on permissions, not on whether the reviewer is human. Discovery does not grant execution authority.

## Continue

- [Install and run](install-and-run.md): versions, seekdb dependencies, existing storage, and updates.
- [Deploy the Server](../operate/deploy-server.md): background services, SSH, HTTPS, and backups.
- [Configure models](configure-models.md): API protocols, vector dimensions, and extraction checks.
- [Troubleshoot](../operate/troubleshoot.md): service, model, capture, or recall failures.
