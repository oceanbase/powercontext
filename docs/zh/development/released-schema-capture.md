# 已发布 Server 的结构采集

`scripts/capture_release_schema.py` 为注册受支持的源版本采集真实证据。
工具校验官方 PyPI PowerContext 1.4.0 wheel 的 SHA256，在独立环境安装它，
启动该版本 Server，写入临时 Memory 及可选 Topic／Code 样本，停服后使用
该版本的数据库驱动读取结构。工具不会运行当前迁移框架或修改注册资源。

```sh
uv run python scripts/capture_release_schema.py sqlite \
  --configuration full-server-no-vector --output /tmp/release-140-capture
```

输出目录必须不存在。只能使用临时数据库。OceanBase 使用
`.github/actions/setup-upgrade-oceanbase` 创建的本地隔离租户，并要求配置
`PC_UPGRADE_OCEANBASE_URL`。共享测试工具限制可用数据库名，采集程序在启动
Server 前会拒绝非空数据库。

独立的 **Capture released Server schema** 工作流运行三个真实后端和四种
配置。向量配置使用受控的 3 维／4 维嵌入，Code 配置还会索引临时仓库并记录
独立 Code 缓存的摘要。原生后端需要 Linux CI；已有进程管理工具负责清理
包括脱离原会话的嵌入式引擎在内的子进程。

产物保留原始 DDL、数据摘要、引擎及驱动版本、安装包版本、wheel 来源和摘要、
配置元数据、Server 就绪结果与日志。`canonical-schema.json` 保存完整逻辑
结构及摘要。原生 DDL 由当前已审查的解析器离线处理，并记录解析器文件摘要，
不会使用旧版解析器；SQLite 保留排除 SQLite 内部对象后的原始结构行。
当前运行时不会连接旧库。所有步骤成功才写入 `status=captured`，缺失或未完成
的产物不能作为注册证据。

采集结果仅证明已发布版本在相应配置下实际生成的结构，不能替代目标版本的
基线识别、迁移和完整升级验收，也不代表所有历史配置均受支持。需要审查并
注册采集证据后，再执行完整升级矩阵。

离线复现可通过 `--wheel` 和 `--pypi-metadata` 指定缓存 wheel 与保存的官方
PyPI 发布 JSON，两者摘要必须一致。清单会明确标记元数据来自本地文件。
