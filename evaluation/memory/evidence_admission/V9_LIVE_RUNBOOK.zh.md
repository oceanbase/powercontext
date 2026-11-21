# v9 配对评测执行合同

此入口用于维护者执行和审阅 v5/v9 的同会话小样本比较。请求清单固定为 45 组配对、最多 102 次 HTTP 请求；每次命令只推进一组，完成后必须导入显式审阅结果。真实模型效果与费用需要真实运行验证，本地测试不能替代它们。

代码位于 `src/powercontext_eval_evidence_admission/v9_live/`。执行合同、评分规则、费用预留规则及依赖身份均写入准备后的 plan。既有 v9 编码、SDK 预检、历史结果和失败记录保持冻结。修改执行代码或合同后，已有会话拒绝继续，不能把不同身份的运行混在一起。

## 评分合同

每条候选的审阅身份绑定 case、sample、call、完整输出、候选序号和候选内容。审阅包保存实际提供给该调用的证据与支持范围；评分标签不进入模型请求。默认审阅者为 assistant，`independent_human_review=false`，不能将其描述为独立人工验证。

审阅者需要判断候选实际断言、归属、引用支持以及是否只有一个可独立复用的事实，并显式列出它蕴含的 atom id。仅出现命令、路径或数字不构成语义通过。审阅模板里的 null 必须填成明确判断；缺失、过期或属于其他输出的审阅不能推进会话。

`exact-literals` 固定拆为部署命令、配置路径、作业名和阈值四个子事实。`unicode-boundaries` 固定拆为发布检查要求与保留原始换行要求，emoji、组合字符和 CRLF 的字面量要求仍保留。其他场景沿用每个原必需 claim 的事实身份。模型输出不能反向决定这些分母。

自动检查在语义审阅之外继续约束结果：

- 必需事实的完整字节区间必须由该候选引用的、该次请求内的同源同字段支持覆盖，不能借用其他调用或其他 materialization 的证据。
- 获准的字面量必须以原始 UTF-8 字节出现在对应候选中。NFC 替换、换行改写、emoji 修饰符丢失、数字格式变化不能由语义审阅豁免。
- 覆盖多个独立 atom 的候选记为复合候选，即使包含全部字面量也不能通过原子性门槛。
- 旧 `score_generation` 直接调用并单独报告，不注入新子事实标签。拆分输出可能通过子事实合同而不能匹配旧整句合同，这种差异必须保留，不能声称旧验收已通过。

只有原参考合同确认必要内容可共同容纳的身份才能计为完整成功，并要求必要事实完整覆盖、无不支持候选、无原子性失败、无字面量损失。不可完整容纳的身份报告部分覆盖；即使安全检查允许继续，也不能算完整成功。v5 的质量失败保留用于对照；v9 的质量门槛失败停止后续配对。

v9 copy 字节边界及来源验证由冻结的 renderer 执行；候选字面量在持久化前检查。本执行器不写入生产 Memory，不验证持久化后的原始字节保真，也不承诺完整 UAX 字素边界覆盖。

## 费用与发送合同

