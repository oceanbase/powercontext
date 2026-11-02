---
title: 接入 Agent
description: 选择插件、MCP、Skills、Agent Host 或 Python 框架接入。
---

# 接入 Agent

先选择接入方式，再选择宿主或框架：

- **Plugins** 将宿主需要的配置、工具、Hook 或 Skill 打包。专用插件可接入宿主生命周期；通用 Agent Plugin 提供 MCP 配置和 Skill。
- **MCP** 在 Server 的 `/mcp` 暴露 Agent 工具。MCP 本身不提供自动 Prompt 采集、召回 Hook 或 Skill 安装。
- **Skills** 提供 Agent 可发现的工作指令。安装 Skill 不等于启动 Server，也不会增加宿主工具权限。
  在兼容宿主中使用[通用 Agent Plugin](agent-plugin.md)，或[导出受管理 Skill](../workflows/configure-agent-skill-targets.md)。

## Agent Hosts

9 个 Agent Host 按 `official`（PowerContext 项目维护）和 `community`（社区贡献）标注。
`official` 不代表宿主厂商背书。

| Agent Host | 维护归属 | 接入方式 |
| --- | --- | --- |
| [Codex](codex.md) | `official` | Prompt Hook + MCP + Skill |
| [Claude Code](claude-code.md) | `community` | Prompt Hook + MCP + Skill |
| [DeepSeek Harness](dsh.md) | `community` | HTTP plugin + pc_* + /pc |
| [Hermes](hermes.md) | `community` | MemoryProvider + /pc |
| [OpenClaw](openclaw.md) | `community` | Memory plugin + lifecycle hooks |
| [OpenCode](opencode.md) | `community` | HTTP plugin + pc_* |
| [Pi Coding Agent](pi.md) | `community` | Extension + pc_* + /pc |
| [WorkBuddy](workbuddy.md) | `community` | Prompt Hook + MCP + Skill |
| [ZCode](zcode.md) | `community` | Prompt Hook + MCP + Skill |

所有集成都连接独立运行的 Server。安装、连接设置、认证和诊断步骤见上表中的各 Agent 文档。

远程地址配置见[连接远程 Server](../operate/connect-remote-server.md)：PowerContext 客户端默认允许环回 HTTP，
非环回 HTTP 需要显式同意；宿主原生 MCP 策略独立生效。

需要交互选择多个宿主时，可运行：

```bash
powercontext setup
```

使用与 Server 相同的 ref。`powercontext setup codex dsh` 会安装、配置并诊断两个 Agent。更新已安装连接时使用 `powercontext config agent codex dsh`，不会重新安装或启用插件；原生要求见各 Agent 文档。

## 实验性 MCP 接入

[Cindy](cindy.md)提供统一 Scope 的 Ghost 召回工具，以及可选的自动召回和原始 Prompt 采集，
通过独立原生 MCP 连接访问完整 Server 工具目录。用户无需安装 DSH，目前仍用于评估，不计入已验证 Agent Host 列表。

## 自动 prompt 隐私策略

以下 prompt 适配器默认开启捕获。更新插件时保留已有捕获偏好；本策略增加凭据检查，不改变捕获默认值。

| 宿主 | 自动输入边界 | 关闭捕获 |
| --- | --- | --- |
| Claude Code | `UserPromptSubmit` 文本 | `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false` 或 setup `--no-capture-prompts` |
| Codex | `UserPromptSubmit` 文本 | `POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false` |
| WorkBuddy | 宿主提交的拼接 prompt 文本 | `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS=false` |
| OpenCode | 用户文本部分，排除 synthetic 部分 | `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false` |
| Pi | `before_agent_start` prompt | `POWERCONTEXT_PI_CAPTURE_PROMPTS=false` |
| ZCode | `UserPromptSubmit` 文本 | `POWERCONTEXT_ZCODE_CAPTURE_PROMPTS=false` |
| Kimi Code（实验性） | `UserPromptSubmit` 中的文本部分 | setup `--no-capture-prompts` |

自动捕获前，适配器识别常见凭据赋值（API/access key 和 token、client/private key、secret、password、
passphrase、authorization、cookie）、至少八个 token 字符的 Bearer 值、常见服务商/GitHub/Slack token
形状、包含未闭合情况的私钥块，以及当前 Server 凭据在输入中的精确出现。这是尽力识别：未知格式、混淆或
没有可识别标记的秘密仍可能通过。保守匹配也会跳过 `password=example` 之类示例；“token budgets”这样的
普通词语不属于赋值。共享合成用例位于 `tests/fixtures/automatic_prompt_privacy.json`，它定义已测试示例，
不能证明任意凭据都能被识别。

Authorization、Proxy-Authorization、Cookie 和 Set-Cookie 赋值会先匹配完整的 header 值，包含鉴权方案、
Cookie 属性和折行续接内容，再匹配普通赋值。这种保守处理也会从自动查询中移除同一 header 行的其他说明文字。

识别到凭据时整条跳过捕获，不会修改 Source 正文后继续沿用原事件身份或回执。接受的事件保留既有宿主文本
规范化和身份语义，不同事件与重试继续遵守各自宿主契约。本地诊断或已配置的观测只报告有界原因
`sensitive_content`（Kimi 为 `sensitive`），不包含拒绝的文本。Pi 通过配置的诊断输出报告，`off` 表示关闭输出。

自动只读准备与捕获独立：构建有界查询前，识别到的片段会替换为 `[REDACTED]`，关闭捕获时也如此。查询可能
因此降低针对性，但仍可返回并交付有效历史上下文。变化仅作用于自动查询，不改写存储证据或显式工具调用。
鉴权请求头仍按连接要求，将配置的凭据发送给所选 Server。

如果 prompt 不允许离开宿主，需要同时关闭自动捕获和宿主的自动准备 hook/extension。仅关闭捕获仍允许只读
请求，检测器也不是保密保证。不要在 prompt 中放秘密，应配置可信端点并审查运维访问权限。本策略不扫描
存量 Source、不改写不可变 revision、不防御 prompt injection，也不在 Server 上对全部显式 Source 写入分类。

## Python Agent 框架

这 3 个适配器均为 `community`，安装到应用环境中，不使用 `powercontext setup <host>`：

| 框架 | 接入方式 | 当前发布状态 |
| --- | --- | --- |
| [Pydantic AI](pydantic-ai.md) | Toolset：Memory 读写和上下文 | `experimental` |
| [LangChain](langchain.md) | Middleware：上下文和可选 Source 采集 | `master_only` |
| [LangGraph](langgraph.md) | Recall hook 和 Memory 工具 | `master_only` |

## 评测集成

[Bub 仅用于评测](evaluation.md)，不计入 Agent Host 或 Python Agent 框架支持列表。

## 能力与发布状态

[能力矩阵](capabilities.md)由 `integrations/capabilities.toml` 生成。
9 个 Agent Host 和 Bub 标为 `master_only`；Pydantic AI 标为 `experimental`。
`released` 表示可从指定发布 tag 获取，`master_only` 表示当前实现尚未发布，`experimental` 不承诺稳定性。
这些标签与维护归属、Minimal / Recommended / Full 能力等级分别表达不同信息。

Server 和集成使用相同的发布 tag 或 commit。平台要求见[安装与运行](../get-started/install-and-run.md)：
Windows 支持为 `experimental`，这不表示所有宿主和可选后端都支持 Windows。
