---
title: 管理 Prompt
description: 按 Scope 修改操作提示词、检查示例并恢复 Prompt 版本。
---

# 管理 Prompt

Prompt 自定义功能位于当前 `master`。它修改一个 Scope 内的操作提示词，不能改变输出结构、工具可用性或权限。
采集的用户 Prompt 属于 [Source 证据](sources.md)，是另一条工作流。

## 编辑与验证

1. 打开 Server Dashboard，选择 **Prompts**，再选择目标 Scope 和操作。
2. 检查页面显示的能力。自定义需要已配置的 Provider 和已启用的操作。
   注入的组件可能自行管理提示词，并报告不支持自定义。
3. 选择 **Custom**，编辑指令，按需添加完整 JSON 输入/输出示例。
   生成的示例只是建议，保存前应检查。
4. 选择 **Save new revision**。保存成功会创建不可变的 Prompt Revision。
   如果 head 已变化，重新加载当前版本并协调修改。
5. 再次运行目标操作并检查结果。保存 Prompt 不会重新处理历史 Sources，也不会重跑先前操作。

选择 **Auto** 并保存，可使用当前部署的内置提示词。**Restore as new revision** 将选中的历史内容恢复为新 head，
不会删除中间历史。恢复 Auto 版本时使用当前内置提示词，不会固定到旧部署的内置内容。

在 enforced 模式下，创建、替换、切换 Auto 和恢复都需要当前 `scope.admin` 权限。
Scope 角色被撤销后，拥有 Prompt Artifact 也不能继续修改它。

要在另一个 Scope 中复用指令，请使用已注册的 `prompt_key` 创建或替换目标 Scope 的 Prompt。
通用 Artifact 发布接口会拒绝 Prompt，返回 `422 / artifact_publication_unsupported`，目标 Scope 保持不变。

应用可通过 `GET /v1/scopes/{scope_id}/prompts/{prompt_key}` 读取生效配置，再使用带条件写入的
[Artifact API](artifacts.md)保存版本。支持的 key、请求结构和示例生成见 [HTTP API](../develop/http-api.md)。
