# Evidence admission v8：完整请求预算准入

v8 是评估专用的离线准入原型。它以冻结 v5 各 arm 已选中的输入为边界，使用修复后的 v7 wire 协议重新装箱；不访问被省略的 Source，不读取事实标签来决策，不修改生产路径、数据、标签或持久化规范化。

## 装箱规则

先对实际完整请求计费。原请求已经满足单次预算时保留原请求；超预算时，按原证据和原 range 顺序，将整个 range 稳定装入新请求。相邻同一父证据的 range 合并为一个派生 bundle，新 evidence id 绑定父证据完整内容与新 range 集合，内部 support 保持原 Source、materialization、projection、字段和位置。

完整投影若需拆分，以叶字段作为整体 range，保留字段路径与 JSON 类型；原 metadata 值同样作为内容保留。数值、布尔值、空对象等不因 quote 只索引字符串而被丢弃。跨原始请求不合并，range 不截断、不规范化、不按标签取舍。

单个 range 无法在完整协议下容纳时返回 `indivisible_range_over_budget`。单次合规但累计输入或调用数超限时返回 `resource_budget`。失败计划保留诊断信息，整条样本的可执行调用为零，不能执行部分前缀，也不能把拒绝成本当作成功节省。

v2/v3 沿用各自冻结的 16 次调用、累计输入 8× 单次预算上限。其他参考 arm 的 v8 拆包变体也显式使用 16 次、8× 上限；这些是新策略限制，不能解释成原控制 arm 的既有能力。单次预算衡量输入；最大输出另按冻结的每调用 2048 token 列账。provider 上下文与真实线路 token 仍需单独预检。

## 离线比较

入口：`python -m powercontext_eval_evidence_admission.v8`，仅有 `freeze`、`compare`、`summarize`。compare 在一次本地重放中同时产出 v7 逐请求预算诊断和 v8 计划，不调用 provider。

全部比较覆盖 synthetic-v1 900、validation-v2 360、validation-v3 360，共 1620 个原始样本身份。取消、不可用、原准入拒绝和不可完整容纳场景保留原状态，不能把没有输入的身份算成模型成功。

装箱完成后才使用冻结标签评分，核对三个不同问题：

1. 原已选字节、JSON 值、来源与 support 的清单是否完全保持。
2. 同一事实或字面量是否仍在所有计划请求的并集中。
3. 一个完整事实是否在至少一次请求内共同可见，防止跨请求拼接覆盖掩盖生成不可见的问题。

候选门槛要求冻结分母下 495/495 事实在成本可接受计划中保留且共同可见，没有新增字面量丢失、原同请求可见事实退化、拒绝或预算违规。所有 arm 的失败另外完整列出，不用候选成功替代控制样本的失败。

## 可证明范围与停止条件

离线通过只证明选中证据的准备、成本估算和本地协议处理，不能证明模型会正确选用 copy、按原子事实输出，或真实 provider 会按估算收费。COMPLETE 也不构成真实调用的终态审计证据。

生产 Memory 既有 NFC 与首尾 strip 行为保持；这里的原始字节保真仅在持久化前成立。字素边界检查范围与 v7 相同。

任何预算、内容清单或覆盖失败都阻止启动线上小样本。若离线通过，可另行冻结配对样本的实际请求、v5 基线成本、provider 线路、审计终态与费用上限，再决定是否执行。不得直接恢复此前未运行的两个线上数据集。

## 复现

在仓库根目录设置 `PYTHONPATH=evaluation/memory/evidence_admission/src`，使用 `evaluation/.venv/bin/python` 执行。先 freeze 到新目录；三个 compare 分别指定 dataset、冻结 reference-run、新 output-dir 和新 v7-output-dir。compare 完成后分别用 v7/v8 summarize 合并。输出采用排他创建，不能覆盖历史结果。对当前代码重新运行应创建另一个完整目录。

评估器回归使用 `evaluation/.venv/bin/python -m pytest evaluation/memory/evidence_admission/tests -q`；Ruff 使用仓库锁定工具；类型检查从 evaluation 目录运行 `.venv/bin/ty check memory/evidence_admission/src`。这些检查无需模型凭据。