冻结费用口径为 CNY、峰时全部未命中缓存：输入每百万 token ¥2，输出每百万 token ¥8。输入预留上界为 1048576 token，采用官方 1M 上下文的保守解释；输出上界为 SDK 明确发送的 `max_tokens=2048`。每次发送前预留 ¥2.113536，响应提供完整 usage 后按同一峰时价格结算已知费用上界，并释放差额。[价格与模型上下文](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[输出参数](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)

这是在冻结 token 和价格合同成立条件下的预留机制，不是 provider 账户扣费硬上限。实际账单、缓存折扣、账户币种和未来价格变化不能由本地估算证明。审批文件必须明确接受该条件及模型别名路由；价格或币种合同改变需要停止并另立合同。

剩余额度不足下一次完整预留时，发送前停止。缺失 usage 时保留完整预留，不按零费用继续；已知 usage 超出冻结计费上界、超过对应样本输入预算，或响应模型/fingerprint 元数据发生变化时停止。实际 provider token 与离线字符预算分别报告，不回写旧离线结果。

每次调用至多一次 SDK 请求，无重试、无续写、并发为 1。真实请求只从已有环境变量 `DEEPSEEK_API_KEY` 读取密钥，不读取或输出 shell 配置，不保存认证头。模型请求沿用 `deepseek-v4-flash`、`https://api.deepseek.com` 和已预检参数；响应模型与 fingerprint 留在审计中，保留别名不等于锁定历史物理模型。

## 准备会话

在仓库根目录使用现有评测虚拟环境执行：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_live \
  prepare --v9-root .artifacts/evidence-admission/v9-offline \
  --sdk-preflight-root .artifacts/evidence-admission/v9-sdk-preflight \
  --output-dir /tmp/v9-paired-session
```

输出目录必须不存在。此命令只重建和验证冻结请求，不读密钥、不调用 provider。`plan.json` 保存公开身份与调用清单；`LOCATION.json` 指向仓库外的私有输入、输出及账本目录，目录权限为 0700，文件为 0600。私有临时目录需要自行持久备份；丢失后会话不能继续，不能据此重发已有调用。

生成的 `approval-template.json` 默认 `live_authorized=false`，不能直接执行。正式执行文件需要明确填写批准者、正确 plan hash、CNY 金额、配对数量范围，以及两项条件接受标志。首组建议范围为 `max_pairs=1`、金额提案为 ¥5；未获得对应授权时不得将模板改成已批准。

## 执行和审阅一组

真实执行命令为：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_live \
  run --session /tmp/v9-paired-session \
  --approval /tmp/approved-v9-plan.json --execute-live
```

首组固定为 `exact-literals / 4K / repetition=0`，v5、v9 各一次，共 2 次，计入 102 次上限。输出 `awaiting_review` 后，从返回的私有 packet 和 template 路径进行审阅，将填写后的文件导入：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_live \
  review --session /tmp/v9-paired-session --review-file /tmp/reviewed-v9-pair.json
```

未审阅时再次执行仅返回相同待审阅包，不重新发送。首组候选门槛通过后，才允许通过新的明确审批扩大配对数量；金额变更也记录新的审批身份。每次调用继续只推进一组，不会自动批量跑完。完整审阅不代表 issue 或生产验收通过，仍需汇总旧分数、新评分及全部不可完整容纳身份。

## 中断与恢复

发送前，执行账本先落盘预留和预测 attempt 身份；SDK 尝试日志记录开始、受限私有响应和终态；输出持久化后才结算。账本采用顺序哈希链，并用进程文件锁保护修改。哈希链用于完整性检查，不是对恶意改写的密码学签名。

- 已结算的调用在重启后跳过，不重发；下一次尚未发送的调用可以在原计划和有效审批下继续。
- 存在预留但没有结算时，无论是否已有 HTTP 记录，都视为状态不明，保留预留并停止，不自动释放、不自动重发。
- 取消继续向调用方传播；即使终态或结算写入失败，后续检查也保留未解决状态。
- 审阅回执已落盘但账本写入失败时，可重新导入完全相同的审阅补齐记录；已完成的相同审阅导入具有幂等性。
- ledger 尾部损坏、产物哈希不匹配或私有文件丢失时拒绝继续，不通过改写文件修复历史证据。

查看状态不发送请求：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_live \
  status --session /tmp/v9-paired-session
```

## 离线验证

`prepare --mock` 创建独立模拟会话，随后只能使用 `mock` 命令及 `live_authorized=false` 的模拟审批；模拟和真实会话不能互换。`mock` 使用实际 SDK 与 MockTransport 的固定空候选响应和模拟 usage，不代表事实抽取通过。

专项测试覆盖真实子进程在预留后和模拟 SDK 发送中退出、跨进程锁、费用不足后的未发送调用续跑、未知 usage、取消、审阅恢复，以及旧整句合同与原子事实合同的差异：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m pytest \
  evaluation/memory/evidence_admission/tests/test_v9_live.py -q
```

本地准备与验证证据保存在 `.artifacts/evidence-admission/v9-live-readiness/`。READY 仅表示请求准备完成，审批模板保持未授权；COMPLETE 仅表示本地交付验证完成。真实调用、真实语义评分与实际费用仍需独立执行和报告。
