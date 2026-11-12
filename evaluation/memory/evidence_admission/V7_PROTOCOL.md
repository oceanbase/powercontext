# Evidence admission v7：协议与离线预算核验

v7 只改变评估协议表示，不改变冻结 v5 的事实选择、数据、标签或生产 Atomic Memory schema。入口为 `python -m powercontext_eval_evidence_admission.v7`，提供 `freeze`、`cost-compare`、`summarize`，没有线上调用命令。

## 模型输入与输出

模型可见 quote 索引为 `{"q","e","p","b"}`：请求内短编号、顶层 evidence 下标、相对于该 evidence 的 JSON pointer、该字符串内的 UTF-8 字节区间。JSON pointer 遵循 `~0`、`~1` 转义，明确区分正文、多字段 metadata 和不同 bundle range。输出 copy 部件的字节区间相对于这个已定位的片段。

quote 索引不重复发送完整的来源哈希。原证据里的来源、字段路径和准入元数据仍保留。内部 Quote 绑定 Source、materialization、projection、字段、原始字节位置和片段值；完整请求绑定同时包含整个输入、短编号和索引映射。渲染必须提供原请求绑定，错误请求、上下文或映射均拒绝。

输出沿用 TemplateOutput：模型显式选择 text/copy 部件。渲染器只验证和拼接已选部件，不补齐遗漏，不推定事实成立。生产 CODING 指令中的语言、时间、归属和不可信证据规则保留。复制限制为每部件 1024 字节、每候选 8192 字节、16 部件、64 候选。局部边界检查覆盖 UTF-8、CRLF、组合字符、emoji 修饰符和 ZWJ，不声明完整 UAX 字素支持。

## 成本口径

以 `instructions + input(evidence, quote_index) + output_schema` 的完整 canonical JSON 计算字符估算输入，与 v6 完整请求口径一致。账本分成 instructions、evidence_body、quote_index、output_schema，JSON 转义和外层封装的差额计入 evidence_body，分项之和等于总估算。

逐请求诊断另外统计选中 value 的估算成本，区分「值本身超预算」与「证据包装/索引/固定协议叠加后超预算」。evidence_body 包含包装，不能据此断言原始正文已经超过预算。字符估算不等于 provider 实际 token，线路开销未知。

## 机器验收

每条样本保留单次、累计输入和调用数门槛；v2/v3 使用冻结策略既有的累计 8× 输入预算和 16 次调用上限，其他参考 arm 沿用原先没有额外累计策略上限的规则。

三集合并时校验 COMPLETE 哈希、数据集、代码与依赖身份、原始 manifest 哈希、完整样本身份集合、重复和缺失样本。v7 身份包含 v6 与冻结参考评估器依赖。候选原始覆盖和成本可接受覆盖分别计数；没有新模型输出时，不声明新协议生成质量通过。

COMPLETE 只证明离线产物完整，不证明真实 provider 尝试终态审计。历史 v5 失败响应未知状态保持不变。旧 v7 结果保留在原目录，新验收产物单独输出到 `v7-verified`。

## 后续条件

任何剩余预算失败均阻止线上验证。协议压缩不足以满足预算时，需要独立的预算准入策略；不能删除样本、降低覆盖门槛或把原可容纳事实重新标为不可容纳。线上小样本、provider 实测、生产落地均不属于本协议离线通过的结论。
