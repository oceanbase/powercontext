# v19：冻结准入比较的剩余请求补跑

本协议只延续 v18 的线上执行。准入算法、合成 Source、三种 arm、预算矩阵、原生提示、输出 schema、原子事实和字面量评分、SDK 版本、模型设置均保持原字节。它不修复模型输出，也不改变生产行为。

## 固定范围

v18 的 972 行比较共有 471 个实际请求。原会话已尝试最前面的 4 个，其中 3 个有响应和用量，第 4 个无响应及用量。四个请求全部排除重发；新会话只执行后续 467 个，顺序不变。继承的 preparation、state、preflight、原始记录及剩余请求清单的确定性哈希固定在 `v19/contract.json`。

准备时验证 v18 原有冻结文件逐字节一致，核对 Python、Unicode 数据库和 SDK 版本，将原私有输入、审计记录及派生审阅复制到隔离的新私有目录，原会话保持不变。新 preparation 的身份包含新增执行代码，并显式绑定原身份。初始化前，剩余 467 个请求必须逐一通过真实 SDK/Pipeline 的离线 MockTransport 预检，请求体哈希必须与 v18 的对应值一致；这不算线上覆盖。

每次真实调用前，仍然执行真实 Source 持久化、关闭、重开和投影，重新构建原子记忆提取输入并与冻结值比对，然后进入实际 `AtomicMemoryGenerationPipeline`。模型设置保持 `deepseek-v4-flash`、`https://api.deepseek.com`、thinking disabled、temperature 0、max_tokens 2048、timeout 60 秒、重试 0、并发 1。别名实际返回 `deepseek-flash`，不声称冻结了物理模型。

## 预算与停止

默认仍为用户已批准的 v9 起累计 ¥5。新的金额需要另行明确批准，并在准备时写入授权来源，不能把“补完请求”解释为无限加价。输入按高峰未命中 ¥2/百万 token、输出 ¥8/百万 token 计算已知费用上界，不依赖缓存或空闲折扣，也不声称是账单。

继承已知费用为 v9+早期 ¥0.110650 加 v18 的 ¥0.005214。原第 4 次未知用量始终保留 ¥2.113536 满上下文预留，不推算为零。发送下一次请求前，累计已知费用、所有未知请求预留及下一次 ¥2.113536 预留之和必须不超过批准上限。467 次按原输入估算与输出封顶合计约 ¥10.62；估算无法保证实际费用或覆盖完成。

普通质量失败记录后继续其余固定请求，不重试、不改提示。新的未知用量、预算预留不足、模型变化、身份变化、审计失败或取消均停止本补跑会话；不重置会话。取消发生于 Source 重建或请求中都必须保留终态；响应已经收到时用量仍然入账。评分前先持久化原始结果和用量，避免评分异常擦除费用记录。

## 报告与结论

合并报告包含原 4 次和新增执行记录，分别报告“计划请求均已尝试”和“响应、用量、审计完整”。即使剩余 467 次全部尝试，原第 4 次仍然未知，不能将 471 次描述为全部成功或完整验收。原始自动评分及派生审阅分开保留，语义改写尚未审阅时标为 unresolved，不推断成事实丢失。现有冻结决策合同下，原未知调用仍使最终决策为 `insufficient_evidence`，不授权生产修改。

## 运行与重现

在源码 Git 检出目录中，凭据只从进程 `DEEPSEEK_API_KEY` 或用户指定的本地配置读取。不得将密钥放入命令参数、日志或交付工件。`OLD_SESSION` 和 `NEW_SESSION` 是操作者指定的私有目录；新目录必须不存在，原目录不得替换。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 prepare \
  --source "$OLD_SESSION" --session "$NEW_SESSION"
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 preflight \
  --session "$NEW_SESSION"
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 freeze
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 initialize \
  --session "$NEW_SESSION"
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 run \
  --session "$NEW_SESSION" --execute-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 status \
  --session "$NEW_SESSION"
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 report \
  --session "$NEW_SESSION" --output "$NEW_SESSION/final-report.json"
```

新预算只有在获得明确批准后，才可于 prepare 追加 `--approved-total-cny` 和 `--authorization`；后者只记录授权说明，不包含凭据。开始线上调用前提交本地源码冻结，不推送。运行中按 Ctrl+C 取消，不自动重发；核对终态和记录后再决定新的后续工作。报告只能输出到不存在的文件，重生成应换新文件名并核对确定性内容哈希。原响应仅保留于私有目录，公开交付只包含脱敏分数、计数、审计哈希和限制。
