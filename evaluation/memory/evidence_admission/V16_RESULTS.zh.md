# v16 受保护单元离线结果

唯一最终决策为 **`insufficient_evidence`**。三集离线比较通过，但原始 Unicode 最小样本仍需三次请求，
超过冻结的每例一次门槛，执行停止在 SDK 预检前。没有新增 provider 调用，没有生产行为变更。
最终机器结果为 [result.json](results/v16-offline/result.json)；离线汇总中的 pending 状态仅表示中间阶段，
不替代这个最终决策。

## 实现与执行身份

工程基线为 `87c70714`，v16 核心执行提交为 `faf15ad6c1e20eb5d8ed36ca36f5816c07c09348`。
三集比较前后均检查实际 HEAD blob，所有冻结依赖均与提交一致；不把暂存状态或内容快照冒充已提交执行。
完整内容身份为 `487cdea929226e028da889ac6dc633553e593689732e9167fe115e854546c00d`，
代码、数据、锁文件、schema、指令、SDK 和 Unicode 数据库身份见 [freeze.json](results/v16-offline/freeze.json)。

v16 只改变 copy 单元选择。普通 Unicode 字母和标点保留为原文，反引号内容与字节敏感的组合、CRLF、
ZWJ、旗帜和符号等仍形成可复制单元。没有整叶回退、规范化、替换或删除，也不读取必需事实标签。
v15 的指令、输出 schema、原子性规则、别名、支持校验、证据选择、单次预算、累计八倍预算和十六次调用上限不变。
一个事实仍可含多个 copy，程序不按 copy 数量自动拆候选。

新增版本化 CLI 提供 freeze、历史回放、三集比较、汇总与最小清单检查，默认不读凭据，没有 live 子命令。
离线样本使用真实持久化、关闭并重读的 Source 和实际投影输入；wire 解码逐字节还原，并核对完整的选择范围清单。
真实模型生成未运行。历史模板回放及受控输出测试经过实际 `AtomicMemoryGenerationPipeline`，不构成新模型质量证据。

冻结合同 `stopping` 的描述保留了一个历史标签 `v15`；其 schema、执行检查、身份与本结果均属于 v16。
这里只说明该文字勘误，没有修改冻结文件、门槛或历史结果。

## 三集完整比较

synthetic-v1 为 900 条，validation-v2 和 validation-v3 各 360 条，共 1620 条；
五个 arm、2K/4K/8K 预算、三次重放。候选 arm 为 bounded-fragments-v3，共 324 条。
同一条事实在三次重放中分别计分；495 是冻结的可容纳必需事实分母，不代表 495 个独立真实任务。

| arm | 可执行计划中的输入事实保留 | 潜在调用 v15 → v16 | 累计估算输入 token v15 → v16 |
| --- | --- | --- | --- |
| complete-projection-v1 | 75/495 | 42 → 42 | 95,544 → 95,544 |
| window-reduction-v1 | 171/495 | 114 → 114 | 265,128 → 265,128 |
| bounded-fragments-v1 | 303/495 | 315 → 297 | 1,009,317 → 936,027 |
| bounded-fragments-v2 | 429/495 | 474 → 441 | 1,632,240 → 1,471,926 |
| bounded-fragments-v3 | **495/495** | **522 → 483** | **1,743,060 → 1,564,791** |

候选潜在调用下降 7.5%，估算输入下降 10.2%。这些是协议准备计划和本地字符估算，
不是已发送请求、真实 tokenizer 计费或 provider 用量。

全部 1620 条的历史输入/状态变化、事实或字面量覆盖损失、已可执行样本新增拒绝均为零。
全部 arm 新增协议预算拒绝为零，单次、累计和调用数溢出均为零，非确定性组合为零。
核验逐样本原始状态、完整性、范围身份，以及保留和同请求内事实/字面量集合；总数相等不能抵消任意单项损失。
负向回归分别验证了聚合覆盖完整时的单项输入、字面量和可执行性退化仍使 gate 失败。

原生准备状态单独保留：完整投影 42 prepared、237 budget_rejected、18 unavailable、27 cancelled；
窗口缩减 54 prepared、225 budget_rejected、18 unavailable、27 cancelled；各片段 arm 均为
279 prepared、18 unavailable、27 cancelled。空计划通过额外包装预算不等于完成生成。
状态与完整性计数见 [最终结果](results/v16-offline/result.json)，完整离线门槛见
[summary.json](results/v16-offline/summary.json)，候选逐组合变化见
[candidate-comparison.json](results/v16-offline/candidate-comparison.json)。

## 最小清单仍未通过

离线通过后重新持久化并读取两例，使用候选 arm、4096 预算、重放 0；每例最多一次请求的门槛未调整。

| 样本 | 单元数 | 请求数 | 各请求估算输入 token | 最小清单 |
| --- | --- | --- | --- | --- |
| exact-literals | 4 | 1 | 3795 | 通过 |
| unicode-boundaries | 891 | 3 | 3947、4033、3189 | **失败：超过一次** |

两例的必需精确字面量均能从既有单元复制，原始事实与字面量也在同请求内保留。
这只证明输入和引用单元可准备，不证明模型会正确引用、输出原子候选或保留全部字面量。

