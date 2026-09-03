---
title: Configure Agent Skill targets
description: Register explicit local Codex or Claude Code Skill directories for discovery and managed publication.
---

# Configure Agent Skill targets

Register a local target before scanning external Skills or publishing an approved managed Skill from the Server UI.

## 1. Set the target configuration

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills",
      "allow_managed_publish": true
    }
  ]
}'
```

Use one unique `target_id` per directory. `agent_kind` is `codex` or `claude_code`; `installation_scope` is `user`,
`project`, or `plugin`. For Claude Code, use its Skill directory, for example `/srv/project/.claude/skills`.

## 2. Restart and inspect

Restart the Server, then use the Skills Library or the external Skill commands to scan the configured scope. The Server
only scans immediate package directories under listed targets. It does not infer a home directory or install a package.

`allow_managed_publish` is `false` by default. Set it to `true` only for a target where the authenticated Server may
publish an approved managed Skill. Publication cannot select an arbitrary path or overwrite a foreign or modified
package.

For the configuration schema and compatibility form, see [Configuration](../reference/configuration.md).
