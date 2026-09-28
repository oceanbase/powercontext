---
title: 迁移 Memory 查询索引
---

有界 Memory entry 查询使用可重建的 directory 投影。新数据库会自动标记为 ready。
已有 Memory revision 的数据库升级后，Server 仍可使用旧的全量列表、精确详情、搜索和写入；
但在本迁移验证成功前，新查询会返回 `memory_query_index_unavailable`。

## 查看计划并执行

使用与 Server 相同的部署环境文件：

```shell
powercontext server memory-query-migrate --action plan --env-file .env
```

plan 只读，会报告缺失的投影表、权威 Memory revision 数、已有 directory 行数和持久化阶段。

先备份数据库，停止全部旧 Server 和 Memory writer，关闭它们的自动重启，并在维护窗口内保持停止。
命令无法代替操作者建立这些外部条件。随后执行：

```shell
powercontext server memory-query-migrate --action apply --env-file .env \
  --migration-id rfc1656 --batch-size 100 --maintenance-confirmed
powercontext server memory-query-migrate --action verify --env-file .env
```

`apply` 会逐张创建缺失的功能表，每个 backfill 事务最多提交指定数量的 Memory revision，
并在报告成功前验证结果。中断后使用相同 migration ID 重复执行即可。不要清除 marker 或
directory 行；前一次迁移未完成时，更换 ID 会被拒绝。

Batch size 计数的是 revision，不是一个 revision 内的 entry。因此重建大型初始 manifest 时，
该事务仍会为每个 entry 写入一条派生 directory 行。实现使用 set read 和每 500 行一批的
executemany write；选择 revision batch size 和维护窗口时，要把已存储的最大 manifest 纳入考虑。

只有命令返回 `ready: true` 后才能重启 writer 和 Server 副本。独立的 `verify` action
会重新检查所有权威 revision 和 tag generation 行，可安全重复执行。

## 迁移会改变什么

迁移读取不可变 Memory manifest，重建 revision-valid 紧凑 directory 行，并为每个 Memory Artifact
建立一个 tag generation。它不会改写 Memory Artifact、entry version、tag 或 entry body。
directory 仍是派生状态，完整验证成功前不会成为查询依据。

新版本的 Memory 写入会维护自身 directory delta，但不支持在本离线迁移期间滚动混用新旧 writer。
SQLite 迁移需要持久数据库；内存 SQLite 没有需要保留的升级状态。
