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
授权，以及“显式 `PowerContextScope` 优先于环境配置”的规则，见 [LangChain](langchain.md#配置连接与-scope)。

## 采集

`auto_capture` 默认为 `False`。开启后，主 agent 成功结束时会通过 `/v1/sources/content` 采集最新用户消息和最终
非空回答；正常的 Source 到 Memory 提取稍后由调度器执行。失败、取消、中断或恢复的运行不会被采集；重试也不会为
同一轮生成第二个 Source，因为 Source 标识由 scope 与该轮文本派生。

## 失败行为

召回和采集都是尽力而为。Server 不可用、响应非法或请求校验失败都不会替换模型响应，也不会中断 agent。HTTP 401
和 403 会以 error 级别记录一次，且不包含内容或 token 值；其他失败以 debug 级别记录。

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
