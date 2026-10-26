# V3 线上评测操作步骤

本流程使用冻结的三个数据集、五个实验组和共同评分规则。先准备与核算，再执行独立 smoke、正式生成和人工审计。
当前 provider、模型和凭据选择器尚未确定；未提交代码不能运行 `run-smoke` 或 `run-live`。
这些命令不会自动创建提交，也不能把本地受控测试当成真实 provider 结果。

## 准备与预算

使用最终评测代码分别完成三个离线运行，输出到新的目录，保留旧结果。下列路径是本轮准备目录：

```bash
uv run --project evaluation evidence-admission prepare --experiment-version v3 \
  --output-dir evaluation/memory/evidence_admission/results/live-ready-primary
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v2 \
  --output-dir evaluation/memory/evidence_admission/results/live-ready-validation
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v3 \
  --output-dir evaluation/memory/evidence_admission/results/live-ready-additional
uv run --project evaluation evidence-admission compare-preparation \
  --run-dir evaluation/memory/evidence_admission/results/live-ready-primary \
  --run-dir evaluation/memory/evidence_admission/results/live-ready-validation \
  --run-dir evaluation/memory/evidence_admission/results/live-ready-additional \
  --output-file evaluation/memory/evidence_admission/results/live-ready-comparison.json
uv run --project evaluation evidence-admission plan-live \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-primary \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-validation \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-additional \
  --output-file evaluation/memory/evidence_admission/results/live-request-plan.json
```

`plan-live` 验证当前代码和准备门槛，按五组全部准备调用求和。计划包含三个数据集各自的请求上限、估算输入和最大输出，
以及独立 smoke 的费用规模。估算输入不是实际 usage，模型和价格未确定时不换算货币成本。
现有结果的 327 次只是 v3 候选组调用，不能作为整个线上矩阵的请求上限。

## 配置和 smoke

正式执行需要明确 provider、精确 model ID、HTTPS base URL 和凭据环境变量名。不要将凭据写入清单、命令参数或报告。
下列命令使用环境选择器，运行前必须设置对应配置；`EVIDENCE_ADMISSION_KEY` 是凭据变量名，值通过本机安全配置注入。
`EVIDENCE_AUDIT_ROOT` 必须指向仓库外目录。只有明确更改“不提交”约束并完成代码冻结后，才能执行实际调用。

```bash
uv run --project evaluation evidence-admission run-smoke \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests 8 \
  --audit-dir "$EVIDENCE_AUDIT_ROOT/smoke" \
  --output-dir .artifacts/evidence-admission/live-smoke
```

smoke 固定为 `small-complete` 和 `single-source-overflow`、五组、4K 和一次重放，共 10 个样本、预计 8 次调用。
其 manifest 明确标记 `mode=smoke`，不改正式 experiment 的预算或重放次数。检查结构化输出、精确引用、返回 usage 和
私有 journal；未决语义与关键错误需人工检查后再决定是否进入正式运行。smoke 不计入正式矩阵，联合判定拒绝使用它。
本地任务取消、额度耗尽后的 journal 保留能力由受控测试验证，不代表远端 provider 已停止执行或计费。

## 正式生成

依次运行三个数据集，前一个结束后再开始下一个，保持串行约束。三个运行使用相同 provider、模型和 endpoint。
每次最多一次 provider 请求，零 SDK 重试，输出上限 2048 token，超时 60 秒，温度零。

```bash
uv run --project evaluation evidence-admission run-live --experiment-version v3 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests 561 --audit-dir "$EVIDENCE_AUDIT_ROOT/primary" \
  --output-dir .artifacts/evidence-admission/live-primary
uv run --project evaluation evidence-admission run-live --experiment-version v3 --dataset validation-v2 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests 300 --audit-dir "$EVIDENCE_AUDIT_ROOT/validation" \
  --output-dir .artifacts/evidence-admission/live-validation
uv run --project evaluation evidence-admission run-live --experiment-version v3 --dataset validation-v3 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --max-requests 216 --audit-dir "$EVIDENCE_AUDIT_ROOT/additional" \
  --output-dir .artifacts/evidence-admission/live-additional
```

上述上限需要与本轮 `live-request-plan.json` 核对。失败请求也消耗尝试额度；不增加重试来挑选成功重放。
任何中断、额度耗尽或失败都保留已有私有证据。未生成完整结果不能纳入联合判定，也不支持自动续跑或替换部分重复样本。

## 人工审计

每个原始运行的私有 `outputs.json` 保存实际输出，`events.jsonl` 持久记录成功返回的输出和已知 usage。
`STATE.json` 必须为 complete，公开目录必须有有效 `COMPLETE.json`。失败请求的 usage 可能未知，不能计为零成本。

为每个数据集建立独立私有 reviews 文件。格式中的哈希必须来自相应运行和实际候选；没有待处理语义时也提交绑定运行的空 reviews。

```json
{
  "manifest_hash": "原始运行清单的 digest",
  "reviews": {
    "sample_id": {
      "candidate_hash": {
        "verdict": "supported",
        "claim_hashes": ["已冻结标注事实的 hash"]
      }
    }
  }
}
```

允许的 verdict 为 supported、unsupported、false_attribution。后两者使用空 claim_hashes。
确认 supported 需要核对语义、实际引用和已准入范围；人工结论不能覆盖遗漏支持、已知禁止事实或精确字面量错误。
不能改写输出、输入身份、用量或原始失败状态来获得通过。

分别执行审计，保留原始运行：

```bash
uv run --project evaluation evidence-admission audit --experiment-version v3 \
  --run-dir .artifacts/evidence-admission/live-primary \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/primary/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/primary/reviews.json" \
  --output-dir .artifacts/evidence-admission/audited-primary
uv run --project evaluation evidence-admission audit --experiment-version v3 --dataset validation-v2 \
  --run-dir .artifacts/evidence-admission/live-validation \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/validation/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/validation/reviews.json" \
  --output-dir .artifacts/evidence-admission/audited-validation
uv run --project evaluation evidence-admission audit --experiment-version v3 --dataset validation-v3 \
  --run-dir .artifacts/evidence-admission/live-additional \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/additional/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/additional/reviews.json" \
  --output-dir .artifacts/evidence-admission/audited-additional
```

## 联合判定

```bash
uv run --project evaluation evidence-admission compare-live \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-primary \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-validation \
  --preparation-run-dir evaluation/memory/evidence_admission/results/live-ready-additional \
  --original-run-dir .artifacts/evidence-admission/live-primary \
  --original-run-dir .artifacts/evidence-admission/live-validation \
  --original-run-dir .artifacts/evidence-admission/live-additional \
  --audited-run-dir .artifacts/evidence-admission/audited-primary \
  --audited-run-dir .artifacts/evidence-admission/audited-validation \
  --audited-run-dir .artifacts/evidence-admission/audited-additional \
  --output-file .artifacts/evidence-admission/combined-live.json
```

联合判定核对三个准备、三个原始线上及三个审计结果。它检查 provider/模型/endpoint 一致性、父运行及 review 哈希、
准备输入一致性、所有组的应有生成覆盖和已返回 usage 完整性。候选对可共同容纳事实与字面量必须在实际支持输出中达到 100%。
不可能完整样例继续 bounded，全部生成身份差异单独披露，不通过调整分母掩盖失败。

缺少关键证据时为 insufficient_evidence；证据完整但候选有已知质量或准备回归时为 no_production_change。
只有完整通过且复现单 Source 改善时，才可给出 bounded_projection_follow_up_required。
该结果仍要求独立生产设计，不直接授权修改生产投影、引用或发布行为。
