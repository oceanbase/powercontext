---
title: RFC 2127：推理预算与准入契约
description: 统一定义模型请求发送前的预算口径、生效值解析、准入判定与拒绝语义，覆盖各 Artifact 家族与 embedding。
---

- 提案名称：`inference_budget_contract`
- 起始日期：2026-11-10
- 跟踪 Issue：[oceanbase/powercontext#2127](https://github.com/oceanbase/powercontext/issues/2127)
- 相关 RFC：[RFC 0016](/zh/rfcs/0016_pydantic_ai_inference_integration)、[RFC 2086](/zh/rfcs/2086-core-inference-gates-and-optional-gateway)、
  [RFC 1417](/zh/rfcs/1417_topic_memory)、[RFC 1510](/zh/rfcs/1510-artifact-dreaming)、[RFC 1809](/zh/rfcs/1809-atomic-memory)、
  [RFC 1718](/zh/rfcs/1718_memory_capacity_contract)
- 相关工作：[#2128](https://github.com/oceanbase/powercontext/issues/2128)、[#2126](https://github.com/oceanbase/powercontext/issues/2126)、
  [#2129](https://github.com/oceanbase/powercontext/issues/2129)、[#2130](https://github.com/oceanbase/powercontext/issues/2130)、
  [#2074](https://github.com/oceanbase/powercontext/issues/2074)、[#1989](https://github.com/oceanbase/powercontext/issues/1989)

# 推理预算与准入契约

单次容量与累计工作量分别回答请求能否容纳、操作是否还允许执行。两者均必须成立。各 Family 拥有选材和发布语义，
共享推理层拥有计量、准入和安全拒绝，不复制 Topic/Dream 的持久账本。配置及逐入口覆盖见
[推理预算与准入](/zh/development/inference-budgets)。

## 完整请求边界

内置 SDK 的 HTTP request hook 检查实际序列化请求，发生在 provider 模板、结构化输出说明和协议转换之后，在网络
transport 之前。隐私保护关闭不影响预算检查。Model 参数快照只能作为选材估算，不能代替最终判定，也不通过重复调用
`prepare_request()` 猜测最终内容。

计量包括实际指令、输出 Schema、工具、证据、历史和重试反馈；排除未发送的输入 Schema 和非输入控制参数。未识别的
扩展输入保守计量；不可见的远程历史或不透明媒体不能假装已覆盖。进程内测试模型使用准备后的参数，只证明本地行为。

每次 outbound 都检查。受控 SDK 禁止将本地准入错误作为连接故障隐式重试；SDK 包装后保留原类型和安全事实。

## 容量、输出与执行配额

联合窗口的判据：

```text
estimated_complete_input <= min(input_allowance, context_window - output_reserve - safety_margin)
```

分离限制分别检查输入与输出，并在配置联合窗口时检查总量。输出预留必须覆盖实际发送的响应上限；Family/stage、operation
与部署明确上限取交集。未显式设置预留时从实际响应上限解析；普通默认值不覆盖 Topic 的专有输出策略。最终 body 再检查
输出字段，防止扩展配置覆盖。

`generation_operation_budgets` 的次数和时间通过 `generation_limits(operation)` 真正进入 `InferenceLimits`。次数以一次
Agent 执行为单位，Dream Run 与 Topic work budget 维持各自单位，不能把已有预留当作未分配余额重复拒绝或重复扣除。

`generation_model_context_window_tokens` 默认 125000，属于配置默认值，不保证任意模型的真实容量。自定义网关应显式声明
实际限制，不根据别名猜测。不支持完整计量的模型通路不宣称容量保护。独立 decision/rerank 容量需要单独声明。

## Family 不变量

- Atomic Memory 保留连续窗口、比较批次和已有窗口缩减。选材遵守业务与 request allowance；单条或最小比较输入放不下时拒绝。
- Profile 保留旧 Profile，只提交成功前缀。本地选材共用绝对截止时间；已发送请求或重试历史超限后不得重开生成。
- Experience 保留完整案例；显式 Experience/Skill 的所选证据必需。超长单条改为拒绝，不切片凑预算。
- Handoff 保留必要状态、下一步与引用；Prompt demonstrations 保留所请求的数量与完整动态 Schema。
- Topic 七个 stage 均执行完整请求检查，保留规划、归并、重试预留、同一权威 work budget、fence 和原子发布。
- Dream 保留显式证据、Run 调用次数、deadline、claim/attempt 和审核语义，不改变普通 Source cursor。

失败不能生成不完整成功结果，不能推进未处理输入。缩批、重试或重启不能重置既有累计额度。恢复策略属于 #2128；
超长内容的新投影或分片受 #2074 评估门槛约束；#1989 优化关闭时检查仍有效。

## Embedding

单条 token、每批 token、每批条数是独立限制。两个 adapter 在首次发送前验证全部计划批次，保留连续条数分批；超限就
拒绝，不隐式切开文本或改变向量顺序、数量、profile。generation 的窗口不能保护 embedding。

运行中的类型化推理错误保留已完成批次的已知 usage，缺失量保持未知。预算预留与 provider usage 分开。

## 错误与观测

`InferenceBudgetExceededError` 的 HTTP 状态为 422，错误码为 `inference_budget_exceeded`。MCP 和 Python Client 保留
相同 operation、stage、dimension、estimated_value、limit_value、unit、output_reserve_tokens、safety_margin_tokens、
estimator_id、estimator_version 和 phase。已发生调用由内部事实跟踪，不能用 usage 缺失推断没有发送。

HTTP/MCP 的 `inference.budget.rejected` 事件使用 `log_safely`，`unit=inference`、`budget_unit` 为计量单位。
字段通过 allowlist 进入 JSON、console 和 Server-owned Worker 日志。不记录正文、凭据或 provider 异常原文，日志失败不影响
业务，取消继续传播，不新增执行身份指标标签。日志不是账本。

## 验证

真实 SDK 配合本地 transport 检查模板、Schema、Unicode、输出参数、重试增长、并发和零 outbound。业务测试检查合法缩批、
单条拒绝、固定上下文溢出、cursor/Candidate 不变、累计额度不重置、失败 usage 和日志输出。真实 spawned Worker 验证进程
配置和日志通道；SQLite 不能代替真实 OceanBase，测试 provider 不能代替生产模型计量。
