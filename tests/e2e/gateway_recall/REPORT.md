# Issue #2870 Gateway 追问消歧开发与验收报告

Gateway 现在可以在召回前用会话历史补齐短追问的指代，也能跳过无需记忆的回合。DeepSeek Flash Chat 配对实测中，应召回场景的命中从 **21/36 提升到 31/36**，P95 从 **1.19 秒升至 2.44 秒**，估算费用增加 **37.6%**。功能默认关闭。

## 1. 改了哪些地方

| 位置 | 改动 |
| --- | --- |
| [Gateway 启动配置](../../../gateway/crates/pc-gateway-app/src/main.rs) | 读取 `PC_GATEWAY_CONTEXT_DISAMBIGUATION`（`on` 或 `off`），默认关闭，适用于 Chat 和 Responses 的 API 上游。 |
| [Executor 召回与重放](../../../gateway/crates/pc-gateway-core/src/executor.rs) | 在 replay 未命中的用户回合中选择 query，把选择结果传给 Core 的 `prepare`。刷新失效绑定时复用本请求已选的 query。 |
| [辅助请求与决策解析](../../../gateway/crates/pc-gateway-core/src/disambiguation.rs) | 保留会话和缓存相关字段，在末尾追加判断指令，禁用工具调用，解析 `recall` 和 `query`。 |
| [请求生命周期](../../../gateway/crates/pc-gateway-core/src/lifecycle.rs)、[调用证据](../../../gateway/crates/pc-gateway-evidence/src/lib.rs) | 辅助请求共用父请求取消信号，独立记录发送状态、usage 和 call ID。 |
| [Gateway 入口测试](../../../gateway/crates/pc-gateway-app/tests/gateway_behaviors.rs)、[按量评测脚本](evaluate.py)、[部署文档](../../../docs/zh/docs/operate/deploy-server.md) | 覆盖两种入口、失败回退、重放、真实上游对比及运维配置。 |

## 2. 修改逻辑

原路径从最后一条用户消息提取 `last_user_text`，直接交给 Core。用户只说“继续”时，检索词就可能缺少任务主题。新路径在出站前使用本次请求已经携带的对话，支持区分“继续执行已知动作”和“继续遵守历史约定”这两种情况。

1. **判定是否分析。** 启用上下文注入和消歧开关、属于新用户回合、存在用户文本、replay 未命中且历史随请求携带时，Gateway 才进入辅助调用。工具续写、内部请求、上游托管历史和 replay 命中均跳过辅助调用。
2. **分析会话。** Gateway 保留原始消息序列、模型和适用的缓存路由字段，在尾部追加指令。辅助调用独立经过 `before_send → provider → before_release`，单次尝试，最长八秒，并受父请求截止时间和取消信号约束。
3. **选择 query。** `recall=true` 且 query 合法时用该 query 调用 `prepare`。 `recall=false` 且 query 为 null 时跳过准备，空结果可重放。发送被阻止、释放被拒、交付状态不确定或输出非法时，如果父请求仍有效则恢复原始 `last_user_text`。交付状态不确定时辅助请求不会重发。
4. **发送主请求。** Gateway 保留用户原话，把 Core 返回的上下文按既有逻辑注入。主请求独立过两阶段门控。Core 要求刷新已注入的复用绑定时，Gateway 使用已经选好的 query 重做一次准备。

以登录超时为例。前文只有“按之前约定的兼容规则修登录超时”，最后一句为 `Continue.`。基线将 `Continue.` 交给 Core。这条实测中，消歧 query 为 `previously agreed compatibility rule for login timeout`，Core 找回“保留原异常类型”的记忆，主模型仍接收 `Continue.` 和注入的记忆。另一条同样使用 `Continue.`，前文已明确只运行 `git diff --check`，消歧返回跳过召回。

## 3. 测试数据与 24 条用例

- [标注数据 JSON](../../../gateway/crates/pc-gateway-app/tests/fixtures/context_disambiguation.json) 包含完整对话、最后的用户或工具输入、应否召回、期望记忆 ID 和归属标注。两组检索使用相同的 6 条 Atomic Memory。
- [复跑说明](README.md) 和 [评测脚本](evaluate.py) 负责启动隔离 SQLite Core 和两套 Gateway，建立同样的记忆库并记录实际的 query、注入、usage 和延迟。
- [逐次实测数据](results.json) 包含 Chat 完整对比和 Responses 四类样例，使用相同的列记录实际 query、注入记忆、token 和耗时。

