# v18 公开报告与重建

执行冻结提交：90a3337e4a047bd80949bcbb10938646c01e430c。
运行状态 unknown_usage_stopped，最终决策 insufficient_evidence。
四次真实调用尝试中三次取得响应，一次无响应/usage；剩余 467 次未运行。
这些产物不授权生产修改。

- result.json：完整报告的可读投影，仅省略 rows[*].preparation 大字段，其他评分、状态和配对结果不变。
- full-report.json.gz：CLI 生成的完整脱敏 JSON，保留全部逐行准入指标、遗漏与精确支持范围。
- original-auto-summary.json：原始自动评分汇总及其完整私有报告哈希，三个语义未决没有被覆盖。
- review-index.json：三个绑定助手诊断的派生关系；不是独立人工验收。
- coverage.json：实际四次尝试及未运行范围。
- identity.json / freeze.json：数据、代码、设置、SDK 和实际提交身份。
- preflight.json：471 次真实 SDK/Pipeline 的 MockTransport 预检，provider 调用为零。
- recorded-replay.json：三份可用响应匹配，一份响应缺失；不是完整四次回放通过。
- request-plan.json / report-encoding.json：请求计划和公开报告编码。
- validation.json / artifact-index.json：验证凭据与文件完整性索引。

在匹配冻结源码、SDK 和 Python 的环境，使用原私有会话重建完整报告：

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 report \
  --session /PRIVATE/v18 --output /PRIVATE/new-report.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v18 replay \
  --session /PRIVATE/v18 --output /PRIVATE/new-replay
```

report 不发送请求。replay 只通过 MockTransport 读取已有响应；缺失响应仍不可用。
助手审阅派生分数由导入的绑定审阅产生，原始自动分数及 SDK 回放保持不变。
私有输入、响应或审阅缺失时，公开哈希不能替代这些内容，不能声称已独立重现语义结论。
本轮记录缺少第四次响应及底层具体错误类型，不能从现有产物推断其实际计费或网络根因。

解压完整公开报告并核对内容哈希：

```python
import gzip
import hashlib
import json
from pathlib import Path

root = Path("evaluation/memory/evidence_admission/results/v18-live")
raw = gzip.decompress((root / "full-report.json.gz").read_bytes())
encoding = json.loads((root / "report-encoding.json").read_text())
assert hashlib.sha256(raw).hexdigest() == encoding["full_json_sha256"]
full = json.loads(raw)
assert full["decision"] == "insufficient_evidence"
```

result.json 的重建规则：保留完整 JSON 的全部顶层字段，将每行仅去掉 preparation，
增加 full_report_sha256 和 report-encoding.json 声明的 projection 文本。
使用 ensure_ascii=False、indent=2、末尾换行序列化，不改变任何评分或状态。

```python
compact = {
    **full,
    "rows": [{k: v for k, v in row.items() if k != "preparation"} for row in full["rows"]],
}
compact["full_report_sha256"] = encoding["full_json_sha256"]
compact["projection"] = encoding["projection_text"]
assert json.loads((root / "result.json").read_text()) == compact
```

gzip 使用空文件名、mtime=0、compresslevel=9；不同 zlib 版本可能产生不同压缩字节，
内容校验以解压后的 JSON SHA-256 为准。

CLI 执行需要源码 Git 仓库以核验真实提交；本交付不声称已验证脱离仓库的已安装 wheel 执行。
原始请求、响应和凭据均不在公开包内。不得重建会话、填补未知 usage 或重试第四次调用来绕过冻结停止。
