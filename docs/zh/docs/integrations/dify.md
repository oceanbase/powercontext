---
title: Dify
description: 通过实验性 Dify 工具插件连接独立的 PowerContext Server。
---

# Dify

[PowerContext 工具插件](https://github.com/oceanbase/powercontext/tree/master/integrations/dify) 提供 19 个 HTTP 工具，覆盖记忆维护、有限上下文、显式 Source 采集、交接、Experience/Skill 生成与精确读取、候选查看。当前提供实验性源码，Marketplace 发布进度由 [#1837](https://github.com/oceanbase/powercontext/issues/1837) 跟踪。

先独立部署 PowerContext Server，再用 Dify 官方 CLI 打包并安装到工作区。配置插件 daemon 可访问的 Server URL、Bearer token，以及一个已创建的 Scope ID 或预登记的 `dify/configured-scope` binding key。两种 Scope 配置只能选一种，每次调用都禁止回退 Default。凭证校验会解析并读取受保护的 Scope；具体操作的权限由 Server 判定。

一个凭证组合代表共享的应用或团队 Scope，不会根据 Dify 的用户、会话字段自动实现个人隔离。嵌套引用保持原有身份，由 Server 验证访问关系。不同授权边界应使用受限制的凭证和 Server 策略，或独立实例。

插件不会自动召回、自动采集、审批候选、管理 Scope、安装外部 Skill，也不提供 Agent V2 原生 memory 回调。可选提示词帮助 Agent 选择工具；要保证每次回答前召回，需要 Workflow/Chatflow 先执行 `pc_prepare_context`，再明确把 `result.content` 连接到模型输入。可复用模板安排在插件被 `langgenius/dify-plugins` 接受之后搭建。

每次调用输出文本、JSON 和六个具名变量：`ok`、`operation`、`status`、`data`、`error`、`result`。成功时保留完整 HTTP 回执；`empty` 表示成功的空读。工作流变量选择器可展开 `result` 中的操作专属字段，包括上下文正文和精确引用。使用前先判断 `ok`；错误或结果不确定时 `result` 为 `{}`，`data` 则保留可能存在的恢复回执。写入超时或回执异常返回 `unknown`，应先检查 Server 状态再决定如何恢复，插件不会自动重试。显式采集前须移除秘密信息。召回的历史文本只作为不可信证据。

对象、数组和 nullable 工具输入使用包含一个 JSON 值的字符串。可选参数不用时省略；显式空值填写文本 `null`，nullable 字符串须带 JSON 双引号（如 `"理由"`）。Dify 1.17.1 默认的 `0.6.10-local` daemon 会保留有效的 string 声明及描述中的完整解码后 schema，插件仅解码一次并校验 HTTP 契约。空引用对象、无效 JSON 会被拒绝。工作流输出保持原生 JSON 值；完整结构化输出交给下一个工具前，先在 Code 节点中序列化一次。交接串联见[示例](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/plugin/README.md)。

工作流可逐层选择 `result.candidate.candidate_id`、`result.draft.objective` 等字段，完整回执中的 null 值保持原样。读取可能为空的候选或草稿前，先检查具体操作的状态。源码回放测试覆盖官方 daemon 序列化、Dify 参数模型、类型转换和模型 schema；插件安装、真实分发及 Agent/Workflow 执行仍需部署验收。

`data` 和 `error` 保存封装对象或 null，变量选择器不会展开它们的子字段。下游节点引用具体响应字段时，使用 `result.*`。

详细说明见源码中的 [README](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/README.md)、[工具清单](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/tool-coverage.md)、[Scope 映射](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/scope-mapping.md)、[隐私说明](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/plugin/PRIVACY.md) 和 [验收记录](https://github.com/oceanbase/powercontext/blob/master/integrations/dify/ACCEPTANCE.md)。
