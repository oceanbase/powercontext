---
title: 配置 OpenCode
description: 安装 PowerContext OpenCode 插件并控制其本地行为。
---

# 配置 OpenCode

## 安装或刷新插件

插件要求 OpenCode 1.18.21 或更新的 1.x 版本。插件应与 PowerContext Server、CLI 使用同一 Git ref：

```bash
powercontext setup opencode --source oceanbase/powercontext --ref master
```

该命令会全局注册原生插件，并把属于 PowerContext 的 `project-context` Skill 安装到 OpenCode 配置目录。
如果同名 Skill 不是 PowerContext 安装的，命令会停止并保留原文件。本地 checkout 同样可以使用：

```bash
powercontext setup opencode --source .
```

启动 Server，再打开新的 OpenCode 会话：

```bash
powercontext server run
opencode
```

## 理解插件行为

每个正常用户回合，插件通过 `POST /v1/context/prepare` 获取一个有界上下文，同时通过
`POST /v1/sources/content` 独立采集符合条件的提示词。召回内容会标记为不可信历史，并在模型分发前临时
注入，不会写入 OpenCode 会话记录。

具名 `pc_*` 工具提供精选的 Memory、Handoff、Experience、Skill 和只读 Candidate 操作。持久化变更前
OpenCode 会要求确认；Candidate 的批准和拒绝仍由用户通过 CLI 或 Dashboard 显式执行。

## 配置连接

启动 OpenCode 前设置环境变量：

```bash
export POWERCONTEXT_OPENCODE_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=true
opencode
```

每个 OpenCode session 按显式的 `POWERCONTEXT_OPENCODE_SCOPE_ID`、持久 Session binding、workspace binding、
Server 默认 Scope 的顺序解析。解析结果会固定到 Session，resume 时继续使用同一边界。显式变量只能指向一个
已存在且由 Server 管理的 Scope。

启用可选 Bearer 鉴权时，将完整请求头写入 `POWERCONTEXT_OPENCODE_AUTHORIZATION`，不要把凭据写入 URL。
非 loopback 地址必须使用 HTTPS。不应采集当前提示词时，设置 `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false`。

## 验证安装

```bash
powercontext doctor
powercontext doctor opencode
```

集成专用 doctor 会检查 OpenCode 版本、解析后的插件配置和 PowerContext 所有的 Skill。Server 状态由默认
doctor 单独检查；Server 不可用不会阻止 OpenCode 正常工作。
