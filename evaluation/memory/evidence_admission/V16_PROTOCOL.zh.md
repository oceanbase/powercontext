# v16 受保护单元评估合同

基线为本地工程提交 `87c70714`。实验仅属于 evaluation，不修改生产 Source、投影、持久化、检索、
生命周期、注册表或 API。v5/v14/v15 的失败、未知 usage 和缺失产物保持原状态。

## 唯一策略变量

沿用 v15 的指令、输出 schema、别名、支持校验、预算估算与装箱规则，只改变字符串的单元选择：

- 非空单反引号配对的内部内容建立一个单元，保留原始定界符。
- CRLF、CR、组合标记、修饰符、变体选择符、ZWJ 链及区域旗帜对保持完整。
  单独的标记、肤色修饰符、ZWJ 和 Unicode `So` 类符号也可引用。
- 普通 Unicode 字母和标点保留为原文，不增加单元包装；普通 ASCII 散文同样处理。
- 没有整叶回退、规范化、替换或删除。选择规则不读取标签、必需事实或预期输出。
- 不声称完整 UAX #29 分词或完整 Emoji 属性分类；Unicode 数据库版本固定在内容身份中。

单元身份绑定真实 Source、materialization、projection、字段和原始范围。请求整体绑定别名、原文、
schema、指令及单元。程序只还原模型明确引用的单元，允许一个事实含多个 copy，不自动拆复合候选。
原子性与字面量保真分别评分，不用 copy 数量推断事实数。

## 身份与阶段

准备身份覆盖全部既有评估依赖、运行时代码、数据、v16 核心与测试、合同、本文档和工程历史凭据。
没有执行代码排除项；核心变化使全部比较身份失效，不复用旧通过状态。
正式三集比较必须通过实际 HEAD/blob 校验。本版因已知最小清单失败，不增加线上执行扩展。

历史 v15 三份比较的文件 SHA-256 和内容身份固定在 `contract.json`。新比较逐样本核对 case、arm、
预算、重放、原始状态、完整性、范围清单及事实/字面量覆盖；不只比较总数。旧可执行样本不能新增拒绝。

## 最小回放的已知失败

开发期最小回放已发现 Unicode 仍需三次请求，未达到沿用的每例一次门槛。
普通散文单元移除后，单元数由 1195 降至 891，但只有四种不同字节值，重复的组合字符、CRLF 与符号仍有大量包装。
初始目标断言失败保留；回归测试验证这个预算有效的三次计划不能通过最小清单，没有把三次改判为成功。
规则和门槛不再调整。本版只完成既定三集离线量化后以 `insufficient_evidence` 收尾，不进入 SDK 或 provider 阶段。

## 冻结离线门槛

先进行针对性回归和历史失败回放，再在固定核心身份下完整重跑：synthetic-v1 900、validation-v2 360、
validation-v3 360，共 1620 条；五个 arm、2K/4K/8K、三次重放。候选 324 条，可容纳必需事实分母 495。

每次不超过对应预算、累计不超过预算的 8 倍、最多 16 次请求。wire 必须逐字节还原真实输入，
准入范围不得改变。原始完整投影/窗口的预算拒绝、故障、取消与空计划独立保留，不记作生成成功。

全部样本完整唯一、三集身份一致、全 arm 新增协议拒绝为 0、候选可执行必需事实 495/495、
历史选择/状态/覆盖及可执行性无退化、重放非确定性为 0，离线才通过。
正式离线门槛失败时完成既定三集比较并以 `insufficient_evidence` 停止，不在 v16 内调参继续。

离线通过后，重新从真实持久化 Source 构造 exact-literals 和原始 unicode-boundaries：候选 arm、4K、
重放 0、每例一次请求。全部必需精确字面量须能从已有单元复制；此检查只证明可准备，不能证明模型选择正确。
一次请求门槛失败即停止，不扩大清单、发送前缀或偷偷增加调用。

## 线上与费用

只有离线和最小清单通过，才进入单独执行扩展的提交冻结与实际 SDK/Pipeline 预检。
真实运行经过 `AtomicMemoryGenerationPipeline`；显式 thinking disabled、temperature 0、输出最多 2048、
重试 0、串行逐例审阅，失败或未知 usage 停止并保留审计。先 exact-literals，审阅通过才到 Unicode。
字节、原子事实、原子性、归因、实际用量、模型路由和输出哈希分开记录。

用户明确的 ¥5 条件预算从 v9 批准时点累计。此前 smoke/v5 的 13 份未知 usage 独立保留，不改成零。
批准范围内既有上界 ¥0.090520，条件余量 ¥4.909480。当前官方峰时输入 ¥2/百万、输出 ¥8/百万，
按 1048576 输入、2048 输出的保守单次预留为 ¥2.113536。新调用前仍须核验现行费率和完整预留。
来源及实际路由限制见 [工程费用记录](results/engineering-readiness/provider-rates.json)。

最小小样本成功不自动授权正式联合矩阵。正式比较须使用统一评分、真实 Pipeline、同一冻结数据和设置，
并另行固定调用范围与预算。最终决定只能是 `bounded_projection_follow_up_required`、
`no_production_change` 或 `insufficient_evidence`；只有全部正式门槛通过才形成单独生产后续契约。

## 离线命令

从源码仓库运行，输出目录必须是新目录；默认不访问模型或读取凭据。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 freeze --output .artifacts/evidence-admission/v16-offline/freeze.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 replay --history-root .artifacts/evidence-admission --output-dir .artifacts/evidence-admission/v16-history-replay
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset synthetic-v1 --output-dir .artifacts/evidence-admission/v16-reference-primary
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v2 --output-dir .artifacts/evidence-admission/v16-reference-validation
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v3 --output-dir .artifacts/evidence-admission/v16-reference-additional
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 compare --dataset synthetic-v1 --reference-run .artifacts/evidence-admission/v16-reference-primary --historical-run .artifacts/evidence-admission/v15-offline/comparison-primary --output-dir .artifacts/evidence-admission/v16-offline/comparison-primary
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 compare --dataset validation-v2 --reference-run .artifacts/evidence-admission/v16-reference-validation --historical-run .artifacts/evidence-admission/v15-offline/comparison-validation --output-dir .artifacts/evidence-admission/v16-offline/comparison-validation
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 compare --dataset validation-v3 --reference-run .artifacts/evidence-admission/v16-reference-additional --historical-run .artifacts/evidence-admission/v15-offline/comparison-additional --output-dir .artifacts/evidence-admission/v16-offline/comparison-additional
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 summarize --run-root .artifacts/evidence-admission/v16-offline
uv run --project evaluation python -m powercontext_eval_evidence_admission.v16 minimal-plan --run-root .artifacts/evidence-admission/v16-offline
```

报告必须保留引用的 v15 比较文件；路径缺失或哈希不匹配时报告不可用，不重建成假历史。
准备结果、受控输出与历史模板回放均不构成新增 provider 或独立语义审阅证据。
原始字节保真截至提取输出；生产 Memory 的 NFC 与首尾 strip 合同没有改变，也没有新增 Memory 写入。
