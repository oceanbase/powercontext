# 固定生成条件的准入比较

研究问题：在相同生成指令、模型设置和预算定义下，证据准入是否改善实际事实交付，伴随哪些损失与成本。
本协议只修改 evaluation；不修改生产 Source、投影、注册表、提取、reconciliation、Memory、游标或 API。
v5—v17 的冻结合同和失败保持原样；新结果不与历史协议混算，不恢复停止会话。

## 实验范围

三个 arm：complete-projection-v1、window-reduction-v1、bounded-fragments-v3。
三集合成数据 synthetic-v1、validation-v2、validation-v3；2K/4K/8K；三次重放，共 972 行。
所有数据已参与开发，属于已知回归和压力集，不声称独立未见测试。
可信结构 arm 不适用：当前 Content adapter 没有可信所有权结构，禁止从角色文本补造。
历史 v1/v2 策略不增加本轮付费调用。私有语料未执行，不能假定线上代表真实用户分布。

沿用 v5 片段选择算法、512 字节边界参数、原始范围束和排序，去掉后置 citation 重写。
三组都使用生产 CODING 提取指令和 AtomicMemoryExtractionOutput，不添加 copy、单元索引、字节计算指令或输出修复。
该变化会改变实际输入和成本，必须重新准备全部矩阵，不能继承 v16 的 495/495 作为新结果。
事实与字面量标注不变；独立断言的子事实沿用已声明的 v10 对照，不回写历史整句分数。

每个 arm 经过新 Scope 中的真实支持写入、SQLite 持久化、关闭、重读和真实投影。
各 arm 使用隔离数据库，但逻辑 Source ID、materialization 和 projection 哈希须逐配对一致；Scope 不跨组池化。
不是共享同一个物理数据库或 Scope，不能宣称验证真实宿主或远端后端。
同一 Source 身份重放的原始输入哈希必须一致，不通过剔除内容或身份字段制造确定性。
每次实际请求重新构造对应 Source 输入并核对准备哈希，再经真实 AtomicMemoryGenerationPipeline。

## 预算和执行

预算定义保持生产 character:weighted@1 对“指令 + 提取输入”的检查。
SDK 输出 schema、完整 HTTP 消息估算与 provider usage 单独记录，不宣称字符估算是真实 token 硬上限。
完整投影按当前完整输入检查拒绝；窗口按当前精确连续窗口缩减，包括单 Source 下限；禁止给对照拆片以绕过拒绝。
候选每样本最多 16 次请求、累计估算输入不超过预算 8 倍。对照保留原生窗口行为，不暗示累计调用成本相同。
每个请求预算、累计请求和 token 成本都逐样本披露，不能只比较单次成本。

模型 deepseek-v4-flash，endpoint https://api.deepseek.com，thinking disabled、temperature 0、输出 2048、60 秒。
串行、一次 HTTP 尝试、零重试、无 continuation。旧别名实际由官方当前 Flash 路由提供服务，不声称物理快照固定。
2026-11-06 核验官方峰时输入未命中 ¥2/百万、输出 ¥8/百万；来源见 contract.json。
¥5 从 v9 起累计，已知峰时上界 ¥0.110650；更早 13 份未知用量独立保留，不按零计。
每次须预留完整 1M 输入加 2048 输出的 ¥2.113536；返回完整 usage 后按保守上界入账释放预留。
余额不足以预留下一次则停止，不能为了完成矩阵降低保护或增加预算。完整矩阵不保证在余额内执行完毕。

## 停止与结果状态

