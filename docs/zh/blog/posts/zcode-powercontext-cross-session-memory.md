---
slug: zcode-powercontext-cross-session-memory
title: "新开一个会话，不必再从头说起"
description: "从 AgentMeld 项目自动生成的两条工作约定出发，记录我给 ZCode 接入 PowerContext 时，对提示词采集、记忆召回与工作交接的处理。"
cover: assets/blog/zcode-powercontext-cross-session-memory/cover.png
cover_alt: 一条蓝色记忆带连接两个工作空间，让项目上下文跨会话延续。
date: 2026-10-27
authors:
  - jackie-cqz
categories:
  - 工程实践
draft: false
pin: false
source: https://jackie-cqz.github.io/zh/posts/memory-across-sessions/
---

*ZCode × PowerContext：跨会话记忆的接入实践*

之前参与 Agent 研发时，我接触过记忆获取与存储相关的工作，也参考过 [Hermes Agent 的记忆设计](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)。它把用户偏好和沟通方式保存在 `USER.md`，把环境信息、项目约定等记在 `MEMORY.md`，在新会话开始时加载。这让我对长期偏好有了更具体的认识：有些事情，确实不该每次都重新说明。

最近，组里的个人桌面 Agent 研发推进得很快。我负责的多个 feature 分散在不同分支，有些还涉及不同的 harness。代码能切回去，已经讨论过的接口约定、方案取舍和任务进度，却经常要在新会话里重新补一遍。

所以，给 ZCode 接入 PowerContext 时，我最想解决的是：已经说清楚的项目背景，能不能在下次工作时接着用？ZCode 处理眼前的任务，PowerContext 保存可复用的材料，插件负责把两边接进正常的对话流程。

我主要做了 ZCode 侧的插件接入，包括 Hook 提示词采集与上下文准备、原生 MCP 和 Skill 连接，以及 Scope 工作流、安装诊断和验收。下面先看一次实际的自动记忆生成，再展开这些选择，以及如何在新会话中检查召回。

## 从 AgentMeld 的两条工作约定开始

