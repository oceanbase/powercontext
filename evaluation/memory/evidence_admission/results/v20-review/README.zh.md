# 人工审阅反馈与交付索引

本目录记录 issue #2074 任务发起用户本人的检查反馈，绑定已有 v20 结果，供本地审查后决定是否公开。
用户反馈检查未发现问题，但没有提供逐请求覆盖或逐候选语义判断。助手整理记录，不冒充人工审阅者。
冻结结果仍为 `insufficient_evidence`，没有新增模型请求、改变分数或授权生产修改。

入口为 [审阅交付说明](../../REVIEW_HANDOFF.zh.md)。

| 文件 | 内容与边界 |
| --- | --- |
| `human-review.json` | 用户本人反馈、覆盖未逐项列举的限制、原结果哈希绑定；不是语义评分输入 |
| `failure-audit-index.json` | 178 次已知输出失败、6 个 Unicode 字面量缺失行、2 次历史不可用调用的定位索引 |
| `semantic-review-queue.json` | 全部 533 个未决候选的调用、候选键及上下文绑定；226 个来自有效输出，307 个来自被拒绝输出 |
| `publication-status.json` | 分支、原提交、未推送状态，以及本地上游合并预检的 9 个冲突文件 |
| `validation.json` | 本次实际执行的验证、原交付不变性、脱敏和限制 |
| `artifact-index.json` | 本包及入口文档的 SHA-256 和字节数，自身排除以避免递归 |

`candidate_index` 和 `input_index` 都从 0 开始。没有原始提示、响应、证据 ID 或本机私有审计路径。
取得私有审计后，由 `call_id` 对应状态记录，再用 `row_id` 和 `input_index` 找到本次输入。

本目录的汇总人工审阅记录没有被导入评分器，人工签认清单为空表示未提供逐项清单，不能解释成全部通过。
当前独立人工审阅身份未建立，完整语义判定未完成。被 Pipeline 拒绝的输出始终保留失败结果。
原 `results/v20-live/` 与其索引绑定的文档保持原字节，审阅说明使用独立版本和哈希索引。

可从仓库根目录只读校验本包索引，无需 provider 凭据：

```python
import hashlib
import json
from pathlib import Path

root = Path("evaluation/memory/evidence_admission")
index = json.loads((root / "results/v20-review/artifact-index.json").read_text())
for name, expected in index["files"].items():
    data = (root / name).read_bytes()
    assert len(data) == expected["bytes"]
    assert hashlib.sha256(data).hexdigest() == expected["sha256"]
```
