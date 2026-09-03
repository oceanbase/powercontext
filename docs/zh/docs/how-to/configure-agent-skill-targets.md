---
title: 配置 Agent Skill target
description: 注册显式的本地 Codex 或 Claude Code Skill 目录，用于发现和 managed publication。
---

# 配置 Agent Skill target

在扫描 external Skill，或从 Server UI 发布 approved managed Skill 前，先注册本地 target。

## 1. 设置 target 配置

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

每个目录使用唯一的 `target_id`。`agent_kind` 为 `codex` 或 `claude_code`；`installation_scope` 为 `user`、`project`
或 `plugin`。Claude Code 使用其 Skill 目录，例如 `/srv/project/.claude/skills`。

## 2. 重启并检查

重启 Server 后，在 Skills Library 中或使用 external Skill command 扫描已配置的 scope。Server 只扫描列出的 target
下的直接 package 目录，不会推断 home 目录或安装 package。

`allow_managed_publish` 默认是 `false`。只在允许 authenticated Server 发布 approved managed Skill 的 target 上将其设为
`true`。发布不能选择任意路径，也不会覆盖外部或已被修改的 package。

配置 schema 和兼容格式见[配置](../reference/configuration.md)。
