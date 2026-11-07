# 剩余 351 次线上请求执行协议

本协议只延续固定的准入比较，不调整生成提示、输出 schema、Source、准入参数、评分或模型设置。
用户在累计 ¥20 预算提案后明确要求开始；批准上限为 v9 起累计 ¥20，不是新增 ¥20。

## 冻结范围与继承

完整比较为 972 行、471 个实际请求。原会话已尝试 120 次，包含两个无响应及 usage 的请求。
本轮只执行原顺序中其后的 351 次，排除全部 120 次历史尝试，失败与未知请求均不重发。
父会话的 preparation、state、preflight、记录前缀、身份及剩余清单哈希固定于 `v20/contract.json`。
父会话停止状态和全部原始/派生结果保持不变。

准备时逐字节核对父会话冻结的生产及评估文件，并比较 Python、Unicode 数据库、SDK、原生指令和 schema 身份。
新增执行代码产生独立身份，保留原冻结谱系。私有输入与审计复制到隔离新目录，禁止覆盖已有会话。
351 个请求必须先经过无网络实际 SDK/Pipeline 预检，其请求体哈希逐一等于父会话预检的对应值。
本地源码提交冻结后才可初始化并调用 provider。

每次调用前仍重新持久化、关闭、重开真实 Source，投影并重建真实 `AtomicMemoryExtractionInput`，
校验 Source 身份、准入范围和输入哈希，再进入实际 `AtomicMemoryGenerationPipeline`。
使用 `deepseek-v4-flash`、`https://api.deepseek.com`，thinking disabled、temperature 0、max_tokens 2048、
timeout 60 秒、重试 0、并发 1。别名路由及高峰计价已核对 [DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，
不声称冻结了物理模型快照，也不将本地估算 token 称为 provider 实际 token。

## 预算和停止

继承 v9 起累计已知费用上界 ¥0.688902。两次未知调用各保留 ¥2.113536，总预留 ¥4.227072；
未知不计零、不由相邻响应推算、不释放。开始时受保护总额 ¥4.915974，累计 ¥20 下余额 ¥15.084026。
每次发送前还必须容纳 ¥2.113536 的下一次满上下文预留。

收到 usage 后按冻结高峰未命中输入 ¥2/百万 token、输出 ¥8/百万 token 计算已知费用上界，
不利用缓存或空闲折扣释放额度；这不是账单。剩余输入估算 1,194,564 token，输出最多 718,848 token，
未来估算费用约 ¥8.14，不保证实际覆盖或费用。累计已知、全部未知预留和下一次请求预留不得超过 ¥20。

普通质量失败原样记录后继续独立请求，不重试、不修复输出。
新的未知 usage、费用预留不足、模型或身份改变、Scope/审计失败、取消均停止本会话，不自动恢复。
Ctrl+C 在 Source 重建或 HTTP 阶段均须保留终态；已收到用量仍计费。
只增加稳定的本地失败类别，禁止异常值、响应正文或凭据进入公开审计；本地类别不等于 provider 根因。

## 评分、报告与证据边界

沿用冻结的原始自动评分。语义改写未审阅时保持 unresolved，原有三个助手诊断继续独立保留，
不作独立人工验收。新增请求失败不会成为修改指令、编码或 schema 的理由。
未知响应须与已确认引用/输出错误分开，配对解释必须额外检查实际响应覆盖。
原报告宽泛 `known_quality_failure_rows` 标志可能包含未知输出，不能将其全部解释为已确认模型质量错误。

即使本轮 351 次全部尝试，原两个缺失响应仍然缺失，不能声称 471 次全部成功或完整验收。
按继承的冻结决策合同，这些未知仍使结论为 `insufficient_evidence`；不存在生产修改授权。
公开交付只保留配置、哈希、计数、覆盖与分数，完整提示和响应留在仓库外私有审计。

## 显式命令

需要源码 Git 检出、锁定环境和本地 `DEEPSEEK_API_KEY`；不得将密钥写进命令或交付。
`PARENT_SESSION` 为父会话私有目录，`SESSION` 为尚不存在的新私有目录。

```bash
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 prepare \
  --source "$PARENT_SESSION" --session "$SESSION" --approved-total-cny 20.00 \
  --authorization user_start_after_explicit_cumulative_20_CNY_proposal
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 preflight --session "$SESSION"
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 freeze
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 initialize --session "$SESSION"
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 run --session "$SESSION" --execute-live
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 status --session "$SESSION"
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 report \
  --session "$SESSION" --output "$SESSION/final-report.json"
```

默认测试和预检不访问网络、不需要凭据。报告重生成应使用不存在的新输出文件名，核对确定性内容及字节哈希。
停止后不重置状态或重发未知请求；所有本地提交均不推送。
