---
title: 用自定义标签管理制品
description: 为逻辑制品和记忆条目设置标签，并通过精确标签进行检索。
---

# 用自定义标签管理制品

Memory、Experience、Skill 和 Handoff 都可以在各自 Scope 内设置标签。一个 Memory 制品与其中的每条逻辑记忆分别拥有独立的标签集合。
标签跟随逻辑 ID，不会修改内容 Revision、条目版本、血缘、向量或 Context Version。

## 在 Dashboard 中使用

启动 Server，打开总览页的**自定义标签**面板：

1. 选择准确的 Scope、标签对象类型、制品类型和制品。对记忆条目，还需要选择 entry ID。
2. 每行输入一个标签，点击**保存标签**。清空输入框后保存，即可清除该对象的全部标签。
3. 在精确查找输入框中填写标签，选择**全部匹配**或**任一匹配**，点击**查找对象**。
4. 点击结果可以编辑对应对象。勾选**包含非活跃对象**，还能找到已停用的记忆条目和已弃用或退役的制品。

如果标签被其他操作修改，保存会提示冲突并保留输入。先点击**重新读取标签**，查看最新状态，再决定如何保存。
重新读取会替换输入框内容；需要保留的文字请先复制。

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

输出应包含保存的两个标签和匹配的对象。对记忆条目，使用 `get_memory_entry_tags` 与 `replace_memory_entry_tags`，
传入 `(scope_id, artifact_id, entry_id)`。entry ID 来自当前 manifest 或 Memory citation，不是 `entry_version_id`。
一个 Scope 可以有多个 Memory 制品；已有的 Scope 级记忆列表和检索接口操作的是运行时指定的 Memory。

## HTTP 接口与检索过滤

| Method | Path | 用途 |
| --- | --- | --- |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | 读取或替换制品标签 |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | 读取或替换逻辑记忆条目的标签 |
| POST | `/v1/scopes/{scope_id}/artifact-tags/query` | 跨制品类型精确查找标签对象 |

PUT 请求体是 `{"tags":["customer-a","release"]}`，并且必须通过 `If-Match` 提交 GET 返回的 ETag。
缺少 `If-Match` 返回 **428**；标签状态过期或 ETag 属于其他对象，返回 **412**。
条件 GET 检测到标签未变化时返回 **304**。`tag_digest` 仅描述规范化的标签集合，不能代替 HTTP 写入使用的 ETag。

制品列表支持重复的 `tag` 参数，以及可选的 `tag_match=all|any`，例如：
`/v1/scopes/{scope_id}/artifacts/skill?tag=release&tag=customer-a&tag_match=all`。
没有 `tag` 时不能单独传入 `tag_match`。

记忆条目列表和检索请求支持以下可选字段：

```json
{"tag_filter":{"tags":["customer-a","release"],"match":"all"}}
```

记忆检索匹配的是条目自己的标签，不是所属 Memory 制品的标签，且仍然只返回活跃条目。
全文与向量通道都在数据库候选集阶段过滤，之后才应用候选数量限制、融合与重排。
SQLite 和 OceanBase 的带标签向量查询会对符合条件的集合进行精确距离排序，成本可能高于不带标签的近似搜索。
不支持标签过滤的后端会明确拒绝请求，不会静默改成先截断再过滤。

标签查询返回当前的精确 Artifact 引用或 Memory citation，按制品类型、对象类型、制品 ID、对象 ID 排序。
翻页时原样传回 `next_cursor`，并保持 Scope、过滤条件和调用方一致。游标有效期是一小时；无效或不匹配返回 **400**，
过期返回 **410**。单页内部保持一致，但跨页不固定数据库快照。

## 标签规则与存储

- 每个对象最多 32 个标签；一次过滤接受 1–16 个标签。
- 每个标签包含 1–64 个 Unicode 码点，首尾不能有空白，不能包含控制字符、代理字符或未分配字符。
- 匹配键由 NFC 规范化后再执行 Unicode case folding 得到，显示文字保留原始写法。
  `Straße` 和 `STRASSE` 等规范化后重复的标签会让整次请求失败；规范化键不能超过 128 个码点。
- 标签是 Scope 内的检索元数据，不是权限或可信指令，不会进入模型提示词、Skill 包 frontmatter 或发布/导入内容。
  发布后的副本不会继承源对象的标签。
- 只要条目仍在当前权威 manifest 中，即使已经停用也可以维护标签。重建活跃搜索投影不会删除标签。

所有关联都存储在 `pc_artifact_tags` 一张表中，通过外键关联所属制品的 head。
表内保留完整规范化键，并使用 32 字节 SHA-256 键摘要建立索引，以同时满足 OceanBase 的外键列长度要求和 3072 字节索引限制。
匹配时同时校验摘要与完整键。相同集合的重复替换保留关联时间。已有制品无需回填，初始标签为空；备份时应包含此表，
恢复时安排在 Artifact heads 之后。

完整请求与响应结构见 Server 的 [HTTP API 参考](/api)。
