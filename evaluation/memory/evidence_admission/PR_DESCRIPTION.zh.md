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
- 持久化 JSON 在目标目录使用唯一临时文件；中断遗留的 `state.json.tmp` 不再阻塞后续评分恢复。
  保留文件及目录 fsync；不可变审计记录采用原子禁止覆盖发布，并发写入也遵守此约束。
  临时文件不会成为正式状态，正常清理仅删除本次写入自己创建的临时文件。
- 版本化持久化重放身份仅排除信封顶层服务器 `saved_at`，仍检测 Source 值、元数据和嵌套业务时间变化；不迁移旧准备身份。
- 绑定审阅导入读取被 Pipeline 拒绝但 schema 有效的 `.parsed.json`，成功输出仍读取 `.output.json`。
  校验请求、输入、请求体、响应和原审阅绑定；被拒请求保持被拒，不计入已交付事实或合格字面量，审阅仅追加派生记录。
- Pipeline 校验前保存解析输出身份，v19/v20 恢复时核验，包括评分已落盘、游标未推进的状态。
  绑定缺失或证据变化会使缓存评分失效，停止且不重发、不重复计费；报告会重验审阅上下文并重新计算派生评分。
- 正式事实保留、配对改善及决策只使用确定性精确真值或绑定人工审阅；assistant 判断单独保留为诊断，不能解除正式未决。
  人工审阅追加到 `.human.json`，兼容旧位置中的人工审阅，拒绝竞争的人工派生记录，不覆盖原始证据。
- 零事实负对照要求空候选；非空输出即使被审阅标为有支持、未蕴含事实，也计入质量失败。
- 审阅 CLI 回执明确区分导入审阅的 `unresolved`、正式 `formal_unresolved` 和 `formal_human_review_required`，
  并提供 `reviewer_kind` / `score_kind`，诊断未决为零不代表正式通过。
- 特定运行的人工抽查声明和 v6 assistant 原子事实审阅迁至独立、保留原字节的[历史审阅补充包](HISTORICAL_REVIEWS.zh.md)。
  原结果包及校验和不变，补充包尚未公开发布。读取器通过 `POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS` 定位，
  先验字节哈希再解析，保留原有清单、结果、队列和状态绑定；显式自选声明须同时给出 CLI 校验和。
  普通协议和合成测试无需补充包。当前 v6/v7 身份声明预期哈希，不冒充历史执行身份或未发生的验证。
- 当前运行时回归与显式历史指令/schema 夹具分开；不改历史响应、评分、门槛或未知用量。
- 冻结提交检查覆盖运行时源码；保留冻结 JSON 原字节校验、可选 evidence reader 和评估计数兼容修复。
- 生成报告及历史运行记录由独立校验压缩包发布；历史辅助命令通过 `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` 定位解压目录。
  文档链接指向证据包说明并保留包内路径，不恢复代码树中的结果文件。
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

公开归档尚未按正式审阅及零事实负对照修复重新评分。只读检查发现 v18/v19/v20 存在 assistant 诊断评分回执，
原冻结结论均为 `insufficient_evidence`。当前工作区没有原私有会话审计材料，公开包也不包含这些正文；
不能把本地模拟 provider 回归通过表述为历史指标已修正或完整语义审阅已完成。
找回原材料后须在兼容、身份核验通过的环境中另行离线分析并发布派生报告，保留原归档字节，不需要新模型调用。

# Are there any user-facing changes?

本 PR 增加评估命令和文档。相对整合的上游基线 `89d17a046a0a9c3ff113874b46131aee78425c34`，
生产源码及 Gateway 行为不变。refusal 覆盖修改移出评估差异，在 Git 历史保留用于独立后续；本 PR 不声称修复该上游缺口。
不增加生产准入策略、功能标志、迁移或 API。

# How was this change tested?

本地整合包含上游 `89d17a046a0a9c3ff113874b46131aee78425c34` 和审阅文件外置修改。
本交付保留在本地，不声称已有远程 CI 通过证据。下述全量评估结果记录于整合提交之前，
当时工作区/暂存区的 `pyproject.toml` 与实际 HEAD blob 不同。

- 针对固定上游 SHA 的迁移历史基线检查通过；schema release/draft 回归 38 项通过。
- 最终受影响离线套件 297 项通过，覆盖外部回执、合成评分、v6-v8、版本化 CLI、继承的 v10 评分、
  v18 审阅/负对照门槛、持久化写入及 v19/v20 续跑。
- 版本化 CLI 另行独立运行 31 项通过。真实子进程单独运行耗时 2.17 秒，最终联合回归中耗时 1.98 秒，
  保持原 30 秒限制。没有复现评审环境超时，不提高限制，也不声称已普遍解决慢环境问题。
- 原始保留回执已通过外部读取器核验：声明保留 46 个抽样选择项、15 项部分判断，assistant 回执保留 29 项。
  这是回执/结构核验，不是私有会话回放或历史语义重算；原结果包校验和也已重新确认不变。
- 评估目录 Ruff check/format、评估源码类型、冻结合同字节及差异空白检查通过。

```bash
uv run --locked python scripts/schema_release.py check \
  --base-ref 89d17a046a0a9c3ff113874b46131aee78425c34
uv run --locked pytest tests/test_schema_release.py tests/test_schema_draft.py -q

uv run --project evaluation --locked pytest -c evaluation/pyproject.toml \
  evaluation/memory/evidence_admission/tests/test_{manifests,historical_review_receipts,sample_review,versioned_cli,v10_scoring,v18_comparison,v18_state_writes,v19_continuation,v20_continuation}.py \
  evaluation/memory/evidence_admission/tests/test_v{6,7,8}*.py -m "not live" -q
```

测试使用真实本地 Source/SQLite/Pipeline 和受控 provider 响应，未新增模型请求或费用。
历史响应正文不可用和语义未决仍保留；这些检查不证明新增真实 provider 验收或历史指标已修正。

用户在整合提交前运行了 `make evaluation-unit-test`：首组 pytest 为 2 failed、1736 passed、48 skipped、
2 deselected。两个失败来自 v12/v13 的真实 Git 冻结测试：其首次成功断言使用实际仓库的 `pyproject.toml`，
未提交的合并字节不同于 HEAD，因此被运行时保护正确拒绝。Make 在根环境 recovery/budget 测试组之前停止。
这不是全量评估通过记录。其他完整工程检查尚无当前树的通过证据，用户此前的基线不能代替本次验证。
审阅补充包仅在本地，尚无公开地址；发布时须上传并下载核验，原结果包及历史执行身份保持不变。

历史结果工具通过 `POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS` 定位，运行专属审阅通过独立的
`POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS` 定位。默认合成测试无需证据包、凭据或网络。
线上执行、费用计算、取消及报告见[英文手册](LIVE_RUNBOOK.md)和[中文手册](LIVE_RUNBOOK.zh.md)。
发布后须核对真实 workflow 合并/基线和完整 CI；本地固定基线通过不会改变旧 GitHub Actions 事件的 `SCHEMA_BASE`。

# AI usage statement

OpenAI Codex 协助实现、测试、分析和文档整理。任务发起用户提供人工检查反馈和显式部分判断。
未批准未审候选，未决语义、不可用响应、跳过项和验证边界均显式保留。
