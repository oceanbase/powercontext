---
title: 使用标签整理内容
description: 为逻辑制品和记忆条目设置标签，并通过精确标签进行检索。
---

# 使用标签整理内容

当前记忆以 Atomic Memory 独立制品保存，每条记忆有自己的标签。标签跟随逻辑 ID，不改变内容版本、lineage 或向量。
其他制品也可在各自 Scope 内设置标签。

对应的 `family` 值为 `atomic-memory`、`topic-memory`、`experience`、`skill`、`handoff`、`profile`、`prompt`。
Profile 的 `artifact_id` 为 `profile`，Prompt 的 `artifact_id` 为提示词 key（例如 `atomic_memory.extract`）。
标签要求对象已经保存为制品；尚未保存的内置默认 Prompt 没有独立标签，需要先保存 Prompt 制品，再设置标签。

开启访问控制时，标签遵循所属对象的读取与修改权限。只读分享者可以读取该对象的标签，不能修改标签或执行 Scope 级标签查询。
跨对象查询需要 `scope.read`；Topic Memory 标签使用 `scope.read` 读取、`scope.admin` 修改。
Prompt 标签沿用 Prompt 的读取权限，修改需要当前 `scope.admin` 权限；撤销 Scope 管理权限后不能凭保留的制品所有权继续修改标签。
Profile、Experience、Skill、Handoff 和单条记忆的标签使用对应对象的 `artifact.read` / `artifact.write` 权限。
权限不足返回 **403**，撤销分享后立即失去相应标签访问权限。

标签通过下文的 Python Client 或公开 API 管理。Dashboard 用于阅读已保存内容，不提供标签编辑和查询页面。

## 使用 Python Client

需要一个运行中的 Server，以及已有的 Scope 和制品。请从 Dashboard 或对应 API 获取 ID，不能用标题代替 ID。
在终端设置以下非敏感参数：

```bash
export POWERCONTEXT_TAG_SCOPE='已有的-scope-id'
export POWERCONTEXT_TAG_FAMILY='skill'
export POWERCONTEXT_TAG_ARTIFACT='已有的-artifact-id'
```

在已安装 `powercontext` 的环境中运行下面的代码。若 Server 开启认证，通过 `POWERCONTEXT_SERVER_AUTH_TOKEN` 提供 bearer token，
不要把凭据写进代码。

```python
import asyncio
import os

from powercontext.client import PowerContextClient
from powercontext.http import QueryArtifactTagsRequest, ReplaceArtifactTagsRequest


async def main():
    scope = os.environ["POWERCONTEXT_TAG_SCOPE"]
    family = os.environ["POWERCONTEXT_TAG_FAMILY"]
    artifact = os.environ["POWERCONTEXT_TAG_ARTIFACT"]
    async with PowerContextClient(
        "http://127.0.0.1:8000", token=os.getenv("POWERCONTEXT_SERVER_AUTH_TOKEN")
    ) as client:
        current = await client.get_artifact_tags(scope, family, artifact)
        if current is None:
            raise RuntimeError("An unconditional read must return the current tag set")
        saved = await client.replace_artifact_tags(
            scope, family, artifact,
            ReplaceArtifactTagsRequest.model_validate({"tags": ["customer-a", "release"]}),
            expected_etag=current.etag,
        )
        print(saved.tag_set.model_dump(mode="json")["tags"])
        matches = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["CUSTOMER-A"]})
        )
        print([item.target.model_dump(mode="json") for item in matches.items])


asyncio.run(main())
```

输出应包含保存的两个标签和匹配的对象。Atomic Memory 使用同一套 `get_artifact_tags` / `replace_artifact_tags`，
传入 `family="atomic-memory"` 和搜索或列表返回的 `artifact_id`。

迁移前的 entry 标签接口仍可接受旧 `(scope_id, artifact_id, entry_id)`，并在 API 层映射到对应的新制品。
迁移时已经不在旧当前 manifest 中的条目无法通过该逻辑入口定位；保留的旧 citation 用于精确历史读取。
新记忆不生成旧 entry ID。兼容范围见[原子记忆](atomic-memory.md)。

## HTTP 接口与检索过滤

| Method | Path | 用途 |
| --- | --- | --- |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | 读取或替换制品标签 |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | 兼容读取或替换迁移前 entry 的标签 |
| POST | `/v1/scopes/{scope_id}/artifact-tags/query` | 跨制品类型精确查找标签对象 |

PUT 请求体是 `{"tags":["customer-a","release"]}`，并且必须通过 `If-Match` 提交 GET 返回的 ETag。
缺少 `If-Match` 返回 **428**；标签状态过期或 ETag 属于其他对象，返回 **412**。
条件 GET 检测到标签未变化时返回 **304**。`tag_digest` 仅描述规范化的标签集合，不能代替 HTTP 写入使用的 ETag。

制品列表支持重复的 `tag` 参数，以及可选的 `tag_match=all|any`，例如：
`/v1/scopes/{scope_id}/artifacts/skill?tag=release&tag=customer-a&tag_match=all`。
没有 `tag` 时不能单独传入 `tag_match`。

Atomic Memory 的 list/search 接口使用如下标签字段：

```json
{"tags":["customer-a","release"],"tag_match":"all"}
```

`/v1/memory/search` 等兼容入口仍接受旧 `tag_filter` 请求结构。两者都匹配独立记忆的标签。
全文和向量查询先应用权限、标签等资格条件，再限制候选数量和重排；搜索只返回在役记忆。
当前 SQLite 和 OceanBase 的 Atomic Memory 向量搜索使用精确距离排序，计算量随合格向量数和维度增长。

标签查询返回当前精确 Artifact 引用，按制品类型、对象类型、制品 ID、对象 ID 排序。
不传 `families` 时查询当前支持的制品类型，也可指定 `{"tags":["release"],"families":["atomic-memory","skill"]}`。
旧 `memory` 集合不作为当前记忆的发现入口。标签会在内容更新和服务重启后保留。
翻页时原样传回 `next_cursor`，并保持 Scope、过滤条件和调用方一致。游标有效期是一小时；无效或不匹配返回 **400**，
过期返回 **410**。单页内部保持一致，但跨页不固定数据库快照。

## 标签规则与存储

- 每个对象最多 32 个标签；一次过滤接受 1–16 个标签。
- 每个标签包含 1–64 个 Unicode 码点，首尾不能有空白，不能包含控制字符、代理字符或未分配字符。
- 匹配键由 NFC 规范化后再执行 Unicode case folding 得到，显示文字保留原始写法。
  `Straße` 和 `STRASSE` 等规范化后重复的标签会让整次请求失败；规范化键不能超过 128 个码点。
- 标签是 Scope 内的检索元数据，不是权限或可信指令，不会进入模型提示词、Skill 包 frontmatter 或发布/导入内容。
  发布后的副本不会继承源对象的标签。
- 非在役记忆的标签仍保留在权威标签表中；重建搜索投影不会删除这些标签。

所有关联都存储在 `pc_artifact_tags` 一张表中，通过外键关联所属制品的 head。
表内保留完整规范化键，并使用 32 字节 SHA-256 键摘要建立索引，以同时满足 OceanBase 的外键列长度要求和 3072 字节索引限制。
匹配时同时校验摘要与完整键。相同集合的重复替换保留关联时间。已有制品无需回填，初始标签为空；备份时应包含此表，
恢复时安排在 Artifact heads 之后。

完整请求与响应结构见 Server 的 [HTTP API 参考](/api)。
