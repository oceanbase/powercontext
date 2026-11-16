# V3 线上评测操作步骤

本流程使用冻结的三个数据集、五个实验组和共同评分规则。先准备与核算，再执行独立 smoke、正式生成和人工审计。
这是显式选择 v3 的通用 CLI 流程；历史 v18–v20 三 arm 执行另有冻结协议，不混用结果。
每次新运行需明确 provider、模型和凭据选择器；未提交代码不能运行 `run-smoke` 或 `run-live`。
这些命令不会自动创建提交，也不能把本地受控测试当成真实 provider 结果。英文费用说明见 [LIVE_RUNBOOK.md](LIVE_RUNBOOK.md)。

## 准备与预算

使用最终评测代码分别完成三个离线运行，输出到新的目录，保留旧结果。以下 `.artifacts` 路径用于新运行；
[下载证据包](RESULTS_ARTIFACT.md)中的 `results/...` 仅用于历史证据，不覆盖它们。

```bash
uv run --project evaluation evidence-admission prepare --experiment-version v3 \
  --output-dir .artifacts/evidence-admission/live-ready-primary
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v2 \
  --output-dir .artifacts/evidence-admission/live-ready-validation
uv run --project evaluation evidence-admission prepare --experiment-version v3 --dataset validation-v3 \
  --output-dir .artifacts/evidence-admission/live-ready-additional
uv run --project evaluation evidence-admission compare-preparation \
  --run-dir .artifacts/evidence-admission/live-ready-primary \
  --run-dir .artifacts/evidence-admission/live-ready-validation \
  --run-dir .artifacts/evidence-admission/live-ready-additional \
  --output-file .artifacts/evidence-admission/live-ready-comparison.json
uv run --project evaluation evidence-admission plan-live \
  --preparation-run-dir .artifacts/evidence-admission/live-ready-primary \
  --preparation-run-dir .artifacts/evidence-admission/live-ready-validation \
  --preparation-run-dir .artifacts/evidence-admission/live-ready-additional \
  --output-file .artifacts/evidence-admission/live-request-plan.json
```

`plan-live` 验证当前代码和准备门槛，按五组全部准备调用求和。计划包含三个数据集各自的请求上限、估算输入和最大输出，
以及独立 smoke 的费用规模。估算输入不是实际 usage，模型和价格未确定时不换算货币成本。
现有结果的 327 次只是 v3 候选组调用，不能作为整个线上矩阵的请求上限。

### 运行前计算最大费用

通用 `run-live`/`run-smoke` 执行 `--max-requests` 尝试次数限制，**没有 `--limit-cny` 或金额账本**。
历史 v20 的累计 ¥20 批准和逐次满上下文预留只约束那次冻结执行，不约束本入口，也不授权新调用。
这里采用人工计算上界；CLI 不会自动读取或执行该金额。若需要自动货币限额，应使用另行验证的执行器或
provider 已确认的硬消费上限，不能将账户余额、告警或普通取消当作硬上限。

在发送 smoke 前保存本次费用表，至少包含：

| 参数 | 单位与依据 |
| --- | --- |
| `N` | 本命令 `--max-requests`；多命令取各自上限之和，包含 smoke |
| `Imax` | 单请求最多可计费输入 token，覆盖系统指令、schema、证据、包装及 provider 计费开销 |
| `Omax` | 单请求最多可计费输出 token，覆盖适用的隐藏 reasoning；需确认 provider 对输出上限的语义 |
| `Pin` / `Pout` | 同一币种、每百万 token 的最高适用输入/输出价格；记录官方公开 URL、核验日期、模型和计费档位 |
| `Fmax` | 每请求额外费用上界；无额外费用时明确填 0 |
| `R` | 尚未结清的既有调用费用预留；仅在本次与它们共用同一批准额度时合计 |
| 批准额度 | 本次累计授权金额、币种及适用范围；换算货币时记录保守汇率及税费 |

```text
单请求费用上界 = Imax × Pin / 1_000_000 + Omax × Pout / 1_000_000 + Fmax
本次费用上界 = Σ(每个命令 N × 对应单请求费用上界)
受保护总额 = 已知历史费用 + R + 本次费用上界
```

