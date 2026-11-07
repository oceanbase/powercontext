# 人工抽查结果与验收状态

当前冻结决策为 **`insufficient_evidence`**。用户已报告46条抽查描述，覆盖优先语义队列的全部11个场景。
29条在该队列内可以唯一定位，17条仍存在 arm 或请求下标歧义。
已经绑定的部分维度判断为：**两处错误归因、13个非原子候选**。
这些证据进一步否决当前候选进入生产，不等于完整语义评分已经闭环。

当前 PR 正文为 [PR_DESCRIPTION_REVIEWED.zh.md](PR_DESCRIPTION_REVIEWED.zh.md)。
审阅数据入口为 [人工抽查附录](results/v20-human-sample/README.zh.md)，
上游冲突和工程整合范围见 [UPSTREAM_INTEGRATION.zh.md](UPSTREAM_INTEGRATION.zh.md)。
原线上报告、原汇总审阅记录和原整合凭据继续保留原字节；本附录不替换它们。

## 审阅身份与记录性质

审阅者是任务发起用户本人，公开标识为 `issue-2074-requesting-user`。
用户提供抽查范围和判断；助手整理绑定、执行程序核验和生成附录。
记录日期为2026-11-07，实际审阅日期没有单独提供。没有建立第二名独立审阅者身份。

`no_problem_reported` 表示用户报告被抽查输出未发现问题，不展开为所有候选的 supported、atomic、
false_attribution 和 entailed_atom_ids 均已逐项确认。无法唯一定位的记录不进入候选级判断。
用户所说的一条输出可能包含多个候选，因此46条描述不是46个已判定候选，不能据此从226中减去46。

## 覆盖清单

| 场景 | 用户报告条数 | 可唯一定位 | 有歧义 |
| --- | ---: | ---: | ---: |
| exact-literals | 9 | 9 | 0 |
| forged-role-marker | 4 | 0 | 4 |
| identity-replay | 4 | 0 | 4 |
| many-source-overflow | 4 | 3 | 1 |
| mixed-scaffolding | 4 | 4 | 0 |
| single-source-overflow | 4 | 4 | 0 |
| small-complete | 4 | 0 | 4 |
| unicode-boundaries | 4 | 4 | 0 |
| validation-crlf-scaffolding | 1 | 1 | 0 |
| validation-literal-offset | 4 | 4 | 0 |
| validation-reordered-sources | 4 | 0 | 4 |
| 合计 | **46** | **29** | **17** |

原始声明见 [human-sample-review-v1.json](manifests/human-sample-review-v1.json)，
逐条候选调用选择见 [coverage.json](results/v20-human-sample/coverage.json)。
单独的17条待确认清单见 [pending-mappings.json](results/v20-human-sample/pending-mappings.json)。
`resolution=ambiguous` 的 `possible_records` 列出所有匹配项；补充 arm 和 input_index 后才能唯一绑定。
这些匹配候选是程序列出的可能位置，不代表用户已审阅所有匹配项。

## 已明确的判断

### 错误归因

| 场景 | 预算 | 重放 | 候选下标 | 用户判断 |
| --- | ---: | ---: | ---: | --- |
| mixed-scaffolding | 2048 | 2 | 2 | 工具来源限定丢失，归为当前用户事实 |
| validation-crlf-scaffolding | 2048 | 0 | 2 | 工具文本被归为当前用户事实 |

两项均绑定 bounded-fragments-v3 的有效 Pipeline 输出，完整记录见
[human-judgements.json](results/v20-human-sample/human-judgements.json)。
这里只写入用户明确提供的 `false_attribution=true`，不推测其他未明确给出的审阅字段。
引用可解析和 Pipeline 接受不证明语义归因安全。这两项足以违反零关键错误归因的正向门槛。

mixed-scaffolding 的其他重放保留所有权候选的来源限定，并不表示其全部候选均获批准。
历史偏好候选和 Skill 指令相关候选仍需分别判断。
validation-crlf-scaffolding 的历史偏好候选下标0属于用户指出的归因风险，下标1的指令提取也未获得完整判断；
没有将风险自动记成第三处已确认错误。

### 原子性

exact-literals 的9次输出，以及被抽查的 validation-literal-offset 的4次输出，
分别将多个可独立变化的断言合在一个候选中，记录13项 `atomic=false`。
这是按本轮冻结的独立断言要求作出的部分维度判断；不意味着四个字面量在任何语境下都必须拆成四个候选。
没有临时改变 validation-literal-offset 的原子事实 ID 或标注粒度。

这13次输出的四个 ASCII 精确字面量均通过程序字节检查。字面量保留与原子性分别报告，不能互相抵消。
本结果限于所审记录，不能表述为该模型对所有输入均不拆分。

### Unicode 与重放

被抽查的四次 unicode-boundaries 输出均只保留1/3个必需字面量。
分解字符字节序列和 CRLF 未保留；emoji 保留。程序检查不执行 Unicode 规范化、strip 或换行转换。
此观察不扩展为所有 Unicode 内容必然失败，也不凭字节缺失替代完整语义判断。

mixed-scaffolding 的2K三次调用具有一个输入哈希、一个请求哈希和三个输出哈希。
这说明独立线上调用的生成结果不同，不说明输入准备不确定，也不否定已保存响应的离线重放一致性。
字节与重放计数见 [observations.json](results/v20-human-sample/observations.json)。

## 多请求计数解释

