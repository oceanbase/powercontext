---
title: 管理上下文的架构与职责
description: 了解 Scope、Source、Artifact、Runtime 和宿主之间的边界。
---

# 管理上下文的架构与职责

本页说明管理上下文时各组件负责什么，以及数据如何从证据流向可复用制品。它是跨工作流的架构说明；具体
写入、审核和检索步骤仍以各制品页面为准。

## 上下文如何产生和使用

### 数据放在哪里

Scope 是数据归属和访问控制的边界，管理自己的 Source、Artifact、Candidate 和处理状态。图中的外框表示一个
Scope；其中的箭头表示内容形成的路径。

### 内容如何形成

![一个 Scope 内的两类制品形成路径：从 Source 处理，或显式创建和更新；需要审核时经过 Candidate](/docs-diagrams/context-formation-zh.svg)

Source 保存证据。处理证据与提交制品是不同的动作：采集完成不表示制品已经生成，生成完成也不一定表示内容已经获批。
制品还可以通过支持该操作的类型接口显式创建或更新，不必先经过自动提取。

- Experience、Skill 的生成提案通过 Candidate 审核后提交；直接调用 Artifact Create/Replace 可以在内容校验通过后立即创建正式 Revision，不会自动生成 Candidate。
- Profile 根据策略自动提交或进入审核，也支持人工创建和替换。
- Memory 和 Topic Memory 按各自的处理规则提交；Topic Memory 不支持手工创建和更新。
- Handoff 通过交接工作流提交；Prompt 通过配置接口管理。

成功提交会形成不可变的 Artifact Revision。具体权限、版本校验与生成条件见各类型的操作指南。

### 制品如何使用

| 使用目的 | 内容与操作 |
| --- | --- |
| 为当前任务提供上下文 | Memory、Experience、Profile、Topic Memory → 按召回和 assembly 规则选择 → PreparedContext |
| 跨会话继续任务 | Handoff → 读取交接内容 → 继续工作 |
| 让宿主发现技能 | Skill → 导出指定 approved Revision → 宿主本地 Skill |
| 调整生成行为 | Prompt → 对应操作使用的提示词配置 |
| 查找与追溯 | 对支持标签的内容进行筛选；按精确 Revision 读取历史 |

这些使用方式是并列入口。Handoff 本身是一类 Artifact；PreparedContext 是一次请求的临时输出。各类制品不会全部
自动进入 PreparedContext，例如 Profile 需要显式选择，Skill 和 Handoff 使用各自的消费方式。

## 各组件运行在哪里

![Agent 宿主通过集成访问 PowerContext Server；Server 内的应用与后台处理使用持久化存储并按需调用模型服务](/docs-diagrams/context-deployment-zh.svg)

Agent 和集成运行在宿主侧，通过 HTTP 连接 PowerContext Server。Server 负责应用接口、授权和后台处理；
后台 worker 在图中是逻辑职责，不表示必须单独部署一个服务。Server 按需调用配置的生成或 embedding 模型。
SQLite 使用本地数据文件，OceanBase / SeekDB 使用配置的数据库连接；图中存储框表示可选后端。

部署参数、服务检查和恢复步骤见[部署与运维](../operate/index.md)。

## 组件和职责边界

| 组件或角色 | 负责什么 | 不负责什么 |
| --- | --- | --- |
| Agent / Host | 提交请求，决定何时保存、交接或使用上下文 | 不因为召回内容而获得额外的执行权限 |
| HTTP、MCP、Python Client、CLI | 把不同集成投影到同一个 Server 契约 | 不应假设各集成暴露完全相同的能力 |
| PowerContext Server | 解析 Scope，执行访问控制，提供 Source、Artifact 和 PreparedContext API | 不替 Agent 做用户授权决定 |
| 后台 Runtime / Worker | 从符合条件的 Source 生成 Memory、Experience、Skill、Profile 或 Topic Memory | 不绕过 Candidate 审核，也不把生成结果当成执行指令 |
| Review / Projection | 审核 Candidate，或把精确的 approved Skill Revision 导出到宿主 | 不修改历史 Revision，不自动安装或执行 Skill |
| 数据库和持久化层 | 保存 Scope、Source、Artifact Revision 和处理状态 | 不替代部署层的备份与恢复策略 |

模型生成、人工审核和执行权限是三个独立边界：需要审核的提案在批准后提交，直接 Create/Replace 则按接口校验和调用方的治理策略提交正式 Revision。
导出创建宿主本地副本，不授予执行权限。`PreparedContext` 是一次 Agent turn 的临时结果，不是新的 Artifact。

## 当前支持的内容类型

| 类型 | 作用和当前生命周期 | 进入下一步的方式 |
| --- | --- | --- |
| Scope | 隔离 Source、制品、Candidate 和运行时状态，并承载访问策略 | 所有内容操作先解析 Scope |
| Source | 保存捕获证据或外部材料引用，是生成和审计的依据 | 显式读取，或由配置的 pipeline 异步处理 |
| Memory | 保存事实、决定、约束、状态和下一步；`retire` 退出 active recall 但保留历史 | 显式写入或从 Source 提取后检索 |
| Experience | 记录可复用的情境、行动、结果和经验 | 生成提案：proposal → Candidate → 审核 → approved Revision → PreparedContext recall；直接写入：Create/Replace → Revision |
| Skill | 保存可导出的名称、描述、instructions、校验和 lineage | 生成提案：proposal → Candidate → 审核 → 精确导出到宿主；直接写入：Create/Replace → Revision → 精确导出 |
| Profile | Scope 的稳定背景信息和 subject 画像 | 由 subject Source 或策略生成，可审核、替换和回滚为新 Revision |
| Topic Memory | 从长期 Source 增量提炼的主题摘要，支持渐进式 detail | `flush` 请求后台处理，`search` 找到当前 head，`get` 读取精确 Revision |
| Handoff | 记录任务边界、交接和里程碑 | prepare → 可选 commit → 接收方 acknowledgement / outcome |
| Prompt | Scope 级操作提示词配置，不是事实证据，也不进入普通 recall | 由具备 `scope.admin` 的调用方管理 |
| Tag | Memory、Experience、Skill、Handoff 的筛选元数据 | 通过标签 API 管理，不是 Artifact family |

写入型 Artifact family 的代码枚举是 `memory`、`experience`、`skill`、`handoff`、`profile`、`prompt`；读取
制品时还包含专用的 `topic-memory`。Topic Memory 目前没有通用的 create、update、delete 或 retire API。

## 数据治理与恢复的现状

当前实现提供以下数据一致性和边界能力：

- Scope 级访问控制，以及写入时的 `expected_version`、ETag / `If-Match` 等并发保护；
- Artifact 的不可变 Revision 和精确引用，Memory 的 `retire`，以及 Profile 通过追加 Revision 完成替换和回滚；
- Candidate 的 pending、approved、rejected 隔离，避免未审核内容进入 Artifact 检索或 `PreparedContext`。

当前没有通用的用户级 Artifact 删除、TTL/保留策略、导出/导入或一键 Scope 恢复 API；Topic Memory 也没有手工删除
接口。部署者可以按[部署 Server 的备份原则](../operate/deploy-server.md)备份数据库或数据目录，并按[故障排查与迁移](../operate/troubleshoot.md)
中的说明恢复或迁移。这些是运维职责，不应混入管理上下文的数据生命周期。
