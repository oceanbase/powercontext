# 固定准入比较的线上补跑交付

本目录对应执行冻结提交 `1515a0d1`。范围是原 471 次清单中尚未尝试的 351 次，
不重发此前 120 次尝试，不改变原生生成、准入或评分条件。累计批准预算为 v9 起 ¥20，
继承的已知费用及两个未知调用的满上下文预留不清零。

实际执行范围、失败、未知结果和最终结论以 [V20_RESULTS.zh.md](../../V20_RESULTS.zh.md)、
`coverage.json` 和 `result.json` 为准，不能将预检数量当作线上执行数量。

- `request-plan.json`、`preflight.json`、`identity.json`、`freeze.json`：固定清单、351 次无网络 SDK/Pipeline 预检、代码/配置/环境身份与实际提交证明。
- `result.json`：机器可读摘要，保留全部结果、分数、配对和记录，只省略 `rows[*].preparation`。
- `full-report.json.gz`：完整报告，解压字节哈希及压缩参数见 `report-encoding.json`。
- `coverage.json`、`coverage-quality-separation.json`：区分尝试、有效输出、已知失败、未知用量与未运行范围。
- `failure-diagnostics.json`：已记录输出的确定性校验，只含类别、计数和哈希，不含模型原文。
- `literal-absence.json`：6 个有效 Unicode 输出中必需原字节串缺失的逐样本计数，不表示语义已获审阅。
- `recorded-replay.json`：实际可用响应的离线 SDK/Pipeline 回放，缺失响应单独记录，不冒充匹配成功。
- `validation.json`、`integrity.json`、`artifact-index.json`：工程验证、脱敏/重现与交付文件哈希。

冻结报告的 `candidate_known_quality_failure_rows` 可能包含不可用输出；必须与覆盖和质量分离说明一起读取。
配对也必须检查双方实际响应覆盖，不能只用 `both_semantics_resolved` 判断。
未决语义不能直接判为事实丢失，引用存在也不证明模型实际使用对应证据。
原有三个助手诊断仅作为绑定的派生审阅保留，不表示完成独立人工关键审阅。
`all_available_matched=false` 兼容字段包含两项不可用响应，不代表发现可用响应的回放差异。

在执行提交的源码 Git 检出及锁定环境中访问私有审计，使用不存在的新文件名重生成报告。
`SESSION` 是操作者指定的私有目录，完整提示、响应和凭据不得加入公开交付：

```bash
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.v20 report \
  --session "$SESSION" --output "$SESSION/regenerated-report.json"
```

公开哈希不能恢复缺失的原响应。完整报告解压后可按以下方式验证摘要投影：

```python
import gzip
import hashlib
import json
from pathlib import Path

root = Path("evaluation/memory/evidence_admission/results/v20-live")
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

gzip 使用 level=9、mtime=0、空 filename。线上命令、费用保护和取消流程见
[V20_PROTOCOL.zh.md](../../V20_PROTOCOL.zh.md)。停止会话不得自动重置、重发或清除未知费用。
所有本地提交均未推送，本评估不改变生产行为。
