# v17 串行多请求线上诊断合同

本实验仅验证 v16 已冻结的候选准备策略能否经真实 SDK 和 AtomicMemoryGenerationPipeline 完成生成。
保留 v16 的 insufficient_evidence、三次对一次的最小清单失败和全部历史结果，不追认旧门槛通过。
本合同在新输出检查前单独提交；不是正式全 arm 联合比较，也不授权生产行为变更。

## 范围与冻结

编码、事实选择、原始证据、指令、输出 schema、支持范围及资源预算全部沿用 v16。
本次只将诊断清单显式设为 exact-literals 一次、unicode-boundaries 三次，总共最多四次请求。
两例均为 synthetic-v1 / bounded-fragments-v3 / 4096 / repetition=0。
Unicode 每请求估算为 3947、4033、3189，累计 11169，不超过原每例八倍预算；没有增加原生预算。

继承离线证据时逐文件核验旧冻结依赖、SDK 和 Unicode 数据库身份，以及三集完整比较文件哈希。
新执行代码与测试有独立身份，实际 HEAD blob 校验后才能运行。不用新增执行文件改变旧 v16 的身份或结论，
也不声称重新跑过三集比较。任何旧准备依赖变化均拒绝继承，须另行完整离线验证。
预检和每次调用都重新捕获、持久化、关闭重读真实 Source 并构建输入，计划须逐项匹配旧最小清单。

## 执行与停止

先以 MockTransport 通过实际 SDK/Pipeline 验证四个请求、配置、schema 和最终消息文本预算；
这只是传输预检，不是模型质量证据。真实 HTTP body 哈希必须与预检相同。
请求模型 deepseek-v4-flash，base URL https://api.deepseek.com，thinking disabled，temperature 0，
输出最多 2048 token、60 秒超时、SDK 和 HTTP 重试均为零，禁止 continuation。
官方文档当前说明该旧别名路由至 DeepSeek-V4.1-Flash-1017；固定请求别名不等于固定物理快照。

先运行精确字面量，再审阅该响应；仅通过后才依次运行 Unicode 请求。每次都须审阅才允许下次请求。
输入或传输预算、引用、精确字面量、原子性、归因、未决语义、模型路由、未知 usage 或请求失败均停止，
不重试、不跳到后面的请求、不修改事实标签、不自动拆候选，也不启动正式矩阵。
每个请求只要求保留其实际准入的原子事实；每例结束还须汇总证明全部必需原子事实保留。
语义审阅使用既有 v10 绑定和评分合同。assistant 诊断须如实标记，不作为独立人工验收。

私有目录保存原始请求、响应、模板、输出和绑定审阅；目录 0700、文件 0600。
API key 只从 DEEPSEEK_API_KEY 或用户指定的 ~/.bashrc 同名纯字面量赋值载入，不输出、不持久化、
不执行 shell 启动文件。公开报告仅含计数、配置、安全类别及哈希。
发送前持久化 in_flight，收到原始响应先 fsync 再解析；进程中断或未完成状态不能自动重发或冒充完成。
协程取消传播并记录 cancelled，不发布 Memory 或生产游标；远端取消和断电恢复不在本诊断证明范围内。

## 费用

已批准 ¥5 从 v9 起累计，既有峰时全未命中上界为 ¥0.090520。
2026-11-04 核验官方输入/输出峰时价格为 ¥2/¥8 每百万 token：
https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ 。
每次发送前保留输入完整 1048576 上下文加 2048 输出的 ¥2.113536，而非把字符估算当作计费用量。
串行返回完整 usage 后按保守费率记账，再决定下次是否能预留；不预先承诺四次都可完成。
失败缺失 usage 不计零，保留预留暴露并停止。更早 v5 未知 usage 独立保留，不属于本次已批准预算范围。

## 命令与审计

在源码仓库、匹配旧 SDK 的环境运行；会话必须使用新目录。preflight 不访问 provider。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 freeze
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 preflight --baseline-root .artifacts/evidence-admission/v16-offline --session /PRIVATE/v17-preflight
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 prepare --baseline-root .artifacts/evidence-admission/v16-offline --session /PRIVATE/v17-live --preflight-session /PRIVATE/v17-preflight
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 run --session /PRIVATE/v17-live --execute-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 review --session /PRIVATE/v17-live --review-file /PRIVATE/bound-review.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 status --session /PRIVATE/v17-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v17 report --session /PRIVATE/v17-live --output /PRIVATE/final-report.json
```

Ctrl-C 或取消任务会停止本地运行。in_flight、awaiting_review、failed、quality_failed、cancelled 均禁止再次发送。
awaiting_review 只能导入绑定审阅后前进；运行中断后的未知费用必须先核对，不能重建账本绕过。
报告从固定计划及私有状态重新生成，不覆盖旧文件。最小诊断全部通过也继续标为 insufficient_evidence，
因为正式各 arm 覆盖和独立关键审阅仍未完成。任何生产后续均须单独满足 issue 的完整决策门槛。
