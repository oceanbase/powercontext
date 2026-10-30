---
title: 上下文复用与最终准入
description: 复用精确的 Atomic Memory 上下文，并按当前授权检查最终请求与响应。
---

# 上下文复用与最终准入

准备一次上下文，保留短期复用证明，在每次模型请求发送前和完整响应交付前验证实际字节。使用公开准入 API 前，先按[推理隐私保护](inference-protection.md)配置 enforcing 目标。调用方负责模型执行与响应交付；检查本身不会调用模型，也不会授予模型调用额度。

## 准备一次，每次复用都验证

`POST /v1/context/prepare` 默认仍返回 `schema`、`status`、`content`、`content_bytes`；
既有 `explain` 选项仍控制可选诊断。增加 `include_reuse: true` 后返回：

```json
{
  "schema": "powercontext.prepared-context-with-reuse.v1",
  "context": {
    "schema": "powercontext.prepared-context.v1",
    "status": "ready",
    "content": "<完整的历史上下文>",
    "content_bytes": 26
  },
  "reuse": {
    "schema": "powercontext.context-reuse.v1",
    "constraints": {
      "preserve": ["content", "citations", "trust", "order"],
      "refresh_on": ["permission_change", "source_ineligible", "context_boundary"]
    },
    "expires_at": "2026-10-26T12:05:00Z",
    "binding": "<不透明的加密绑定>"
  }
}
```

以上尖括号内容、字节数和时间只是文档示例，不是运行时脱敏格式。实际使用返回的准确正文和绑定。
Python Client 返回 `PreparedContext` 或 `PreparedContextWithReuse`，读取字段前先判断结果类型。
进程内沿用 `runtime.context.for_scope(scope_id).prepare(request)`；
`validate_reuse` 检查保留的证明，不做检索或 Embedding。嵌入式应用自行提供当前可信授权上下文。

首个复用 profile 支持单 Scope、实际选中的 Atomic Memory，不支持 context references 或代码。
它记录最终选中的当前 revision、生命周期版本和完整有界历史依赖。合并结果的旧祖先在领域规则允许时仍可作为证据。
根记忆被修订或遗忘、访问撤销、引用范围变化、绑定过期、Source 不再符合资格，都会停止旧值复用。
请求复用时实际选中了其他 family，会明确拒绝；普通 prepare 保持原支持范围。

绑定经过认证加密，期限五分钟，最多 64 个依赖、八层祖先。绑定不是访问权限。
Server 用既有签名秘密派生独立用途的密钥，并绑定持久 Server 身份；同部署的多个进程需要共享该秘密。
进程内 Runtime 默认使用临时密钥。没有新增 prompt 或响应缓存表。

## 检查真正的最终载荷

统一调用 `POST /v1/context/check`，扁平操作名 `check_context`，分两个时机：

1. `before_send`：原生协议渲染和缓存设置完成后，写出任何模型请求之前。
2. `before_release`：收齐完整原始响应后，交付正文、工具参数或错误内容之前。

两个阶段都沿用根级 `scope_id`、当前认证/委托和当前 Scope、资料权限。
`scope.read` 不授予模型付费或工具执行权限；请求字段不能降低部署策略。

版本化载体保存原始 JSON 文本，不接受重新拼装的摘要对象：

```json
{
  "scope_id": "<已有 Scope>",
  "phase": "before_send",
  "target_profile": "coding",
  "payload": {
    "schema": "powercontext.model-json-payload.v1",
    "headers": {"content-type": "application/json"},
    "body": "{\"model\":\"approved-model\",\"max_tokens\":1024,\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}"
  },
  "prepared_contexts": []
}
```

复用时，每个历史准备块都提交 binding 和 `powercontext.model-json-text-location.v1` 位置：
JSON pointer，以及解码后 UTF-8 文本的 `start_byte`、`end_byte` 半开区间。
必须定位准确的完整块，保留历史资料信任包装和顺序，并位于受支持的 user 消息文本中。
提升到 system/developer、修改正文、重叠或乱序位置、漏报准备块均会拒绝。
要求准备块的目标不能静默降级为不带绑定的纯检查。

准入检查不会脱敏，也不会还原占位符。替换准备块中的姓名、路径、地址或其他值会改变正文，
使原来的精确复用证明失效；替换可逆也不等于仍是原准备值。
网关不能在请求获准后脱敏，或在响应获准后还原值，再把原批准用于变化后的字节。
变化后的载荷需要单独准入；本 API 不提供占位符映射，也不授予逆转隐私变换的权限。

只有 `allow` 且 `coverage.complete=true` 才能继续对应动作。
`block`、`withhold` 虽然返回 HTTP 200，但都禁止执行；只判断 HTTP 成功是不够的。
载体或位置不合法返回 422，权限失败返回 401/403，正文超限返回 413。
必需检查不可用时不发送、不交付；未配置 enforcing 的公开检查只返回暂缓。

发送批准绑定准确载荷、目标、策略、身份和 Scope。60 秒发送期限与五分钟响应关联期限分开：
把发送批准的 `binding` 作为 `before_release.request_binding`，提交完整响应载体及
`completion: "complete"`，再次验证当前策略、身份和准备依赖。
交付批准不能用于发送新请求，关联期限也不能延长发送期限；绑定不提供恰好一次执行保证。

公开 carrier 当前支持完整 JSON。本地 protected transport 另外支持单 choice 的 OpenAI Chat SSE，
需要显式设置 `allow_streaming=true`，其他流式 profile 暂缓发送。
交付前按字节、时间、并发上限缓冲完整流；不能把 SSE 摘要包装成 JSON 并声称是完整原始响应。
Strict 缓冲会推迟首段内容到达时间。

HTTP over UDS 与远端 HTTP(S) 使用相同 DTO。UDS 不替代 bearer 认证。
`examples/context_protection/external_executor.py` 给出了外部执行器示例：
用检查结果控制一次准确 JSON 请求和响应，落实字节上限、取消、期限及本次调用的绑定关联。
执行侧必须保留批准的准确字节、遵守期限、拒绝后停止、传播取消，并记录失败或不确定发送的用量。
本功能不安装具体第三方网关插件或 Rust 网关。
执行器必须在本地保留每次调用对应的 request binding；无状态 Core 无法证明任意外部执行器实际发送了哪次获准请求。
对于不透明上游网关，保护只到实际观察的端点，不能证明其内部路由、保留策略、账号/区域物理隔离或隐藏重试。

## 能力与成本边界

调用方负责保留和刷新复用元数据。在有效期内复用准备结果可以避免重复检索与组装，但不会创建服务端上下文缓存，也不会管理模型提供方的 KV cache。核算任务成本时，应同时比较重复准备、精确复用和已有的一次准备基线。API 验证当前授权与精确载荷身份，无法证明外部执行器实际发送或交付了获准字节。

响应获准不代表模型正确理解了证据或选择了正确动作。字面模板标记仍是普通内容；
API 不会推测缺失的值，也不会证明这些标记可直接作为执行参数。
应用在运行工具或持久化生成的事实之前，仍需验证动作、目标和授权。
模型选错了操作，随后还原真实路径也不能让该操作变得正确。
