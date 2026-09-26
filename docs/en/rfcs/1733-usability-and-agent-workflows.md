---
title: Usability and Agent workflows
description: A common direction for installation, operations, staged configuration, scenario Skills, and the local Client.
---

- Proposal Name: `usability_and_agent_workflows`
- Start Date: 2026-09-24
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1733](https://github.com/oceanbase/powercontext/pull/1733)

# Summary

PowerContext usability follows two directions: reduce the cost of installation and continued operation, and help Agents understand and use product capabilities. Installation starts with a Bash / PowerShell bootstrap script. Independent installer/operations tooling prepares the environment, organizes initial configuration and integration, and handles ongoing maintenance. A shared Client provides configuration, connection, and common host operations; capabilities requiring persistent execution can adopt a local daemon incrementally. An installation/maintenance Skill and a product Skill guide environment maintenance and product use through the corresponding tools.

This RFC defines component responsibilities and interactions as a common direction for evolving entry points. Dedicated designs determine command names, platform adapters, daemon execution, and distribution formats.

# Motivation

Users need a continuous journey from installation and configuration through Agent connection to first use, followed by accessible diagnosis, maintenance, and upgrades. This journey should minimize prior knowledge of Python environments, deployment options, model configuration, and internal product concepts.

Installation, configuration, and Agent integration include steps that users must connect manually. Service addresses, credentials, work boundaries, and capability state must remain consistent across entry points. Completing one step does not establish that the product is usable. Installation and configuration entry points should handle these transitions and guide users through connection and their first operation.

Product use also needs continuous guidance. When a user wants to save a decision, continue work, capture experience, or create a Skill, the Agent should select suitable capabilities, perform the required operations, and return an artifact that can be inspected and used further.

# Guide-level explanation

Users run a Bash / PowerShell installation script or ask an Agent to start through the installation/maintenance Skill. The installation flow determines whether to use a local or existing Server, prepares the environment, selects a host and project, and completes the connection. Initial configuration and host integration are part of installation.

Personal local mode enables the Dashboard by default without an auth token and does not require a Server-side model for basic use. The installer saves connection settings and project bindings. Users do not need to copy tokens, export global environment variables, or enter Scope IDs. Existing Server users configure only the local Client and host integration.

Usage guidance follows tasks such as saving decisions, finding context, and continuing work. After installation, provide scenarios that match enabled capabilities and reveal advanced configuration as needed.

After installation, the local installer/operations entry handles status checks, logs, and process management. The Client / daemon provides subsequent connection configuration, project binding, and host integration. The installation/maintenance Skill calls these operations for user requests. Local maintenance tools remain usable when the Server or daemon is stopped or unavailable.

The installation and operations relationships are shown below. The Bash / PowerShell script obtains maintenance tools during initial installation; users and the installation/maintenance Skill subsequently call the local entry directly. Installer/operations tooling manages distribution resources, native services, and initial configuration. Connecting to a remote Server does not install a local one.

```text
Bash / PowerShell (first install)
                 |
                 v
           Installer / Ops <---- User / powercontext-install Skill
                 |
                 +-- packages --------> Server / Client / Plugins
                 |
                 +-- service control -> Native service manager
                 |                          +-- Local Server
                 |                          `-- Client daemon
                 |
                 `-- initial setup
                      +-- Server CLI ------> Local Server settings
                      `-- Client operations -> Connections / Projects / Hosts
```

Runtime calls are shown below. The product Skill guides host workflows as needed. The Client CLI and host plugins share common Client operations, while the daemon handles work requiring persistent execution. Native MCP connects the host directly to the Server. Each Server endpoint refers to the configured local or remote service that owns domain state and artifacts.

```text
powercontext-project-context Skill
                  |
                guides
                  v
            Host / Plugins
                  |
                  +-- Native MCP ---------------------------> Server (MCP)
                  |
                  `-- Shared Client <---- Client CLI
                            |
                            +-- direct calls ---------------> Server (HTTP API)
                            |
                            `-- persistent work --> Client daemon
                                                        `--> Server (HTTP API)
```

# Reference-level explanation

## Component responsibilities

| Component | Responsibility | Interaction |
| --- | --- | --- |
| Bash / PowerShell bootstrap script | Inspect the platform, obtain installation resources, and start installation | Obtain and invoke independent installer/operations tooling |
| Installer/operations tooling | Prepare dependencies and organize initial configuration/integration; install, upgrade, repair, uninstall, manage processes, and diagnose | Invoke package managers, native service adapters, and common Client operations |
| Configuration wizard | Collect necessary input such as usage mode, host, and project | Share the wizard and configuration operations between the installer and subsequent configuration entry points |
| Skills | Recognize intent, select and guide workflows | Call local tools or product interfaces actually exposed by the host |
| Host adapters / Plugins | Handle loading, events, tool names, connections, Scope binding, and permission interaction | Connect common workflows to each host |
| Client tool (CLI / daemon) | Provide connection configuration, project binding, host integration, and client domain operations | Share common Client operations across the CLI, installer, and plugins; run persistent capabilities in the daemon |
| Server tool (CLI / service) | Manage Server configuration, run the service, execute domain operations, and retain state/artifacts | The CLI configures and starts the foreground process; the running service exposes HTTP API and MCP capabilities |

Skills describe workflows; tools implement operations. Installer/operations tooling manages Server and Client processes, the Client provides configuration and client execution, and the Server owns domain state. The distribution layer owns plugin downloads and version selection.

## Commands and operation entry points

Commands belong to three tools: Installer / Ops, Client, and Server. Each tool has its own command entry. The tool names and subcommands below describe ownership and hierarchy; dedicated designs determine executable names and exact arguments.

```text
Installer / Ops CLI
+-- install | upgrade | repair | uninstall
+-- status | doctor
+-- server
|   `-- start | stop | restart | logs
`-- client
    `-- start | stop | restart | logs

Client CLI
+-- config
|   `-- show | validate
+-- connection
|   `-- configure
+-- project
|   `-- bind
+-- host
|   `-- connect
+-- dashboard
|   `-- open
+-- memory | handoff | experience | skill
`-- daemon
    `-- run

Server CLI
+-- config
|   `-- show | validate
`-- run
```

The Installer / Ops CLI remains on the machine after installation and runs independently of the Server and daemon. It owns the software lifecycle, overall status, and diagnostics. Its `server` and `client` groups select the local process to manage. Subsequent maintenance calls this tool directly without downloading the bootstrap script again.

The Client CLI owns client connection configuration, project binding, host integration, opening the Dashboard, and domain operations. Its `config` group handles only client settings. The `connection`, `project`, and `host` operations share their implementation with the installer and daemon. `daemon run` is the foreground execution interface for the persistent Client process.

The Server CLI owns Server configuration and foreground execution. Its `config` group handles storage, model, listener, and access settings. Installer/operations tooling uses native service managers to invoke Server CLI `run` or Client CLI `daemon run`, providing unified start, stop, restart, and log operations.

Responsibilities of `powercontext setup`, `powercontext service`, and `powercontext doctor` move to these tools. The old commands and forwarding aliases are not retained.

## Installation and maintenance

Bash / PowerShell scripts inspect the platform, obtain installation resources, and start installation. The installer prepares missing runtime dependencies, uses isolated environments, and coordinates compatible Server, Client, host integration, and Skill versions. Download sources and installation targets are explicitly configurable, preserving existing user choices.

The installer organizes initial configuration: it calls the Server tool for local service configuration and common Client operations for connections, project bindings, and host integration. The installer and daemon share these Client operations. The personal local path completes configuration and service startup; installer/operations tooling registers the personal service when users select persistent operation. Users add hosts or switch Servers through the Client configuration entry afterward. Environment changes still go through installer/operations tooling.

Manage programs, configuration, and user data separately. Upgrades reuse configuration; uninstall preserves user data by default. Program rollback and data-format recovery are separate operations.

Common flows own environment inspection, operation order, and status reporting. Platform adapters prepare dependencies and manage native services. The operating system manages Server and client daemon lifecycles separately. Remote connection configures only the local Client and plugins without implicitly changing the remote deployment.

Operations support repeated execution and interruption recovery, reporting completed work, failures, and next actions. Recovery checks actual state and preserves user configuration and existing results. The installation/maintenance Skill chooses subsequent steps from tool results. Environment inspection, configuration repair, and native service management do not depend on a running Server or daemon.

The [EverMe installation Skill](https://everme.evermind.ai/SKILL.md) uses a CLI for installation, host configuration, and diagnostics. The [Lody CLI](https://lody.ai/docs/cli/) retains local repair commands that do not require a running daemon. These examples support independently usable maintenance operations; the installation architecture determines their distribution format.

## Staged configuration

The wizard offers quick local use, existing Server connection, and full configuration. Users select a usage mode, host, and project before expanding model, storage, and advanced settings as needed. The wizard reuses valid configuration and skips satisfied requirements.

New personal local configurations use these defaults:

| Setting | Default behavior |
| --- | --- |
| Server | Listen only on local loopback addresses |
| Dashboard | Enabled by default with an entry to open it directly |
| Authentication | No auth token required; Dashboard, HTTP API, and MCP use consistent local settings |
| Storage and models | SQLite and the default user data directory; basic save and retrieval require no Server-side model, while generation and vector capabilities are enabled as needed |
| Project binding | Resolve or create a Scope through the Server for the selected project, then save and reuse the binding |
| Host integration | Save connection settings automatically without manually transferring addresses, tokens, or Scope IDs |

The local Dashboard without a token follows the direction in [#1707](https://github.com/oceanbase/powercontext/pull/1707). Existing configurations retain user choices; remote connections follow Server authentication requirements. Exposing a local service to a LAN or remote clients requires an explicit access change and authentication configuration. Credentials use controlled input or existing references and do not enter progress records or diagnostic results.

```text
Choose local / remote -> Apply settings -> Select host / project -> Connect
```

Interactive wizards, the installation/maintenance Skill, and scripts share configuration and execution operations. Explicit parameters and structured results let Agents avoid simulating long terminal questionnaires. Machine-readable output and permission to interact are separate controls.

Resume reinspects actual state and continues incomplete steps. Configuration generation, environment changes, service startup, and host connection have separate states. Generating a configuration file does not establish that the product is usable.

## Skills and scenario workflows

Initially provide two discoverable Skill entries:

| Skill | Responsibility | Distribution |
| --- | --- | --- |
| `powercontext-install` | Installation, configuration, host integration, upgrades, diagnosis, and repair | Distributed independently; loadable before Runtime installation or while services are unavailable |
| `powercontext-project-context` | Saving and retrieval, handoff and continuation, experience capture, and Skill creation/use | Shipped with host integrations; loads scenario workflows as needed |

The installation/maintenance Skill calls the bootstrap script, local operations tool, and Client configuration operations, loading installation or maintenance workflows for the task. The product Skill keeps its layered structure and loads references for Memory, Handoff, Experience, and Skill workflows. Both entries use short descriptions to limit initial context.

Common Skills describe task goals, operation order, and result semantics. Host adapters determine how workflows are discovered and triggered. Configuration and usage guidance distinguish automatic Hook execution, Agent decisions based on guidance, and workflows explicitly requested by users. Each host describes the modes it supports; loading a Skill does not establish automatic execution.

[Nowledge integration definitions](https://github.com/nowledge-co/community) distinguish these modes. Its [Lody integration](https://mem.nowledge.co/integrations/lody) connects the Agent that Lody actually runs. PowerContext reuses existing host integrations for such launchers and adds adapters where a launcher provides additional capabilities.

Each scenario describes triggering intent, required capabilities, input, steps, and results. Common guidance covers these journeys:

| Scenario | Workflow | Result |
| --- | --- | --- |
| Save and retrieve context | Save decisions or constraints, search and read when needed | Memory citation or retrieval results |
| Handoff and continuation | Inspect work and prepare a handoff; commit with explicit durable-milestone intent; receiver verifies and continues | Temporary handoff or exact committed revision, plus receiving state |
| Capture experience | Select material, generate a candidate, review within authorization | Experience candidate or approved revision |
| Create and use Skills | Generate a candidate; review, export, or install within authorization | Candidate, version, export location, or installation result |

```text
Intent -> Load workflow as needed -> Execute -> Verify result
```

Workflows select operations using Server capabilities and the host's actual tool inventory. Alternative CLI/API paths must already be supported and authorized. Missing capabilities leave explicit incomplete steps. Scenario descriptions grant no additional authority.

Completion comes from actual operation results. Preparation, persistence, review, and installation are reported separately. Failures and unknown outcomes preserve existing results and require inspection before retry.

## Distribution and compatibility

Follow the division in [#1691](https://github.com/oceanbase/powercontext/pull/1691): generate common Skills and workflows from one baseline, retain host loading/event/permission differences in adapters, and provide shared execution through the installed Client. The proposal's Python calls and JSON Lines worker provide common execution; an independent persistent daemon is a follow-up design.

The shared Client provides configuration, host integration, and operations common to plugins. Capabilities requiring persistent execution can adopt a local daemon incrementally. The installer, desktop, Client CLI, and plugins reuse these capabilities while retaining their own interactions. Installer/operations tooling owns the environment, lifecycle, and diagnostics of the Server and daemon. The Server owns Scopes, domain state, and artifacts; host adapters handle events, work boundaries, and permission interaction.

[Lody](https://lody.ai/docs/quickstart/) separates its interfaces from local execution. Its desktop app can manage the local runtime or connect only to other machines. PowerContext follows this division when designing persistent capabilities on the shared Client.

Select compatible Server, Client, and plugin combinations through interface and capability requirements. Host rules and Skills reusing existing Client capabilities can evolve independently; changes requiring new Client capabilities declare corresponding version requirements. Installation metadata remains readable offline or without a Server. Actual Server and host state determine product capabilities.

Domain operations are available through the Client CLI, HTTP API, MCP, and direct SDK calls. Installation and operations use the Installer / Ops CLI. Personal mode changes authentication defaults while retaining domain authorization rules. This proposal does not change domain objects or persistence formats. Dedicated designs define installation migrations, daemon protocols, and scenario changes.

# Drawbacks

Common workflows, distribution resources, and host adapters require coordinated maintenance. Resumable flows must reconcile records with the actual environment. A daemon adds local resource consumption and installation, diagnosis, and upgrade costs. Its responsibilities should follow actual needs for persistent execution. Reusing Client operations still requires local diagnosis and repair that work independently of the daemon. Common components should focus on stable operations/results while retaining host differences in discovery, permissions, and interaction.

# Rationale and alternatives

The script entry lets users start installation before the Runtime is installed. Independent installer/operations tooling manages local processes and environments and remains usable when the Server or daemon fails. Sharing Client configuration operations between the installer and daemon keeps initial integration and subsequent configuration consistent. The installation/maintenance Skill covers the environment lifecycle, while the product Skill organizes product operations by task.

Documentation alone leaves users assembling steps. Putting operation logic inside Skills increases execution differences. Centralizing installation inside the Runtime couples maintenance and service releases. Separating responsibilities allows entry points to evolve independently while reusing existing capabilities.

# Prior art

- The [installation architecture proposal](https://github.com/oceanbase/powercontext/pull/1408) and [script installer](https://github.com/oceanbase/powercontext/pull/1529) establish installation outside the service. [RFC 1299](1299_local_server_availability_and_service_installation.md) provides the personal Server native-service foundation; this proposal unifies its public operations entry.
- The [Agent Plugin distribution proposal](https://github.com/oceanbase/powercontext/pull/1410) and [shared execution/distribution work](https://github.com/oceanbase/powercontext/pull/1691) separate common content, Client execution, and host adaptation. Local daemon design continues these boundaries.
- [uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/) demonstrates independent installation and explicit version selection.

# Unresolved questions

Installation and distribution architecture determine each tool's executable name, exact arguments, installation records, platform support, and formats. Client configuration entry points, daemon execution, and plugin compatibility continue as follow-up design to #1691. These dedicated designs must maintain consistent operations/results and independently usable local maintenance.

# Future possibilities

Desktop and Web management can reuse the same state and operation interfaces. Additional scenarios can reuse common Skills and host adapters. Client capabilities requiring persistent execution can adopt the daemon incrementally.
