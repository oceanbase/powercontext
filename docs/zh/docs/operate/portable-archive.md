---
title: 导出和恢复可移植归档
description: 创建、验证并恢复经过校验的 PowerContext 逻辑归档。
---

# 导出和恢复可移植归档

可移植归档是用于迁移或恢复完整 PowerContext scope 的 `.pcb` 文件。它是逻辑归档：保存领域 identity 和不可变历史，
而不是复制 SQLite 文件。它适用于受控的本地备份、离线传输，以及未来的后端迁移。

这是一个离线运维命令，不调用远程 Server API。不传 `--env-file` 时打开默认 SQLite 数据库；传入后使用该部署的
SQLite、SeekDB 或 OceanBase 配置。恢复前应停止 PowerContext 写入进程；导出使用一个数据库 transaction 获取一致的
逻辑 snapshot。

## 前提条件

从创建本地数据的同一个 PowerContext revision 安装 CLI。归档命令使用
`POWERCONTEXT_HOME/powercontext.db` 中持久化的 SQLite 数据库；未设置 `POWERCONTEXT_HOME` 时，PowerContext
会使用操作系统用户数据目录。

```bash
uv tool install "powercontext[cli] @ git+https://github.com/oceanbase/powercontext.git@master"
export POWERCONTEXT_HOME="$HOME/.local/share/powercontext"
```

归档包含恢复所需的内容和元数据。应按项目数据的访问控制和保留要求，将它放在受保护的位置。格式会排除凭据和已配置的
provider secret，但不会加密项目内容。

## 创建并检查归档

传入每一个需要包含的完整 scope。不能用前缀选择 scope，也不能只导出某一种 record。

```bash
powercontext archive export \
  --scope-id project:payments \
  --output ./backups/payments.pcb

powercontext archive inspect ./backups/payments.pcb
```

外层存储已经压缩时可增加 `--no-compress`。导出授权一定发生在第一次数据库查询之前；离线命令以操作系统文件权限和数据库
读取权限作为 operator authority，不复用或导出 Server bearer token。无内容的 JSON 进度写到 stderr，最终 receipt
仍以 JSON 写到 stdout。

`export` 会输出 JSON，其中包括 bundle ID、record 数和 checksum。`inspect` 不打开目标数据库，只校验 ZIP 结构、
单 record digest、总 digest、record 数和所选 scope。若外部备份系统需要独立校验记录，请把输出的 checksum 一并保存。

## 在恢复前验证

恢复前始终先针对目标环境验证：

```bash
powercontext archive restore ./backups/payments.pcb --dry-run
```

dry-run 不会写入领域数据。它会校验 checksum、必需的 Source 和 Artifact 依赖，以及已配置 Runtime 是否支持归档中的
source type 和 Artifact family。验证失败会以退出码 `2` 结束，只输出不含内容的原因，不会打印 record body。成功报告
包含 `already_present`、`conflicts`、所需和不支持的 Source type/Artifact family，以及目标是否支持 projection rebuild。
可用 `--env-file ./target.env` 检查另一个部署。

要执行写恢复，必须显式确认：

```bash
powercontext archive restore ./backups/payments.pcb --yes
```

结果为 JSON，包含 `inserted`、`already_present` 和 `projections_ready`。只有 `projections_ready` 为 `true`
时才能宣称搜索已就绪。Runtime 会在恢复权威数据后重建可移植的 Memory 和 Experience 搜索 projection。

## 归档保留的内容

格式版本 1 支持以下可移植关系记录：

| 会保留 | 不可移植 |
| --- | --- |
| Scope identity、层级、context/external reference 和创建 identity | Scope access binding 和 host-local default selection |
| Source journal head 和 Source record | 搜索 projection 和 index |
| Artifact Revision、lineage、跨 Scope 发布来源和 head | Source cursor 和 scheduler state |
| Memory entry version 和 head | External Skill registration 和 host-local installation state |
| Candidate version、decision head 及其 evidence reference | Audit event、usage fact、evaluation receipt 和 restore receipt |
| 作为 Source 保存的 Work contract、task outcome、Handoff boundary 和 receipt | Credential、bearer token、provider secret 和 host-local Skill 安装状态 |

归档包含带版本的 manifest（`format_version`、producer version、scope、数量、排除项和总 checksum）以及 NDJSON record。
它是权威的 round-trip 格式。CSV 可以单独用于有边界的分析，但无法保留不可变 revision、lineage 或 evidence reference，
不得用于恢复。

## 恢复和冲突处理

恢复是幂等的。重复回放同一归档时，内容相同的 record 会计入 `already_present`。已有不可变 identity 对应不同 payload
时，恢复绝不会覆盖它：命令以退出码 `3` 结束，并回滚本次写 transaction。解决目标数据冲突，或选择一个干净的目标后，
再次运行同一个恢复命令。

如果命令在完成前中断，不能宣称恢复已成功。权威 record 会在 transaction 中写入；失败的写入不会留下看似成功的部分
恢复。先确认上一次命令已经停止，再对同一目标重新运行同一个归档。

目标数据库会按 `bundle_id` 保存 durable receipt。`authoritative_restored` 表示逻辑数据已经提交但搜索尚未重建完成；
`ready` 表示 projection rebuild 已完成。若重建失败，重新恢复同一 bundle 会跳过相同记录，只有验证完成后才把 receipt
推进为 `ready`。

## 从 SQLite 迁移到 OceanBase

分别创建环境文件，数据库凭据不会写入 bundle：

```bash
powercontext archive export --env-file ./sqlite.env \
  --scope-id project:payments --output ./payments.pcb
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --dry-run
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --yes
```

环境文件使用 Server 配置文档中的 `POWERCONTEXT_SERVER_DATABASE_KIND` 和
`POWERCONTEXT_SERVER_DATABASE_URL`。写入前，dry-run 应报告不存在不支持的 family，且冲突数为零。

## 格式兼容窗口

格式版本 `1` reader 接受任意 PowerContext producer version 生成的格式版本 `1` bundle。producer version 仅用于诊断，
不能替代 archive schema version。writer 只生成版本 `1`，不支持降级到格式 `0`。遇到未知 archive 或 record schema
version 时，reader 会在目标写入前拒绝。跨 PowerContext 大版本升级时，应保留旧二进制，直到恢复演练通过。

## 部署原生备份

逻辑导出不能替代 crash-consistent 部署备份：

- SQLite：复制数据库前停止所有 PowerContext writer，或使用 SQLite online backup API，例如
  `sqlite3 powercontext.db ".backup './backups/powercontext.db'"`。随后执行
  `sqlite3 ./backups/powercontext.db "PRAGMA integrity_check"`，按源数据库等级保护备份，并测试重新打开。
- OceanBase：根据实际部署版本启用 tenant data backup 和 log archiving，独立保管备份目标与加密材料，并在隔离 tenant
  进行恢复演练。point-in-time recovery 使用原生备份；逻辑跨后端迁移使用 portable bundle。

## 当前边界

导出会把数据库 row 流式写到临时 NDJSON 文件。验证只在临时磁盘 index 中保存 record identity，恢复则按依赖层流式
回放，因此归档的聚合 payload 不会常驻进程内存；Scope metadata 由 manifest scope list 约束。命令不是远程 Server
API，也不会把部署配置或数据库凭据写入归档。
