# Issue #2074 审阅交付

本评估的唯一冻结结论为 **`insufficient_evidence`**。当前候选存在引用、结构化输出及字面量失败，
不满足生产后续门槛。原 471 次计划请求已全部尝试，469 次有可审计响应，2 次历史响应及用量不可用。
本交付供审阅者检查本地分支和决定是否公开；没有执行推送或创建 PR。

## 人工审阅记录

审阅者为本 issue 的任务发起用户本人，公开记录使用 `issue-2074-requesting-user` 标识，
不推断其真实姓名或 GitHub 账号。记录日期为 2026-11-07（Asia/Shanghai）；单独的实际审阅日期未提供。
用户反馈决定性失败证据与评估路径检查没有发现问题，助手负责整理记录。

该反馈没有逐项列出 `call_id`、检查数量、人工与程序核验的覆盖边界，因此不能表述为
全部 177 次引用失败、6 个 Unicode 样本或全部输出均已人工逐条签认。
也没有提供逐候选语义判断，不能清除 533 个语义未决。未建立独立审阅者身份，不能称为独立人工复核。

机器记录为 [human-review.json](results/v20-review/human-review.json)。它绑定冻结身份、执行提交和公开结果哈希，
属于审阅反馈记录，不是评分器接受的逐候选语义审阅文件，不导入原会话或改变分数。
原有报告中“独立人工关键审阅未完成”的限制仍成立。

## 测量结果与可支持的结论

| arm | 计划及尝试 | 有效 Pipeline 输出 | 已知输出失败 | 历史不可用 | 未运行 |
| --- | ---: | ---: | ---: | ---: | ---: |
| complete-projection-v1 | 42 | 41 | 0 | 1 | 0 |
| window-reduction-v1 | 102 | 102 | 0 | 0 | 0 |
| bounded-fragments-v3 | 327 | 148 | 178 | 1 | 0 |
| 合计 | 471 | 291 | 178 | 2 | 0 |

候选离线可容纳必需内容覆盖为父事实 495/495、原子事实 531/531。这些计数包含预算和重放，
不是独立工作负载数量，也不表示生成保留率。

- 177 次候选输出引用本次请求中不存在的 evidence ID，涉及 292 个候选，实际 Pipeline 拒绝。
  本次整理重新比较全部 177 次的已保存输入与解析输出，集合差异计数与原诊断一致。
- 1 次候选输出达到共同的 2048 输出 token 上限，`finish_reason=length`，未形成有效结构化输出。
- 6 个 Unicode 有效输出分别只出现 1/3 个必需字面量。该描述来自既有字节检查，不能代替语义判定。
- `candidate_known_quality_failure_rows=185` 含一个历史不可用候选结果；其中 184 行是确认的失败，
  不能把 185 全部解释成模型质量错误。
- 469 个可用响应在原有 Source/SDK/Pipeline 离线回放中全部匹配；两次缺失响应不计作回放成功。

现有证据足以否决当前冻结候选进入生产，不证明现状对所有工作负载充分，也不证明所有有界策略均无价值。
issue 的 `no_production_change` 可以表达已测候选存在不可接受的损失或风险；但本次冻结合同要求执行完整且
语义未决解决后才进入该结果。当前没有通过修改门槛、丢弃失败或补造缺失响应改变原决定。

累计已知费用上界为 ¥3.521362，历史未知调用预留 ¥4.227072，受保护总额 ¥7.748434，低于批准的累计 ¥20。
这些是冻结计价下的费用上界与预留，不是账户账单。本次整理新增 provider 请求为 0。

## 失败索引与语义待审范围

[failure-audit-index.json](results/v20-review/failure-audit-index.json) 为 178 次已知输出失败补充
`row_id`、`input_index`、输入和响应哈希、错误引用候选下标；另列出 6 个字面量缺失行及两次不可用调用。
下标从 0 开始，索引不含提示、响应或证据 ID 原文。人工逐调用签认状态仍为未提供。

[semantic-review-queue.json](results/v20-review/semantic-review-queue.json) 保留全部 533 个未决候选，
分为有效输出优先审阅与被拒绝输出诊断两类，没有根据分组移除任何正式结果。

| arm | 有效输出中的未决候选 | 被拒绝输出中的未决候选 | 合计 |
| --- | ---: | ---: | ---: |
| complete-projection-v1 | 41 | 0 | 41 |
| window-reduction-v1 | 64 | 0 | 64 |
| bounded-fragments-v3 | 121 | 307 | 428 |
| 合计 | 226 | 307 | 533 |

有效输出待审涉及 133 次调用；被拒绝输出诊断涉及 177 次调用。候选数、调用数、比较行数和独立事实数不得混用。
每项附实际候选下标、`candidate_key`、候选哈希及上下文绑定，队列本身没有新增语义判断。

下一步如需完整生成质量比较，先按共同标准判定有效输出中的 226 个未决候选：证据支持、用户归因、
必需事实覆盖、额外断言、原子性及精确字面量。保留三个 arm 的配对和全部重放。
即使完成这些判断，307 个被拒绝输出中的未决项与两次不可用响应也不会自动消失。

