# 线上补跑交付

本目录记录冻结提交 `1c62e664` 的实际执行结果。最终为 `insufficient_evidence`：新增 116 次尝试，
其中 98 次返回有效 Pipeline 输出，17 次已知引用错误，1 次无响应及用量，剩余 351 次未运行。
原 v18 的四次尝试不重发；合并共 120 次尝试、118 个响应、2 个不可用响应。

- `result.json`：机器可读摘要，保留全部分数、结果、配对和记录，仅省略 `rows[*].preparation`。
- `full-report.json.gz`：完整报告，解压字节哈希及压缩参数见 `report-encoding.json`。
- `coverage.json`、`coverage-quality-separation.json`：区分尝试、有效输出、未知、未运行及原指标的解释限制。
- `failure-diagnostics.json`：仅包含计数和哈希；17 个已知失败都引用了不存在的 evidence ID，不包含模型原文。
- `request-plan.json`、`preflight.json`、`identity.json`、`freeze.json`：剩余清单、467 次离线预检、代码/配置/SDK 身份与实际提交证明。
- `remaining-request-plan.json`、`remaining-budget-plan.json`：排除已尝试 120 次后的 351 次清单与后续预算提案；提案不是批准或可执行会话。
- `recorded-replay.json`：118 次可用响应的输出及评分匹配，2 次不可用；`all_available_matched=false` 包含不可用项，不代表已观察到回放差异。
- `validation.json`、`integrity.json`、`artifact-index.json`：工程检查、脱敏/重现与公开工件哈希。

原自动分数、原始失败以及三个继承的助手诊断保持分离。语义未决不能解释为已丢失，
未运行或缺少响应也不能解释为策略质量失败。冻结报告兼容字段 `candidate_known_quality_failure_rows` 含一个未知响应，
必须与质量分离说明一起读取。配对也必须检查实际响应覆盖。

需要在原执行提交的源码 Git 检出和锁定环境中访问私有审计才能重生成完整报告；公开哈希不能恢复原始响应。
`SESSION` 为操作者的私有会话目录：

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v19 report \
  --session "$SESSION" --output "$SESSION/regenerated-report.json"
```

重生成文件必须不存在。用 `report-encoding.json` 的 `uncompressed_sha256` 校验原字节，
或用报告的确定性 JSON 摘要校验内容。压缩格式为 gzip level=9、mtime=0、空 filename：

```python
import gzip
import hashlib
import json
from pathlib import Path

root = Path("evaluation/memory/evidence_admission/results/v19-live")
raw = gzip.decompress((root / "full-report.json.gz").read_bytes())
encoding = json.loads((root / "report-encoding.json").read_text())
assert hashlib.sha256(raw).hexdigest() == encoding["uncompressed_sha256"]
full = json.loads(raw)
summary = json.loads((root / "result.json").read_text())
for row in full["rows"]:
    row.pop("preparation")
full["projection"] = summary["projection"]
assert full == summary
```

新的未知调用已触发停止，不自动恢复或重发。累计已知费用上界 ¥0.688902，加两次未知预留 ¥4.227072，
受保护总额 ¥4.915974；原 ¥5 不足以再预留一次完整请求。继续需要明确的新增预算授权和冻结的剩余清单，
并保留本会话及原 v18 结果。完整说明见 [结果报告](../../V19_RESULTS.zh.md)和[协议](../../V19_PROTOCOL.zh.md)。
