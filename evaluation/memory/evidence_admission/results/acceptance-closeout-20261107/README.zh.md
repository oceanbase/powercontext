# Issue #2074 受限交付凭据

唯一评估决策为 `insufficient_evidence`。本目录整理验收对照、人工定位待确认项、
历史不可用核对及工程验证，不修改原生成协议、分数、门槛或线上会话。

- `status.json`：交付边界、原结果绑定及仍未完成的证据。
- `validation.json`：本次工程检查、完整矩阵、串行服务链、补充类型检查及未成功尝试。
- `historical-unavailable-check.json`：两次历史调用的请求存在、原响应和用量未恢复状态。
- `human-confirmation-template.json`：17条仍需用户确认的抽查身份，答案保持未选择。
- `final-hooks.json`：新增交付文件的最终 hooks 结果。
- `integrity.json`：原交付与原私有会话保持原字节的核验计数。
- `artifact-index.json`：本次新增文档和凭据的仓库相对路径、大小及 SHA-256。
- `COMPLETE.json`：仅表明本次交付工件已封存，不表示全部测试或 issue 门槛通过。

Python 3.12 和3.14 的根包主集共保留三个未解决测试失败；不能声称 CI 全绿。
完整命令与逐环境结果以 `validation.json` 为准。默认 tox 的中断尝试不计为通过，
实际执行采用主集与独立串行服务链分区，未删去服务链覆盖。

原533项语义未决、17条人工定位未确认和两次历史不可用保持原状态。
部分维度的人工判断不等于完整语义审阅；本次不新增 provider 请求，不推送或创建 PR。

英文 PR 说明见 [PR_DESCRIPTION_READY.en.md](../../PR_DESCRIPTION_READY.en.md)，
中文验收对照见 [ACCEPTANCE_CLOSEOUT.zh.md](../../ACCEPTANCE_CLOSEOUT.zh.md)。
原始日志保留在本机，仓库仅保存脱敏摘要和哈希，不发布原始私有内容、提示、响应或凭据。