在被抽查的 many-source-overflow 与 validation-reordered-sources 预算、重放组合中，
窗口及候选组的完整样本均准入8/8个必需事实：2K拆为8次请求，4K拆为2次请求。
每次输出中的一条或四条事实不能解释为整条样本只准入1/8或4/8。
完整投影对照的预算拒绝仍单独保留；没有改预算以使其进入生成。

准备阶段8/8与全部请求返回也不直接证明完整语义交付8/8。
必须聚合同一 row_id 的全部请求及审阅结果；不能只看待审队列中的某一个输出。

## 绑定验证与评分边界

附录工具在当前检出中只读校验已保存输入、请求、响应、输出及原上下文收据的哈希，
不使用新 schema 为旧响应补 description，也不调用当前 Pipeline 重演旧输出。

另在原执行提交 `1515a0d11cc2040d0beb341f6626a33c09cce73b` 及其锁定环境中，
实际核验630个依赖文件，并重算76个可能调用、112个未决候选出现位置的原上下文绑定。
76和112是程序核验范围，包含歧义选项，不是人工审阅覆盖。
凭据见 [frozen-binding-validation.json](results/v20-human-sample/frozen-binding-validation.json)。

目前完整 v10 逐候选审阅新增数为0。15项是单维度判断，未补造 supported、atomic、false_attribution
及事实 ID 之间缺少的其他判断，因此原机器未决533项保持不变。新的知识是已明确的质量失败下界，
不是原报告已获得完整重算。原自动评分与本附录分别存在。

现有 v18 review 仅接收成功 output，且已有派生文件禁止覆盖。
本附录不向原会话写入，不把被拒绝的 parsed 输出改名为成功输出，也不覆盖已有助手诊断。

## 可重现命令

以下在当前仓库执行。`REVIEW_SESSION` 由操作者设置为仓库外原私有会话，
`REVIEW_OUTPUT` 为不存在的新目录，不能位于原会话内。

```bash
REVIEW_PACKAGE=evaluation/memory/evidence_admission
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.sample_review build \
  --session "$REVIEW_SESSION" \
  --declaration "$REVIEW_PACKAGE/manifests/human-sample-review-v1.json" \
  --queue "$REVIEW_PACKAGE/results/v20-review/semantic-review-queue.json" \
  --result "$REVIEW_PACKAGE/results/v20-live/result.json" \
  --fixtures "$REVIEW_PACKAGE/fixtures" --output "$REVIEW_OUTPUT"
uv run --locked --project evaluation python -m powercontext_eval_evidence_admission.sample_review verify "$REVIEW_OUTPUT"
```

原上下文重算需在上述原提交的干净独立检出中，用其锁定 Python 环境运行：

```bash
"$FROZEN_PYTHON" "$REVIEW_TOOL_ROOT/scripts/verify_sample_review_context.py" \
  --session "$REVIEW_SESSION" --appendix "$REVIEW_OUTPUT" \
  --queue "$REVIEW_TOOL_ROOT/results/v20-review/semantic-review-queue.json" \
  --output "$NEW_VALIDATION_FILE"
```

`REVIEW_TOOL_ROOT` 是当前评估包的绝对路径；脚本不放进原检出或修改其依赖。
输出文件须不存在。原代码身份不匹配、上下文或字节变动均拒绝，不关闭身份保护。
默认命令不需要 provider 凭据，不访问网络。工程检查见
[validation.json](results/v20-human-sample/validation.json)。

本轮完整无模型回归为538 passed、41 skipped；新增14项审阅回归在 Python 3.11 的完整套件中通过，
并在3.12、3.13、3.14分别专项通过。Ruff check、242个 Python 文件格式、evaluation 类型检查、
make check 的全部 hooks、三套锁文件与61项集成清单测试通过，评估包 wheel/sdist 构建通过。
这些专项不等于四版本完整 tox；根包全量测试没有因本次仅评估层的修改再次执行，
已有整合阶段凭据单独保留。完整交付的独立索引为
[delivery-index.json](results/v20-human-sample/delivery-index.json)。

## 按 issue 要求仍需保留的缺口

| 项目 | 当前状态与影响 |
| --- | --- |
| 17条抽查描述的准确定位 | 仍需补 arm 或 input_index；不得自动挑选其中一次调用 |
| 完整语义判定 | 原533项未决仍保留；15项部分判断不冒充完整审阅，307个被拒绝输出的诊断也未完成 |
| 两次历史响应/用量 | 仍不可用；人工抽查不能补出缺失响应或将未知用量计零 |
| 生产后续资格 | 当前候选已有引用、字面量、原子性及错误归因失败，不能授权生产实现 |
| 当前 master 的生成效果 | 原实测属于旧提交与旧 schema；当前上游必填 description 的线上比较没有执行 |
| 四版本完整 tox | 当前组合仍未跑完整矩阵；历史完整验证与专项验证分开报告 |
| 历史重现 | 依赖冻结提交可获取及本机私有原始材料；公开哈希不能恢复缺失响应 |

issue 允许选择 insufficient_evidence，失败候选是有效研究结果，不要求为收尾持续修生成协议。
私有语料、无可信结构来源的 tier arm、远端数据库和 provider 退款验证不是无条件必备项。
只有正向冻结门槛通过才需要生产后续契约；本次不生成该授权。

本轮没有新增 provider 调用、改动生产源码、修改预算或阈值、推送或创建 PR。
