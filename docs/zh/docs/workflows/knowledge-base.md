---
title: 知识库插件
description: 导入 Markdown 和图片，维护文档版本，并跨知识库 Scope 检索。
---

# 知识库插件

`knowledge-base` 插件实现 [RFC #2570](https://github.com/oceanbase/powercontext/pull/2570)，拥有 `document` Artifact family，
支持 SQLite、嵌入式 SeekDB 和 OceanBase MySQL 租户，提供导入、解析、不可变 chunk 结果、混合检索和 Dashboard 页面。

## 安装与启动

从仓库检出目录执行。以下命令创建独立本地实例、安装可选模型／OCR 依赖，并仅监听本机。
首次向量调用下载固定版本的公开模型；离线部署前应准备模型缓存。Server 与 Worker 必须使用相同的 Python 环境、插件代码和本地数据目录。

```bash
uv sync --locked --extra knowledge-base --extra server --extra cli
mkdir -p "$PWD/.powercontext/kb-demo/plugins"
cp -R plugins/knowledge-base "$PWD/.powercontext/kb-demo/plugins/knowledge-base"
export POWERCONTEXT_KB_DATA_DIR="$PWD/.powercontext/kb-demo/data"
export POWERCONTEXT_KB_IMPORT_ROOTS="$PWD/docs"
export POWERCONTEXT_SERVER_PLUGINS_DIR="$PWD/.powercontext/kb-demo/plugins"
export POWERCONTEXT_SERVER_WORKSPACE="$PWD/.powercontext/kb-demo"
export POWERCONTEXT_SERVER_DATABASE='{"kind":"sqlite","url":"sqlite+aiosqlite:///.powercontext/kb-demo/core.db"}'
export POWERCONTEXT_SERVER_RUNTIME='{"artifact_processing_families":[]}'
export POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
uv run --no-sync powercontext server run --host 127.0.0.1 --port 8765
```

沿用部署的认证与权限配置，登录后打开 `/dashboard/plugins/knowledge-base`。
空间创建／重命名、知识库创建和处理配置要求 `server.admin`；导入要求 Scope contribute，检索要求 Scope read；
编辑与遗忘还检查 Artifact 权限。插件页面拿不到会话凭据；Dashboard 桥接仅转发已声明的插件路由，以及显式启用的本插件 family 操作，
保留调用者身份和版本前置条件。
针对已有文档的新替换请求，包括文件变化后重新上传、重试和显式重解析，还要求该文档的 Artifact write 权限。
重放已经接受的请求时，保留原先固定的版本基准和回执。

使用 SeekDB 时另安装 `--extra seekdb` 并配置核心 SeekDB 数据库。OceanBase 按 Server 部署指南准备 MySQL 用户租户与核心数据库。
插件使用相同后端／账号的独立数据库，名称为 `powercontext_plugin_<核心数据库名和插件名的 24 位摘要>`。
账号不能创建数据库时，管理员可预建，启动错误会给出准确名称。核心表不增加字段。
SQLite 使用框架管理的插件存储目录中的独立数据库文件。

`POWERCONTEXT_KB_IMPORT_ROOTS` 使用操作系统路径分隔符列出允许的本地目录；空值禁用 Server 目录导入，仍允许浏览器上传。
单文件上限 16 MiB，图片上限 2,500 万像素，每批最多 100 个明确路径，单文档最多 2,000 个 chunk。
Server／Worker 必须共享本地文件系统。本期不包含远端对象存储、PDF／Office、恢复、永久清理和生成式问答。

## 空间、创建与重试

空间是插件内部的组织层 Scope，不是核心父 Scope 或权限租户。每个知识库 Scope 属于一个空间。
首次解析默认空间时创建个人空间；默认关系以认证主体为键，切换时检查当前版本。可见空间不意味着具有其中知识库的权限。

记 `P=/v1/plugins/knowledge-base/api`。通过 `GET P/me/default-space` 获取默认空间。
创建知识库时必须明确指定空间和稳定的请求键：

```json
{"space_scope_id":"<space ID>","title":"Operations","idempotency_key":"operations-create-1"}
```

将其提交到 `POST P/knowledge-bases`，返回 `scope_id`、插件配置和配置 `version`。
创建身份包含调用者、明确的空间和请求键。即使默认空间已改变，也用**相同正文和请求键**重试；
核心 Scope 已创建而插件归属未提交时，重试修复同一个知识库。缺少空间返回 422，同一身份变更创建参数则冲突。
没有完成注册的 Scope 不允许导入，也不进入空间检索。

`GET/POST P/spaces` 列表／创建空间，`PATCH P/spaces/{space_id}` 用 `{title,version}` 重命名。
`PUT P/me/default-space` 接收 `{space_scope_id,version}`；`GET P/knowledge-bases?space_scope_id=...` 只返回可读知识库。

## 导入与处理

Dashboard 支持选择／拖拽上传，以及明确指定 Server 目录文件。Markdown 内图片保留引用；
独立导入的 PNG／JPEG／WebP 图片使用 RapidOCR 和 ONNX Runtime，生成带原始文字框、阅读顺序、置信度的 Markdown 文本块。
两条路径都按标题组织和合并块，再按句子／行及长度切分。默认最大 360 字符、重叠 40 字符；向量化前检查完整输入的 tokenizer 长度。
Markdown 中的 HTML 块（包括 HTML 表格）在 chunk 中保留原始内容和出处行号。

浏览器上传把文件字节提交到：

```text
POST P/scopes/{scope_id}/uploads?relative_path=runbooks/restart.md&idempotency_key=upload-1
Content-Type: application/octet-stream
```

本地导入向 `POST P/scopes/{scope_id}/imports` 提交：

```json
{"root":"/allowed/runbooks","paths":["restart.md","checklist.png"],"idempotency_key":"batch-1"}
```

目录导入按路径逐项返回结果。确定性的文件错误、请求冲突或缺少文档写权限时，该项 `accepted` 为 false；冲突和权限错误还包含
`status` 和 `error`。其余文件继续处理，成功项保留请求与 Source 回执；基础设施故障仍会使请求失败。

本地导入拒绝路径穿越和文件符号链接。OpenDAL 管理按内容寻址的不可变本地快照，Worker 读取时校验摘要。
浏览器上传和目录导入拥有独立、稳定的 collection 身份。同 collection／路径对应同一文档；字节变化产生新原文 Source，重命名视为新文档。
字节与配置未变化时可复用已发布文档，不重复解析；同一请求键提交不同字节／显式参数会冲突，重试沿用首次接受时固定的配置。

接受导入时固定快照、原文 Source、处理配置、向量模型配置和预期 document revision。
只有 `knowledge-base-process` Source 进入框架 cursor／Supervisor，`knowledge-base-file` 仅作为不可变证据。
不新增插件任务队列或调度器。确定性的输入错误被记录，后续文件仍可处理；基础设施错误沿用框架窗口重试／退避，可能阻塞该 Scope，直到故障修复。

`GET P/scopes/{s}/imports/{request_id}` 显示 pending／processing／failed，或经核心历史验证的完成状态，同时单独展示可用版本。
`POST .../imports/{request_id}/retry` 接收**新的** `idempotency_key`，创建新处理请求。
`GET P/scopes/{s}/documents` 返回的近期请求采用相同的发布校验；即使处理观察记录仍为 processing，已提交的任务也返回 completed。
`POST P/scopes/{s}/documents/{d}/reprocess` 按当前配置重新解析；可选 `manual_edits_policy:"replace"` 明确允许替换人工修改，
否则冲突的重解析失败。重解析同时固定原文快照及其对应的 document revision。并发上传、编辑或遗忘会阻止过期处理结果发布，
包括读取快照期间发生的变化；如需使用最新原文，应提交新的重解析请求。
以相同重解析键和显式文档／策略重试时，返回已接受请求的回执，即使原文件或处理配置已经变化。
Source 登记中断时也从原先固定的输入和版本基准继续，不以当前 revision 重新确定基准；同键改用其他文档、策略或操作会返回冲突。
同步 chunk 编辑在发布时也检查治理版本；向量准备期间发生遗忘时，编辑会被拒绝。

`PATCH P/scopes/{s}/settings` 接收 `{version,chunking:{max_chars,overlap}}`；已有文档只在重解析后改变。
部署使用插件独立、固定版本的 `BAAI/bge-small-zh-v1.5` 512 维模型，不继承核心模型。
本期不支持在线切换模型，配置指纹不一致时拒绝处理。后续解析器可实现内部解析协议并在
`pc_knowledge_base/content.py` 注册扩展名，不需要新增 Artifact 插件。

## 读取、编辑与遗忘

通用 Artifact GET／历史版本接口返回精简 manifest，包括标题、结果身份、chunk 数量、原文引用和处理配置。
chunk 正文保存在独立不可变记录中，通过以下插件接口读取：

| 操作 | `P/scopes/{s}/documents/{d}/revisions/{rev}` 下的接口 |
| --- | --- |
| chunk 目录 | `GET /chunks?offset=0&limit=50`，仅元数据，最多 100 条 |
| 单个 chunk | `GET /chunks/{chunk_id}` |
| 批量正文 | `POST /chunks/batch-get`，正文 `{"chunk_ids":["..."]}`，最多 100 个 |

明确版本不存在时不会回退到当前版本。编辑使用通用 `PUT /v1/scopes/{s}/artifacts/document/{d}`，
带 `If-Match: "revision:3"`：

```json
{"content":{"operation":"edit_chunk","chunk_id":"<ID>","text":"Verify the latest backup before restarting."}}
```

移除 chunk 使用 `operation:"remove_chunk"`，不传 `text`。两种操作都同步完成，新增一个 document revision，
并由核心生成包含原始编辑命令的 lineage-only Source。手动替换正文保留逻辑 chunk ID，重新切块生成新 ID。
移除最后一块形成合法空版本，历史 chunk 和索引继续保留。
新结果及索引先持久化，再提交 manifest；发布失败只留下不可见的未引用结果。

`POST /v1/scopes/{s}/artifacts/document/{d}/forget` 接收 `{revision,state_version}`。
遗忘把生命周期改为 deprecated，不新增文档版本和编辑 Source。普通检索立即排除，已授权的明确历史版本仍可读取。
原文 Source 和快照保留。本期不实现 Source 删除、级联删除或恢复。

## 检索与存储

```json
{"space_scope_id":"<space ID>","scope_ids":["<operations Scope>","<development Scope>"],"query":"restart prerequisites","mode":"hybrid","limit":10}
```

提交到 `POST P/retrieve`。省略空间时解析当前默认空间；省略 `scope_ids` 选择其中可读知识库，明确传空数组则不选择任何库。
明确选择不可读或其他空间的知识库会使整个请求失败。最多选择 20 个库、返回 50 个命中。
`POST P/scopes/{s}/retrieve` 直接查询单库，不受默认空间影响。通用 document 搜索与 Prepared Context 复用当前版本过滤。
插件检索接口逐块返回结果。通用 Artifact 搜索适配层最多召回 50 个排序后的 chunk，按 document revision 汇总，limit 按文档计数；
文档得分取其最佳命中 chunk 的得分。Prepared Context 接收汇总片段，文本中保留 chunk ID、标题路径、原始 Source 引用、位置和人工修改标记，
避免 Artifact 级去重丢掉同一文档的其他命中片段；输出仍受上下文字节预算限制。

SQLite 使用 FTS5 和 sqlite-vec 余弦距离；SeekDB／OceanBase 使用原生 FULLTEXT 和 VECTOR 存储／距离计算。
先融合全文与向量排序，再校验当前 active document／result／模型配置，并补充候选，不直接比较各库原始 BM25 分数。
命中包含 Scope、明确文档版本、chunk ID、原始 Source 和位置。每路候选最多 5,000；大量历史结果可能影响召回率，本期不承诺大规模性能。

四张插件领域表为 `pc_knowledge_base_spaces`、`pc_knowledge_base_space_defaults`、`pc_knowledge_base_resources`、
`pc_knowledge_base_chunks`；全文／向量投影属于派生结构。核心仍管理 Artifact 版本、生命周期、编辑证据、Source、cursor、租约与重试。
备份实例时应一起保留核心数据库、插件数据库和快照目录。

## 验证

```bash
uv run --locked --extra knowledge-base pytest tests/knowledge_base tests/e2e/test_knowledge_base.py
```

默认端到端测试只在临时插件副本中使用确定性向量，正式插件没有模拟向量回退。
设置 `POWERCONTEXT_TEST_KB_MODELS` 为已准备模型的本地数据目录，可额外运行真实向量模型验收。
设置 `POWERCONTEXT_TEST_KB_SQL` 为隔离的 OceanBase／SeekDB 配置以验证原生投影；
设置 `POWERCONTEXT_TEST_KB_SERVER_DATABASE` 为隔离核心数据库配置以验证完整 Server／Worker 流程。