现有 `v18 review` 实现只接受成功的 `.output.json`，不接受失败后的 `.parsed.json`，
并且已有派生记录采用拒绝覆盖的写入规则。不得改名输出或覆盖记录以绕过限制。
当前报告的合并逻辑会读取派生评分，后续导入应先确认调用和输出绑定，并另行保存审阅理由。

## 私有原始材料的核对方法

由操作者在本机指定私有会话目录 `SESSION`。原始证据不加入 Git；公开索引无法恢复原始响应。

1. 用失败索引中的 `call_id` 对应 `SESSION/state.json` 的记录。
2. 用 `row_id` 打开 `SESSION/inputs/<row_id>.json`，按 `input_index` 选择实际输入。
3. 对照 `SESSION/audit/<call_id>.request.json`，确认允许的 evidence ID 确实进入请求。
4. 检查同一调用的 `.response-bytes.json`、`.parsed.json`，或成功后的 `.output.json`。
5. 用 `SESSION/preparation.json` 的 `sample.supports` 核对不可变物化、投影及允许范围。
6. Unicode 使用 JSON 解码后字符串的 UTF-8 字节，不凭视觉判断 CRLF、组合字符或 emoji。
7. 将人工结论和实际覆盖另记，保持原输入、响应、状态、哈希与评分不变。

公开文件索引中的 `sha256` 是文件原字节的 SHA-256；调用记录的 `input_hash`、`response_hash` 和
审阅上下文哈希采用评估器各自的绑定规则，不能直接混比。原响应绑定使用 `digest(raw_bytes.hex())`，
候选输出绑定使用 `digest(output.model_dump(mode="json"))`；完整上下文绑定还包括 case、输入及 supports。
核对时复用 `models.digest` 与 `v10.scoring.review_binding`，不要因文件排版不同误报绑定错误。

## 验收与工程检查

本评估提供版本化 CLI、合成夹具、真实 Source 持久化及重读、真实 Pipeline 线上路径、
三个 arm 的固定比较、失败及取消测试、费用控制、机器报告和可读结果。
结构化 tier arm 因适配器缺少可信结构来源不适用；私有语料是可选扩展。

仍需保留的限制包括语义未决、两次不可用响应、人工覆盖未逐项列举、单一 provider 配置、
模型别名而非固定物理快照、本地 SQLite 而非远端宿主验收。候选质量失败是评估结果，
无需为交付研究而将候选修到通过；也不能将这些限制描述为全部验收项通过。

本地整理的实际检查见 [validation.json](results/v20-review/validation.json)。
历史完整回归 556 项及四版本专项结果见 [原验证凭据](results/v20-live/validation.json)，
各次执行的代码与范围分别保留，不把历史结果当作本次重新执行。

原 [v20 交付](results/v20-live/README.zh.md)的全部索引文件保持原字节，本审阅包单独建立哈希索引。
生成协议、准入策略、评分规则、原始结果、冻结哈希和生产代码没有修改。

## 推送与上游合并边界

当前分支为 `evaluation/2074-bounded-evidence-admission`。本地整理前的交付提交为
`4ec630a182364ee699edd563ec036f1769afbe1a`，执行冻结提交为
`1515a0d11cc2040d0beb341f6626a33c09cce73b`。

以本地 `origin/master` 的 `44281c818c73c68368b0e7c0fabec4391d65be9f` 比较，整理前已有
30 个分支独有提交、447 个变更文件，工作区文件内容合计约 91.8 MB。这不是网络传输大小；历史冻结与失败材料保留。
文件统计使用 `origin/master...HEAD`，共同祖先为 `31ecdd61`；本地 `origin/master` 另有 168 个分支未包含提交，
不能把比较用的远端跟踪引用当作当前分支的直接起点。
源包 `src/powercontext/` 没有分支差异。包外变更包括评估注册及锁文件、冻结 JSON 检查、
LongMemEval 可选 evidence 能力检查、SWE-bench 计数类型兼容、测试进程导入路径和 tox 环境隔离。
这些也在拟推送范围内，不能只审本次新增的文档。

本地 `upstream/master` 指向 `e356ba2f`，包含当前分支没有的 308 个提交；没有联网确认它是最新上游。
不改分支和工作区的 `git merge-tree` 检查发现以下 9 个冲突文件：

- `.gitattributes`
- `.gitignore`
- `.pre-commit-config.yaml`
- `Makefile`
- `evaluation/pyproject.toml`
- `evaluation/uv.lock`
- `pyproject.toml`
- `tests/test_ops.py`
- `tox.ini`

因此，本地交付可供审阅及后续推送为评估分支，但**不能声称可以直接合入当前上游**。
没有实际合并、rebase、squash、清理冻结历史或重跑模型。
若要以可合并 PR 交付，仍需单独整合上游、解决冲突并验证新的组合状态；不能把原冻结实测追认为新组合实测。

未跟踪的 `website/content/` 与 `website/public/contributors/` 不在提交范围，未修改或删除。
远端推送和 PR 创建仍等待用户审查后的明确指示。PR 正文见 [PR_DESCRIPTION.md](PR_DESCRIPTION.md)，
当前发布状态见 [publication-status.json](results/v20-review/publication-status.json)。
