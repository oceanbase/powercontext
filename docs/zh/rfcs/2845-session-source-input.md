---
title: "RFC 2845：Session Source 输入"
description: "为实时对话轮次和历史对话补录定义有角色的 Source 输入。"
---

- Proposal Name: `session_source_input`
- Start Date: 2026-11-26
- RFC PR: [oceanbase/powercontext#2845](https://github.com/oceanbase/powercontext/pull/2845)

# Summary

扩展现有 Content Source 采集接口，使其除了接收普通文本，也能接收按顺序排列的 User 和 Assistant 文本消息。实时采集在 Assistant 最终回复完成后提交一个用户请求及其回复；历史补录可以一次提交多轮。一份提交保存为一条不可变 Source，后续处理保留消息角色和顺序。普通文本行为保持不变。

# Motivation

Coding Agent 的用户输入经常依赖上下文，例如“你改错了”。单独保存这句话，无法判断它针对 Assistant 的哪项回复。保存完整轮次能够为记忆提取提供上下文，历史补录则可以一次提供更长的对话。Assistant 回复是有用的观察材料，但它可能包含计划、推断或错误，不能当作用户确认的事实。

基线实现的 `POST /v1/sources/content` 接收一个字符串 `content` 和可选 `metadata`；文本证据投影向处理器提供内容和元数据，但没有结构化的消息角色。Source 以身份去重，已有身份的内容或元数据不同时会冲突。采集成功不代表记忆已经提取。相关讨论见 [Discussion #2756](https://github.com/oceanbase/powercontext/discussions/2756)。

# Guide-level explanation

## 实时轮次

调用方在收到 Assistant 最终文本回复后提交：

```json
{
  "scope_id": "scope-1",
  "source_id": "session-123-turn-1",
  "session": {
    "session_id": "session-123",
    "messages": [
      {"role": "user", "content": "修改登录模块的超时处理"},
      {"role": "assistant", "content": "已完成修改，并补充测试。"}
    ]
  },
  "metadata": {}
}
```

每个轮次使用不同的稳定 `source_id`。调用方重试同一轮次时复用原 ID 和完全相同的请求。`session_id` 用于关联同一会话的多个 Source，不是授权边界。

## 历史补录

一次提交的 `messages` 可以包含多轮 User 和 Assistant 对话。它作为一条 Source 保存，不拆分成每条消息。历史补录和实时 Source 允许部分内容重复，服务端不跨 Source 去重。每个补录批次使用独立、稳定的 `source_id`。

# Reference-level explanation

## 接口和验证

扩展 `POST /v1/sources/content` 和批量接口中的每个 Source 项，新增 `session` 对象。请求必须且只能提供 `content` 或 `session` 之一；省略 `session` 的现有请求保持不变。`session` 含非空 `session_id` 和按时间顺序排列的非空 `messages`；每条消息仅包含 `role`（`user` 或 `assistant`）与非空文本 `content`。不强制角色交替或要求首尾角色，历史片段可以从任意位置开始。未知字段、空白文本、未知角色和两种输入同时提供均返回 422。批量接口沿用全有或全无的事务语义。

单条消息最多 200,000 字符，单个 Session 最多 512 条消息；所有消息内容合计最多 200,000 字符。超限返回 422，不截断、不静默拆分；调用方自行按消息边界分段，并为每段分配新 `source_id`。这些是提议的初始上限，须在实现前结合存储和模型预算验证。响应继续使用 202、Source 引用和 journal position；重复请求和冲突沿用现有规则。

## 身份和持久化

保留现有 Content Source 的身份空间；不复用 `source_type` 作为对话类型参数。保存一个带明确版本和类型的结构化 payload，完整保留 `session_id`、角色、顺序和文本。普通 Content Source 按现有格式读写；Session 必须能经持久化、读取、重启和重放后无损还原。不同 `source_id` 的内容允许重叠；同一身份的内容或 metadata 不同返回 409。无损结构是权威数据，面向模型的文本表示是派生投影，不得反向当作权威原文。

本 RFC 不要求新增数据库表或列；实现前须验证现有 Source payload 编码、schema 注册、恢复及历史读取是否支持扩展。若必须改变表结构，应另行说明表、字段、存量数据影响与回滚方案并获得确认，不能通过修改已发布迁移掩盖兼容性问题。

## 处理与安全边界

普通文本沿用现有处理路径。Session 由角色感知投影提供给现有生成管线：按顺序呈现消息，角色通过结构边界与消息正文区分，不能仅依赖可由正文伪造的 `User:` 文本。处理提示应区分用户指令、Assistant 的推断和已经验证的结果；不得因为 Assistant 声称“已完成”就直接认定事实成立。源内容始终是不可信数据，不是处理器指令。保留 Source 引用作为生成结果的证据，不保证每次提取都会产生记忆。

捕获成功表示 Source 已持久保存并进入现有处理机制，不表示模型处理、索引或召回成功。处理失败遵循现有重试与状态机制；实现必须验证 Session 不会被静默跳过，且普通 Source 不发生回归。第一版不新增 Agent Memory 或 Agent Experience，也不改变召回 API。

调用方在采集前对两种角色执行适用的隐私过滤和密钥脱敏；服务端不能假设所有客户端都执行过脱敏。日志、指标和错误响应不得包含消息正文或凭据。Session 保留、删除和访问控制沿用 Source 的现有策略，不因 `session_id` 获得额外授权。

## 接入和交付

实时接入需要在宿主能够确定最终回复时提交一个 User/Assistant 对；不采集中间工具调用、工具结果或流式片段。没有最终文本的中断或失败轮次不作为完整轮次提交。Source 提交失败不得改变模型回复，也不能在未说明重试和丢失语义时宣称可靠交付。各宿主对最终回复事件的支持不同，必须逐个验证；服务端接口就绪不等于插件或网关已接入。历史导入由调用方显式提供消息并分段。

## 验收

- 原有单条及批量 `content` 请求、响应、重试和冲突行为保持不变。
- 单轮和多轮 Session 经 API、存储、重启及支持的读取路径后保留原始角色、顺序和文本。
- 同 ID 同内容重试不新增记录；同 ID 不同内容或元数据冲突；不同 ID 的部分重复允许保存。
- 无效角色、空白、两种输入同时存在、超限及批量冲突有明确结果；批量失败不留下部分写入。
- 实际处理入口能区分 User 与 Assistant，且对正文中的伪造角色标记不改变真实角色；验证失败和重试行为。
- 对接一个真实支持最终回复的宿主，验证成功、工具续写、中断和采集失败；未接入或仅使用模拟输入的宿主不得标记为已验证。
- 使用标注的对话数据对比仅 User 与 User/Assistant 提取，检查主体归因、错误事实、重复和成本；不以“接收成功”代替质量验证。

# Drawbacks

保存 Assistant 回复会增加隐私暴露、存储和模型处理成本。长历史可能超出预算，分段可能损失上下文。不同 Source 的重复内容可能生成重复候选。角色标签能帮助归因，但不能保证模型提取正确。

# Rationale and alternatives

相比把对话拼进 `content`，结构化输入可明确角色、顺序与格式，避免把用户文本中的标签误当作消息边界。相比每条消息一条 Source，本方案把一个轮次或历史片段作为证据单元，更容易保留局部上下文；代价是无法独立寻址其中每条消息。相比可变 Session，追加不可变 Source 可复用已有重试和冲突语义，但不会自动提供完整会话重建或跨片段去重。新增独立接口也可行，但当前输入仍属于 Content Source，扩展现有接口减少入口分裂。

# Prior art

mem0 支持从对话消息提取记忆，也提供不经推理的写入路径；OpenViking 区分会话原文、归档和异步派生记忆；Memobase 缓冲聊天后生成画像和事件；mem9 支持会话保存和事实提取。它们的留存、失败和检索语义不同，不能把某一次接收直接等同于原文可靠保存或可搜索。本方案借鉴原文与派生记忆分离，而不照搬其存储或召回接口。

# Unresolved questions

- 实现前需核实现有 Source 扩展、投影与持久化注册是否能够无损读取新 payload，并确定处理器的角色感知表示和测试基线。
- 初始大小上限与宿主最终回复事件的可用性需要通过实测验证。
- 采集失败的宿主持久重试机制和跨重启交付保证，须按具体接入单独设计；本 RFC 不假设所有宿主都支持。

# Future possibilities

可以另行评估会话历史参与查询构造、按会话读取或检索原文、工具消息，以及新增 Agent Memory 或 Experience。上述能力不属于本 RFC 的验收范围。
