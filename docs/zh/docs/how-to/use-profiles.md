---
title: 生成和审核 Scope 画像
description: 通过普通 Scope、主体双写与现有 Artifact/Candidate API 管理画像。
---

# 生成和审核 Scope 画像

Profile 是当前 Scope 证据的完整 Markdown 快照，不是事实源。用户 Scope 通常保存一个人的特征；
群聊 Scope 描述整体情况并保留人物归属。每个 Scope 的身份固定为 `family=profile, artifact_id=profile`。

## 主体双写

业务系统提供 `subject_key`（例如业务 user_id），首期 `subject_type` 仅支持 `user`。
请在现有业务 Scope 中调用：

```http
POST /v1/scopes/S_GROUP/subject-sources

{"subject_key":"U1","content":{"speaker":"U1","text":"我偏好简洁的中文回答"}}
```

服务端通过已有 Scope Binding 的 `integration=subject, kind=user, external_id=U1` 查找普通 Scope。
无绑定时自动创建无父节点的 Scope，并启用自动画像；也可传入已经存在的 `subject_scope_id` 来绑定。
有绑定时直接复用，显式 Scope 与绑定不同返回 409，不静默换绑。两个实际 Scope 相同返回 422。

成功返回两条相同 source_id、content 和 digest 的 Source，scope_id 不同、position 独立。
创建 Scope、绑定、必要的权限关系、策略和两条 Source 在同一事务内完成；原单 Scope Source API 保持单写。
新的 HTTP 重试会产生新 Source 对，不提供请求幂等。两份 Source 独立存在，没有跨 Scope 删除级联。

启用权限时，两个实际 Scope 都需要写权限；新建绑定需要 server.admin。自动新建的 Scope 为调用 Principal
授予 scope.contributor，不能据此写入其他已有 Scope。subject_key 不是认证 Principal，也不会授予权限。
已有换绑、解绑接口不迁移历史数据；多主体绑定同一 Scope 会混合证据。

## 配置与生成

复用已有 Scope 不自动修改其策略。先 GET `/v1/scopes/{scope_id}/profile-policy`；没有策略返回 404。
首次配置用 expected_version=0，后续使用最新版本。策略更新保留待审 Candidate：

```http
PUT /v1/scopes/S_GROUP/profile-policy

{"generation_enabled":true,"activation_mode":"review_required","expected_version":0}
```

默认模式是 automatic。生成需要配置 generation model；后台调度由独立开关控制，默认关闭，
仅配置模型不会自动启用。开启后默认每天北京时间 02:00 扫描，启动时也扫描未消费的 Source。
配置模型后，通过环境变量启用和调整调度：

```bash
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_SCHEDULE_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_CRON="0 2 * * *"
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_TIMEZONE="Asia/Shanghai"
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_CONCURRENCY=4
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_SOURCES_PER_WINDOW=32
```

启用权限的后台任务使用已有 `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID` 配置；
该服务 Principal 必须获得相关 Scope 和已有 Artifact 的写权限。静态本地管理员部署可复用原有后台身份。

`POST /v1/profile/flush`，body 为 `{"scope_id":"S_GROUP"}`，立即处理一个窗口；
后台调度关闭时仍可调用，使用调用方权限，不要求配置后台 Principal。
返回 updated、noop、review_pending、disabled 或 conflict。模型请求遵守现有 generation timeout 和请求上限。
失败不消费窗口；lineage_only Source 被过滤；正文没变化只推进 Cursor，不新增自动 Revision。

每窗口最多 32 条原始 journal 记录；引用旧画像时最多 31 条。每 Scope 每轮最多 100 窗口。
多实例可能重复调用模型，但 Policy/Cursor/Head 校验只允许一个结果提交。没有新 Source 就不重新生成。

## 审核、修改与回退

review_required 产生现有 Candidate，正式画像不变，待审期间暂停该 Scope 后续自动窗口。
通过既有 Candidate Get/List/Revise/Approve/Reject 接口操作：

- Revise 只改 proposal.content；原 source_refs、artifact_refs、target 必须保持不变。
- Approve 原子提交画像、结束 Candidate、推进 Cursor 并清空待审指针。
- Reject 必须给出 reason，固定消费该窗口，不会自动再次提案。
- 待审期间允许人工 Replace。若 Head 已变化，旧 Candidate 不能批准，但仍可 Reject。

人工创建、读取、列表、替换使用现有 Artifact API：

```http
POST /v1/scopes/S_GROUP/artifacts

{"family":"profile","content":{"content":"# Scope 画像\n\n- U1 偏好简洁回答。"}}
```

读取 `GET /v1/scopes/S_GROUP/artifacts/profile/profile` 获取内容与 ETag；
再次 Create 返回冲突。使用同一路径 PUT 和 If-Match 替换正文。
回退先读取 `.../revisions/1`，再以当前 Head 的 ETag 提交该历史正文：

```json
{"content":{"content":"历史版本的完整 Markdown","restored_from_revision":1}}
```

回退生成新的 Revision，不修改历史或倒退 Cursor。generation、时间和证据窗口均由服务端填写，
客户端不能将 GET 返回的 generation 当作可写字段。

按用户查询时，先通过现有 Binding Resolve 传入精确 key 和 `allow_default=false`，再调用普通 Scope API。
不自动遍历群聊或聚合其他 Scope 的所有制品。其他 Artifact Family 继续使用原有的生成与读取方式。

画像不能通过 `POST /v1/artifact-publications` 或 SDK 发布操作复制到其他 Scope。
无论目标是否已有画像，请求都返回 HTTP 422（`artifact_publication_unsupported`，`details.family=profile`），
不创建或修改目标状态。应基于目标 Scope 自身的 Source 生成画像，或使用其已有 Create/Replace 接口。
其他支持发布的制品保持原行为。

## 存储与升级边界

仅新增 `pc_profile_policies`；正文和生成信息进入 Artifact BLOB，审核上下文进入 Candidate BLOB。
其余 Scope、Binding、Source、Head、Lineage、Cursor 和授权表均复用。
部署到正常版本时由既有建表流程创建 Policy 表，不修改已有表定义。

若曾使用未发布的 Subject Root 实验实现，应先备份并在独立测试数据库验证迁移。
服务不会自动删除旧实验表、迁移特殊 Profile ID 或重写旧内容，不能把实验数据库直接当作已兼容数据库。