两组使用相同的六条记忆。

| 记忆 ID | 预置规则 |
| --- | --- |
| `login-timeout` | 保留原异常类型 |
| `backup-restore` | 恢复到新命名空间 |
| `schema-rollout` | 先发布读取端，再发布写入端 |
| `worker-retry` | 沿用现有重试预算 |
| `scope-routing` | 保留调用方原有的 Scope |
| `release-build` | 使用已锁定的发布工具链 |

下表的 24 条样本全部通过 Chat 和 Responses 的脚本化入口验证。“Chat 实测”使用真实 DeepSeek Chat API，每组每条跑 3 次。需要召回的用例按期望记忆是否注入计分。预期跳过的用例同时列出准备次数和带额外记忆的次数。

| ID | 场景和最后输入 | 预期 | Chat 实测（基线 → 消歧） |
| --- | --- | --- | --- |
| `ambiguous-login-timeout` | 登录超时的历史约定未写在本轮。最后一句为 `Continue.` | 命中 `login-timeout` | 0/3 命中 → 3/3 命中 |
| `ambiguous-backup-restore` | 备份恢复的历史约定未写在本轮。最后一句为 `Use the earlier one.` | 命中 `backup-restore` | 0/3 命中 → 2/3 命中 |
| `ambiguous-schema-rollout` | Schema 发布的历史约定未写在本轮。最后一句为 `What was the agreed order?` | 命中 `schema-rollout` | 0/3 命中 → 2/3 命中 |
| `ambiguous-worker-retry` | Worker 重试的历史约定未写在本轮。最后一句为 `You changed the wrong thing. Please follow that rule.` | 命中 `worker-retry` | 0/3 命中 → 3/3 命中 |
| `ambiguous-scope-routing` | Scope 路由的历史约定未写在本轮。最后一句为 `Keep the same restriction.` | 命中 `scope-routing` | 3/3 命中 → 3/3 命中 |
| `ambiguous-release-build` | 发布构建的历史约定未写在本轮。最后一句为 `Proceed with the agreed version.` | 命中 `release-build` | 0/3 命中 → 1/3 命中 |
| `specified-login-timeout` | 修复和测试已完成，只需运行 `git diff --check`。最后一句为 `Continue.` | 零准备和注入 | 3 次准备、0 次注入 → 0 次准备、0 次注入 |
| `specified-backup-restore` | 下一步已明确为读取 Runbook 中的恢复命令。最后一句为 `Only read the restore command in the runbook. Everything else is done.` | 零准备和注入 | 3 次准备、0 次注入 → 1 次准备、1 次注入 |
| `specified-schema-rollout` | 下一步已明确为检查生成的迁移 diff。最后一句为 `Only check the generated migration diff. Everything else is done.` | 零准备和注入 | 3 次准备、0 次注入 → 0 次准备、0 次注入 |
| `specified-worker-retry` | 下一步已明确为查看重试测试输出。最后一句为 `Only check the retry test output. Everything else is done.` | 零准备和注入 | 3 次准备、0 次注入 → 0 次准备、0 次注入 |
| `specified-scope-routing` | 下一步已明确为打开路由测试文件。最后一句为 `Only open the routing test file. Everything else is done.` | 零准备和注入 | 3 次准备、0 次注入 → 2 次准备、1 次注入 |
| `specified-release-build` | 下一步已明确为查看本地构建日志。最后一句为 `Only inspect the local build log. Everything else is done.` | 零准备和注入 | 3 次准备、0 次注入 → 1 次准备、1 次注入 |
| `task-switch-login-timeout` | 从 HTTP 客户端设置切到登录超时，询问新任务此前的约定 | 命中 `login-timeout` | 3/3 命中 → 2/3 命中 |
| `task-switch-backup-restore` | 从备份压缩切到备份恢复，询问新任务此前的约定 | 命中 `backup-restore` | 3/3 命中 → 3/3 命中 |
| `task-switch-schema-rollout` | 从 Schema lint 切到 Schema 发布，询问新任务此前的约定 | 命中 `schema-rollout` | 3/3 命中 → 3/3 命中 |
| `task-switch-worker-retry` | 从 Worker 池大小切到 Worker 重试，询问新任务此前的约定 | 命中 `worker-retry` | 3/3 命中 → 3/3 命中 |
| `task-switch-scope-routing` | 从 Scope 命名切到 Scope 路由，询问新任务此前的约定 | 命中 `scope-routing` | 3/3 命中 → 3/3 命中 |
| `task-switch-release-build` | 从发布打包切到发布构建，询问新任务此前的约定 | 命中 `release-build` | 3/3 命中 → 3/3 命中 |
| `tool-continuation-login-timeout` | 登录超时检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |
| `tool-continuation-backup-restore` | 备份恢复检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |
| `tool-continuation-schema-rollout` | Schema 发布检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |
| `tool-continuation-worker-retry` | Worker 重试检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |
| `tool-continuation-scope-routing` | Scope 路由检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |
| `tool-continuation-release-build` | 发布构建检查工具返回 `The command returned successfully.` | 零准备和注入 | 0 次准备、0 次注入 → 0 次准备、0 次注入 |

