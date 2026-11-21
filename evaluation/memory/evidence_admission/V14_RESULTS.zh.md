# v14 单元引用协议：离线比较结果（未通过，停止）

v14 验证的假设是「预先建立可引用单元、模型引用单元 ID、由程序还原原始字节，比让模型算字节偏移更可靠」。**离线预算门槛未通过**：候选 arm 在 `many-source-overflow / 2048` 出现 3 个新增预算拒绝（v2 参考 arm 3 个、window 参考 arm 3 个，共 9 个）。按冻结规则，本阶段未通过，结论以 `insufficient_evidence` 收尾，不换单元规则继续试。未提交、未推送。

## 一、实现与正确性

- 新增 `v14/` 子包：`codec.py`（单元构造 `split_cells` + `CopyRef{quote_id}` 无字节偏移 + `render` 程序还原）、`wire.py`（编码 + 成本账本）、`admission.py`（复用 v9 装箱逻辑）、`workflow.py` / `__main__.py`（离线 compare/summarize）。
- 单元规则（确定性、与标注无关）：反引号 `` `...` `` span → 单元；无反引号的字符串 → 整串单元。
- 专项测试 9 项通过；完整评估器回归 423 passed（414 + 9，0 回归）；ruff / ty 通过。
- 单元覆盖核验：exact-literals 4 个反引号 span = 4 个精确字面量，逐个覆盖；unicode-boundaries 整串 1 个单元，内含 emoji/组合字符/CRLF。

## 二、离线比较结果（主集 synthetic-v1，900 样本）

候选 arm 180 样本中 3 个拒绝，全部为 `many-source-overflow / 2048`（v9 已解决该残差案例，v14 重新引入）。

| 指标 | 观测 |
|---|---|
| 被拒绝样本 | 9（候选 3 + v2 3 + window 3） |
| 全部拒绝的 case/budget | many-source-overflow / 2048 |
| 拒绝原因 | indivisible_range_over_budget（单次 + 累计超限） |

## 三、根因诊断（精确到成本构成）

一个被拒的候选样本（many-source-overflow@2048）拆成 16 个请求，其中 8 个超限请求每个：

| 构成 | token |
|---|---:|
| 单元表（cell_table） | **709** |
| 证据正文（evidence_body） | 728 |
| 单次估算输入 | 2557（> 2048） |

根因：many-source-overflow 的 8 个 source 都是**无反引号的普通 ASCII**（如 `The synthetic service number 0 uses region-0.`）。v14 的「无反引号 → 整串单元」回退把每条整串都复制进单元表，使单元表≈正文（709 vs 728），有效成本翻倍，连单个不可再分的 range 也超 2048。

这正是步骤 3 预警的粒度权衡：**「整串回退」对需要逐字节保真的 unicode 场景必要，但对普通 ASCII 多源场景过粗，把不需要保真的普通文本也建了单元，制造不可控成本。**

## 四、结论

- 单元 ID 引用 + 程序还原在**正确性**上成立（9 测试、字节保真、无偏移），对 exact-literals/unicode 两个目标用例也成立。
- 但**成本**上，整串回退导致 many-source-overflow 重新超预算，离线门槛未通过。
- 按「任一阶段失败即保留结果停止，不换参数继续试」，本轮停在这里：**决策 = insufficient_evidence**，瓶颈为「单元表成本 vs 预算」，未运行线上小样本，未恢复其他数据集，未改生产行为。

若继续，需另立协议解决「整串回退的成本」，例如单元仅覆盖有逐字节保真需求的文本（反引号 span + 含非 ASCII/组合字符/CRLF 的整串），普通 ASCII 不建单元。这是新的协议版本，不在本轮内自动调整。
