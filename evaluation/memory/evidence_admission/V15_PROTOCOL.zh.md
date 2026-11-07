# v15 内联单元引用评估合同

本评估以提交 `f40a4d13` 为基线，对应 issue #2074。只修改评估层，不修改生产 Source、事实标签、
准入策略、持久化规范化、检索或公共 API。全部提交只保存在本地，不推送。

## 范围与历史证据

已有 v14 探索分别观察到精确字面量复合候选、精确字面量四条独立候选，以及 Unicode 仅保留 1/3。
Unicode 请求估算 6333，大于声明的 4096；该次调用不能成为预算合规证据。
这些结果不追认为正式联合验收，也不通过新分数覆盖。脱敏历史索引见 `results/v15-history/summary.json`。
早期预算失败报告仍在，但其原离线比较路径已被其他身份的成功比较占用；原始失败比较缺失如实记录。

## 确定性协议

- 从已准入字符串构造单元，不读取遗漏 Source，不使用事实标签或预期输出。
- 非空单反引号配对的内部内容作为一个单元，定界符仍保留在证据中。
- 定界符外的 CRLF、CR、组合标记、肤色修饰符、变体选择符、ZWJ 链和区域旗帜对不拆开。
  连续普通非 ASCII 字母按至多 64 个码点组成单元。规则不声称实现完整 UAX #29。
- 普通 ASCII 散文不建立单元；没有整叶回退。混合字符串的反引号外 Unicode 同样处理。
- 有单元的字符串编码为 `{s: [普通字符串, {c: 单元ID, v: 原始内容}, …]}`，没有单元的叶保持原类型。
  单元内容直接内联，取消独立的重复正文表。字段、类型、Source 及引用身份保留。
- 别名表来自全部准入证据，纯文本及零单元请求仍可引用。单元绑定 Source、materialization、projection、
  字段、原始范围、所在叶范围、请求内 ID 和协议版本。请求整体绑定别名、内容、schema、指令及支持范围。
- 程序只还原模型明确引用的单元，不规范化、不补事实、不拆复合候选。多个 copy 可以支持一个事实。
- 使用通用独立断言拆分指令和无关示例；不强制四条候选，不用 copy 数量充当语义原子性。

## 离线执行与冻结门槛

先执行历史输出回放和行为回归，再使用同一代码/合同身份重新执行三集全 arm 比较：
synthetic-v1 900、validation-v2 360、validation-v3 360，共 1620 条；五个 arm，2K/4K/8K，各三次重放。
候选 bounded-fragments-v3 共 324 条，可容纳的必需事实分母冻结为 495。

范围选择前后的字段、字节、身份清单必须一致；所有 wire 解码必须逐字节还原真实输入。
分别核算单次预算、累计 8 倍预算和最多 16 次调用。预算不合规的诊断计划不授权部分调用。
输入可用性与模型输出质量分开，空计划、故障及取消保留原状态，不冒充生成成功。

全部三集使用相同身份、完整且唯一的样本集合；全部 arm 无预算拒绝、候选可执行必需事实 495/495、
无范围或字面量覆盖退化、无非确定性，离线门槛才通过。每个阶段结束后决定能否进入下一阶段。
若离线失败，完成既定三集比较并保留结果，以 `insufficient_evidence` 停止，不在 v15 内修改参数继续试。

## 真实执行前置条件

离线通过后才准备独立线上执行清单：真实 HEAD blob 冻结、提交后 SDK/Pipeline 预检、
历史全部调用的费用结转及现行费率核验。沿用批准的 ¥5 条件预算，不把已知 usage 当账户账单。
模型设置为显式 thinking disabled、temperature 0、输出最多 2048、重试 0。

最小线上清单只含 exact-literals、随后原始 unicode-boundaries，各 4K、候选 arm、重放 0、一次请求。
实际执行须经过 AtomicMemoryGenerationPipeline；调用前记录意图，失败或取消仍保留审计并阻止盲目重发。
原始结构化输出、完整 usage、模型路由元数据及审阅材料保留在私有目录，公共交付只包含脱敏指标和哈希。
字节、事实覆盖、原子性、支持归因必须分别审阅；未审阅、费用未知或质量失败不得进入下一例。

正式联合比较不由最小样例成功自动授权。只有统一正式比较和冻结质量门槛均通过，才可选择
`bounded_projection_follow_up_required` 并形成生产后续契约。否则按证据选择 `no_production_change`
或 `insufficient_evidence`；不能从一个失败样例推断全部候选无价值。

## 离线命令

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v15 freeze --output .artifacts/evidence-admission/v15-offline/freeze.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v15 compare --dataset synthetic-v1 --reference-run .artifacts/evidence-admission/v5-preparation-primary --output-dir .artifacts/evidence-admission/v15-offline/comparison-primary
uv run --project evaluation python -m powercontext_eval_evidence_admission.v15 compare --dataset validation-v2 --reference-run .artifacts/evidence-admission/v5-preparation-validation --output-dir .artifacts/evidence-admission/v15-offline/comparison-validation
uv run --project evaluation python -m powercontext_eval_evidence_admission.v15 compare --dataset validation-v3 --reference-run .artifacts/evidence-admission/v5-preparation-additional --output-dir .artifacts/evidence-admission/v15-offline/comparison-additional
uv run --project evaluation python -m powercontext_eval_evidence_admission.v15 summarize --run-root .artifacts/evidence-admission/v15-offline
```

公共协议只有离线命令。线上执行的必要条件不满足时不得用直接 SDK 脚本绕过。
