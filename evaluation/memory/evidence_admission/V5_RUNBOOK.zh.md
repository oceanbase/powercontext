# 引用协议线上评测

实验 v5 按原始 v3 实验的封装及指令成本选择片段，选定后再转换为不含内部片段 ID 的模型输入。模型使用更简明的统一引用指令，实际输入成本与保守选择成本分别记录，前者不得超过后者。默认实验仍为 v1，必须明确指定 `--experiment-version v5`。事实标注、共同可容纳性参考和验收门槛不变，见 [协议](V5_PROTOCOL.md)。

## 离线准备与冻结

从仓库根目录执行以下命令，使用尚不存在的新目录：

```bash
uv run --project evaluation evidence-admission prepare --experiment-version v5 \
  --output-dir .artifacts/evidence-admission/v5-preparation-primary
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v2 \
  --output-dir .artifacts/evidence-admission/v5-preparation-validation
uv run --project evaluation evidence-admission prepare --experiment-version v5 --dataset validation-v3 \
  --output-dir .artifacts/evidence-admission/v5-preparation-additional
uv run --project evaluation evidence-admission compare-preparation --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-preparation-primary \
  --run-dir .artifacts/evidence-admission/v5-preparation-validation \
  --run-dir .artifacts/evidence-admission/v5-preparation-additional \
  --output-file .artifacts/evidence-admission/v5-comparison.json
uv run --project evaluation evidence-admission plan-live --experiment-version v5 \
  --preparation-run-dir .artifacts/evidence-admission/v5-preparation-primary \
  --preparation-run-dir .artifacts/evidence-admission/v5-preparation-validation \
  --preparation-run-dir .artifacts/evidence-admission/v5-preparation-additional \
  --output-file .artifacts/evidence-admission/v5-request-plan.json
```

三组共 1620 个样本；候选必需事实分母固定为 495。保留率、精确字面量、故障/取消状态和配对回归检查全部通过才进入线上。要求五组的选择范围、事实、字面量、状态和完整性与原始 v3 实验完全一致；编码与指令改变仍会影响实际输入哈希和成本，需单独披露。重新核算正式调用上限，不沿用旧实验的 1077 次。

通过本地代码与测试检查后创建评测代码、数据、清单及依赖的本地冻结提交。不要把其他已暂存改动混入冻结提交，不推送。线上命令会核对源码与冻结文件和 HEAD 的字节一致性。

## 小规模真实调用

从安全的本机配置为进程注入凭据，`EVIDENCE_ADMISSION_KEY` 是变量名占位，不在参数中填密钥。`EVIDENCE_AUDIT_ROOT` 必须位于仓库外，私有目录不能复用。固定两个用例、五组、4K 和一次重放，预计 8 次请求；零 SDK 重试，最大输出 2048 token，超时 60 秒。

```bash
uv run --project evaluation evidence-admission run-smoke --experiment-version v5 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests 8 --audit-dir "$EVIDENCE_AUDIT_ROOT/smoke" \
  --output-dir .artifacts/evidence-admission/v5-live-smoke
```

CLI 正常退出只代表结果保存完成。检查原始报告的 scope、execution、失败分类和已返回用量。失败请求若已有用量照常计入；未知用量不能计零。

## 审阅与放行

私有 `outputs.json` 保存实际候选，`events.jsonl` 保留追加记录。用原始 manifest 的 digest 和实际候选哈希绑定 reviews 文件，具体格式与语义约束见 [审阅格式](LIVE_RUNBOOK.zh.md#人工审计)。没有未决语义时也使用绑定该运行的空 reviews。逐一核对候选含义、精确值和合法引用；不能修改输出或把内部定位 ID 转换成合法引用来通过。

```bash
uv run --project evaluation evidence-admission audit --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-live-smoke \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/smoke/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/smoke/reviews.json" \
  --output-dir .artifacts/evidence-admission/v5-audited-smoke
uv run --project evaluation evidence-admission report --experiment-version v5 \
  --run-dir .artifacts/evidence-admission/v5-audited-smoke
```

审阅产物为 `audited_smoke`，原始 `smoke` 保持不变。必须达到 `smoke.ready_for_formal=true`：执行范围完整，引用和用量合法，未决语义为零，所有组无已知不支持/错误归属/非法支持，候选必需事实和字面量 100% 保留。smoke 的正式验收结论始终为 `insufficient_evidence`，不能加入正式比较。

## 正式矩阵

三个数据集依次执行，使用相同 provider、精确模型 ID 和 endpoint，额度来自新请求计划。每次 `run-live --experiment-version v5` 还必须提供以下两项：

```text
--smoke-run-dir .artifacts/evidence-admission/v5-live-smoke
--smoke-audited-run-dir .artifacts/evidence-admission/v5-audited-smoke
```

命令在初始化模型之前核验原始和审阅 smoke 的派生关系、当前冻结代码与 provider 身份，并将两份 manifest 哈希写入正式记录。正式输出仍需分别审阅为 `audited_live`，再通过 `compare-live --experiment-version v5` 联合三个准备、三个原始和三个审阅结果。smoke 不替代任何正式样本。任何失败都保留，不能挑选重试结果拼成完整运行。

最终通过只意味着可另行设计生产证据准入契约，不直接授权生产投影、记忆写入或发布行为变更。
