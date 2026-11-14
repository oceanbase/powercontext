# Source 证据准入评测

本评测回答：超大或混合 Source 是否需要在 Atomic Memory 提取前增加有限预算的证据准入步骤。
实现仅位于 evaluation 工程，不修改生产投影、注册表、Memory 发布、检索、生命周期或 API。

生成报告、运行记录与历史结果包已移出代码 PR，独立证据包的执行修订、SHA-256、下载地址占位和解压命令见
[RESULTS_ARTIFACT.md](RESULTS_ARTIFACT.md)。只有历史回放需要设置
`POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS`；默认测试不下载证据包，也不需要 provider 凭据。

[v20 实际结果](V20_RESULTS.zh.md)已补跑剩余 351 次，全部有响应及用量，未新增未知调用。
原 471 次清单现已全部尝试，未运行请求为 0；合并 469 个可用响应回放全部匹配，2 个历史缺失仍不可用。
输出引用、结构化解析及 Unicode 字面量门槛存在失败，533 个候选语义未决，最终仍为 insufficient_evidence。
[执行协议](V20_PROTOCOL.zh.md)和独立证据包记录实际范围、累计 ¥20 保护及验证凭据。

[v19 补跑](V19_RESULTS.zh.md)继承冻结的 v18 生成与准入条件，新增尝试 116 次：98 次有效 Pipeline 输出、
17 次引用错误、1 次未知响应及用量。剩余 351 次未运行，合并两次未知费用预留后原 ¥5 不足以继续。
原请求不重发，原失败不覆盖；[协议及显式命令](V19_PROTOCOL.zh.md)和独立证据包记录范围与限制。

[v18 准入比较](V18_PROTOCOL.zh.md)固定原生生成指令与 schema，只比较完整投影、当前窗口和有界片段。
三集、三预算、三重放共 972 行；预算拒绝、输出失败与语义未决分别报告。
普通质量失败保留后继续独立计划请求；费用、取消、Scope 或审计问题停止整轮。历史协议结果不混算。
[v18 实际结果](V18_RESULTS.zh.md)：离线与预检完成，线上四次尝试中三次返回、一次无响应/usage，
按规则停止，其余 467 次未运行；最终仍为 insufficient_evidence。

[工程验证](ENGINEERING_READINESS.zh.md)提供四个 Python 版本的根包与完整 evaluation 测试、类型检查、
完整 hooks 和构建凭据。工程通过不代表正式线上比较通过；当前总体决策仍为 `insufficient_evidence`。

[v17 线上诊断](V17_RESULTS.zh.md)在实际提交冻结后，经 SDK/Pipeline 执行两次请求：精确字面量 4/4，
Unicode 第一条为 2/3，遗漏必需 CRLF，因此其余两条未运行。新增真实用量完整记录，失败可离线回放；
这只是候选最小诊断和 assistant 审阅，未完成正式各 arm 比较。[显式线上命令](V17_PROTOCOL.zh.md)默认不执行网络调用。

[v16 合同](V16_PROTOCOL.zh.md)只改变可复制单元的选择，保留 v15 指令、输出 schema、源范围与预算。
Unicode 最小样本仍需三次请求，超过未调整的每例一次门槛；[v16 结果](V16_RESULTS.zh.md)分别记录离线输入覆盖
与这个失败的准入门槛。本版没有新增 provider 调用或生产授权。

默认实验仍为 v1。新增 v2 的窗口优先、整记录分片、共享出处包装、成本上限与配对比较见
[V2_PROTOCOL.md](V2_PROTOCOL.md)。使用 `--experiment-version v2` 选择，压力集另加 `--dataset validation-v2`；
报告和人工审计必须使用相同选择参数。原始 v1 清单、算法及结果保留，v2 矩阵重新执行全部对照组。
[V2 离线比较结果](V2_RESULTS.zh.md)给出原有 8 个组合的修复、其他样例非退化核验、额外调用成本和仍未解决的压力样例。