这次验收用的是我自己的 [AgentMeld](https://github.com/jackie-cqz/AgentMeld) 项目，一个面向 AI 编程团队的多 Agent 协作工作空间。当时，我在对话里补充了两项后续开发要遵守的习惯，发送的原文是：

> 示例提示词
>
> AgentMeld 项目以后采用规划、多个 worker 实施、多个 review 的工作方式。这个项目做一点提交一次。

随后，我在自动生成的记忆中验证了这两条约定：

- **工作方式**：先规划，再由多个 worker 实施，最后由多个 review 审查。
- **提交习惯**：做一点提交一次，也就是小步提交。

**验收流程示意**

1. **会话 A · 说明工作习惯**

   在普通对话中，说明 AgentMeld 的协作方式和小步提交习惯。

2. **Server · 自动生成记忆**

   后台从这次说明中生成记忆，已验证其中包含工作方式和提交习惯两条约定。

3. **新会话 · 检查召回**

   接下来在相同 Scope 下开启新任务，只问项目的工作方式与提交习惯，核对召回材料和来源。

当时新增并确认的是这两条自动生成的记忆。它们很贴近日常开发：一个项目怎样组织实施和审查，代码应该按什么节奏提交，都是后续任务还会用到的信息。新会话怎样取回它们，可以沿着后面的步骤继续检查。

这也是我希望记忆系统留下来的东西：聊天里已经说清楚的工作习惯，能成为有来处、可读取的项目材料，让下一次任务有机会接着用。

## 先把值得记住的事留下来

项目里需要延续的内容，有时只是一条约定，有时是一项决定背后的理由，还有时是做到一半的工作状态。把它们保存下来时，也需要保留来处，方便以后核对。

PowerContext 用下面几种材料组织这些信息：

| 概念 | 在项目中有什么用 |
| --- | --- |
| Source | 保存原始材料，例如用户提交的一条项目说明 |
| Memory | 保存可以继续复用的项目知识，并关联来源 |
| Scope | 组织不同项目或工作范围内的上下文 |
| Handoff | 记录进行中任务的目标、进度、阻塞、下一步与证据 |

前面那句 AgentMeld 的项目说明，可以先被采集为 Source；后台处理再从中提取可复用的工作方式和提交习惯，后面的任务根据当前问题取回相关内容。

如果需要接续的是一项做到一半的工作，仅保存一条长期约定还不够。接手者还需要知道已经改过什么、验证过什么，以及哪些部分尚未完成，这时就可以使用 Handoff。

这些数据由 PowerContext Server 保存。ZCode 插件承担宿主适配，让它们能出现在正常的工作流程里。

## 让记忆接进日常工作

我希望用户仍然可以像平常一样和 Agent 讨论项目：需要主动查找或保存时有工具可用，开始处理一条新消息时，也有机会带回相关的旧材料。

ZCode 提供的插件、生命周期 Hook、MCP 和 Skill 等入口，让这条使用路径有了落点。这次集成主要使用三部分：

| 入口 | 职责 |
| --- | --- |
| Hook | 在用户提交提示词等生命周期节点准备上下文、采集材料 |
| MCP | 让模型主动搜索、保存记忆或处理工作交接 |
| Skill | 指导模型什么时候使用这些能力，以及怎样处理结果 |

整体链路可以画成这样：

```text
用户
  ↓
ZCode
  ├─ 生命周期 Hook ── HTTP ─────┐
  ├─ 原生 MCP 客户端 ───────────┤
  └─ Skill：指导模型使用工具     │
                              ↓
                     PowerContext Server
                       ├─ Source
                       ├─ Memory
                       ├─ Scope
                       └─ Handoff
```

插件安装在 ZCode 一侧，Server 是另一个需要单独启动的进程。

这里我做的一个选择，是把存储、检索和后台处理留在 PowerContext Server，ZCode 插件专注于宿主适配。这样，记忆能力的演进和对话入口的适配有各自的位置，插件也不需要再维护一套独立的记忆存储。

对应的插件代码位于 [`integrations/zcode/plugins/powercontext`](https://github.com/oceanbase/powercontext/tree/master/integrations/zcode/plugins/powercontext)，主要结构如下：

```text
powercontext/
├─ .zcode-plugin/
├─ hooks/
├─ scripts/
├─ shared/
├─ skills/
└─ .mcp.json
```

Hook 声明、MCP 连接和 Skill 都随插件一起安装，用户不需要分别拼装。

## 这次说过的话，下次怎样找回来

用户正常提交一条消息时，插件就有机会把已有材料带回来，也把这次输入留作后续处理的来源。实现中用到的关键入口是 `UserPromptSubmit`。

用户提交消息、模型开始分析之前，Hook 会尝试解析当前 Scope，再准备相关上下文，并独立采集当前提示词。

```text
用户提交消息
    ↓
解析当前 Scope
    ↓
请求 PreparedContext
    ↓
采集当前提示词为 Source
    ↓
把可用上下文输出给 ZCode，继续当前任务
```

这里有两件不同的工作。

**召回已有材料。** 插件向 Server 请求 PreparedContext，由 Server 选择并渲染相关内容。当前 ZCode 接入使用最多 8000 字节的有界上下文，避免每轮都把历史材料完整搬进来。

**保存当前输入。** 当前提示词被采集为 Source，再由 Server 按配置处理。先保留来源、再提取值得复用的内容，让普通对话里的项目说明也有机会成为后续任务的材料。

因此，一次完整的自动记忆过程会跨过几个阶段：

```text
普通项目说明
    → Source
    → 后台提取 Memory
    → 后续任务请求 PreparedContext
    → ZCode 将上下文交给模型
```

这也解释了为什么安装插件后，还需要配置 Server 的后台能力。ZCode 的模型负责当前对话，Server 的 Generation 负责材料提取与生成，两边分别配置模型连接。

另一个选择是让自动 Hook 尽量少打断正常工作：记忆服务暂时不可用时，对话可以继续；需要主动搜索或保存时，再通过 MCP 工具拿到明确结果。我也补了运行观察和诊断入口，方便区分采集、准备上下文和连接出了什么问题。

### Scope 决定这些材料属于哪个项目

跨会话复用的前提，是采集和召回落到同一个 Scope。

ZCode 插件会按显式 Scope、session binding、workspace binding 和 Server 默认 Scope 的顺序解析。工作区绑定使用规范化路径生成定位键，再映射到实际 Scope；路径本身不是 Scope ID。

同一个项目开新任务时，应继续解析到相同 Scope。长期使用多个项目时，建立稳定的 workspace binding，会比把所有材料都放在默认范围内更容易管理。

## 把这条链路跑起来

下面以 Windows PowerShell 为例，从同一份 PowerContext 源码安装 Server 和插件，方便保持接口版本一致。需要提前准备 Git、uv、Python 3.11+，以及能被 ZCode 找到的 Node.js 24+。

### 安装 PowerContext

```powershell
git clone https://github.com/oceanbase/powercontext.git
Set-Location powercontext

uv tool install --force ".[cli,server]"
```

### 配置并启动 Server

先生成一份供 Server 使用的本地配置：

```powershell
New-Item -ItemType Directory -Force '.powercontext' | Out-Null

powercontext config init --language zh `
  --output '.powercontext/server.env'
```

在向导中选择存储、监听地址和模型连接。首次本地体验可以使用 SQLite 与 `127.0.0.1:8000`。

如果要体验普通对话自动生成 Memory，需要配置 Generation，并启用 Source 处理和 Memory 调度。相关调度配置包括：

```ini
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
```

两个调度分别负责处理 Source 窗口和调度 Memory 生成。这里的 60 秒表示检查间隔，实际生成还受队列和模型调用影响。

显式保存记忆和全文检索可以先不配置 Generation；语义检索则还需要相应的 Embedding 配置。模型及后台处理说明见[配置模型与完整记忆](https://github.com/oceanbase/powercontext/blob/master/docs/zh/docs/get-started/configure-models.md)。

配置文件可能包含凭据，只供 Server 本地读取，不提交到 Git。完成后校验并启动：

```powershell
powercontext config validate `
  --env-file '.powercontext/server.env'

powercontext server run `
  --env-file '.powercontext/server.env'
```

保持这个终端运行。

### 安装 ZCode 插件

完全退出 ZCode，包括系统托盘中的进程。另开 PowerShell，把下面的路径替换成刚才的仓库绝对路径：

```powershell
$pcRepo = 'C:\path\to\powercontext'

powercontext setup zcode `
  --source $pcRepo `
  --server-url 'http://127.0.0.1:8000'

powercontext doctor zcode
```

安装器会复制插件，并将其注册到 ZCode 的用户配置中，保留其他模型和插件设置。

`doctor` 用于检查安装声明与只读连接。重新打开 ZCode 后，还要在真实任务中确认原生 MCP 工具已经加载。

## 换个会话，看看它还记不记得

要看看记忆有没有帮上忙，可以从一条具体的项目约定开始：先在普通对话中说明，再换一个没有带上旧聊天记录的新任务，看看还能不能找回来。

先在 ZCode 中确认能通过 PowerContext MCP 调用 `list_scopes`，找到本次使用的真实 Scope。

要沿着前面的 AgentMeld 案例检查，可以直接发送当时的项目说明，不额外要求模型调用记忆写入工具：

> 示例提示词
>
> AgentMeld 项目以后采用规划、多个 worker 实施、多个 review 的工作方式。这个项目做一点提交一次。

这句话包含两项长期约定：开发时怎样组织协作，以及代码按什么节奏提交。

接着检查三件事：

- 当前输入是否被采集到正确 Scope 的 Source。
- 后台生成的 Memory 是否保留了“规划 → 多个 worker 实施 → 多个 review 审查”和“小步提交”两条约定。
- Memory 的来源是否能对应到本次 Source。

确认生成之后，在同一项目和 Scope 下开启全新任务，只问：

> 示例提示词
>
> AgentMeld 项目的工作方式和提交习惯是什么？请根据已有项目上下文回答，没有依据就说不知道。

新问题里不再带上那两条约定，也不粘贴上一轮聊天记录。核对时，同时查看本轮 PreparedContext 和宿主输入，确认回答对应到这次保存的材料。

可以沿着 Source、Memory、PreparedContext 和宿主输入逐层检查，让最终回答对应到具体材料。独立测试目录、对象引用和检查步骤见[桌面验收文档](https://github.com/oceanbase/powercontext/blob/master/integrations/zcode/acceptance/desktop.md)。

## 做到一半的工作，也要交接清楚

项目约定能帮 Agent 理解背景，而接续一项做到一半的工作，还需要更具体的状态：改了哪些文件，哪些检查已经做过，哪里仍然卡着。

这些进度可以组织成 Handoff，留给下一次任务读取。

例如，接口迁移只完成了一部分，接下来准备换任务继续，可以明确告诉 Agent：

> 示例提示词
>
> 请检查当前代码和已经执行的验证，将这次工作提交为持久交接里程碑。记录目标、已完成事项、未完成事项、阻塞、真实验证结果和下一步，并返回保存后的精确引用。

接手时就能围绕这些材料继续：

```text
目标：完成接口迁移
已完成：已改动的模块与对应文件
待处理：尚未迁移的调用点
验证：实际运行过的命令及结果
下一步：优先处理的阻塞与建议顺序
证据：相关文件、Source 和产物引用
```

持久交接会经历准备与提交。提交成功后，用返回的准确版本继续工作，接手者就有了明确的读取入口。

### 使用时，留意这几个边界

- 自动采集先保存 Source，Memory 生成还依赖后台配置、提取策略与处理进度；完成保存要以实际返回和读回结果为准。
- 跨会话复用依赖正确的 Scope。不同分支是否共享材料，应按任务关系安排；其他 harness 也需要各自的接入，本文展示的是 ZCode。
- 记忆生成和新会话召回要分别检查。新任务读取记忆或 Handoff 后，仍要检查当前代码和验证状态。

## 让下一次工作有个清楚的起点

最开始让我在意这件事的，是那些反复补充背景的时刻。分支换了，会话换了，一项工作却还在继续；如果接口约定、方案取舍和上次的进度都能找到来处，接下来就容易展开得多。

这次接入里，Hook、MCP、Skill、Scope 和 Handoff 最终都服务于这件事：让当前任务需要的材料有机会留下来，也让下一次工作有办法找到它们。安装诊断、运行观察和验收，则帮助我确认这条路径有没有真的走通。

相关实现参考：

[PR #1752](https://github.com/oceanbase/powercontext/pull/1752) · [PR #1814](https://github.com/oceanbase/powercontext/pull/1814) · [PR #1850](https://github.com/oceanbase/powercontext/pull/1850)

我希望以后再打开一个新任务时，可以少说一遍“我先给你介绍一下这个项目”，更快地回到“我们接着上次的工作往下做”。
