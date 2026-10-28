# 真实生成管线与评分合同

本评估帮助维护者核对 [issue #2074](https://github.com/oceanbase/powercontext/issues/2074) 的执行路径、可比性和评分证据。
入口是 `python -m powercontext_eval_evidence_admission.v10`，只支持离线比较和已有输出回放，不读取 provider 凭据，
不初始化线上模型，不写入生产 Memory。生产 Source、投影、公共 API、提取与 reconciliation 行为保持原样。

## 执行边界

普通样本复用已经冻结的 Source 写入、SQLite 持久化、关闭重读、真实投影及 `AtomicMemoryExtractionInput` 构造路径。
输入准备之后，协议 extractor 将原生提取输入编码为紧凑请求；模型输出恢复为 `AtomicMemoryExtractionOutput`，
由实际 `AtomicMemoryGenerationPipeline.extract()` 返回候选。

```text
真实 Source 写入 → 持久化并重读 → 真实投影 → 原生提取输入
  → 评估准入 → AtomicMemoryGenerationPipeline.extract
  → 协议 extractor → 注入的 structured generator → 协议还原 → Pipeline 校验
```

模块没有通用线上构造器。SDK 集成测试注入 `httpx.MockTransport`；这证明本地协议与 Pipeline 可以组合，
不能证明真实模型能执行原子拆分、copy 或 Unicode 保真。已有线上输出的回放只使用已保存的最终候选，
不重新调用 SDK，不能把历史直接调用 generator 的请求追认为经过了真实 Pipeline。

请求身份绑定原生输入、支持范围、wire 内容、引用映射、指令和 schema。原始 Source 值不规范化、不补全。
此阶段不执行 Artifact 持久化，因而不保证持久化后仍保留原始字节，也不承诺完整 Unicode 字素算法。

## 比较合同

事实选择仍是冻结的 v5 各 arm 算法。选择器与协议不能读取 ground truth；标注只供完成选择之后的评分使用。
新协议对所有正式 arm 使用同一套指令和 `TemplateOutput`，保留所有权、语言、时间和不可信输入规则，
通用地要求拆分可独立变化的断言，包括共享同一主体的断言，并要求精确字面量和原始 Unicode 使用 copy。
指令不包含当前失败样本的具体命令、字段、数值或 atom 标签。该指令是否改善真实输出仍未测量。

完整投影与窗口控制保留原始请求边界，不利用额外协议拆包帮助控制组通过预算。片段 arm 可以在同一个原请求内，
按已选完整范围的稳定顺序重新装箱；不跨原请求合并，不丢弃、不截断、不补充事实。重新装箱后的来源、值、
范围、materialization 和 projection 身份必须与原选择集合一致。

单次预算保持 2048、4096、8192；片段 arm 的累计预算、调用次数上限沿用冻结配置。
总指令、紧凑输入和输出 schema 均计入字符估算。协议超预算、原始准入预算拒绝、不可用与取消分别披露。
空请求计划不代表该样本成功生成；保留原始 `reference_status` 与完整性状态。
控制组因保留窗口边界而发生的协议预算拒绝单独报告，不能被候选覆盖率掩盖。

旧 v5/v9 的配对结果是协议诊断，不能单独隔离准入策略效果。正式生成必须在新合同内比较各 arm，
固定 provider、请求与返回模型身份、指令、schema、预算和输出上限，不能混用历史物理模型输出。

## 评分合同

三个口径分别报告，不互相覆盖：

| 口径 | 规则 |
| --- | --- |
| 旧整句 scorer | 保持原始父 claim、分母、accepted formulations 与 unresolved 结果 |
| 原子事实覆盖 | 使用已冻结的子事实定义，逐条验证语义审阅、原始支持范围及精确字面量 |
| 父事实语义覆盖 | 一个父 claim 的所有子事实均被支持且字面量齐全，才计为保留；任一子事实缺失均不计 |

每个 atom 对应唯一原始父 claim 和唯一标注来源范围。引用必须属于当前请求，实际准入字节必须覆盖该范围。
只允许在同一 materialization/projection 内拼接连续支持；来源不同、版本不同或相同文本出现在其他位置都不能代替。
精确字面量按 UTF-8 字节比较，不执行 NFC、换行转换或数值修正。

审阅记录绑定 case、完整输入、支持范围和实际输出哈希，候选身份包括输出内索引。未知语义保持 unresolved。
禁止仅因字符串出现就自动批准语义；禁止事后拆分生成候选再计分。支持、错误归因、原子性和字面量分别评分。
一个候选蕴含多个独立 atom 时，即使父事实语义完整，也必须判为非原子候选。

人工与 assistant 审阅身份必须如实区分。当前评分输出始终 `formal_acceptance=false`，只提供离线诊断；
它不实现正式联合生产 gate，也不能用 assistant 单独审阅替代 issue 的关键证据要求。
给历史输出补充父事实语义诊断不构成历史门槛通过，不消除旧合同 unresolved。

## 运行与产物

以下命令不访问线上模型。已有目录会被拒绝覆盖，比较期间代码身份变化会阻止结果发布。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v10 replay \
  --canary-dir .artifacts/evidence-admission/v9-live-canary-20261028 \
  --output-dir .artifacts/evidence-admission/v10-replay

uv run --project evaluation python -m powercontext_eval_evidence_admission.v10 compare \
  --v9-root .artifacts/evidence-admission/v9-offline \
  --output-dir .artifacts/evidence-admission/v10-comparison
```

回放需要原真实会话的仓库外私有目录仍可用。缺失时明确失败，不尝试重建响应或重新发送请求。
聚合只输出安全标签、哈希、范围与计数，不复制 prompt、原始响应、凭据或私有目录路径。
`comparison.json` 保存逐样本数据、继承依赖身份和 baseline 哈希；`summary.json` 给出预算、损失与重放确定性。
`COMPLETE.json` 的结果哈希标记本地执行产物完整，不表示模型质量或 issue 验收通过。

## 线上与验收条件

当前唯一总体决策是 `insufficient_evidence`。新协议未运行线上生成，也未完成运行前 Git 提交冻结。
本地哈希冻结不能替代 issue 要求的提交冻结，事后提交不能补正已经观察过的线上输出。
继承的实验身份包含 Git revision；提交后必须在最终提交身份下重建并核对离线比较和 SDK 请求冻结，
不能直接把当前工作区产物的身份当作提交后的线上授权。

线上准备必须另行补齐实际 SDK 请求冻结、终态审计、完整 usage 与费用控制，并让正式执行器真实调用 Pipeline。
离线通过后，先审计小样本的实际独立事实拆分、copy、Unicode、出处和失败分类，再决定是否扩大覆盖。
不能直接恢复两个未运行的正式数据集，也不能无视失败继续发送剩余配对请求。

仅当正式联合评估满足 issue 和冻结合同，才可选择 `bounded_projection_follow_up_required`，生成单独生产契约。
本评估本身也允许在充分披露有效证据和限制后选择 `no_production_change` 或 `insufficient_evidence`。