每项按 provider 的计费粒度向上取整，受保护总额不得超过适用批准额度。
没有确认的缓存折扣按最高输入价计算。超时、失败及取消请求也可能计费，每次仍占完整预留，未知 usage 不计零。
新增命令、手工重试或提高次数上限必须重新计算；SDK 零重试和 60 秒超时本身不提供金额上限。

`plan-live` 的估算输入和 2048/4096/8192 预算不是 provider 计费上界，不能直接填入 `Imax`。
可以在 provider 确认其为计费硬边界的前提下，用完整模型上下文输入上限作保守 `Imax`；
输出配置为 2048，但只有 provider 确认包含所有可计费输出时，才能令 `Omax=2048`。
无法界定输入、reasoning 或额外计费时，不存在可由此公式证明的最大费用；应先取得硬边界或硬消费控制再调用。

仅用于演示算术：假定 `N=8`、`Imax=128000`、`Omax=2048`、`Pin=2`、`Pout=3`、`Fmax=0`，
价格单位为同一货币/百万 token，且无额外舍入收费，则 smoke 上界为 `8 × (0.256 + 0.006144) = 2.097152`。
这些是假设数据，不是 DeepSeek 或其他 provider 的现行价格、上下文保证或消费批准。
下方示例的 smoke 和三个正式上限合计为 `8 + 561 + 300 + 216 = 1085` 次，不能只按 smoke 或候选组核算整轮。

## 配置和 smoke

正式执行需要明确 provider、精确 model ID、HTTPS base URL 和凭据环境变量名。不要将凭据写入清单、命令参数或报告。
下列命令使用环境选择器，运行前必须设置对应配置；`EVIDENCE_ADMISSION_KEY` 是凭据变量名，值通过本机安全配置注入。
`EVIDENCE_AUDIT_ROOT` 必须指向仓库外目录。只有完成代码冻结及本次费用核算、取得适用调用授权后，才能执行实际调用。

```bash
uv run --project evaluation evidence-admission run-smoke --experiment-version v3 \
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

先按下文审计格式建立 smoke 的私有 `reviews.json`，再生成与该 smoke 绑定的审计结果：

```bash
uv run --project evaluation evidence-admission audit --experiment-version v3 \
  --run-dir .artifacts/evidence-admission/live-smoke \
  --private-outputs "$EVIDENCE_AUDIT_ROOT/smoke/outputs.json" \
  --reviews "$EVIDENCE_AUDIT_ROOT/smoke/reviews.json" \
  --output-dir .artifacts/evidence-admission/audited-smoke
```

只有审计后的 smoke 满足冻结的 `ready_for_formal` 才能进入正式运行；未决或失败不能用空审阅文件掩盖。

## 正式生成

依次运行三个数据集，前一个结束后再开始下一个，保持串行约束。三个运行使用相同 provider、模型和 endpoint。
每次最多一次 provider 请求，零 SDK 重试，输出上限 2048 token，超时 60 秒，温度零。

```bash
uv run --project evaluation evidence-admission run-live --experiment-version v3 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/audited-smoke \
  --max-requests 561 --audit-dir "$EVIDENCE_AUDIT_ROOT/primary" \
  --output-dir .artifacts/evidence-admission/live-primary
uv run --project evaluation evidence-admission run-live --experiment-version v3 --dataset validation-v2 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/audited-smoke \
  --max-requests 300 --audit-dir "$EVIDENCE_AUDIT_ROOT/validation" \
  --output-dir .artifacts/evidence-admission/live-validation
uv run --project evaluation evidence-admission run-live --experiment-version v3 --dataset validation-v3 \
  --provider "$EVIDENCE_PROVIDER" --model "$EVIDENCE_MODEL" \
  --base-url "$EVIDENCE_BASE_URL" --token-env EVIDENCE_ADMISSION_KEY \
  --smoke-run-dir .artifacts/evidence-admission/live-smoke \
  --smoke-audited-run-dir .artifacts/evidence-admission/audited-smoke \
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
  --preparation-run-dir .artifacts/evidence-admission/live-ready-primary \
  --preparation-run-dir .artifacts/evidence-admission/live-ready-validation \
  --preparation-run-dir .artifacts/evidence-admission/live-ready-additional \
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
