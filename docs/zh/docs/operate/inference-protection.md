---
title: 推理隐私保护
description: 在受控供应商传输边界检查最终模型请求与完整原始响应。
---

# 推理隐私保护

PowerContext 在自身控制的推理传输边界，于请求发出前检查输入、完整原始响应交付前检查输出。保护需要显式启用并验证目标配置，不增加数据库表。

## 检查保留原文

在 `enforcing` 模式下，获准请求的正文与受支持的完整响应正文原样通过，包括看起来像占位符的字面文本。
命中禁止值会直接拒绝，PowerContext 不会替换这些值后再发送请求。此保护不对提示词或响应做脱敏、
假名化替换或自动还原。

先替换数值或文本、事后还原，并不保证模型作出等价的任务决策。只在字段间传递不透明标识符，
与根据真实 IP 网段、路径归属或令牌声明进行推理不同。替代值可能改变模型选择操作时所依据的信息；
如果模型已选错操作，把路径还原为真实路径也无法纠正该操作。此保护不提供可逆脱敏机制，
也不保证替换内容后仍保持任务语义。

## 配置受支持的目标

隐私策略由可信部署配置，入口是 `InferenceConfig.protection`。默认 `disabled` 保持原行为。
`shadow`、`advisory` 只观察已经物化的请求，不检查响应、不阻止交付，不能宣传为防止泄露；
`enforcing` 在必需检查失败时阻断或暂缓。表示优化和复用选项不能关闭强制检查。

例如，为一个受控文本模型端点构造配置：

```python
from pydantic import SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime import InferenceConfig

inference = InferenceConfig(
    generation_model="openai:approved-model",
    generation_base_url="https://models.example/v1",
    generation_model_settings={"max_tokens": 1024},
    protection=InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr("synthetic-secret-for-a-local-test"),),
        targets={
            "coding": UpstreamProfile(
                endpoint="https://models.example/v1/chat/completions",
                model="approved-model",
                protocol="openai-chat",
                context_window_tokens=32768,
                max_output_tokens=1024,
                validated_utf8_token_bound=False,
            )
        },
    ),
)
```

`SecretStr` 只在展示配置值时隐藏原文，规则匹配仍使用真实配置值；它不会对请求或响应内容脱敏。

端点、模型和限制仅为示例。这个示例会暂缓发送：部署方需要先验证模型 tokenizer 和协议模板满足保守的
UTF-8 上界及配置的协议预留，再设置 `validated_utf8_token_bound=True`。字符数估算不是模型窗口的硬保证。
生成模型必须声明窗口、输出限制，最终请求也必须携带有效的正整数输出上限；超预算会暂缓。

Server 可通过 `POWERCONTEXT_SERVER_INFERENCE_PROTECTION` 设置相同对象的 JSON。
真实禁止值放在受保护的部署配置中，不写入提交的示例。若响应中的模型名不同于请求别名，
需要在 `response_models` 中明确列出获准别名。

`openai-responses` 目标可以把 `admit_encrypted_items` 设为 `["reasoning"]`、`["agent_message"]` 或两者，默认为空。
Codex 等客户端发送 `store: false` 并请求 `include: ["reasoning.encrypted_content"]`，因此响应带有之后请求会重放的加密
推理；子代理收到的任务也是 `agent_message` 中的加密部分。Core 无法检查这些值：

- 在 `before_release`，Core 放行响应中的加密推理。请求带有会话时，Core 返回一个 `receipt`，绑定该主体、Scope、目标和
  会话，有效期一小时。回执证明响应中的加密值；受检请求的工具定义中声明为 `"encrypted": true` 的函数调用参数的值，
  作为可交接值一并证明（Codex 以这种方式声明 `spawn_agent` 的任务）。其他参数值不记入回执。
- 在 `before_send`，位于准入位置的加密值只有由签发给本请求会话的未过期回执证明时才放行。准入位置是根级
  `reasoning` 条目的 `encrypted_content`，以及只含 `type: "encrypted_content"` 和 `encrypted_content` 两个字段的
  `agent_message` 内容部分。签发给本请求 `parent_conversation_id` 的回执只证明其中的可交接值，而且只用于
  `agent_message` 部分：子代理能收到自己的任务，但拿不到父会话的推理。否则请求以 `encrypted-origin-unverified` 被扣留。
- 带会话的 `before_send` 放行时，Core 还会返回一个 `receipt`，替换该请求提交的、属于本会话的回执。它保留这些回执中
  所有未过期的证明及其原有效期，因此重试或分叉的历史不会丢失证明；并为该请求携带的值、以及历史重放的函数调用中
  已证明的可交接值重新计算一小时有效期，因此父会话继续对话也不会让尚未交接的子代理任务失去证明。长会话每次提交的
  回执数因此保持不变；一个值在最后一次通过验证后的一小时内仍可放行。
