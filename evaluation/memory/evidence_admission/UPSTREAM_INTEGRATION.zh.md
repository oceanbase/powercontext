# 上游整合与 PR 审阅入口

本次整合目标为从 `upstream/master` 和 `origin/master` 实际拉取的
`b8b189091e7a412300cf1292531208a8ead0e325`。保留本分支所有实验冻结提交和失败历史，
通过合并加入上游，不使用 rebase、squash 或强制改写历史。没有执行远端推送或创建 PR。

当前 PR 正文为 [PR_DESCRIPTION.zh.md](PR_DESCRIPTION.zh.md)。
建议标题：`test(evaluation): measure bounded evidence admission`。
预期目标为 `oceanbase/powercontext:master`，来源为
`Lkx-JY/powercontext:evaluation/2074-bounded-evidence-admission`；来源分支尚未推送本次提交。
PR 正文中的来源仓库链接在推送后生效；本地审查可使用本页相对文件链接。
[原审阅交付](REVIEW_HANDOFF.zh.md)及其索引记录的是上游整合前的检查范围，保留原字节；
其中的上游冲突、远端新鲜度和 PR 正文链接属于当时状态，不作为当前发布状态。

## 冲突处理

| 文件 | 合并选择 |
| --- | --- |
| `.gitattributes` | 保留上游迁移资源 LF 约束及历史 v14 合同原字节保护 |
| `.gitignore` | 同时保留上游 gateway 忽略项与本分支 `.pytest-tmp/` 忽略项 |
| `.pre-commit-config.yaml` | 合并上游 Helm/固定夹具配置和 v14 专用字节检查，不停用 JSON 语法校验 |
| `Makefile` | 保留升级策略、全部锁文件、串行 E2E、capture-ownership 与 agent-tool-recovery 步骤，加入 evidence-admission |
| `evaluation/pyproject.toml` | 合并两套评估注册、extras、源码包、数据、测试和类型配置，删除冲突造成的重复 TOML 段 |
| `evaluation/uv.lock` | 从上游锁文件重新解析新增 extra，包名与版本集合保持不变；解析器规范化锁文件格式和 marker |
| `pyproject.toml` | 同时保留两套独立评估的根类型检查边界 |
| `tests/test_ops.py` | 使用上游已包含环境导入路径修复的实现 |
| `tox.ini` | 保留上游锁定同步、环境隔离及 posargs，增加包含两套评估的四版本 evaluation 环境 |

生产源码相对整合目标没有改动。上游的生产行为变化由该上游提交提供，不能当作本评估的策略实现。

## 必填 description 与预算上下文

上游原生提取合同为 `powercontext.atomic-memory.extract.coding.v3`，候选必须包含 `description`。
评估模板显式声明该字段并透传，测试构造有效的新合同样本；实际原生响应缺字段仍被拒绝。
不对历史响应补字段，不重写事实正文、字面量或支持范围。

自动评分不能只凭 text 精确匹配而忽略一个未审阅的 description。未知 description 保持未决，
已知缺乏支持或缺失精确字面量的候选仍保留失败。人工语义审阅需同时阅读 text 和 description。

历史预算回归用例的原公开指令上下文固定为
`8219e869fe2e2d74768f1960243edd7141a259941ffe6059145ad2bf34f2cf8d`，仅由具名测试 fixture 注入。
这不是生产默认值，也没有被用于当前线上调用。原断言和预算保留。
当前默认仍使用上游指令，其哈希为
`789bfdf1d29f8d4f92e728764d46b2a90f644dcf5090f01540370f23cbb88f8f`。
该上下文中 v2 在 2K 的位置事实和 observation 样例各遗漏一个必要事实；新增测试保留失败与遗漏状态，
不将历史回归通过表述为新上下文的保留率通过。
历史窗口预算用例在具名 fixture 中固定对应公开指令。单元预算用例还固定原 schema 的成本表示，
该 schema 的 SHA-256 为 `cf395b2b0a009c7774551346c2c5a6cc089b5929b9d0e45a89d24b86aeb89be2`；
此注入仅服务一项无模型历史成本回归，不改变当前响应的必填字段校验。当前 v16 的 2K 多源单元计划
仍被预算拒绝，并由独立的当前运行时用例覆盖。没有通过放宽预算来消除该结果。

