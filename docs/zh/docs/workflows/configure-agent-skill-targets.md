---
title: 将 Skill 安装到 Agent
description: 注册显式的本地 Codex 或 Claude Code Skill 目录，用于发现和 managed publication。
---

# 将 Skill 安装到 Agent

在扫描 external Skill，或以编程方式发布 approved managed Skill 前，先注册本地 target。

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

重启 Server 后，在**运行 Server 的主机**上执行扫描。`path` 是 Server 所在主机的路径；远程 Server 不会因为这个配置
而写入你本地电脑的 Skill 目录。先设置一个已有 Scope ID，再验证扫描结果：

```bash
export POWERCONTEXT_SCOPE_ID='已有的-scope-id'
powercontext external-skill scan --scope-id "$POWERCONTEXT_SCOPE_ID"
powercontext external-skill list --scope-id "$POWERCONTEXT_SCOPE_ID"
```

列表中的 package 必须来自 target 的直接子目录，并且包含可读取的 `SKILL.md`。要核对某个 package 的精确版本，先从
`list` 响应复制 `external_skill_id` 和 `fingerprint`，再执行：

```bash
powercontext external-skill resolve \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --fingerprint 'SHA256_FROM_LIST' \
  EXTERNAL_SKILL_ID
```

`resolve` 只读取并核对 host-local package，不安装或执行它。文件内容变化后 fingerprint 会失效；重新 scan、检查差异，
再决定是否导入。Server 只扫描列出的 target 下的直接 package 目录，不会推断 home 目录或安装 package。

`allow_managed_publish` 默认是 `false`。只在允许 authenticated Server 发布 approved managed Skill 的 target 上将其设为
`true`。发布不能选择任意路径，也不会覆盖外部或已被修改的 package。

如果 Server 与 Agent 不在同一主机，应使用已注册的 remote receiver target 和独立的 receiver 凭据完成发布；不要把远程
机器路径写成当前用户的本地路径，也不要把普通用户 Bearer token 当作 receiver 凭据。发布前先读取精确的 managed Skill
Revision，发布后检查 receiver 返回的 generation、tree digest 和本地冲突状态。

配置 schema 和兼容格式见[配置](../operate/configuration.md)。
