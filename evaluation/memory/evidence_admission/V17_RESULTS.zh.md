# v17 最小线上诊断结果

真实执行两次请求后命中 Unicode 精确字面量停止条件。唯一最终决策仍为 **insufficient_evidence**。
精确字面量样本通过，Unicode 第一条响应遗漏必需 CRLF；其余两条请求未运行，没有重试或扩大矩阵。
机器结果见 [result.json](results/v17-live/result.json)。

## 执行范围与身份

执行前冻结提交为 `887fb5e2`，基线为本地评估分支 `58931cfd`；不是当前 master 适配验证。
实际调用发生在 2026-11-05（北京时间）。配置与门槛见 [V17_PROTOCOL.zh.md](V17_PROTOCOL.zh.md)，
完整依赖身份见 [freeze.json](results/v17-live/freeze.json)。

本次没有改变 v16 的编码、单元选择、源范围、指令、schema、原子事实合同或估算预算。
只对新的最小诊断清单明确允许 exact-literals 一次、unicode-boundaries 三次，总计最多四次请求。
v16 原始每例一次门槛失败、三集离线数据和 insufficient_evidence 结论保持原样。
继承离线证据前逐项校验旧代码、数据、SDK、Unicode 数据库及三份比较文件的哈希；
没有声称在 v17 下重新执行过 1620 条离线矩阵。

预检和每次真实请求均重新捕获、持久化、关闭并读取真实 Source，构造实际提取输入。
四个计划请求先经实际 SDK/Pipeline 与 MockTransport 预检；真实请求的 body 哈希须匹配预检。
实际生成经 `AtomicMemoryGenerationPipeline.extract()`，没有直接绕过 Pipeline 调用模型。
没有执行 reconciliation、Memory 发布、生产游标推进或任何生产行为修改。

## 真实结果

| 请求 | HTTP / Pipeline | 候选数 | 精确字面量 | 原子事实合格保留 | 结果 |
| --- | --- | --- | --- | --- | --- |
| exact-literals-0 | 200 / 返回 | 4 | **4/4** | **4/4** | 诊断通过 |
| unicode-boundaries-0 | 200 / 返回 | 2 | **2/3** | **1/2** | **CRLF 遗漏，停止** |
| unicode-boundaries-1 | 未运行 | 未测 | 未测 | 未测 | 前一响应质量失败 |
| unicode-boundaries-2 | 未运行 | 未测 | 未测 | 未测 | 前一响应质量失败 |

精确字面量样本使用四个 copy 单元，生成四个独立候选；字节检查全部通过。
Unicode 的 emoji 和分解形式组合字符保留，必需 CRLF 没有出现在输出中。
两条语义断言各有对应候选，未合并为复合候选；其中一条因缺失精确字面量，不计为合格保留，
所以冻结 scorer 为原子事实 1/2，而不是声称其全部语义内容消失。

原始响应模板只引用了 emoji 与组合字符单元，没有引用 CRLF。
该请求实际含 110 个可复制的 CRLF 单元，输出使用零个。
本地渲染只是连接模型选择的 parts，没有删除换行；缺失已发生于模型返回模板中。
“可逐字节复制”与“模型一定选择复制”仍是不同的能力。

绑定 case、输入、支持范围与输出哈希的 assistant 诊断中，两例均未发现无支持候选、错误归因或非原子候选。
这些语义计数没有冒充独立人工验收；字面量遗漏则由确定性字节检查直接证明。
候选支持解析成功也不证明模型实际上使用了某个具体片段。

## 预算口径与真实用量

| 请求 | 计划估算输入 | SDK 消息文本估算 | provider 输入 | provider 输出 |
| --- | --- | --- | --- | --- |
| exact-literals-0 | 3795 | 3755 | 3162 | 191 |
| unicode-boundaries-0 | 3947 | 3907 | **5679** | 115 |

实际请求合计输入 **8841**、输出 **306** token，usage 完整，未知用量请求为零。
两次均为 finish_reason=stop，没有 reasoning 内容或重试。
SDK/Pipeline 内部经过时间约为 3.56 秒和 4.56 秒，不包含前置 Source 重建时间。

