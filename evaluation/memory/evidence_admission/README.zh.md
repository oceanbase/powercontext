# Source 证据准入评测

本评测回答：超大或混合 Source 是否需要在 Atomic Memory 提取前增加有限预算的证据准入步骤。
实现仅位于 evaluation 工程，不修改生产投影、注册表、Memory 发布、检索、生命周期或 API。

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

`results/synthetic-v1-preparation/` 保存本地模型无关结果。未运行线上模型时，候选正确性、错误归因等指标为 null，
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
输入准备通过不能替代模型输出通过。实际线上结果尚未取得，未提交冻结仍阻止 provider 初始化。

## 线上生成与人工审计

线上命令 `run-live` 必须显式传入 provider、model、凭据环境变量名、HTTPS endpoint、总请求上限和仓库外审计目录。
冻结清单与评测代码必须先进入 Git；命令自身不会创建提交。默认离线命令不读取凭据、不访问 provider。
具体命令见 [英文说明](README.md#opt-in-live-generation)。

线上阶段调用真实 `AtomicMemoryGenerationPipeline.extract()`，不调用 reconciliation 或 Memory 发布。
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
