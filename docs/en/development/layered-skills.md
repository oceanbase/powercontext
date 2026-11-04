---
title: Layered PowerContext Skills
description: Shared Skill resources, host bindings, and native distribution.
---

# Layered PowerContext Skills

PowerContext distributes one shared workflow Skill with a host-specific binding reference. The entry, `powercontext-project-context`, directs the Agent to the detail needed for its request. Each installed package contains its own resources and can be used independently of the source checkout or another host's package.

The responsibilities of system guidance, tools, and Skills are described in [Agent tool routing and result reporting](integration-guidance.md).

## Entry and references

`SKILL.md` contains the discoverable name and description, intent routing, and common result and authorization rules. Its references cover Scope and Memory, Work Handoff, candidate Review, and Experience/Skills. The Agent reads the relevant reference when it needs workflow detail; a self-contained tool call can use its existing description directly.

`references/host.md` describes the installed host's Scope selection, resolver commands, and connection facts. Keeping this information separate lets each host bind the shared workflow to its native session and workspace model.

## Shared source and distributions

The maintained Skill source is `integrations/agent-plugin/powercontext/skills/powercontext-project-context/`.

```text
Shared Skill directory
          |
          v
Resource synchronization <--- Package locations
          |              <--- Host-owned references/host.md
          v
Native Skill packages
          |
          v
Host packaging and installation

Host-owned MCP declarations ---> Native MCP configuration
```

Each native package contains a complete copy of the shared Skill directory, including assets and metadata, with its own `references/host.md` and any additional host files.

Each host maintains its small MCP declaration in the native package format, including authentication fields and variable interpolation. The portable Agent Plugin maintains its own `mcp.json`. The host's setup and configuration code supplies runtime endpoints and credentials.

## Native discovery and binding

Hosts discover the same `powercontext-project-context` entry through their native package declarations or Skill directories. DSH installs it under `$DSH_HOME/skills/powercontext-project-context`; Hermes uses `$HERMES_HOME/skills/powercontext-project-context`. Their plugins continue to provide tools and lifecycle integration independently of Skill discovery.

Installation refreshes the packaged guidance while preserving personal text outside managed regions and user-added files. Kimi stores its resolved configuration in `references/host.md`. If that file is missing, rerun `powercontext setup kimi-code` before changing the configuration.

When upgrading a Skill with a legacy resource manifest, setup rejects a newly packaged non-Markdown resource that would overwrite a different user file. The existing installation remains intact. Move the conflicting file to a separate name and rerun setup; identical files do not block the upgrade. Successful migration removes the obsolete manifest.

## Capabilities and authority

A shared reference describes a workflow, while the host adapter determines its available operations, Scope binding, and approval path. The Agent follows the active catalog's exact tool names, inputs, and return shapes. For example, OpenClaw does not expose Memory inventory or candidate Review even though those references ship with the shared Skill.

Host permissions and Server authorization govern each operation. Skill files describe how to use those operations. A missing Skill leaves independently sufficient tool guidance usable; a missing workflow or tool must be reported rather than recreated through another operation.

Contributor validation and model evaluation are described in the [guidance evaluation guide and record](integration-guidance-evaluation.md).
