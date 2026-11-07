# 真实 Pipeline 小样本执行合同

本入口用于评测维护者验证 v10 指令、编码和评分在真实 SDK 与
`AtomicMemoryGenerationPipeline.extract()` 下的行为。它只执行 `synthetic-v1` 的
`bounded-fragments-v3 / 4096 / repetition=0`：先 `exact-literals`，审阅通过且范围获批后才可执行
`unicode-boundaries`。每个样本一次调用，最多两次；不构成五组对照或三个数据集的正式联合矩阵。

## 冻结与证据

候选输入逐请求绑定已保存的 v11 比较结果。v10 生成指令、TemplateOutput、编码、原子/父事实评分和
旧整句 scorer 保持原样。来源经本地 SQLite 持久化和重读，实际 SDK 只收到冻结的压缩输入。
评分标签不进入模型输入。模拟预检以标注构造受控响应，只证明本地执行和审计路径。

发送前核验所有运行时、评估源码、冻结数据、依赖文件和本执行合同的当前字节与实际 Git HEAD blob 相同；
暂存、哈希快照和历史提交状态均不能代替本次提交冻结。会话绑定提交和内容身份，变化后拒绝继续。
Git 提交不追认旧 v6–v9 运行的冻结条件。v9 原子性失败及 v10 预算失败原样保留。

## SDK 与评分

SDK 参数沿用已核对设置：`deepseek-v4-flash`、`https://api.deepseek.com`、JSON 输出、
`max_tokens=2048`、temperature=0、禁用重试和重定向，未显式改变 thinking。
实际两条 system 消息、一条 user 消息、schema、完整正文哈希和响应在 SDK 解析前审计。
每次最多一次 HTTP 请求，外部响应限 65536 字节。请求正文和响应、审阅包保存在仓库外，权限 0700/0600；
不保存认证头、密钥，不输出原始异常值。

官方说明旧别名请求由当前 Flash 路由服务，不能据此锁定历史物理模型；默认思考模式下 temperature
不保证确定性。每次响应模型与 fingerprint 需保持同一会话一致。
见[模型及价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)和
[请求参数](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)，核对日期为 2026-10-28。

返回后进入待审阅，重复执行返回相同审阅包，不重发。显式审阅须绑定 case、输入、支持范围和输出，
判断实际断言、归属、引用与独立原子性，列出确实蕴含的 atom id。缺失判断保持 unresolved。
原子事实、精确字面量及全部子事实共同满足的父事实覆盖分别报告；旧整句分数保持独立，不改分母。
此外每个样本至少实际使用一段合法 copy，才算本次路径探测完成；纯 text 输出不能证明 copy 路径。
Unicode 仍要求原始 UTF-8 字节，不能规范化组合字符、emoji 或 CRLF。

默认 assistant 审阅属于诊断，不能描述为独立人工验收。v10 scorer 的静态 formal blockers 原样保留，
不用于描述本执行器是否发送了真实请求。即使两例通过，也不授权生产或正式联合矩阵。

## 费用、停止与授权

冻结峰时全未命中缓存费率为输入每百万 token ¥2、输出 ¥8；按 1,048,576 输入 token 与 2048 输出 token
计算，单次完整预留为 ¥2.113536。该金额是条件计费上界，不是 provider 账户硬上限；角色开销、实际 tokenizer、
缓存和实际账单不能由字符估算证明。完整 usage 返回后结算并释放差额。缺失 usage 保留全部预留并停止。
用量超过冻结上界或实际输入超过样本 4096 预算、协议或质量失败、模型元数据变化均停止。
取消继续传播，预留保留；状态不明的请求从不自动重发或释放。

审批必须绑定本次 plan hash，明确金额、最多执行样本数，并接受条件计费与当前别名路由。
旧 v9 审批绑定旧计划且会话已停止，不能自动改成新会话已批准。默认模板未授权；本命令不会提交审批或创建 Git 提交。
账户实际支出和跨会话总额须在批准前核对，不能用新会话的零账本忽略既有支出。

## 操作步骤

在仓库根目录使用现有 evaluation 环境。输出目录必须不存在，所有默认步骤都不读取密钥。

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 preflight \
  --baseline-dir evaluation/memory/evidence_admission/results/v11-offline \
  --output-dir .artifacts/evidence-admission/v12-preflight
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 freeze
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 prepare \
  --baseline-dir evaluation/memory/evidence_admission/results/v11-offline \
  --output-dir .artifacts/evidence-admission/v12-session --mode live \
  --preflight-dir .artifacts/evidence-admission/v12-preflight
```

`freeze` 必须在源码和合同已实际提交后成功。先完成提交冻结及预检，再核对 plan 和预算审批。
只有显式 `--execute-live` 与本计划的有效审批同时满足，才从环境变量 `DEEPSEEK_API_KEY` 读取凭据发送：

```bash
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 run \
  --session .artifacts/evidence-admission/v12-session \
  --approval /outside-the-repository/approved-v12-plan.json --execute-live
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 review \
  --session .artifacts/evidence-admission/v12-session \
  --review-file /outside-the-repository/reviewed-v12.json
uv run --project evaluation python -m powercontext_eval_evidence_admission.v12 status \
  --session .artifacts/evidence-admission/v12-session
```

只批准首个样本时，审阅通过也不会自动执行第二个。任一失败保持原判。
本地 Pipeline、Scope、SQLite、模拟取消不证明远端取消、OceanBase、生产工作器或断电恢复。
总体决策在正式覆盖和关键语义审阅完成前保持 `insufficient_evidence`。
