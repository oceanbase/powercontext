---
title: Deep Agents
description: 为 Deep Agents agent 及其声明式 subagent 增加有界 PowerContext 召回。
status: community
---

# Deep Agents

`community`

`create_powercontext_deep_agent` 把 [Deep Agents](https://github.com/langchain-ai/deepagents) 的
`create_deep_agent` agent 连接到单独运行的 PowerContext Server。它只是 `powercontext-langchain` 中
`PowerContextMiddleware` 的薄适配层：召回和可选采集仍由 middleware 完成，适配层只负责让内置的 `general-purpose`
subagent 和每个声明式 subagent 也拿到它。

Deep Agents 会自行组装 middleware 栈，并把每个声明式 subagent 编译成独立图，因此传给 `create_deep_agent` 的
middleware 只包住主 agent 的模型调用。经由 `context=` 传入的 `PowerContextScope` 仍会到达 subagent，因为 Deep Agents
会把父级 runtime context 转发给 `task` 工具。

## 安装

安装适配层包；它依赖 `powercontext-langchain` 与 `deepagents`。

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

```bash
uv pip install ./integrations/deepagents
```

## 创建 agent

```python
from powercontext_langchain import PowerContextScope
from powercontext_deepagents import create_powercontext_deep_agent

agent = create_powercontext_deep_agent(
    model,
    tools=application_tools,
    system_prompt="You are a deployment assistant.",
    subagents=[
        {
            "name": "researcher",
            "description": "Reads the repository and reports deployment steps.",
            "system_prompt": "You research deployment steps.",
        }
    ],
    auto_capture=True,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "How should we deploy this service?"}]},
    context=PowerContextScope(),
)
```

其余关键字参数会原样转发给 `create_deep_agent`，包括 `backend`、`checkpointer`、`store`、`interrupt_on` 和
`response_format`。

## 适配层做了什么

- 主 agent 获得一个 `PowerContextMiddleware(auto_capture=...)`。如果 `middleware=` 中已有
  `PowerContextMiddleware`，则复用该实例，不再重复添加。
- 内置的 `general-purpose` subagent 会获得一个 `PowerContextMiddleware(auto_capture=False)`；否则 Deep Agents 会
  在不带召回的情况下创建该 subagent。若 harness profile 禁用了它（`GeneralPurposeSubagentProfile(enabled=False)`），
  适配层会尊重该配置（包括使用 Deep Agents 已弃用默认模型的情况），不会因为接入 PowerContext 而重新开启应用已关闭的
  委派能力。
- 每个声明式 subagent 的 `middleware` 列表末尾追加一个 `PowerContextMiddleware(auto_capture=False)`。已有的
  `PowerContextMiddleware(auto_capture=True)` 会被替换为等价的 recall-only 副本，因此被委派的轮次绝不会被采集。
  适配层会复制规格与 middleware，不会修改调用方传入的对象。
- `context_schema` 默认取 `PowerContextScope`。传入其他值时原样转发，此时该 agent 的 PowerContext 回退到环境变量
  配置。

## Subagent

召回与采集刻意不对称：

- **召回**同时运行在主 agent 和每个 subagent（包括内置的 `general-purpose`）中，使用 `context=` 中的 scope。
  被委派的任务因此能看到与父级相同的有界历史。
- **采集**只保留在主 agent。subagent 以 `auto_capture=False` 运行，因此一轮委派只会由父级记录一次，即使同一轮
  中运行了多个 subagent。

编译型 subagent（`{"name": ..., "runnable": ...}`）和远程 `AsyncSubAgent` 规格自己持有图，适配层原样返回，不
会介入。需要时请在这些图中显式加入 `PowerContextMiddleware`。

召回内容仍然只修改当前 `ModelRequest`，不会进入 agent state、checkpointer 或 subagent 的对话记录，也不会在重复
模型调用或上下文压缩之间累积。

## 配置连接与 scope

适配层复用 LangChain 集成的 Client、Settings 和 Scope。`POWERCONTEXT_LANGCHAIN_*` 变量、token 处理、远程 Server
授权，以及“显式 `PowerContextScope` 优先于环境配置”的规则，见 [LangChain](langchain.md#配置连接和-scope)。

## 采集

`auto_capture` 默认为 `False`。开启后，主 agent 成功结束时会通过 `/v1/sources/content` 采集最新用户消息和最终
非空回答；正常的 Source 到 Memory 提取稍后由调度器执行。失败、取消、中断或恢复的运行不会被采集；重试也不会为
同一轮生成第二个 Source，因为 Source 标识由 scope 与该轮文本派生。

适配层通过 `PowerContextMiddleware` 继承 LangChain 的[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。
主 agent 和每个 subagent 的召回 query 中，识别到的凭据都会替换为 `[REDACTED]`；用户消息或助手回复中识别到凭据时，
整轮跳过采集。识别是尽力而为的，因此无法保证采集的内容不含机密。

## 失败行为

召回和采集都是尽力而为。Server 不可用、响应非法或请求校验失败都不会替换模型响应，也不会中断 agent。HTTP 401
和 403 会以 error 级别记录一次，且不包含内容或 token 值；其他失败以 debug 级别记录。

## 把提示词时上下文交给 gateway

agent 的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on`
后，gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
在每种隐私模式下都是如此：Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。
`create_powercontext_deep_agent` 会给主 agent、内置的 `general-purpose` subagent 和每个声明式 subagent 加入
`PowerContextMiddleware`，而 middleware 会向每次模型调用添加这样的块。适配层没有关闭召回的选项，因此模型经过
gateway 的 agent 请改用 Deep Agents 自带的 `create_deep_agent` 创建，并从 `middleware=` 和各 subagent 定义中移除
`PowerContextMiddleware`。middleware 从不把召回内容保存到 agent state，因此已有的 thread 可以继续经过 gateway。

模型客户端由应用持有。让它指向 gateway 的 Responses 路由，并在 `X-PowerContext-Conversation-ID` 请求头中标识会话：

```python
import os

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI


def gateway_agent(thread_id: str):
    model = ChatOpenAI(
        model="approved-model",
        base_url="http://127.0.0.1:8787/gateway/v1",
        api_key=os.environ["PC_GATEWAY_KEY"],
        use_responses_api=True,
        default_headers={"X-PowerContext-Conversation-ID": thread_id},
    )
    return create_deep_agent(model, tools=application_tools, subagents=subagents)
```

不使用适配层时，由 gateway 接管适配层原来做的事：

- 召回：gateway 为它承载的每个模型请求准备上下文。没有单独设置 `model` 的声明式 subagent 使用主 agent 的模型，
  因此它的请求带有同一个请求头；它的用户消息是任务描述，所以 gateway 为该任务准备上下文。单独设置了 `model` 的
  subagent 和编译好的 subagent graph，只有当它们的模型也指向 gateway 时才能得到上下文。
- 采集：`auto_capture` 不再生效。gateway 把每个放行的、不调用工具的响应采集为一轮，
  范围从请求中最后一条用户消息到该响应。因此 subagent 的最终回答会作为单独的一轮被采集，与主 agent 的一轮并存；
  “只由主 agent 采集”的适配层规则不再适用。
- 工具：本适配层和 `powercontext-langchain` 都不提供 Memory 工具，因此没有需要保留或移除的工具。

`PC_GATEWAY_KEY` 保存 gateway 的 `PC_GATEWAY_API_KEY` 值。这个请求头标识会话：每个会话使用一个稳定的 ID，例如
checkpointer 的 thread ID，长度为 1～256 个可见 ASCII 字符；不要让所有会话共用一个 ID。组合部署（`PC_GATEWAY_CORE_UDS`）
在一个会话中每条用户消息准备一次，并把结果重放给这条消息的工具循环步骤。没有这个请求头时，它根据请求的历史开头推导会话：
除非设置 `use_previous_response_id=True`，`ChatOpenAI` 每次调用都发送 thread 的完整历史；
以 system 消息和一条用户消息开头的历史即可推导。没有这个请求头时，subagent 任务从新的历史开始，
因此有自己推导出的会话；`mode: "fork"` 的 subagent 带着父 agent 的历史，与父 agent 共用推导出的会话。请求头仍能区分开头相同的会话，
并为采集和前缀提取提供在 gateway 重启后保持不变的会话标识；推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
独立运行的 gateway（`PC_GATEWAY_CORE_BASE`）只有设置 `PC_GATEWAY_INJECTION=on` 才会注入。
见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)。

请使用 Responses 路由（`use_responses_api=True`）。在这个路由上，gateway 以非流式方式调用模型，采集通过检查的响应，
并以流式重放给发送流式请求的客户端。在 Chat Completions 路由上，enforcing 模式的 Core 会暂缓流式响应，gateway
返回 HTTP 400。非 enforcing 模式下，gateway 原样放行流式响应；设置 `PC_GATEWAY_SEDIMENT=on` 后，还会从中采集完成的一轮。

配置 Core 和 gateway 时还需要：

- 除非每个请求都满足 enforcing 规则，否则请让 Core 运行在非 enforcing 隐私模式（`disabled`、`shadow` 或 `advisory`）：
  enforcing 模式的 Core 会暂缓没有 `store: false`、或没有不超过目标 token 预算的输出上限的 Responses 请求，gateway 返回
  HTTP 400。gateway 在启动时发现 Core 的模式，修改模式后请重启 gateway。
- 此处使用 Responses 路由，应将 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 设为 Core 中 endpoint、`openai-responses`
  协议和模型均匹配的 target profile。未设置时该路由返回 HTTP 501 `responses_route_disabled`。
  将 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为该目标的服务商 Key，并设置 `PC_GATEWAY_SEDIMENT=on`。
  不要设置 `PC_GATEWAY_UPSTREAM_URL*`；这些旧变量会导致启动失败。
- 即使监听回环地址也必须设置非空 `PC_GATEWAY_API_KEY`，并将 `PC_GATEWAY_CORE_TOKEN` 设为已认证的服务身份。
  Core 访问控制必须为 `enforced`；在所选 Scope 上授予 `gateway.user`，并在 `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID`
  对应的 Server resource 上授予 `server.observer`（默认 deployment ID 为 `powercontext`）。本指南开启了采集，
  因此还要授予 `scope.contributor`（包含 `scope.read`）来替代 `scope.viewer`。
  详见[Gateway 认证与目标要求](../operate/deploy-server.md#网关模型目标)。
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话。请把它设为适配层使用过的 Scope，
  使 gateway 能召回之前采集的 Memory。

## 可运行示例

`integrations/deepagents/examples/quickstart.py` 针对一个 Server 和 OpenAI 兼容的模型端点跑一轮采集。
`integrations/deepagents/examples/full_flow.py` 会进一步把采集到的轮次 flush 成 Memory，等待提取结果可检索，再在新
会话里提出一个改写后的问题。完整流程要求 Server 配置生成模型与 embedding 模型；quickstart 只依赖召回。

## 已验证版本

21 个确定性测试基于脚本模型和进程内 PowerContext Server（SQLite）运行，覆盖：中间件注入、声明式 subagent 与内置
`general-purpose` subagent 的召回、对自带 `auto_capture=True` 的 subagent 做采集归一化、单次采集归属、流式
`astream`、中断/恢复、重复模型调用不累积上下文，以及不同运行之间的 Scope 隔离。

真实模型验证使用 PowerContext Server 0.1.dev1（HTTP + SQLite）、`deepagents` 0.7.39、`langchain` 1.5.0、
`langchain-core` 1.8.1、`langchain-openai` 1.8.0，以及 vLLM 0.29.0 上的 Qwen2.5-0.5B-Instruct：

- 召回内容进入 system prompt，回答遵循了已存储的规则；
- `auto_capture=True` 为该轮新增了一个 Source；
- 同一 Scope 下的后续轮次再次召回了该规则。

流式与中断/恢复也用同一真实模型验证：`astream` 产出 17 个 chunk 并写入 1 个 Source；`interrupt_on` 的运行在工具
执行前暂停，`Command(resume={"decisions": [{"type": "approve"}]})` 之后恰好写入 1 个 Source。

自动的 Source 到 Memory 提取与语义召回用两个 vLLM 服务的模型完成了验证：生成用 Qwen3-4B，embedding 用
bge-small-en-v1.5（`--runner pooling`）。被采集的轮次经 flush 提取为 Memory，改写后的查询（"What deployment command
should we use?"）返回了非空的 prepared context，agent 在新会话中召回了它。只用 Qwen2.5-0.5B 或 Qwen3-0.6B 时提取
结果为不可用或为空，说明这两个模型不足以满足提取 schema。

JavaScript 与 Managed Deep Agents 未验证：JavaScript 的 `deepagents` 包存在（1.16.5），但 PowerContext 没有
JavaScript 适配层；Managed Deep Agents 是 LangSmith 的托管部署。
