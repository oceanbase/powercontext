---
title: 在 Kubernetes 上部署
description: 使用 Helm 和外部 OceanBase 部署 API 与后台进程。
---

# 在 Kubernetes 上部署

[Helm Chart](https://github.com/oceanbase/powercontext/tree/master/deploy/helm/powercontext) 封装当前的 `api` / `background`
角色，连接外部 OceanBase，提供已有 Secret 引用、API 健康探针、ClusterIP Service、可选 Ingress 和资源配置。

部署前需要指定固定版本的应用镜像并准备 Secret。多个 background 副本通过数据库租约选出一个活跃
supervisor，其余作为候选实例；增加副本不会增加活跃 supervisor 的数量。当前 background runner 没有
HTTP 健康端点，Pod Ready 不代表后台处理正常，需结合租约续期、日志和持久化处理进度判断。
`background.maxWorkers` 通过兼容别名设置 Topic Memory 配额，不是进程总数上限；
`maxWorkers=2` 且七个内置 family 全部启用时，配额合计为 11；验收配置的 `test` 模型
启用四个 family，配额合计为 8。未配置生成模型时默认不启用任何 family；实际并发取决于启用能力和调度。
应为 supervisor 及其子进程共同规划资源。

可通过 `commonLabels`、`podAnnotations`、`podSecurityContext` 和 `securityContext`
适配集群策略。由准入控制分配 UID/GID 的集群，可将 `podSecurityContext` 中的
`runAsUser`、`runAsGroup` 和 `fsGroup` 设为 `null`，并验证准入规则和卷权限。
PDB 和 topology-spread constraints 需在 Chart 外管理。MCP 的 stateless Streamable HTTP
由运行时挂载行为保证，不是 Chart 的环境变量配置。

安装、协调停机升级和集群验收步骤见 Chart 文档。Chart 不支持 SQLite 或内嵌 seekdb；这些后端应使用
单进程评估部署。首次安装前，即使 OceanBase 数据库为空，也必须使用目标镜像执行完整 Server
[数据库迁移](database-migrations.md)，并确认 `server_ready=true` 后再启动 Pod。
OceanBase 维护必须使用持久化证据目录和 `--lock-coordination single-host`；所有命令及重试
都在同一台固定维护主机上运行，保持 hostname 和目录一致。容器命令见 Chart README；
先启动一个 API Pod、保持 background 副本为零，待静态 Bearer 的 Access 绑定初始化完成、
API Ready 后，再从保存的配置恢复目标副本数。
已有数据库还需审阅迁移计划、完成备份并停止所有写入；
[Atomic Memory 迁移](atomic-memory-migration.md)和[处理状态迁移](artifact-processing-migration.md)
说明各自的兼容入口及范围。Chart 不会自动执行迁移。历史集群证据对应旧镜像和 schema；
应使用目标应用镜像单独验收初始化、副本启动及 Pod 替换。

集群验收时，将 Chart 中的 `values-acceptance.yaml` 与本地镜像和 Secret 引用配置一起使用。
该配置启用两个 API 副本、一个初始 background 副本及本地 `test` 生成模型。验收脚本会拒绝少于
两个 API 副本的配置，并在创建测试数据前及替换 API Pod 后确认至少两个 API Pod 处于 Ready 状态。
仅在隔离测试 namespace 和可丢弃的外部 OceanBase 数据库上运行；脚本会写入数据、删除 Pod 并调整
background 副本数，必须显式传入 `--confirm-disposable`。脚本还创建两个子 Scope，
在一个 API Pod 上以 `limit=1` 取得游标，再让其他各 API Pod 续读，检查签名键共享及结果无遗漏、无重复。
配置测试通过不代表真实集群验收通过。
