# 显式关闭 thinking 的真实 Pipeline 小样本合同

本合同验证评估维护者能否在固定输出上限下取得可审阅的真实模型输出。它是单候选诊断，不是正式联合比较。
总体决策在正式覆盖与关键审阅完成前为 `insufficient_evidence`，不授权生产修改。

## 固定范围

沿用 v10 指令、TemplateOutput、copy 编码、原子/父事实评分和独立的旧整句诊断；不改变候选选择或标注。
来源仍经实际 Source 写入、本地 SQLite 持久化、重读和真实投影，再进入 AtomicMemoryGenerationPipeline.extract。
按照已保存 v11 请求身份验证准入集合。只运行 synthetic-v1、bounded-fragments-v3、4096、repetition=0，
顺序为 exact-literals 和 unicode-boundaries，每例最多一个请求。第一例审阅通过后才可进入第二例。

## 请求与审计

请求 deepseek-v4-flash，base URL 为 https://api.deepseek.com，temperature=0、max_tokens=2048、JSON 输出。
通过 SDK extra_body 显式传入 thinking.type=disabled，传输层在触网前验证最终请求正文；禁用重试、重定向和并发。
SDK 预检保存完整正文哈希，真实请求必须逐项匹配。关闭 thinking 不保证生成成功，也不保证输出确定性。
响应模型与 fingerprint 需保持会话内一致，记录请求别名不等于锁定物理模型。

分别记录 pipeline_entered、pipeline_returned_successfully、provider_request_started、provider_response_received、
structured_output_validated、protocol_output_validated 和实际耗时。进入 Pipeline 不等于成功返回候选。
length/空最终内容保持 response_incomplete；stop/空内容、JSON、schema、copy、引用和质量失败保持各自分类。
若 disabled 请求返回非零 reasoning_tokens，则另记 thinking_response_mismatch 并停止。
无有效候选时不运行质量评分，保留 null；任何失败、未知发送、缺失用量、模型元数据变化或取消均停止，不自动重试。
取消传播且保留未解决预留，不把日志或结算失败替换为业务成功。

原始请求、响应、审阅和审计只保存在仓库外 0700/0600 私有目录，不保存认证头或密钥。
公开结果只保存安全标签、范围、哈希、状态、计数、用量和条件费用。

## 评分与推进

每例必须保留全部冻结原子事实、父事实及精确字面量，零关键不支持断言、错误归因、无效引用和非原子候选，
零 unresolved，且至少有一段合法 copy。Unicode 按原始 UTF-8 字节核验，不规范化 emoji、组合字符或 CRLF。
copy 与独立原子性是额外实验门槛，不将其表述为 issue 原文唯一实现要求。
审阅绑定 case、输入、支持范围和输出；assistant 审阅仅是诊断，不作为独立人工正式验收。
旧整句分数、历史复合候选、v12 length 失败和未知历史根因原样保留，不能用新增诊断追认旧实验通过。

## 冻结与费用

触网前，源码、数据、依赖、Ruff 配置、本合同及历史预算来源的字节必须与实际 Git HEAD blob 相同。
运行前提交冻结；暂存、事后提交和模拟提交标记不能替代。源码修复使用新执行身份，不改写历史结果。
官方参数与费率核对日期为 2026-10-31：
[思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)；
[模型及价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。
冻结峰时全未命中费率输入每百万 token ¥2、输出 ¥8；最大输入 1048576、最大输出 2048，
单次条件费用预留 ¥2.113536。完整 usage 结算后释放差额；费用是条件上界而非账户硬限制或实际账单。
承接已批准 ¥5 总额和已记录累计上界 ¥0.053030，本会话可用上限 ¥4.946970；不能用新账本忽略旧支出。
计划绑定既有用户授权、最大两例、金额和当前别名路由；超出授权范围不执行。

## 命令

以下输出目录须不存在，默认步骤不读取 DEEPSEEK_API_KEY。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 preflight --baseline-dir "$POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS/v11-offline" --output-dir .artifacts/evidence-admission/v13-preflight
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 replay --baseline-dir "$POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS/v11-offline" --historical-session .artifacts/evidence-admission/v12-readiness/live-session --output-dir .artifacts/evidence-admission/v13-failure-replay
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 freeze
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 prepare --baseline-dir "$POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS/v11-offline" --output-dir .artifacts/evidence-admission/v13-session --mode live --preflight-dir .artifacts/evidence-admission/v13-preflight
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 run --session .artifacts/evidence-admission/v13-session --approval /outside-the-repository/approved-plan.json --execute-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 review --session .artifacts/evidence-admission/v13-session --review-file /outside-the-repository/review.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 status --session .artifacts/evidence-admission/v13-session
uv run --project evaluation python -m powercontext_eval_evidence_admission.v13 report --session .artifacts/evidence-admission/v13-session --output-dir .artifacts/evidence-admission/v13-result
```

先提交冻结，再在该提交下重新预检和准备会话。任一步失败即停止；两例通过后仍需独立冻结正式联合比较。
完整比较须包含相同设置下的对照、窗口和候选、所有冻结数据/预算/重放、真实用量及关键审阅，不能继承旧协议模型输出。
只有正式 gate 全部通过，才选择 bounded_projection_follow_up_required 并生成单独生产后续契约。
