# v6 离线验证操作说明

本流程验证失败审计、事实粒度、显式片段复制及请求成本，不调用模型、不读取密钥、不继续 v5 的两个未运行数据集。生产代码、公共 Atomic Memory schema 和 NFC 写入合同保持不变。完整合同见 [V6_PROTOCOL.md](V6_PROTOCOL.md)。

## 环境与不可变输入

使用当前已冻结的 `evaluation/.venv/bin/python`，不要在回放过程中同步或升级依赖。先保留 v5 原始/审计运行和持久私有日志目录，并记录其逐文件哈希及原有 73 个暂存文件的工作区、索引哈希。

v6 入口独立于旧 CLI。下列命令在仓库根目录执行，输出目录必须不存在。SQLite 在受限执行环境中可能需要使用允许本地异步数据库操作的执行权限；这不需要真实 provider 网络权限。

```bash
export PYTHONPATH=evaluation/memory/evidence_admission/src
evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 freeze \
  --output .artifacts/evidence-admission/v6-offline/code-freeze.json

evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 replay \
  --session .artifacts/evidence-admission/v5-formal-20261027-144142/session.json \
  --output-dir .artifacts/evidence-admission/v6-offline/replay

evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 prepare \
  --dataset synthetic-v1 \
  --reference-run .artifacts/evidence-admission/v5-preparation-primary \
  --output-dir .artifacts/evidence-admission/v6-offline/preparation-primary

evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 prepare \
  --dataset validation-v2 \
  --reference-run .artifacts/evidence-admission/v5-preparation-validation \
  --output-dir .artifacts/evidence-admission/v6-offline/preparation-validation

evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 prepare \
  --dataset validation-v3 \
  --reference-run .artifacts/evidence-admission/v5-preparation-additional \
  --output-dir .artifacts/evidence-admission/v6-offline/preparation-additional

evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v6 summarize \
  --run-root .artifacts/evidence-admission/v6-offline \
  --session .artifacts/evidence-admission/v5-formal-20261027-144142/session.json
```

汇总依赖执行前生成的 `history-preservation-before.json`。其中 `public`、`private` 分别是 v5 会话对应目录内全部相对文件名到 SHA-256 的映射，`staged_files` 是原 73 个暂存文件名到 SHA-256 的映射。运行期间不得改写历史文件或当前 v6 源码。

## 分辨结果含义

- `replay/replay.json`：完整历史回放、180 个失败样本身份、225 个保护样本身份、独立字面量子事实诊断。原评分必须仍为 198/216、4/63，13 次历史失败响应保持未知。
- `preparation-*/preparation.json`：实际重做的 1,620 个准备样本、原输入哈希、新协议成本、超预算样本。输入探针不代表模型已正确使用新 schema。
- `summary.json`：分 arm 成本及覆盖、历史不可变证明、离线成本门槛和线上预算草案。
- `new_protocol_model_generation=not_run`：没有新的 v6 模型生成结果，不能由离线测试改成通过。

命令未生成 `COMPLETE.json` 的目录不是完整运行。已生成的运行不得覆盖；修复后的再次执行使用新的目录，并保留失败原因。

如果新索引或 schema 导致超预算，停止线上推进，保留全部超预算身份。不得沿用 v5 的输入成本、静默丢样本或把字符估算预算称为 DeepSeek 实际 token 硬限制。只在离线结果明确后另行决定是否启动小样本线上验证。

## 检查范围

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src evaluation/.venv/bin/python -m pytest \
  evaluation/memory/evidence_admission/tests -q
.venv/bin/ruff check evaluation/memory/evidence_admission
.venv/bin/ruff format --check evaluation/memory/evidence_admission
(cd evaluation && .venv/bin/ty check memory/evidence_admission/src)
```

SDK 故障测试使用真实 SDK 调用链和模拟 HTTP 响应，覆盖解析、schema、引用、领域校验、截断、结束标记、用量缺失、HTTP 错误、超时和取消。它们证明本地处理行为，不证明 DeepSeek、OceanBase、真实宿主或生产发布流程已验证。