Unicode 实报输入超过 4096，说明当前字符估算不能作为 provider 真实 token 的硬上限。
本合同冻结的是既有字符估算预算，账单 token 另行记录；不能据此追改本轮门槛，也不能宣称真实输入小于 4K。
这一偏差作为额外限制保留，实际停止原因是冻结的 CRLF 字面量失败。
费用保护采用每次完整 1M 上下文加 2048 输出的上界预留，不依赖上述字符估算，所以没有突破费用控制。

按峰时、全部输入缓存未命中的冻结费率，本轮费用上界 **¥0.020130**，不是账户账单。
按用户确认的 v9 起累计口径：此前已知上界 ¥0.090520，累计 **¥0.110650**，
条件余量 **¥4.889350**。更早十三份未知 usage 独立保留，不按零计，也不计入这次已批准预算。
会话因质量失败停止，余量不构成继续发送请求的授权。

请求模型仍为 deepseek-v4-flash，响应模型字段为 deepseek-flash。
[官方说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)表明旧别名当前路由至 V4.1 Flash。
费率在冻结时及调用前分别核验；请求别名、响应模型字段或相同 fingerprint 均不证明固定物理模型快照。

## 回放和工程检查

两份原始 HTTP 响应都通过 MockTransport、实际 SDK/Pipeline 和重新构造的 Source 输入回放。
请求哈希、输出哈希、模板哈希及完整评分与线上一致；新增 provider 调用为零。
Unicode 的 2/3 字面量和 1 次 literal_mismatch 原样复现，未替换输出、改标签或自动补 CRLF。
回放结果与逐字面量哈希检查见 [recorded-replay.json](results/v17-live/recorded-replay.json)。

新增 18 项回归通过；提交冻结后完整无模型回归为 **508 passed**、1935 warnings，Python 3.11.15。
`make check`、完整 hooks、Ruff、格式检查及整个 evaluation 类型检查通过。
wheel/sdist 包含全部六个 v17 文件并与冻结源码逐字节匹配。
受限环境停滞的尝试、首次 JSON 格式自动修正及冻结前中断的回归单独记录，不计为通过。
其他 Python 版本和根包全量测试没有为本次 evaluation 增量重跑；旧工程验证保留其原有身份和范围。
详见 [validation.json](results/v17-live/validation.json)。

## 交付与剩余覆盖

原始请求、响应、模型模板、输出和审阅保存在仓库外私有目录，目录 0700、原始审计文件 0600。
密钥只在请求进程内读取，没有输出或持久化。公开文件只包含标签、配置、计数与哈希，
文件索引和扫描记录见 [artifact-index.json](results/v17-live/artifact-index.json)。

在匹配冻结源码与 SDK 的环境，私有会话可只读查看状态或生成新的汇总文件：

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 status --session /PRIVATE/v17-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 report --session /PRIVATE/v17-live --output /PRIVATE/new-report.json
```

quality_failed 会话禁止再次发送；不能重建会话绕过停止条件来补跑剩余两条。
原始响应缺失时无法重复 SDK 回放，哈希不能代替缺失内容；公开汇总不包含这些私有内容。
沿用 v10 的评分数值与绑定规则；本次运行状态、实际提交冻结及验收限制由 v17 单独记录。

本次只覆盖候选 arm 的两个样本、一个预算和一次重放，Unicode 只执行其计划中的第一条请求。
不是正式各 arm 联合比较，也没有独立关键语义审阅、当前 master 适配或远端取消结论。
字节保真截至提取输出；生产 Memory 的 NFC 与首尾 strip 合同没有改变，没有 Memory 写入。
无法据单次 ASCII 成功认定普遍保真，也无法据本次 Unicode 失败推断所有有界策略均无价值。

如果继续研究，应先保留本次失败，围绕必需 CRLF 的可执行输出约束和字符估算/真实 tokenizer 偏差
另立协议，先做最小回放与离线验证；不直接继续未运行请求或扩大正式矩阵。
当前不形成生产后续契约，不授权投影、注册表、API 或生产截断变更。
