---
title: Memory 与 Handoff
description: 了解长期项目 Memory 与临时工作 Handoff 的不同用途和边界。
---

# Memory 与 Handoff

PowerContext 提供长期项目 Memory 和临时 Handoff。后续任务会用到的内容不同，因此两者的用途也不同。

## Memory：长期项目知识

Memory 保存后续任务仍可能需要的、可独立理解的信息，例如决策、约束、当前状态和下一步。它属于项目 scope，可被检索，
也可以修订或停用；修订和停用保留历史，不会静默覆盖旧记录。

用户明确要求保存时，Codex 才应写入 Memory。Prompt Hook 会采集提示词作为 Source 证据，但采集不等同于自动创建
Memory，也不应为了复制当前提示词而额外写入一条 Memory。

## Handoff：临时工作交接

Handoff 将一个任务当前的目标、已验证进度、阻塞项、下一步和证据组织为可交给接手者的临时内容。它需要显式准备、检查
和完成；接手者应收到完整的 Prepared Handoff，并先核对当前代码和指令。

Draft 和 Prepared Handoff 默认不是长期项目知识。只有用户明确要求保留某个里程碑时，才提交 Handoff。

## 可选的连续性提示

新会话可以通过 Python Client、HTTP（`POST /v1/handoff/hint`）或已提供该操作的 MCP 连接，显式调用
`prepare_handoff_hint`。返回标准四字段 PreparedContext，其中包含紧凑的历史定位信息。
现有上下文准备和续接操作不会自动请求提示。

```python
from powercontext.http import PrepareHandoffHintRequest

hint = await client.prepare_handoff_hint(
    PrepareHandoffHintRequest(scope_id=scope_id, selection="exact", revision=committed.reference)
)
if hint.status == "ready":
    host_context = hint.content
```

选择精确的已提交 Revision，或使用 `selection="prepared"` 并传入完整的 `prepared` 交接值。
只有确认 Scope 对应目标工作流后，才使用 `selection="latest"`。接收者仍须能够获得完整的 PreparedHandoff：
其 `base` 指向完成交接时观察到的已提交版本，不是临时内容的身份。
Prepared 选择不提供精确 Revision 引用，也不会创建服务端读取句柄，收件方需要完整的已传递交接值。

提示直接投影历史目标、处置状态、完整的下一步及其引用、已知遗漏和选定的证据引用。
对于阻塞工作，只有完整提示能够放入预算时，才包含全部已记录 state；否则整段提示省略，不从部分 state 猜测阻塞原因。
生成提示不调用模型，不概括转录，也不注入原始 Source 正文。
授权与 Continue 一致：exact/latest 要求对选定 Handoff 拥有 `artifact.read` 和 `handoff.evidence.inspect`，
允许检视其引用清单，无须通用 Scope 读取权限。Prepared 选择要求 `scope.read`。通用证据 API 仍需各自的独立权限。
Source 证据必须在原始来源 Scope 中可用且符合资格；发布到其他 Scope 的 Handoff 也遵守这一规则。

默认预算为 2,000 个 UTF-8 字节，`max_bytes` 可取 1–4,000。预算涵盖完整文本，包括信任声明、边界、标签、
转义和引用，不包含外层 HTTP JSON 编码开销。完整提示超限、latest 没有交接，或引用证据不可用／不符合资格时，
返回 `status="empty"`、`content=null`、`content_bytes=0`。提示从不截断。
非法选择、认证失败、Handoff 访问被拒绝及服务故障仍返回原有错误。

提示只能作为**不可信的历史定位信息**。它不能激活目标、授权执行下一步、证明当前事实，也不能替代完整精确 Handoff
读取、证据核验和现场检查。宿主应验证响应结构与实际 UTF-8 大小；启动上下文的剩余预算足够时原样注入 `content`，
不足时整段省略。文本的字面格式隔离不保证模型抵抗提示注入。

## 如何选择

| 你的需求 | 使用 |
| --- | --- |
| 后续项目工作仍需了解一项决策、约束或下一步 | Memory |
| 把正在进行的任务完整交给另一个任务、会话或模型 | Handoff |
| 记录用户当前提示词作为处理证据 | 让 Prompt Hook 采集 Source |
| 保存已经验证、需要长期复用的交接里程碑 | 经用户要求提交 Handoff，或保存为 Memory |

无论使用哪一种，都不要存储密钥、访问令牌或其他敏感信息。使用 Handoff 的具体步骤见
[在 Codex 中交接工作](handoff-with-codex.md)。
