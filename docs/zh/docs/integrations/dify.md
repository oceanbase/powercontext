---
status: community
title: Dify
description: 将传统 Workflow Agent 和 Agent V2 接入 PowerContext 外部记忆。
---

# Dify

`community` · `experimental`

接入包含两个独立的 Dify 插件包：PowerContext 工具插件，以及带自动记忆的 Function Calling 策略插件。
Agent V2 还需要 Dify `feat/agent-v2-mem` 分支中的原生外部记忆层。仅安装市场插件无法给未修改的 Dify 版本增加该原生层。

## 安装与作用域配置

使用官方 Dify 插件 CLI 分别打包 `integrations/dify/powercontext` 和 `integrations/dify/powercontext_agent`，
再从 Dify 插件管理页安装。工具插件依赖固定提交的 PowerContext Client；Server 应使用兼容版本。
仓库中的 `integrations/dify/README.md` 和 `ACCEPTANCE.md` 提供开发、协议和部署验收说明。
发布前的隔离本地环境启动脚本和实际验收记录位于 `integrations/dify/local/`。

在 PowerContext 工具提供方中配置：

| 字段 | 说明 |
| --- | --- |
| `base_url` | 插件容器能够访问的 PC Server 地址，建议使用 HTTPS |
| `token` | Server 访问令牌，保存在 Dify 的密钥凭据中 |
| `namespace` | 当前 Dify 工作区或部署的固定唯一标识 |
| `scope_id` | 可选，使用该凭据的调用将共享这个现有 Scope |
| `allow_insecure_http` | 显式允许可信私网中的未加密 HTTP，默认关闭 |

容器内的 `127.0.0.1` 指向容器自身。插件不会自动创建 Scope，也不会在绑定缺失时退回默认 Scope。

默认使用运行时的 Dify app ID 和 user ID。业务模式使用作者配置的固定业务标识，在该应用内共享记忆。
这里的 user ID 是 Dify 运行时身份，不一定等于外部业务系统的用户名。管理员应先建立 Scope 和绑定：

Workflow Service API 的 `sys.user_id` 是外部调用标识，插件收到的是内部 `EndUser.id`。管理员可通过 Console API
查询工作流运行详情，使用 `created_by_end_user.id` 建立用户绑定。

```bash
export POWERCONTEXT_BASE_URL=https://memory.example.com
export POWERCONTEXT_TOKEN=your-admin-token
uv run --project integrations/dify python integrations/dify/provision_scope.py \
  --namespace production-workspace --app-id dify-app-id --subject-kind user --subject-id runtime-user-id
```

传入 `--scope-id` 可绑定现有 Scope。绑定键的 `integration` 为 `dify`，`kind` 为 `user` 或 `business`，
`external_id` 为 `[namespace, app_id, subject_kind, subject_id]` 的紧凑 UTF-8 JSON 的 SHA-256。
解析时始终设置 `allow_default=false`。凭据中的显式 Scope ID 会有意绕过按身份绑定。

Scope 和绑定只负责选择数据，不构成授权。跨信任边界的隔离需要 PC 访问控制，或独立的凭据、Server 实例；
共享管理员令牌不能代替多用户授权。

## 传统 Workflow Agent

1. 安装两个插件包，配置 PowerContext 提供方凭据。
2. 在 Agent 中选择 **PowerContext Function Calling** 策略。
3. 添加 `prepare_context`、`capture_event` 和需要的业务工具，为两个回调选择 PC 凭据。
4. 设置模型、query 和 instruction。模型未声明上下文长度时，填写 `context_window`。
5. 选择用户模式或填写业务标识，并建立对应绑定。

策略会将两个记忆回调从模型工具列表中移除，自动在开始时召回，在执行中记录可见输入、输出及工具调用和结果。
它预留模型输出预算，优先清理较旧的工具结果，再摘要完整的旧交互，并保留首条用户请求。
UTF-8 文本和工具 schema 字节数用于保守估算输入预算。压缩后仍无法容纳时，会在模型请求发出前报错。
该策略面向文本对话，不估算图片、音频的模型专属 token 成本。

## Agent V2 与独立 Agent 应用

将包含记忆层改动的 Dify API、Web、`dify-agent` runtime 一起部署。在共享 Agent 配置中，先通过“工具”添加并授权
记忆工具，再进入“高级设置 → 外部记忆”，选择召回和记录工具、身份模式、字节预算及是否记录执行历史。
Workflow Agent V2 和独立 Agent 应用使用相同配置链路。

配置快照只保存工具和凭据引用，运行时才通过现有租户工具管理逻辑解析密钥。两个记忆工具由运行时自动调用。
召回内容进入临时 instructions，参与 Dify 已有的上下文压缩预算；保存历史时会被清除，不会逐轮重复积累。
本地历史、暂停和恢复状态仍由 Dify 管理。

## 从证据到长期记忆

`capture_event` 写入的是持久化 ContentSource 证据。它不会直接完成长期记忆提取。需要配置 PC 提取管线和调度器，
或在明确的检查点调用 `flush_memory`，之后才能通过召回读取新记忆。插件还提供 `search_memory`、
`remember_memory`、`get_memory_entry`、`revise_memory_entry`、`retire_memory_entry`。

召回默认 8,000 字节，单条事件默认 8,192 字节，范围均为 512–32,768。已知的模型输入预算会进一步限制召回量。
回调超时为 10 秒；失败回调在本次运行中停止重试，下次运行重新尝试。记忆不可用时继续业务执行并记录降级诊断。

采集会脱敏凭据类字段和 PC 令牌，丢弃任意附加 metadata，截断超限事件，不主动采集隐藏推理和二进制附件。
自由文本仍可能含个人信息或无法自动识别的敏感内容，应按应用需要选择是否记录。

Server 成功接收才是持久化边界。确定性 Source ID 提供稳定的重试标识，但没有客户端持久化 outbox，也不保证恰好一次。
进程终止、取消等情况可能丢失尚未确认的事件。

市场发布与 Dify 原生层的上游合并、发版是两个独立交付步骤。自动化测试验证了 SDK 注册、运行时、API/UI 配置，
以及实际 PC HTTP/SQLite 的采集、提取、召回链路；还需在运行中的 CE、plugin-daemon 和真实模型插件上完成部署验收。
