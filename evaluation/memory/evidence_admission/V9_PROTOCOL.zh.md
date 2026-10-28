# Evidence admission v9：紧凑请求协议

v9 是评估专用的离线协议。它保持冻结 v5 各 arm 的已选事实、样本和标签，复用 v7 的输出 schema 和复制校验，以减少每次请求的重复编码开销。它不修改生产 Atomic Memory、API、依赖或持久化规范化。没有线上调用入口。

## 模型可见表示

每个顶层 evidence 使用请求内短编号 e0、e1 等，保留 source_ref、journal_position 和非空 via_artifact。完整 evidence id、support、materialization、projection 和复制映射绑定在内部请求中。

完整投影使用 data 保留原对象、数组和键名；每个叶值包装成包含 v 的对象，保留字符串、数值、布尔值、null、空对象和空数组的 JSON 类型。源数据中的 v、q、copies 等同名键也按原树形结构保留，不能成为程序生成的复制指令。完整投影的原始 source_metadata 全部作为 data 保留，包括时间、归属、看似哈希的业务值。

片段与 bundle 使用 fields 字典表示原 JSON pointer，ranges 中保留字段下标 f、原 UTF-8 区间 b 和类型化值 v。片段只内置化已识别的程序生成来源信封：materialization_hash、projection_hash、admission_policy、source_declaration_changed。来源哈希必须与支持身份一致；未知信封字段或身份冲突明确拒绝，不能静默隐藏可能的业务事实。

字符串直接携带短 q 编号，无需重复发送独立 quote_index。一个值对应多个已支持子区间时，copies 列表显式列出每个 q 和局部区间 b；缺口不能通过复制被补齐。模型输出的 copy 偏移相对于对应 q 的字符串，候选 evidence_ids 必须引用 e 编号。渲染后 e 编号还原为完整 evidence id。

## 身份和保真

每次渲染和还原必须提供原请求绑定。绑定覆盖完整输入、支持身份、实际 wire payload、编号映射、字段位置、指令及输出 schema。跨请求、跨来源、跨版本、篡改值、字段或编号均不能复用旧绑定。

离线比较先经过实际 JSON 序列化与反序列化，从 wire payload 读回全部选中源值，再核对完整输入与来源清单。复制索引也逐项核对实际值、位置、来源及完整性。事实评分使用还原后的输入，不能只对内部原输入评分而忽略发给模型的内容。

复用 v7 的复制限制：每个 copy 最多 1024 UTF-8 字节、每候选最多 8192 字节、16 部件、64 候选；UTF-8、CRLF、组合字符、emoji 修饰符及 ZWJ 局部边界保持。未声明完整 UAX 字素支持。复制不自动补齐遗漏，不批准语义或事实归属。

生产 CODING 指令中的语言、时间、归属、独立事实拆分和不可信证据规则保留，只调整候选表示与引用说明。生产 Memory 的 NFC 和首尾 strip 保持，因此原始字节保真只证明到持久化之前。

## 预算和选择

以完整 canonical JSON 的 instructions、input、output_schema 计算输入字符估算，与 v7/v8 同口径。账本分成指令、选中值、字段字典、上下文/复制编号/JSON 封装、输出 schema，分项之和等于总估算。provider 实际 usage、线路 token 和金额仍未知。

优先保持原请求数量和边界。只有原请求的 v9 表示仍超单次预算时，才沿用整段顺序装箱；不截断、不按标签取舍、不跨原请求合并。所有诊断范围都保留，任何单次、累计或调用数超限均使整个样本不可执行。

冻结候选限制保持：2K 档单次输入 2048、累计输入 16384、最多 16 调用；其他档累计上限同为 8×。控制 arm 的 v9 变体显式使用 16 次、8× 上限，不声称原控制策略本来支持拆包。

## 离线验收

最小回放共 51 个原身份：4 个历史失败场景 × 3 预算 × 3 重放，加 3 个保护场景的 4K × 3 重放，再加两个剩余 2K 失败组合的各 3 个重放。

完整比较覆盖 synthetic-v1 900、validation-v2 360、validation-v3 360，共 1620 个原身份。保留取消、不可用、原准入拒绝和不可完整容纳的状态。候选分母保持 495；必须同时达到成本合规后的 495/495、完整事实在至少一次请求中共同可见、没有新增字面量丢失、没有其他样例退化。原不可完整容纳场景不得因为已选范围成功编码而改称完整成功。

合并时核对三集完整样本集合、依赖冻结、参考 manifest、v7/v8 基线文件哈希及 COMPLETE。逐条输出预算、覆盖、拒绝和完整计划成本。COMPLETE 仅证明离线产物完整，不证明模型生成成功或真实调用终态审计通过。

## 复现入口

使用现有 evaluation 虚拟环境，不同步或升级依赖。在仓库根目录设置 PYTHONPATH=evaluation/memory/evidence_admission/src，执行 evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9。

命令包括：

- freeze：指定 --output 保存独立代码与合同身份。
- minimal / compare：指定 --dataset、--reference-run、--output-dir、--v7-baseline、--v8-baseline。minimal 选择上述固定场景；compare 保留数据集全部身份。
- summarize：指定 --run-root，合并 comparison-primary、comparison-validation、comparison-additional。

输出排他创建，不覆盖旧结果。首次完整投影展平布局的 51 样本失败结果及代码快照单独保存在 initial-flat-layout；最终合同修订为 native-tree-and-inline-ranges-v2，预算、数据和门槛保持。

任何离线失败都阻止线上验证。离线通过只允许另行准备 provider 预检：冻结实际请求、基线比较、费用上限、错误分类和尝试终态审计，再决定是否执行配对小样本。不得直接恢复此前两个未运行的线上数据集，也不构成生产授权。
