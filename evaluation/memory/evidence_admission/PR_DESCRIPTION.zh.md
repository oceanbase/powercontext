# Which issue or RFC does this PR close?

关联 [oceanbase/powercontext#2074](https://github.com/oceanbase/powercontext/issues/2074)。
冻结决策为 **`insufficient_evidence`**。有界准入改善了输入覆盖，但候选未通过生成质量门槛，关键证据仍有未决项。
本交付不宣称全部验收通过；是否据受限结果关闭 issue，由维护者决定。

# Rationale for this change

缩减 Source 数量无法解决单个 Source 的提取输入本身超预算的问题。
本评估实际捕获 Source，持久化、关闭并重读，再构造 `AtomicMemoryExtractionInput`，比较完整投影、当前窗口和有界片段。
线上执行使用真实 `AtomicMemoryGenerationPipeline`。各 arm 共用冻结的生成指令、输出 schema、模型设置和单次输入预算；
输入准入与最终生成质量分别评分。

# What changes are included in this PR?

- 提供版本化 CLI、合成夹具和清单、确定性准备与回放、仅评估准入策略、评分、选择加入的线上执行及脱敏报告。
- 覆盖单源和多源溢出、混合/超大字段、位置事实、精确字面量、CJK/UTF-8、不可能完整性、重放、故障、取消及 Scope 隔离。
  Content adapter 没有可信结构所有权分层，结构化层 arm 标为不适用。
- 支持精确不可变 Source/物化/范围身份，区分完成、有界、不可用和取消；所有权污染与大小准入保留独立分数和决策。
- v19/v20 返回响应及用量在评分前以 `scoring_pending` 落盘；恢复时读取已存响应，不重发、不重复计费。
  必需评分缺失产生 `scoring_incomplete`，阻止 `no_production_change`。
- 当前运行时回归与显式历史指令/schema 夹具分开；不改历史响应、评分、门槛或未知用量。
- 冻结提交检查覆盖运行时源码；保留冻结 JSON 原字节校验、可选 evidence reader 和评估计数兼容修复。
- 生成报告及历史运行记录由独立校验压缩包发布；历史辅助命令通过 `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` 定位解压目录。
- 为通用 `run-live`/`run-smoke` 提供运行前费用上界计算。这些命令执行请求次数限制，不执行货币上限；历史 v20 账本是另一条路径。

# 证据与审阅入口

下载 [powercontext-2074-evidence-8bf9c58d.tar.gz](https://github.com/user-attachments/files/34846203/powercontext-2074-evidence-8bf9c58d.tar.gz)。

- SHA-256：`ff529bf0875c2441835bc5f35af3fe60df0c62c8722370f410aec73e499c0d65`
- 结果包整理快照：`8bf9c58d428995c97858b6764d4563c3cfe9ea44`
- 最后线上续跑执行修订：`1515a0d11cc2040d0beb341f6626a33c09cce73b`

整理快照不是执行修订；各轮原执行身份与哈希仍在包内。校验 SHA-256 并解压后，将
`POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` 设为解压后 `results` 的绝对路径。
完整校验和设置命令见 [证据包说明](RESULTS_ARTIFACT.md)。

| 解压后的包内路径 | 证据用途 |
| --- | --- |
| `results/v20-live/` | 冻结配置、结果、诊断、记录回放、验证和产物哈希 |
| `results/v20-human-sample/` | 声明的抽查覆盖、部分判断、字节核验和上下文绑定 |
| `results/acceptance-closeout-20261107/validation.json` | 历史工程验证，保留失败和中断 |
| `results/acceptance-closeout-20261107/final-hooks.json` | 历史逐 hook 结果 |
| `results/acceptance-closeout-20261107/artifact-index.json` | 收尾文件哈希 |
| `results/acceptance-closeout-20261107/COMPLETE.json` | 完成标记；记录的收尾状态仍为 `closed_with_failures` |

上述是包内路径，不是仓库内文件链接。仓库保留 [人工审阅说明](HUMAN_REVIEW.zh.md)、
[验收映射](ACCEPTANCE_CLOSEOUT.zh.md)和[审阅确认](REVIEW_CONFIRMATION.zh.md)。
结果包只含原公开脱敏证据，不含原始私有 Source、渲染提示、私有响应正文、凭据或敏感路径。
完整响应回放还需要原私有审计材料。

# 历史代码可用性

重建后的交付分支 `Lkx-JY/powercontext: evaluation/2074-bounded-evidence-admission` 不包含原执行修订的祖先历史。
当前 PR 实现与历史执行代码使用不同身份。

原历史已独立发布到 `Lkx-JY/powercontext: evaluation/2074-bounded-evidence-admission-37commits`：

- [固定归档尖端](https://github.com/Lkx-JY/powercontext/tree/3cc97c26e6cc6a6d99c678bc4da10b54755a9960)：
  `3cc97c26e6cc6a6d99c678bc4da10b54755a9960`。
- [最后执行修订](https://github.com/Lkx-JY/powercontext/tree/1515a0d11cc2040d0beb341f6626a33c09cce73b)：
  `1515a0d11cc2040d0beb341f6626a33c09cce73b`，包含于该归档历史。

2026-11-16 已核验远程归档 SHA，并在新仓库获取原执行修订。
[历史代码与复现说明](HISTORY_REPRODUCTION.md)给出恢复命令。发布代码不能恢复缺失私有响应；结果包和原私有审计材料仍是独立要求。

# 记录结果与限制

正式比较共 972 行：36 场景 × 3 arm × 3 预算 × 3 重放。历史配置请求 `deepseek-v4-flash`，关闭 thinking，
温度零，输出上限 2048 token，无重试。2048/4096/8192 输入预算是估算值，不是 provider 计费 token 硬上限。
候选每样本最多 16 次请求、累计估算输入倍率为 8；单次预算相同不表示请求数或费用相同。

| arm | 尝试请求 | 有效 Pipeline 输出 | 已知输出失败 | 历史不可用 |
| --- | ---: | ---: | ---: | ---: |
| `complete-projection-v1` | 42 | 41 | 0 | 1 |
| `window-reduction-v1` | 102 | 102 | 0 | 0 |
| `bounded-fragments-v3` | 327 | 148 | 178 | 1 |
| 合计 | 471 | 291 | 178 | 2 |

全部计划请求已尝试。候选历史输入覆盖为可评样本父事实 495/495、原子事实 531/531；计数包含预算和重放，不是生成评分：

- 177 次候选输出引用请求中不存在的 evidence ID，被真实 Pipeline 拒绝。
- 1 次候选输出达到共同输出上限，未形成有效结构化输出。
- 6 个有效 Unicode 输出分别仅保留 1/3 必需字节字面量。
- 533 个候选判断仍语义未决：226 个来自有效输出，307 个来自被拒绝输出。

469 个可用历史响应回放一致；两次历史响应及用量仍不可用。
用户报告抽查 11 场景的 46 条输出，其中 29 条可唯一定位，17 条仍缺 arm 或请求序号。
两项误归因和 13 项非原子判断为绑定上下文的部分判断，不是完成语义审阅，不清除原未决计数，也不声称存在第二位独立审阅者。

历史累计已知费用上界 ¥3.521362，未知用量预留 ¥4.227072，受保护总额 ¥7.748434，低于批准的累计 ¥20。
这些是上界及预留，不是账户账单，也不授权新调用；通用线上 CLI 不继承该账本或金额上限。

候选未通过生产后续门槛。冻结规则下，缺失响应/用量和关键语义未决也阻止确定的 `no_production_change`。
这些结果不能证明所有有界策略都无价值。测量范围限于合成场景、记录的 provider 配置及执行修订；模型别名不代表固定物理快照。
不宣称已在整合 schema 下重新比较、通过远程数据库或宿主验收，或证明 provider 取消退款行为。

# Are there any user-facing changes?

本 PR 增加评估命令和文档。相对审查的上游基线 `cfdb617cddc77994e5e7033bc3b7ac84d47940c9`，
生产源码及 Gateway 行为不变。refusal 覆盖修改移出评估差异，在 Git 历史保留用于独立后续；本 PR 不声称修复该上游缺口。
不增加生产准入策略、功能标志、迁移或 API。

# How was this change tested?

前次本地评估运行：1460 passed、48 skipped，随后工具恢复套件 104 passed。
用户随后确认本轮修改之前的 `make check`、`make evaluation-unit-test`、专项离线套件、Ruff check/format、
evaluation 类型检查、`make test`、`make build` 和 `tox` 已通过。
这些记录不代表最新文档和范围修改或后续冲突整合树已通过验证。

本轮仅静态核对空白、证据入口、归档身份和 Gateway 差异范围，全量验证由用户执行。
历史包依赖测试默认跳过；证据包不能替代原运行时和不可用私有响应。默认测试不要求 provider 凭据或网络。
线上执行、手工费用计算、取消、审计和报告重建见 [英文手册](LIVE_RUNBOOK.md)和[中文手册](LIVE_RUNBOOK.zh.md)。

更新前 PR 相对 `master` 显示 `CONFLICTING` / `DIRTY`。本次不包含上游合并或冲突解决，合并前仍需检查新 Head 的远程可合并状态及 CI。

# AI usage statement

OpenAI Codex 协助实现、测试、分析和文档整理。任务发起用户提供人工检查反馈和显式部分判断。
未批准未审候选，未决语义、不可用响应、跳过项和验证边界均显式保留。
