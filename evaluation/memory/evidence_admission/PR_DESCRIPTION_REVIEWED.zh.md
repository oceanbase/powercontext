# Which issue or RFC does this PR close?

关联 [oceanbase/powercontext#2074](https://github.com/oceanbase/powercontext/issues/2074)。
本 PR 交付评估及其受限结果，唯一冻结决策为 **`insufficient_evidence`**，不宣称全部验收门槛通过。
是否据此关闭研究 issue，由维护者结合未决项审查决定；本 PR 不自动关闭 issue，也不授权生产后续。

# Rationale for this change

现有 Atomic Memory 处理器通过缩减 Source 数量应对总输入超预算，但无法在一个 Source 本身超大时继续缩减。
混合证据中的工具输出、脚手架、重复内容和大字段也可能挤占事实空间，并带来错误归因风险。
本评估衡量是否需要有版本、可界定预算和可取消的证据准入层。

评估从实际 Source 写入开始，持久化、关闭并重读，再使用真实投影构造 `AtomicMemoryExtractionInput`。
线上阶段调用实际 `AtomicMemoryGenerationPipeline`，分别记录准备、调用、输出校验、支持范围、费用与审阅状态。
输入覆盖改善与最终生成质量分开报告，不把 Source 引用或请求成功当作事实保留证明。

# What changes are included in this PR?

## 评估包与固定比较

- 增加 `evaluation/memory/evidence_admission/`，提供版本化 CLI、合成夹具、实验清单、准入策略、
  确定性评分、无模型测试、选择加入的线上执行和内容安全的汇总报告。
- 增加只读人工抽查附录工具：严格验证原记录哈希，保留多选歧义和部分判断，禁止写入原会话或覆盖已有附录；
  另有独立脚本在原冻结环境重算上下文，避免新 schema 替代历史合同。
- 必需用例涵盖单源及多源溢出、混合/超大字段、位置事实、重复、伪角色、精确字面量、Unicode、
  不可能完整性、故障、取消、重放、Scope 隔离和遗漏说明。
- 正式固定比较共 972 行：36 个用例、3 个 arm、3 档预算和 3 次重放；原生指令、输出 schema、模型设置和预算共同冻结。
  可信结构分层 arm 因 Content adapter 没有可信结构来源而不适用，不能从文本角色标记补造。
- 保留全部历史协议、失败、停止原因和冻结提交，避免将不同生成协议的结果拼接成一个比较。
- 与上游已落地的 `capture_ownership` 共存于 evaluation 项目，保留两套注册、依赖、测试和打包配置。
  所有权污染与大小准入仍是独立的分数和决策，没有合并结论或引入通用角色清洗器。

## 原冻结线上结果

全部 471 次计划请求均已尝试，未运行数为 0。469 次有可审计响应；两次历史响应及用量不可用。

| arm | 尝试请求 | 有效 Pipeline 输出 | 已知输出失败 | 历史不可用 |
| --- | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 42 | 41 | 0 | 1 |
| window-reduction-v1 | 102 | 102 | 0 | 0 |
| bounded-fragments-v3 | 327 | 148 | 178 | 1 |
| 合计 | 471 | 291 | 178 | 2 |

这些是调用状态，不是独立事实保留率。候选的历史离线输入覆盖为父事实 495/495、原子事实 531/531，
不能抵消以下输出问题：

- 177 次候选输出引用请求中不存在的 evidence ID，涉及 292 个候选，被真实 Pipeline 拒绝。
- 1 次候选输出达到共同输出上限，未形成有效结构化输出。
- 6 个有效 Unicode 输出分别只出现 1/3 个必需字面量。
- 533 个候选语义未决，其中 226 个来自有效输出，307 个来自被拒绝输出。

469 个可用响应的历史离线回放全部匹配；两次不可用响应不计为回放通过。
累计已知费用上界 ¥3.521362，加未知调用预留 ¥4.227072，受保护总额 ¥7.748434，低于批准的累计 ¥20。
这不是账户账单。本次上游整合没有新增 provider 调用。

## 人工审阅与证据边界

人工审阅者为任务发起用户本人，公开标识为 `issue-2074-requesting-user`。用户报告46条抽查描述，
覆盖有效输出优先队列的全部11个场景；29条可唯一定位，17条仍需补充 arm 或请求下标。
没有将46条描述当成46个已判定候选，也没有将同预算、重放下所有匹配调用认作都被人工审阅。

已绑定15项部分维度判断：两个有效输出中的当前用户错误归因，以及13个非原子候选。
未补造其余 supported、atomic、false_attribution 或事实 ID 判断，完整 v10 语义审阅新增数为0，
原机器533项未决及原自动分数保留。新的判断作为独立附录，不能覆盖旧记录或批准被拒绝输出。

程序确认被审13次 ASCII 字面量输出各保留4/4；四次 Unicode 输出各只保留1/3。
多源场景2K的8次请求与4K的2次请求应按完整样本聚合，准备覆盖均为8/8，不能以单次输出描述为准入缺失。
相同 mixed-scaffolding 2K请求的不同生成输出，也不等于输入不确定或原响应离线回放失败。

在原提交及原锁定环境中，630个依赖、76个可能调用和112个候选出现位置的原上下文绑定已核验。
这些是程序验证范围，不是人工签认数量。公开附录只有配置、标签、计数、判断和哈希，没有原始提示或响应。

当前候选因已确认的引用、字面量及新增人工归因/原子性失败，未获生产后续资格。
结论限定已测负载与配置，不推断所有有界策略无价值。按本轮冻结规则，两次历史不可用和未决语义仍使
最终决策为 `insufficient_evidence`，没有查看结果后修改门槛。

详细范围、审阅重现命令及剩余验收项见
[人工审阅说明](https://github.com/Lkx-JY/powercontext/blob/evaluation/2074-bounded-evidence-admission/evaluation/memory/evidence_admission/HUMAN_REVIEW.zh.md)。

## 上游整合与 schema 兼容

- 合入本次从两个远端获取的 `master`：`b8b189091e7a412300cf1292531208a8ead0e325`。
  解决 9 个配置冲突，保留上游迁移资源换行约束、Helm/固定夹具检查配置、升级策略检查、
  三套锁文件检查、串行 E2E 步骤、agent-tool-recovery 测试及 tox 环境隔离。
- 使用上游 evaluation 锁文件作为起点增加本套件依赖；包名与包版本集合相对该上游锁文件不变。
- 对齐上游原生候选的必填 `description` 字段。模板输出显式提供该字段并原样传递，原生响应保持严格校验，
  不为缺字段的实际响应生成默认摘要，不补造或修改历史响应。
- 正文精确匹配但 description 尚未获得支持判定时，不自动批准完整保留；已知无支持或字面量错误仍保留失败。
- 原 8 个预算回归固定其原公开指令上下文及 SHA-256，保留原断言。当前默认执行仍使用上游原生指令。
  新指令下 v2 的位置事实和 observation 2K 样例各漏一个必要事实，另有当前运行时回归明确保留这些损失；
  没有修改策略参数、预算或阈值以使它们通过。
- 历史窗口/单元预算测试同样显式固定公开指令上下文；一项无模型历史单元成本测试还固定原 schema 表示及哈希，
  当前模型校验仍要求 description。当前指令下 legacy v16 的 2K 多源单元计划
  仍被预算拒绝，另有回归保留该结果；没有把历史预算通过追认为当前通过。
- 上游投影哈希变化后，旧记录中的范围 ID 不再假定属于当前输入；保留旧引用失败，并另外测试当前范围 ID
  也不能冒充顶层 evidence ID。
- 历史 630 个冻结依赖均可从原执行提交逐字节核验；整合树中有 171 个对应路径发生变化。
  原 21 个线上交付索引文件和 8 个审阅包索引文件保持原字节。新代码拒绝重算旧身份会话，
  历史线上结果必须在原提交及原锁定环境下重现，不能追认为已在当前组合上验证。
- 依赖静态 v11 基线的 v12/v13 正向会话测试，在当前不兼容的 schema 下明确跳过，不能计作通过。
  新增两项测试验证当前代码拒绝旧基线且不创建会话；Git 提交字节检查仍执行，不放宽身份保护。
- 审计测试的合成响应补齐当前必填字段，使引用错误与领域错误仍能到达目标校验；真实缺字段响应仍按 schema 拒绝。

## 随评估保留的工程修复

- 历史 v14 合同退出自动 JSON 重排，改由独立脚本校验已冻结的原始字节；仍执行 JSON 语法检查。
- LongMemEval 用显式的可选 evidence reader 协议处理尚不可用的精确证据能力，保留运行时拒绝与 revision 传递，
  消除直接访问不存在成员的类型诊断，不增加替代证据路径。
- SWE-bench Pro 的 MCP 计数校验兼容整数和浮点样本，继续拒绝非有限、负值及非整数计数。
  这些修复只位于评估及检查工具中，不修改生产行为。
- v12 Git 冻结守卫测试显式创建被忽略的测试父目录，消除对本地历史 `.artifacts/` 的依赖；
  已提交字节通过、未跟踪字节拒绝的原断言保持不变。

# Are there any user-facing changes?

增加评估命令、测试和报告；生产 `src/powercontext/` 相对上述上游提交无差异。
本 PR 不新增生产截断、投影注册、功能标志、迁移、公共 API 或 Memory 发布行为，也不改变授权、
Source 窗口、CAS、取消、搜索、排名、引用或生命周期语义。

上游已引入的 1.5.0 与原生提取 v3/description 合同是整合基线，不是本评估新增的生产接口。
原始提示、响应和私有审计留在仓库外；公开交付只保留允许的合成夹具、配置、标签、范围、计数及哈希。

历史重现依赖本分支内的冻结提交。合并和清理分支时应保留这些提交的可达性；若最终采用 squash，
需要另行保留可检出的冻结引用，否则仅有报告中的提交哈希不足以保证后续获取原执行代码。

# How was this change tested?

上游整合阶段的实际命令、通过/失败记录和未执行项目以
[本次验证凭据](https://github.com/Lkx-JY/powercontext/blob/evaluation/2074-bounded-evidence-admission/evaluation/memory/evidence_admission/results/upstream-sync-20261107/validation.json)为准；
[整合说明](https://github.com/Lkx-JY/powercontext/blob/evaluation/2074-bounded-evidence-admission/evaluation/memory/evidence_admission/UPSTREAM_INTEGRATION.zh.md)区分当前检查与历史实测。

本轮新增人工附录的实际检查见
[审阅验证凭据](https://github.com/Lkx-JY/powercontext/blob/evaluation/2074-bounded-evidence-admission/evaluation/memory/evidence_admission/results/v20-human-sample/validation.json)。
本轮完整无模型回归为 **538 passed、41 skipped**，原41项历史兼容性跳过保持不变。
新增14项审阅回归在3.11完整套件中通过，并在 Python 3.12、3.13、3.14分别专项通过；
本轮 make check、Ruff check/format、evaluation 类型检查及评估包 wheel/sdist 构建也通过。
原冻结环境重新核验的是上下文绑定，不是再次发起模型调用或重新批准旧质量门槛。
下表保留上游整合阶段的执行范围；本轮没有将根包完整测试、四版本完整 tox 或线上矩阵追认为重新执行。

| 上游整合阶段检查 | 实际结果与范围 |
| --- | --- |
| `make check` | 通过：三套锁文件、全部 hooks、根包及 Pydantic AI 集成类型、61 项集成清单测试 |
| `PYTEST_WORKERS=4 make test` | 通过：主阶段 9691 passed、848 skipped；串行 ZCode E2E 另有 4 passed，包含 doctest |
| evidence-admission 完整无模型回归 | 524 passed、41 skipped、0 failed；跳过项是依赖原基线的历史 v12/v13 正向会话 |
| 当前 Git 冻结目录修复 | 完整回归后仅补一行测试 mkdir；两项 Git 字节守卫专项检查通过，评估实现未再变更 |
| 原冻结 v12/v13 会话补验 | 原提交、原锁文件和 Python 3.11 下 43 passed、0 skipped；不追认为当前运行时通过 |
| 其他评估套件 | 928 passed、1 skipped，包含 capture-ownership、LongMemEval、work-continuity 等 |
| agent-tool-recovery | 104 passed |
| `make contract-test` | 生成代码一致性检查及 61 项测试通过 |
| Ruff / evaluation 类型 | 235 个 Python 文件格式通过，lint 与 evaluation 类型检查通过 |
| Python 3.11 / 3.12 / 3.13 / 3.14 | 每版 30 项 schema、v18 比较和当前范围 ID 专项检查通过；完整 tox 矩阵未运行 |
| `make docs-test` | 独立干净检出通过，校验 1095 个页面；同步生成输出后主检出 1097 个页面链接通过 |
| 根包与 evaluation 打包 | `uv build` 的 wheel、源码包构建通过；两套评估包、合成数据及 CLI 入口均已核验 |
| 完整性 | 原 21 + 8 个交付索引文件、630 个原 Git 冻结依赖核验通过，生产源码相对上游无差异 |

首次根测试因旧静态导出缺少 4 个上游页面而失败；重建导出后完整命令重跑通过。
审计测试补齐必填 description，以及历史测试父目录的前置条件失败、修复与重跑均保留在验证凭据中。
历史正向测试的跳过、完整 tox 未运行和 533 个语义未决，均不计为验收通过。

默认测试不需要 provider 凭据；本次整合不补跑线上矩阵，也不产生新的生成质量结论。
SQLite、受控传输和本地测试不能证明远端数据库、宿主或 provider 的实际取消行为。

# AI usage statement

OpenAI Codex 协助实现评估、测试、执行记账、冲突整合、分析与文档。
人工检查反馈及明确的部分维度判断由任务发起用户本人提供；助手整理绑定并执行程序核验，没有代签未审候选。
未决语义、不可用响应和验证限制均显式保留。所有变更仅在本地，未推送、未创建 PR。