v3 的分组分配、完全重复片段延后和联合离线门槛见 [V3_PROTOCOL.md](V3_PROTOCOL.md)。
使用 `--experiment-version v3`；数据集为默认 `synthetic-v1`、`validation-v2` 和 `validation-v3`。
v3 仅按投影字段结构分组，不推断角色或所有权；没有增加单 Source 补扫调用。

```bash
uv run --project evaluation evidence-admission prepare --experiment-version v3 \
  --output-dir .artifacts/evidence-admission/v3-primary
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v2 \
  --output-dir .artifacts/evidence-admission/v3-validation
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v3 \
  --output-dir .artifacts/evidence-admission/v3-additional
uv run --project evaluation evidence-admission compare-preparation \
  --run-dir .artifacts/evidence-admission/v3-primary \
  --run-dir .artifacts/evidence-admission/v3-validation \
  --run-dir .artifacts/evidence-admission/v3-additional \
  --output-file .artifacts/evidence-admission/v3-comparison.json
```

联合结果必须同时包含三个数据集，并核验当前代码、冻结清单、完整样本覆盖和相对 v1/v2 的非退化。
`offline_preparation_passed` 只说明输入准备比较；最终生产决策仍为 `insufficient_evidence`。
原有 28 个样例的五组矩阵为 1260 个样本，8 个新增压力样例另有 360 个样本。
[V3 离线结果](V3_RESULTS.zh.md)记录联合门槛、495/495 保留、非退化和成本测量。

每个样例都会创建独立 SQLite 数据库和 Scope，通过既有接口捕获 Source，关闭数据库后重新读取，
调用真实 `project_atomic_memory_evidence()` 并构造 `AtomicMemoryExtractionInput`。
普通 Content Source 和 SourceObservation 路径分别覆盖，不以字符串截断单测代替持久化回放。

## 执行离线评测

在仓库根目录执行：

```bash
uv sync --project evaluation --locked
uv run --project evaluation evidence-admission validate
uv run --project evaluation evidence-admission prepare \
  --output-dir .artifacts/evidence-admission/preparation
uv run --project evaluation evidence-admission report \
  --run-dir .artifacts/evidence-admission/preparation
```

完整矩阵为 20 个样例 × 3 个实验组 × 3 档预算 × 3 次独立重放，共 540 个样本。
预算为 2,048、4,096 和 8,192 个估算 token，包含指令、元数据、证据身份和完整输入包装。
正文片段上限为 512 个 UTF-8 字节。所有参数和决策门槛在清单中固定。

三个实验组分别是完整投影、当前窗口缩减和有限片段选择。结构分层实验组标为不适用：
Content adapter 没有提供可信的角色或所有权结构，文本中的角色标签不会被当成结构来源。
片段策略不读取 required spans，只按字段、位置和确定的轮转规则选择，最后恢复原始顺序。

窗口控制组真实使用 `SourceWindowTrigger` 和 `MemorySourceWindowRepository`，评测消费位置独立保存于内存，
不发布生产游标。行为测试另外执行真实 Atomic Memory 处理器，对照预算拒绝和窗口缩减序列。
这一控制组用于比较准入行为，不宣称模拟了完整 reconciliation 或生产工作器恢复。

## 阅读结果

结果目录包含每个样本的准备状态、片段支持范围、遗漏、哈希、延迟和 JSON/Markdown 报告。
`COMPLETE.json` 与文件哈希共同确定结果是否可消费；未完成或被修改的目录会被拒绝读取。
报告区分 `complete`、`bounded`、`unavailable`、`cancelled`，以及准备、预算拒绝和生成等执行状态。

独立结果包中的 `results/synthetic-v1-preparation/` 保存本地模型无关结果。未运行线上模型时，候选正确性、错误归因等指标为 null，
不会用零次错误冒充模型质量通过。输入重放确定性、事实是否进入输入和模型是否正确提取是独立指标。
即使候选策略增加了可准备样本数，只要丢失可容纳的必要事实，也不能满足生产后续门槛。