历史错误引用保持原值。投影哈希变化后，当前范围 ID 的错误使用通过另外的 Pipeline 回归覆盖，
不伪造旧 ID 仍属于当前范围，也不把范围 ID 修正成顶层 evidence ID。

## 历史证据与当前检查

原线上结果仍为 471 次已尝试、469 次响应可用、178 次已知输出失败、两次历史不可用；
533 个候选语义未决，唯一冻结决策仍为 `insufficient_evidence`。没有新增人工语义判断或模型调用。

原执行提交 `1515a0d11cc2040d0beb341f6626a33c09cce73b` 的 630 个依赖已从 Git 树逐字节核验；
当前整合树中有 171 个对应路径不同。原线上交付的 21 个索引文件和审阅包的 8 个索引文件保持原字节。
因此，历史生成与回放凭据属于原执行代码和锁定环境，不能改写为新上游的实测凭据。
新代码读取原 v20 会话时以 `v18_preparation_identity_or_reliability_changed` 拒绝重算，保持身份保护。

v12/v13 正向会话测试依赖静态 v11 基线，在当前 schema 不兼容时明确跳过，不计为当前通过；
新测试验证拒绝旧基线发生在会话发布之前，原 Git 提交字节检查继续执行。
当前 v17 的继承校验也必须拒绝已变化的依赖，不能改写冻结身份以恢复通过。

原 v12 Git 守卫测试假定被忽略的父目录已存在，在干净检出中会先报 FileNotFoundError。
当前测试显式创建父目录；历史补验只创建该目录，不修改原冻结代码或断言。
原执行提交及原锁文件下，Python 3.11 的 v12/v13 会话补验为 **43 passed、0 skipped、0 failed**。
此结果属于原合同；当前完整离线回归的 **524 passed、41 skipped** 仍按各自状态报告。

审计错误分类测试的合成响应补齐当前必填 description，以触达其原本要检验的引用和领域错误；
另有专门回归确认真实响应缺少 description 仍属于 schema 拒绝，不会自动修补。

使用原提交的独立检出及其锁文件才能重现历史；不要在当前树中关闭哈希检查或修改旧清单。
本次整合的工程回归与历史 provider 质量是两类证据。

当前实际验证、先前失败/中断和未运行项见
[validation.json](results/upstream-sync-20261107/validation.json)，
字节与源码边界见 [integrity.json](results/upstream-sync-20261107/integrity.json)。
本次开始前存在的 488 个未跟踪网站文件保持不变，不纳入本分支的评估提交。

## 建议审查顺序

1. 阅读当前 PR 正文，确认 `insufficient_evidence` 的范围和未决项。
2. 阅读原 `results/v20-live/` 汇总及 `results/v20-review/` 审阅记录，核对历史已完成覆盖与不可用项。
3. 查看本目录新整合凭据，区分原冻结环境的生成结果与新运行时的工程检查。
4. 用 `git diff upstream/master -- src/powercontext/` 确认没有本分支新增的生产差异。
5. 检查 evaluation 配置、schema 适配、评分中的 description 边界，以及历史测试 fixture 的使用范围。
6. 审核测试跳过、完整 tox 未运行和语义未决等限制，再决定是否推送或提出 PR。

## 仍需审查的事项

- 人工反馈没有逐请求覆盖清单，不能认定全部失败已逐条人工签认；独立审阅与完整语义判定没有补齐。
- 当前候选未获生产后续授权；不得把冲突解决或工程检查通过当作质量门槛通过。
- 新运行时的 schema、指令、Source 投影及依赖不同；如需再次线上比较，应另行冻结，不能延用旧结果授权。
- 历史重现依赖冻结提交仍可获取；最终采用 squash 或清理远端分支前，应保留可检出的冻结引用。
- 当前发布状态以 [integration.json](results/upstream-sync-20261107/integration.json) 为准。
  推送与创建 PR 仍等待用户明确指示。