Unicode 相比 v15 的 1195 个单元、四次请求、累计 13958 估算 token，改善为 891 个单元、三次请求、
累计 11169 估算 token（约下降 20.0%）。未装箱输入仍为 8611，高于 4096。
891 个出现位置仅有四种不同字节值：一个 ZWJ 组合、297 个组合字符、297 个 CRLF、296 个重复符号。
移除普通 Unicode 散文的包装减少了开销，但重复受保护单元的包装仍然过多。
明细见 [最小清单](results/v16-offline/minimal-readiness.json) 和
[成本诊断](results/v16-offline/unicode-diagnostic.json)。

初始目标测试曾为 25 passed、1 failed，直接暴露上述三次对一次的失败；
[失败凭据](results/v16-offline/development-goal-failure.json)保留原始测试及日志哈希。
最终回归验证这个预算有效的三次计划必须被最小清单拒绝，没有改变选择规则、把上限改成三次，或把研究目标判为成功。
开发期已知失败在正式比较前写入合同；三集比较只是按计划量化影响，不声称盲测验证了成功假设。

## 历史、工程与费用

历史 v14 两份已保存模板经原 Pipeline 回放，exact-literals 仍为 4/4，Unicode 仍为 1/3；
Unicode 原估算 6333 大于 4096 的失败保留。最早缺失 raw template 的探索无法回放。
没有新增语义评分或覆盖历史 assistant 诊断，见 [历史回放](results/v16-offline/historical-replay.json)。
v14 合同原始 SHA-256 和三份 v15 原始比较文件 SHA-256 均保持不变。

新增 29 项行为回归；issue 指定的无模型测试为 **490 passed**，1934 个警告。
完整 evaluation 在 Python 3.11.15、3.12.3、3.13.13、3.14.5 上均为 **1320 passed、1 skipped**，
每版完整类型检查通过，1935 个警告。四版本结果、构建及实际安装凭据见
[validation.json](results/v16-offline/validation.json)。警告包括既有 SQLAlchemy 弃用提示和刻意构造的未知 usage 场景，
不声称零警告。唯一原生 lineage 跳过不计通过，缺失的 #1975 evidence API 没有新增。

`make check`、完整 hooks、根包及集成类型、整个 evaluation 类型、issue 目录 Ruff 与格式检查通过。
根包与 evaluation 的 wheel/sdist 构建通过，v16 文件逐字节匹配；归档不含本地缓存或私有评测产物。
实际安装的 wheel 在仓库外可读取合成清单、显示 v16 帮助，执行冻结则明确要求 Git 源码仓库。
根包工程基线的四版本完整测试和 doctest 均为 7053 passed、627 skipped；v16 仅修改 evaluation，
没有重复该根包矩阵。基线 Python 3.12 初次失败、修复及完整重跑范围见
[工程报告](ENGINEERING_READINESS.zh.md)，不描述成四版本从未失败的单次运行。

本版新增 provider 调用为 **0**，新增调用费用为 **0**。
用户确认的 ¥5 条件预算从 v9 批准时点累计，已知保守上界保持 ¥0.090520，条件余量保持 ¥4.909480。
更早 smoke/v5 的十三份未知 usage 仍独立保留，不按零计，也不用于伪造完整账户账单。

## 复现与验收边界

完整离线命令见 [V16_PROTOCOL.zh.md](V16_PROTOCOL.zh.md)。使用新的输出目录，保留三份固定哈希的历史 v15 比较，
在同一提交、锁文件和执行环境完成准备与比较；模型凭据不需要设置。
已有产物可在匹配的执行环境中只读重算汇总，不删除或覆盖已保存结果：

```bash
uv run --project evaluation python - <<'PY'
import json
from pathlib import Path
from powercontext_eval_evidence_admission.v16.workflow import summarize
print(json.dumps(summarize(Path(".artifacts/evidence-admission/v16-offline"), persist=False), ensure_ascii=False, indent=2))
PY
```

代码、数据、依赖、历史文件或环境身份不匹配时应报告无法复现，不能跳过校验。
公开交付只含安全标签、计数、配置及哈希；私有完整比较和日志留在被忽略的本地产物目录。
公开 JSON 的文件哈希及脱敏检查记录见 [产物索引](results/v16-offline/artifact-index.json)。
本地文件取消与协程测试不证明远端 provider 取消、OceanBase、Windows 或生产工作器行为。
字节保真截至提取输出；生产 Memory 的 NFC 与首尾 strip 合同保持不变。
普通 Unicode 散文不再全部拥有 copy 单元，不能据此声称任意 Unicode 输出均可强制逐字节保真。

issue 的正式联合线上比较、实际模型原子性/Unicode/归因质量、完整真实用量和独立关键审阅仍缺失。
历史请求也不能追认新合同通过，引用范围可解析不证明模型实际使用。
可信结构分层因 Content adapter 没有可信所有权层级而不适用，不从角色文本推断权限。
没有形成生产后续契约，也不授权注册表、API、投影或 Memory 行为变更。

如另行启动下一协议，先评估重复字节值共享与每个真实 Source/字段/出现范围独立绑定的编码成本，
确认不跨 Scope 合并支持、不改正文与事实选择，再冻结独立版本并重跑相同离线门槛。
只有该版本的原始 Unicode 一次请求门槛也通过，才可进入提交冻结、SDK 预检、费用预留与逐例线上审阅。
这个方向尚未实现或验证，不能视为本版已解决的问题。