精确字面量按其在标注事实内部第一次出现的原始字节位置独立计数；事实的其他部分缺失，不会把仍保留的字面量
一起计为丢失。其他位置的相同字符串也不能替代这个标注位置。片段总数、准入数和遗漏数分别记录。
生成汇总另外报告必要事实保留率、重复候选数、支持与归因错误、请求及 token 用量。生成事实的分母包含有生成评分
样本中的全部必要事实，包括遗漏或不可能容纳的事实；没有生成结果的样本单独通过覆盖情况披露，不记作零分。
缺失用量为 null，部分用量标记不完整，报告不自行换算货币成本。

最终决策只能为 `no_production_change`、`bounded_projection_follow_up_required` 或 `insufficient_evidence`。
未运行线上生成、关键审计未完成、覆盖不足或冻结文件未提交，都无法授权生产后续。
每个实验组都必须完成应有的生成或得到正常的对照预算拒绝；部分窗口已经准入时，仍需完成这些窗口的生成。
对照组运行失败、仅完成准备或语义审计未决，不能被候选组成功或样本记录数量齐全掩盖。

## 线上联合评测准备

[线上准备结果](LIVE_READY_RESULTS.zh.md)记录本轮实现、离线等价核验、完整请求预算和未执行事项。

[线上操作步骤](LIVE_RUNBOOK.zh.md)给出完整的准备、预算、独立 smoke、三个正式运行、人工审计和联合判定命令。
[线上契约](LIVE_PROTOCOL.md)及 `live-policy-v1.json` 固定门槛；v3 的选择策略、原有标注和历史结果不变。
`plan-live` 统计五组全部请求，`run-smoke` 使用独立范围，`compare-live` 同时绑定三个准备、三个原始及三个审计运行。
联合门槛检查实际生成的必要事实和精确字面量，并验证父运行、输入、引用、模型/endpoint 及真实返回 usage。
输入准备通过不能替代模型输出通过。已有历史 v5 线上结果和 v9 首组真实配对，尚未完成正式联合判定。
新增协议必须完成各自的 Git 提交冻结，不能借用历史冻结授权初始化 provider。

## 线上生成与人工审计

