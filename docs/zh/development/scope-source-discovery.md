# Scope 发现与旧回执迁移

`query` 对指定原始字段执行数据库原生子串匹配，不支持正则表达式或通配符语法。大小写、重音及全半角的匹配行为遵循底层数据库的字符比较规则，不保证不同存储后端结果完全一致。服务端只去除 query 首尾空白，不执行 NFKC、casefold 或大小写转换。`%`、`_`、`*` 和反斜线按普通文本传入。

查询直接使用原始字段，不读取规范化搜索投影。SQLite 使用 `instr(original_column, :query) > 0`；OceanBase/SeekDB 的 MySQL 方言使用 `locate(:query, original_column) > 0`。query 始终使用绑定参数，不拼接 SQL，不额外强制 collation。已有搜索派生列仅为兼容既有表结构而保留，不参与匹配，也不要求重新规范化历史数据。

## 旧 Handoff 回执迁移

启用访问控制的服务在开始接收请求前执行可重复迁移。每批最多读取 100 条 Source 身份，并逐条加载内容；只扫描公开 content Source。回执 schema 仅用于筛选候选，不作为可信证明。回执写入会先持久化身份预留，并在 Source 成功落库后持久化独立的提交事件；迁移只有同时核对身份预留和提交事件后，才补充服务端 `handoff_receipt` 标记。迁移不修改 Source 内容、ID、digest 或 journal position，不创建版本，也不触发生成或 consumer 队列。

无法找到已提交回执证明的候选写入 `pc_receipt_migration_review(scope_id, source_id, reason)`，reason 为 `missing_committed_receipt`。清单只含身份和原因，不含内容。只有身份预留而没有提交事件的记录（包括 Source 写入冲突后的遗留预留）不能自动升级。迁移可重复执行，恢复可信的提交证明后再次启动会补标记并移除对应待确认项。身份存储发生异常时停止启动，不把异常当作记录缺失。

已有可信标记的回执在身份记录缺失时，详情和集合读取均返回 503。未确认的历史 marker-only Source 保持原读取行为；清单不认定其为真实回执，也不自动补造提交者。管理员需恢复记录或人工核实。迁移前即丢失全部可信证据的真实回执不能仅凭内容自动识别。

管理员通过数据库受控访问查询待确认清单：

```sql
SELECT scope_id, source_id, reason
FROM pc_receipt_migration_review
ORDER BY scope_id, source_id;
```

验收覆盖旧回执升级、重复迁移、原 Source 内容/身份/位置不变、迁移后身份缺失返回 503、普通 marker-only 可读、证据恢复后清单清理。

## 验证

定向测试覆盖 Scope 原始字段 SQL 匹配、query/field 配对、分页、Unicode 长查询、SDK 显式 limit、旧回执启动升级与重复迁移，以及分批扫描和证据恢复后的待确认清单清理。

不确定的历史数据保持待确认状态，不自动推断提交者。迁移不会执行 LLM 调用。更改文本或字符比较配置后，调用方应从第一页重新开始；游标绑定原始查询值，不能将大小写或全半角变体作为同一请求复用游标。