## 4. 怎么验证

同一组 24 个构造案例覆盖模糊追问、明确任务、切换任务、工具续写，各 6 条。两组复用相同的 SQLite Core 和 6 条预置 Atomic Memory。每组每例测三次，第一次冷请求，后两次用相同 provider 前缀。每次更换 Gateway 会话 ID 以避开重放。基线和实验前缀隔离，共 72 个客户端请求/组。输入通常约 1,400–1,700 token，包含稳定的构造前缀。Core 为每次调用实际签发并核对绑定。Chat 的 provider 专用 thinking 字段使用非 enforcing Core。Responses 小样本使用 enforcing Core。

| 验收 | 实际运行 | 结果 |
| --- | --- | --- |
| 开关关闭 | Chat 和 Responses 的 24 条样本通过 Gateway 二进制。检查送往 Core 的原始 query、准备次数和注入记忆。 | 两入口基线路径通过。 |
| 模型决策及门控 | 脚本化模型结果分别通过 Chat 和 Responses。检查收敛到实际发送的 query、是否跳过、独立的发送和释放检查。 | 改写和跳过通过。工具续写零辅助调用。 |
| 失败、超时、取消 | 分别阻止发送、拒绝释放、返回非法 JSON 和空 query、交付后挂起、在辅助调用期间断开实际 TCP 连接。 | 原 query 回退通过，交付后的不确定调用无重发。断开后无 prepare 和新发送。辅助与主请求分别保留发送事实，证据中无输入正文。 |
| 重放、刷新及捕获 | 触发 `context-refresh-required`、复发相同锚点、跳过召回后的空重放、远端历史引用和内部请求。捕获开启时检查来源。 | 一次分析对应两次同 query 准备。重放、远端历史、内部请求无新辅助调用，捕获仅包含主模型回合。 |
| 按量 API | Chat 完整比较。Responses 对登录超时的一组四类案例做一轮实测。 | Chat 共 144 个主请求全部 HTTP 200。Responses 的 8 个主请求全部 HTTP 200。完整 Responses 复测遇到上游 HTTP 402。 |

## 5. 最终结果与验证边界

### 召回质量与退化样本

按标注的“该不该调用 `prepare`”计算召回判断准确率。每组 54 次用户回合观测来自 18 个样本各重复三次。工具续写由 Gateway 直接识别，统计时排除。

| 指标 | 基线 | 开启消歧 |
| --- | --- | --- |
| 召回判断准确率 | 36/54（66.7%） | 50/54（92.6%） |
| 应召回场景的目标记忆命中率 | 21/36（58.3%） | 31/36（86.1%） |
| 明确任务中的多余准备 | 18 次 | 4 次 |

判断准确率衡量 Gateway 是否在标注要求的场景发起检索。命中率还要求把目标记忆注入主请求。少做的 14 次准备是 Core 检索调用次数。实验使用固定的单次 Gateway 请求，未记录 Agent 完成任务所需的用户轮次、模型轮次和工具调用次数，也未验证最终任务是否做对。需要在同一批可执行任务上跑完整 Agent 工作流，比较任务成功率、达到成功所需的轮次、全程耗时和总费用，才能判断召回增益能否抵消每次请求的额外开销。