线上命令 `run-live` 必须显式传入 provider、model、凭据环境变量名、HTTPS endpoint、总请求上限和仓库外审计目录。
冻结清单与评测代码必须先进入 Git；命令自身不会创建提交。默认离线命令不读取凭据、不访问 provider。
具体命令见 [英文说明](README.md#opt-in-live-generation)。

本节 `run-live` 调用真实 `AtomicMemoryGenerationPipeline.extract()`，不调用 reconciliation 或 Memory 发布。
独立 v9_live 曾直接调用 SDK generator，其首组只作为协议诊断；v10 的 Pipeline 适配目前仅有本地验证。
SDK 重试和 HTTP 重定向关闭，每次生成最多一次请求，输出 token、超时、重复次数和温度由清单固定。
Ctrl-C 会传播取消，完整评测报告不会在取消后发布。不能由本地任务取消推断远端 provider 已停止计费。

仓库外私有审计目录权限为 0700，文件为 0600。运行前保存清单，每次成功返回的结构化输出、输入输出哈希及已知
用量立即追加到 `events.jsonl`，在下一次生成前 flush/fsync。即使同一样本有多个窗口，中途取消或耗尽请求额度，
此前输出也会保留。`STATE.json` 区分运行中、取消、失败和完成；完整运行才汇集出供审计使用的 `outputs.json`。
进程被强制终止时可能留下运行中状态或最后一行不完整；这些材料不能当作完整评测。本实现不支持自动续跑，
也不承诺机器断电恢复。未完成 journal 只供本地检查，`audit` 仍要求原始完整运行。

无法确定的语义改写进入人工审计。审计记录绑定运行、候选和输出哈希，重新回放时验证输入与输出一致，
并检查引用范围和精确字面量；人工结论也不能给未准入的内容补造支持。`audit` 不产生新的模型请求。
未知样本、未知审计对象、重复调用位置或被修改的私有输出会在发布前被拒绝；原始请求、用量和时间记录保持原值。

## 私有语料与限制

私有清单只使用安全标签、环境变量选择器和文件哈希，内容与人工审计材料保存在仓库外。
聚合结果不输出 Source 原文、prompt、模型响应、凭据、敏感路径或物理 Scope 标识。
私有运行与合成矩阵独立报告，不能代替合成矩阵的生产决策门槛。

本评测的 SQLite、合成文本和本地协程证据，不证明真实宿主 payload、OceanBase、生产工作器或远端取消行为。
字段偏移与片段引用是评测契约，不能据此宣称生产 SourceRef 已具备片段级引用能力。
详细算法、完整性和成本定义见 [PROTOCOL.md](PROTOCOL.md)。
实现检查与完整 issue 验收的差异见 [验收状态与限制](ACCEPTANCE.zh.md)。

## 引用协议实验

使用 `--experiment-version v4` 明确选择[引用协议](V4_PROTOCOL.md)。它保留 v3 选择算法，独立升级证据编码、统一引用指令和样本/报告格式；事实标注与门槛不变。所有准备、比较、计划、smoke、审阅与正式命令均需携带该版本参数。

新 bundle 不向模型提供内部 range ID。非法引用继续失败，已返回的用量照常记录；旧实验继续使用原编码。smoke 可独立审阅为 `audited_smoke`，达到 `ready_for_formal=true` 后才能为正式运行提供证明。两种 smoke 都不能参与正式联合判定。完整步骤见 [v4 操作说明](V4_RUNBOOK.zh.md)。

[实验 v5](V5_PROTOCOL.md) 将保守选择成本与模型输入编码分开：按原始 v3 实验选择，选定后移除内部 ID、使用简明引用指令并重新核算实际成本，要求五组选择范围完全保留。所有命令明确使用 `--experiment-version v5`，见 [完整步骤](V5_RUNBOOK.zh.md)。v4 的已知对照组回归保留披露，不覆盖其结果。

## 当前验收状态

真实生成已取得历史 v5 结果及 v9 首组两次请求；正式联合验收仍未完成。
最新统一状态见 [验收对照](ACCEPTANCE.zh.md)，本地修复的执行与评分合同见
[v10 协议](V10_PROTOCOL.zh.md)。[v11 协议](V11_PROTOCOL.zh.md)补齐真实 Source 数缩减的完整协议预算，
重新执行 324 个窗口样本并按哈希复用 1296 个未变对照样本；六个历史拒绝全部恢复，未观察到输入覆盖退化。
成本和证据边界见 [v11 结果](V11_RESULTS.zh.md)，旧失败结果保留。
当前唯一总体决策为 `insufficient_evidence`，没有生产后续授权。


[v12 小样本执行合同](V12_PROTOCOL.zh.md)提供独立真实 Pipeline 入口，先验证精确字面量，再按审批范围与审阅结果
决定是否验证 Unicode。发送前核验实际 HEAD blob、SDK 预检和计划绑定预算；默认预检只用受控模拟响应。
它不构成全组正式矩阵，不覆盖旧失败或授权生产修改。


v12 提交冻结后的首个真实请求因 `length` 失败：2048 个输出 token 全用于 reasoning，最终内容为空。
Unicode 未运行，失败与离线回放见 [真实结果](V12_RESULTS.zh.md)。总体仍为 `insufficient_evidence`。

## 显式 thinking 小样本执行

[v13 执行合同](V13_PROTOCOL.zh.md)固定关闭 thinking、2048 输出上限、实际 HTTP 正文校验、分离的 Pipeline 状态和历史预算承接。
入口支持离线 SDK 预检、原始响应失败回放、提交冻结、逐例真实运行、绑定审阅和脱敏报告。
精确字面量样本通过审阅后才可运行 Unicode；任何失败均停止，不自动重试或扩展正式矩阵。
两例诊断通过也不等于 issue 验收或生产授权，正式联合比较和关键审阅仍须单独完成。

[v13 真实结果](V13_RESULTS.zh.md)：首例返回结构化候选，但四个精确字面量保留 0/4，冻结质量门槛失败。
已停止且未执行 Unicode 或正式矩阵；最小回放复现失败，总体决策保持 insufficient_evidence。