本地输入、身份、范围、UTF-8、安全、确定性、取消和审计可靠性不通过时禁止进入线上。
候选覆盖损失是被测结果，直接取消其相应正向资格，不在本协议中修策略重跑。
字面量缺失、事实遗漏、非原子候选或有完整原始响应及 usage 的输出校验失败，保存后继续独立计划请求。
引用/无支持错误按关键质量失败计，不能平均掉；不会因为继续收集其他样本而被改成通过。
未知 usage、预算预留不足、模型身份异常、跨 Scope、审计失效、冻结变化或取消停止整轮。
in_flight 在发送前落盘，原始响应在解析前 fsync；中断、失败终态均禁止重发或重建账本绕过停止。
正常完成的已知失败可以继续下一计划调用，不重试替换。取消不发布 Memory 或生产游标。
故障/取消夹具在本地确定性路径验证；不为模拟取消发送付费请求，不声称真实远端取消成功。

## 评分与决策

报告分别展示准入状态、范围与覆盖、执行状态、实际支持输出。预算拒绝是零调用测量结果，不是模型字面量丢失。
所有样本计入端到端表；双方实际执行子集只作诊断，不外推，不仅保留成功返回样本。
未运行不能填零；语义未决不能当成确定丢失；“已证明交付集合差异”不等于真实语义净损失。
只允许完整精确断言与正确范围直接确定支持，其他改述保持未决，直到导入绑定输入/输出的审阅。
精确字面量按 UTF-8 字节检查，无规范化、strip、换行转换、自动补值或候选拼接。
独立子事实分别评分；关系型事实保持完整语义，不通过拼接无关候选补成通过。
重复/近重复占用、原子性、支持/归因、字面量、耗时、实际用量、逐样本失败分开披露。
LLM 不能成为关键事实、出处、取消和决策的唯一证据。assistant 审阅明确标记，不冒充独立人工。

正向门槛包含 issue 的 100% 可容纳必需输入、精确字面量、零关键无支持/错误归因、精确支持解析、
确定性、不完整性披露、取消无完成发布，以及一个可复现单 Source 或混合问题相对窗口的改善。
本协议另要求完整输出原子事实/字面量保留、零非原子候选；属于显式冻结附加条件，不冒充 issue 原文指定的实现。
完整且语义已决的比较中，候选失败或无合格改善可选择 no_production_change，结论限定受测负载。
覆盖不完整、费用停止、关键语义未决等选择 insufficient_evidence。
只有完整正向门槛通过才选择 bounded_projection_follow_up_required 并生成独立生产后续契约。
执行完成与质量通过分开；观察新生成问题不修改本合同，也不自动进入下一版编码研究。

## 命令和交付

私有 session 必须使用仓库外新目录，目录 0700、原始审计文件 0600；不得提交 prompt、响应、凭据或私有路径。
凭据只从 DEEPSEEK_API_KEY 或用户明确指定的 ~/.bashrc 同名纯字面量读取，不执行启动文件。
离线命令不读取凭据、不访问 provider；执行前提交冻结代码、数据、测试和合同，不推送。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 prepare --session /PRIVATE/v18
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 preflight --session /PRIVATE/v18
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 freeze
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 initialize --session /PRIVATE/v18
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 run --session /PRIVATE/v18 --execute-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 status --session /PRIVATE/v18
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 review --session /PRIVATE/v18 --call-id HASH --review-file /PRIVATE/review.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 replay --session /PRIVATE/v18 --output /PRIVATE/v18-replay
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 report --session /PRIVATE/v18 --output /PRIVATE/new-report.json
```

Ctrl-C 取消并记录终态；不得恢复 in_flight/cancelled 会话发送。raw 响应缺失时明确回放不可用，哈希不能代替原文。
审阅使用 v10 Review schema，包含 reviewer、reviewer_kind、output_hash、context_hash、逐候选 judgements。
首次自动审阅在私有 audit 中，可作为格式和绑定模板；导入审阅生成单独派生文件，不覆盖原始响应或自动评分。
公开交付包含机器报告、逐样本覆盖、配对结果、代码/数据/配置哈希、费用限制和验收对照。
历史材料单独引用；最终报告清楚列出未运行项、未决语义与当前主线未适配风险。