- 回执绑定目标配置的 `endpoint` 和 `protocol`。修改其中任一项后，已签发的回执全部失效，因此加密值不会被放行到
  返回它的目标之外的地方。
- 一个回执最多容纳 1024 个值。放不下的检查以 `encrypted-receipt-capacity-exceeded` 被扣留，而不是让之后的请求失去
  证明。
- 其他位置的加密值，以及含有其他字段的 `agent_message` 部分，仍按 `content_unsupported` 处理。

放行只证明值的来源，不代表 Core 检查过其中的明文。PowerContext 网关按会话保存并提交回执。

规则检查配置的准确值，以及可选的有限格式：`email`、`private_key`、`api_token`。
检查覆盖原始 JSON、解码文本、工具参数和数字词法表示。重复 JSON 键、不透明图片/音频/文件、
未知请求字段、不完整输出和不支持的目标无法取得完整覆盖。
这不是通用 PII 分类器、语义 DLP 或工具执行授权。

有界的 `openai-responses` profile 要求显式提供布尔值 `store: false`；省略、null 或启用存储都会暂缓。
Responses 默认会存储响应，参见提供方的[数据保留约定](https://developers.openai.com/api/docs/guides/conversation-state#data-retention-for-model-responses)。
内置 Pydantic AI Responses 模型可在对应的 model settings 中设置 `openai_store: false`。
未展开的 `item_reference` 输入（包括省略 `type` 或将其设为 null 的引用）也会暂缓，因为无法检查其历史内容或把它计入窗口预算。
请提供受支持的完整文本和工具输入，而不是提供方的历史条目引用。
Anthropic 文档仅支持内联纯文本来源；PDF/base64、URL、Files API 引用和含图片的文档会暂缓。
Responses 图片生成工具以及不透明的 `image_generation_call` 结果，在输入和输出边界都会暂缓。

内置 OpenAI/Anthropic、Embedding、Rerank 路径把检查接在最终 HTTP transport，
原生 SDK 的格式转换先完成。观察模式保留提供方原有的重试策略；有显式预算约束的 worker 仍关闭 SDK 重试。
Enforcing 关闭 SDK 传输层自动重试；原有阶段的输出 schema 重试仍有界，
每次实际发送都要检查。必需隐私错误会穿透 advisory 决策降级，不会变成空提取结果或已消费的 Memory HOLD。
Memory 写入 gate 遇到这类拒绝时，flush 会在发布前失败，Source 窗口在重启后仍待处理；
修正策略或模型输出后可重试。
独立 Topic Memory worker 的每个生成阶段都继承同一部署策略；拒绝后不会发布或确认处理完成，Source 窗口继续待处理。
无法控制最终传输边界的自定义推理对象，在 enforcing 模式下明确拒绝注入。

## 失败时的供应商用量

供应商报告的输入、输出、缓存读取、缓存写入和推理 Token 计数与估算分开，缺失计数保持未知。响应被拒绝交付时仍可能产生费用；已知用量保留在异常中，并通过现有 Scope 用量记录器报告。缓存读取 Token 可能已包含在输入 Token 中，不能重复相加。

本地流式支持限定为单 choice 的有界 OpenAI Chat SSE，需要显式配置 `allow_streaming=true`。交付前缓冲完整响应，拼接并检查文本、工具参数、`reasoning` 与 `reasoning_content`，包括跨事件拆分的内容。工具索引只接受无符号 JSON 整数写法；`-0`、`0.0`、`0e0` 等歧义写法会在交付前暂缓。字节数、超时和并发限制仍然生效，其他流式协议暂缓交付。严格缓冲会延迟首段内容。

本机 HTTP/SSE 与独立 worker 测试使用受控响应验证边界，不代表外部供应商兼容性、隐藏路由或保留策略，也不证明模型质量。

## 缓存参数与成本边界

原生缓存参数仍由最终 provider adapter 处理，复用现有 Pydantic AI model settings。
显式 TTL/retention 需要受信目标 profile 批准；正文、目标或相关协议头变化后，必须重新检查完整请求。
受保护的 transport 会在 SDK 完成格式转换后检查每次实际发送。

稳定输入有利于保留前缀，但不保证缓存命中。供应商的 cache-read/write 计数描述计费口径，
不证明物理隔离或延迟下降。隐私保护不会创建供应商缓存，也无法验证已检查协议之外的保留行为。

成本应按同一个成功任务完整计入查询 Embedding、生成输入/输出、辅助调用、预热、失败和重试。
缺失 usage 或价格保留未知。输出上限参数不能证明供应商的硬计费上限；
实际报告用量即使超过请求参数，也必须完整保留。分别报告输入缩减、完整任务 Token/费用、质量和延迟。
已有 Atomic 证据正文去重和查询向量复用属于基线，不能算作推理隐私保护新增的收益。
