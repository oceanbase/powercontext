# v9 SDK 请求与审计预检

本报告供评测维护者核对真实 SDK 的请求构造、失败分类和线上小样本的费用提案。预检使用实际 OpenAI/Pydantic AI SDK 和 `httpx.MockTransport`，共完成 45 组配对、90 个样本身份、102 次模拟 HTTP 请求；真实 provider 请求数为 0。协议与审计检查通过，真实生成、事实保留率、原子性和实际计费均未运行。

## 实现边界

独立模块 `src/powercontext_eval_evidence_admission/v9_preflight/` 依赖冻结的 v9 编码和 v6 私有审计边界。v9 协议、三集离线结果、历史 v5 结果及原始失败记录保持原样。运行入口仅提供 `freeze` 和 `mock`；没有真实 provider 执行命令，也不读取环境变量或 shell 配置中的凭据。

适配器向 SDK 传递 v9 的 `Request.payload`、冻结指令和 `TemplateOutput` schema，不以恢复后的完整证据替代压缩输入。输出经过结构校验后恢复短编号和 copy 内容，再验证完整来源引用与领域内容。请求绑定不匹配时在发送前失败。v5 配对基线使用自身的输入、指令和输出 schema。

实际 SDK 请求须包含两条 system 消息和一条 user 消息；预检逐条核对输入、指令、输出 schema 的验证约束、模型名、端点、JSON 模式及输出上限。SDK schema 中的 title、description、discriminator 注解允许变化，名为 title 或 description 的属性本身仍参与校验。实际 schema 和请求正文均保留哈希。

本轮为两组请求均显式设置 `max_tokens=2048`。SDK 通用配置原本生成 `max_completion_tokens`，而 DeepSeek 当前接口文档列出的是 `max_tokens`。这是新配对会话的传输配置修正，不是对历史 v5 请求的重写；尚无真实请求证据判断服务端如何处理旧参数。[接口文档](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)

## 模拟执行结果

| 项目 | v5 配对基线 | v9 |
| --- | ---: | ---: |
| 模拟 HTTP 请求 | 45 | 57 |
| 消息文本字符估算 token | 163806 | 178026 |
| HTTP JSON 整包字符估算 token | 174711 | 191526 |
| HTTP 请求正文 UTF-8 字节 | 678741 | 746007 |
| 消息文本估算超过对应样本预算的请求 | 0 | 0 |
| HTTP JSON 整包估算超过对应样本预算的请求 | 15 | 9 |

消息文本估算包括 SDK 构造的指令、schema 提示和 user 消息。HTTP JSON 还包含转义及传输包装；表内两种口径均保留，不能将 HTTP 字节或 JSON 字符数直接视为计费 token。角色封装、provider 分词及实际输入预算仍待真实 usage 验证。冻结离线草案的输入估算合计为 344268，本轮消息文本估算为 341832；两者构造口径不同，不能据此宣称模型实际节省了 token。

102 次尝试都有唯一开始、唯一终态、请求正文捕获和完整响应捕获，未发生重试。模拟响应固定为空候选，模拟 usage 每次为输入 17、输出 11；这些数字仅用于检验账本，真实 `provider_usage=null`。所有模拟成功终态的语义审阅状态均为 `not_run`，空候选成功解析不能计作事实抽取成功。

## 失败与审计合同

32 项专项测试覆盖合法 copy/引用恢复，以及 HTTP、连接、超时、无效响应 JSON、schema、别名、无支持复制、字节越界、UTF-8 和受保护 Unicode 序列边界、截断、缺失 finish reason、服务端中止、资源中断、内容过滤、空内容、拒绝、意外工具调用、响应捕获超限、输出 usage 超限等失败。

语义质量和原子性失败通过显式审阅回调分别记录；这验证了失败处理路径，未完成真实模型输出的独立语义审阅。取消会继续向调用方传播；终态写入失败时账本覆盖检查失败，不产生成功结论。请求正文被篡改时完整性检查拒绝通过。缺失 usage 保持未知，总用量为 null，已知小计单独保留。

私有审计目录位于仓库外，目录权限 0700，文件权限 0600。请求只捕获正文，不保存认证头或密钥；响应在 SDK 解析前捕获，最多保存 65536 字节，并记录完整与截取哈希、响应长度、完成状态、响应模型、fingerprint 哈希和 usage。响应捕获不完整时不能作为协议成功。上述内容不写入普通运维日志。

## Provider 身份与费用提案

请求沿用已冻结配置：`https://api.deepseek.com`、`deepseek-v4-flash`。截至 2026-10-27，官网说明该旧名称由 DeepSeek-V4.1-Flash-1017 提供服务，原对应模型已下线。保留请求名称不能保证复现历史物理模型；尚未通过真实响应核对模型和 fingerprint。[模型路由说明](https://api-docs.deepseek.com/)

当前 Flash 峰时价格为未命中缓存输入每百万 token ¥2、输出每百万 token ¥8。按全部输入未命中缓存、消息文本估算输入 341832、最大输出 102 × 2048 = 208896 计算，费用代理估算为 ¥2.354832。提议金额为 ¥5.00，`approved_budget=null`。该估算不是硬上界，未核对账户币种、实际 tokenizer 或实际账单。[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

两组都保留 `temperature=0` 且未新增 thinking 设置。当前官方文档说明思考默认开启、思考模式下 temperature 不生效，因此不能据此承诺确定性。若调整思考设置，应冻结新的配置身份后再运行，不能混入当前清单。[参数合同](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)

## 结果位置与复现

从仓库根目录执行，使用现有评测虚拟环境，无需同步或更新依赖：

```bash
PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_preflight \
  freeze --output /tmp/v9-sdk-preflight-freeze.json

PYTHONPATH=evaluation/memory/evidence_admission/src \
  evaluation/.venv/bin/python -m powercontext_eval_evidence_admission.v9_preflight \
  mock --v9-root .artifacts/evidence-admission/v9-offline \
  --session .artifacts/evidence-admission/v5-formal-20261027-144142/session.json \
  --output-dir /tmp/v9-sdk-preflight-reproduction
```

输出目录必须不存在。正式本地结果在 `.artifacts/evidence-admission/v9-sdk-preflight/`：`code-freeze.json` 冻结独立适配器和依赖；`mock/result.json` 保存逐请求检查和费用提案；`mock/session-location.json` 定位仓库外私有审计目录；`checks/` 保存测试、检查及历史保全证据。`mock/COMPLETE.json` 只表示模拟预检结果完整，`live_authorized=false`、`production_authorized=false`。

## 真实小样本前仍需完成

1. 冻结独立子事实、字面量和原子性审阅合同及审阅者；保持旧合同分数与新增诊断并列，不能更改旧分母。
2. 明确可接受的金额及停止策略，落实真实 runner 的调用计数、usage 账本和未知费用处理。¥5.00 目前仅为提案；字符估算和请求次数上限不能替代金额硬上限。
3. 在独立会话中实现执行入口，复用当前适配器，沿用现有 `DEEPSEEK_API_KEY` 配置。冻结清单内先执行一组配对身份，其调用计入最多 102 次；失败立即保留并停止，不增加重试或额外探测。
4. 实际响应确认传输和审计后，才决定是否继续其余小样本。模型路由、usage、协议错误、事实覆盖、原子性与精确字面量均须报告。验证通过仅支持考虑新协议的三集评测，不能据此授权生产接入。

历史 13 个不可恢复响应的根因仍为未知；v5 的既有失败结论不受本次模拟成功影响。