| 分类 | 基线 | 消歧 |
| --- | --- | --- |
| 模糊追问 | 3/18 命中，3 次出现额外记忆 | 14/18 命中，1 次出现额外记忆 |
| 明确任务 | 18 次准备，0 次注入 | 4 次准备，3 次不必要注入 |
| 切换任务 | 18/18 命中 | 17/18 命中 |
| 工具续写 | 0 次准备 | 0 次准备，0 次辅助调用 |

需要召回的回合两组均有实际准备，误跳过为 0。注入重复记忆均为 0。逐条检查了 54 个辅助输出，其中 47 个可解析。45 个可解析输出保留了回合的事实归属。其余两个输出把前一个任务的对象带进新任务，分别是登录超时 query 中的 HTTP client settings 和发布构建 query 中的 release packaging。没有观察到模型补写缺失兼容规则的内容，或其他独立虚构事实。7 个非法输出全部回退原 query。典型输出在 JSON 后附加反引号或协议标记，另一个输出重复生成直至截断。

同一重复序号与基线配对后，有 4 个更差的观测。

| 样本 | 实际 query | 结果变化 |
| --- | --- | --- |
| `task-switch-login-timeout` | `HTTP client settings login timeout compatibility rule agreed to follow` | 基线命中 login-timeout。实验带入旧主题并漏召回。 |
| `specified-backup-restore` | `restore command in the backup restore runbook` | 基线无注入。实验把本地读取动作当成跨会话召回，注入 backup-restore。 |
| `specified-scope-routing` | `scope routing repository routing test file location` | 基线无注入。实验注入 scope-routing。 |
| `specified-release-build` | `local build log location and latest release build log inspection results` | 基线无注入。实验注入 release-build。 |

### 缓存、延迟和成本

| 指标 | 基线 | 消歧 |
| --- | --- | --- |
| 上游调用 | 72 个主调用 | 72 个主调用 + 54 个辅助调用 |
| 至少一个缓存 token 的上游调用 | 48/72，66.7% | 102/126，81.0% |
| 缓存 token / 总输入 token | 66,816/112,299，59.5% | 141,184/200,245，70.5% |
| 辅助调用冷、热缓存命中 | 无辅助调用 | 冷 0/18，热 36/36。热输入 50,688/57,520 token 来自缓存。 |
| 主调用冷、热缓存命中 | 冷 0/24，热 48/48 | 冷 18/24，热 48/48。冷请求中命中的 18 个都在主调用之前执行过辅助调用。 |
| 端到端 P50 / P95 | 0.728 / 1.192 秒 | 1.356 / 2.436 秒 |
| 总费用估计 | $0.008041 | $0.011066，增加约 37.6% |

费用按 DeepSeek 周末公布费率估算。缓存输入、未缓存输入、输出分别为 $0.003、$0.15、$0.60 / 百万 token。记录覆盖所有成功 Chat 调用。此组样本没有显示端到端费用或延迟节省。[DeepSeek 定价](https://api-docs.deepseek.com/quick_start/pricing)

### 验证边界

Responses 的 enforcing Core 四例测试中，命中从 1/2 到 2/2。两组工具续写均没有准备或辅助调用。首次实测后 DeepSeek 余额不足，另一个已配置的按量上游返回欠费状态。完整的缓存、成本与时延结论来自 Chat。

Rust 工作区测试的四个旧 TCP 用例指向已有的端口 8000。它们在新建 Scope 阶段失败，隔离的工作区复测排除了这四个用例，独立启动 Core 的 UDS 用例继续运行。报告用语中的“真实 Gateway 入口”包括二进制搭配脚本化测试服务，只有按量比较和 Responses 小样本把请求交给了真实模型 API。

最终验证通过 31 个 Gateway 二进制行为测试、隔离 Core 的 UDS 测试、Rust Clippy、Ruff、ty 和文档静态站点构建。旧 TCP 用例依赖免鉴权的 Scope 创建，现有端口 8000 的 Scope 查询返回 401。
