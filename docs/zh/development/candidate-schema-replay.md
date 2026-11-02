# Candidate 迁移重放原型

开发命令在真实 SQLite、嵌入式 seekDB 或 OceanBase 上重放冻结的 `s1300` → `s1400` Candidate 转换，从数据库反射生成全库结构和有序变化，再与现有注册内容比较。它不修改生产迁移资源、checksum 或注册格式。

在开发仓库运行：

```sh
uv run --locked python scripts/replay_candidate_migration.py sqlite --output /tmp/candidate-sqlite
POWERCONTEXT_TEST_MIGRATION_SEEKDB=1 uv run --locked --extra seekdb python scripts/replay_candidate_migration.py seekdb --output /tmp/candidate-seekdb
```

OceanBase 复用现有原生测试夹具：将 `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL` 指向专用的 `pc_migration_probe_*` 管理库，再选择 `oceanbase`。夹具创建唯一子库，只删除自己成功创建的库。不能指向生产环境。输出目录必须尚不存在；任何场景失败或跳过都会使命令失败，缺少原生服务不能算通过。

## 生成证据与比较

每个场景记录后端及引擎／绑定版本、冻结资源 hash、bundle 与 revision checksum、源版本、操作顺序、完整反射结构及其摘要，并从相邻真实结构生成变化。数据复制操作即使没有结构变化，也保留独立顺序位置。生成的变化必须能够重建全库结构，包括重命名引起的其他表外键变化。

原生后端记录 `s1400` 冻结 SQL 中每个 Candidate 操作，包括动态外键操作；使用现有后端规范化规则逐点比较真实结构与完整注册检查点。生成结构不依赖预期检查点。SQLite 执行冻结的 Alembic 实现，记录 Candidate SQL 边界，并在 `s1400` 完成时比较冻结目标结构。SQLite 中间结构是事务内观察结果，不是可以独立恢复的持久化节点。

所有场景从冻结的完整 Server 1.3.0 基线开始，写入两个历史 Candidate 版本、当前 head 和 Profile 引用，验证原字段、保留副本、两处跨表外键变化、完整就绪、原维护证据与重复 apply 不写入。这些是基于已捕获基线构建的合成数据，不代替使用真实历史 wheel 的安装包升级验收。

## 中断语义

三个场景分别为完整重放、首次历史保留复制后中断、版本表重命名后中断。中断发生在真实数据库操作已经返回之后；恢复前由原迁移框架关闭连接。

- SQLite 必须把结构和 Candidate 数据回滚到基线，再沿用原备份和维护窗口完成升级。
- seekDB／OceanBase 必须保留已提交的 DDL；未提交的保留复制记录应回滚并可安全重放。恢复复用现有 runner 和原维护证据；只有这种受控连接中断可以明确确认 OceanBase 前一操作已结束。
- seekDB 夹具在单个场景的维护连接之间持有真实引擎租约。它验证原生 SQL 和连接中断恢复，不验证引擎崩溃或重启锁恢复；后两者仍需独立验收。

`Candidate schema replay` 工作流在三种真实后端分别运行全部三个场景并保存生成证据。本地 SQLite 通过不能代表原生后端通过。

## 后续新增迁移

该原型是生产 bundle 外的等价性实验。新增迁移宜优先从真实数据库重放生成待审注册，明确数据转换和恢复边界，减少手工复制中间表定义。接入生产前仍需审查产物格式、完整源版本／后端来源证据，保留历史资源不变，并完成等价的安装包升级、历史数据、重复 apply、备份和中断恢复验收。本原型不生成可安装迁移，也不宣称已经简化现有冻结的 `s1400` 资源。
