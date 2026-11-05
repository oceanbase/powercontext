---
title: PowerContext 分层 Skill
description: 公共 Skill 资源、宿主绑定和原生分发方式。
---

# PowerContext 分层 Skill

PowerContext 分发一份公共流程 Skill，并为各宿主保留独立的绑定说明。入口 `powercontext-project-context` 将 Agent 指向当前请求需要的细节。每个安装包包含自己的资源，使用时不依赖源码检出目录或其他宿主的包。

系统指引、工具和 Skill 的职责见 [Agent 工具选择与结果报告](integration-guidance.md)。

## 入口与参考文件

`SKILL.md` 包含宿主可发现的名称和描述、意图路由，以及通用的结果和授权规则。参考文件分别说明 Scope 与 Memory、Work Handoff、候选 Review、Experience/Skills。Agent 需要流程细节时读取相关文件；工具描述已充分时可以直接调用。

`references/host.md` 说明安装宿主的 Scope 选择、解析命令和连接事实。各宿主通过这份文件，将公共流程接入自己的 Session 和工作区绑定方式。

## 公共源与发行包

公共 Skill 的维护源位于 `integrations/agent-plugin/powercontext/skills/powercontext-project-context/`。

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

各原生包包含完整的公共 Skill 目录副本，包括资产和元数据，并维护自己的 `references/host.md` 及宿主附加文件。

各宿主在原生包中维护少量 MCP 声明，包含各自的认证字段和变量插值。可移植 Agent Plugin 维护自己的 `mcp.json`。实际端点和凭据由宿主的 setup 与配置代码提供。

## 原生发现与绑定

宿主通过原生包声明或 Skill 目录发现统一的 `powercontext-project-context` 入口。DSH 将它安装到 `$DSH_HOME/skills/powercontext-project-context`，Hermes 使用 `$HERMES_HOME/skills/powercontext-project-context`。插件独立提供工具和生命周期集成。

安装会刷新打包指引，并保留受管理区域之外的个人文本和用户附加文件。Kimi 将已解析的配置写入 `references/host.md`。该文件缺失时，先重新执行 `powercontext setup kimi-code`，再修改配置。

升级带旧版资源清单的 Skill 时，如果新增的非 Markdown 包资源会覆盖内容不同的用户文件，setup 会拒绝升级并保留原有安装。请将冲突文件另存为其他名称后重新执行 setup；内容相同的文件不会阻止升级。迁移成功后会移除废弃的资源清单。

## 能力与权限

公共参考文件描述流程，宿主适配器决定可用操作、Scope 绑定和审批渠道。Agent 遵循当前目录中的准确工具名、输入和返回结构。例如，OpenClaw 随包提供公共参考文件，但不开放 Memory 盘点或候选 Review。

Skill 文件提供指引，执行权限仍由宿主和 Server 控制。Skill 缺失时，Agent 可以继续使用说明已充分的工具；流程或工具缺失时，应说明缺失项，而不是用其他操作模拟。

贡献者验证与模型评测见[指引验证方法与记录](integration-guidance-evaluation.md)。
