# v12 真实 Pipeline 首例失败结果

总体决策仍为 `insufficient_evidence`。新执行器及合同已在本地提交 `2aac4022` 冻结，未推送。
实际 HEAD blob 核验、提交后的 SDK 模拟预检、324 个窗口重放及 1296 个历史样本校验均通过。
随后仅发送一个真实请求，首例失败后停止，未重试、未发送 Unicode 样本或扩展正式数据集。

## 实现与本地验证

独立 v12 执行器经过真实 `AtomicMemoryGenerationPipeline.extract()`，保留候选选择和 v10 指令/编码/评分，
增加实际 Git blob 冻结检查、逐例审批范围、费用预留、私有审计和显式审阅门槛。
每次最多一个 HTTP 请求；状态不明、取消、未知用量、协议或质量失败均阻止自动续跑。
受控模拟输出验证 copy、emoji、组合字符和 CRLF；不作为真实模型质量证据。

完整回归 388 项通过，新增回归 17 项通过。模块与根项目类型检查、两套锁文件、全量 hooks 和
wheel/sdist 九个源码/合同文件核验通过。全评估 Ruff 0.16 仍有 13 项保留文件诊断，整个 evaluation
类型检查仍有一项 LongMemEval 既有能力错误；未重跑其他 Python 版本。
提交后窗口对照六个历史拒绝全部恢复，成本合规覆盖仍为 171/495，候选输入覆盖保持 495/495。

## 真实请求与失败分类

样本为 `synthetic-v1 / exact-literals / bounded-fragments-v3 / 4096 / repetition=0`。
请求模型别名为 `deepseek-v4-flash`，返回模型为 `deepseek-flash`；单响应不能证明锁定历史物理模型。
实际请求正文哈希与提交后 SDK 预检相同，真实路径在 SDK 生成前进入 Pipeline。

| 项目 | 观测值 |
| --- | --- |
| 实际请求 / 重试 | 1 / 0 |
| HTTP 状态 | 200 |
| finish_reason | length |
| 真实输入 token | 3166，未超过 4096 |
| 真实输出 token | 2048，全部为 reasoning_tokens |
| 最终 content 长度 | 0 |
| 私有原始响应捕获 | 完整，保留原始字节和哈希 |
| 审计分类 | response / response_incomplete |
| 语义、原子性、字面量与 copy 评分 | 未运行，无结构化输出 |
| Unicode 样本 | 未运行，首例失败后停止 |

本次观测到的直接原因是输出配额全部用于思考，未生成最终 JSON。HTTP 200 与完整响应捕获不代表生成成功。
无候选时保留率记为 null，不将其当作已评分的 0/4，也不认定独立事实拆分策略有效或无效。
旧 v9 原子性失败、旧整句 scorer 未决状态及历史 13 个不可恢复响应的未知根因均不改判。

原始审计字段 `pipeline_executed=false` 在当前代码中表示尚未成功返回，并非调用入口事件。
本次实际调用路径进入了 Pipeline，在 SDK 拒绝不完整响应时失败。报告分别记录调用路径与成功返回状态，
不改写已冻结审计字段。后续合同宜将 started/completed 分开，避免名称歧义。

## 最小回放与费用

同一原始响应经 MockTransport、实际 SDK 和 Pipeline 离线回放，错误分类、finish_reason、用量及响应哈希
与真实失败一致。回放新增 provider 请求为 0；其中用量来自历史响应，不是新增计费。

按冻结峰时全未命中缓存费率，本次已知费用上界为 ¥0.022716。与已批准预算内的 v9 两次请求上界
¥0.030314 合计 ¥0.053030；¥5 条件总预算剩余 ¥4.946970。没有未知用量或未解决预留。
这些数值是条件费率上界，实际账单金额未读取。没有为本次验证增加预算。

[脱敏机器结果](results/v12-live/summary.json)保存提交身份、请求与响应哈希、实际用量和停止状态；
[离线失败回放](results/v12-live/failure-replay.json)保留可审计分类。
`COMPLETE.json` 仅表示失败审计交付完整，不表示生成或 issue 验收通过。
私有输入、请求正文、完整响应及 reasoning 保存在仓库外，未写入公开结果。

## 剩余验收与下一步

本轮建立了提交冻结后的真实 Pipeline 请求证据，但原子性、copy、Unicode 和正式全组覆盖仍未验证。
下一步应另立新执行协议，显式关闭 thinking、保持 2048 输出上限及冻结评分，先完成 length/空 content
回放和新请求参数的离线回归。提交冻结与 SDK/费用预检通过后，才重新验证同一个最小样本。
首例通过并审阅后再考虑 Unicode，不直接扩展尚未运行的数据集。不要在本会话改参数、增加重试或改判失败。

逐项差异见 [验收状态](ACCEPTANCE.zh.md)，原执行合同见 [V12_PROTOCOL.zh.md](V12_PROTOCOL.zh.md)。
