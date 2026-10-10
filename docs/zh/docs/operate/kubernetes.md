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

安装、协调停机升级和集群验收步骤见 Chart 文档。Chart 不支持 SQLite 或内嵌 seekdb；这些后端应使用
单进程评估部署。复用已有数据库前请先完成[处理状态迁移](artifact-processing-migration.md)。

集群验收时，将 Chart 中的 `values-acceptance.yaml` 与本地镜像和 Secret 引用配置一起使用。
该配置启用两个 API 副本、一个初始 background 副本及本地 `test` 生成模型。验收脚本会拒绝少于
两个 API 副本的配置，并在创建测试数据前及替换 API Pod 后确认至少两个 API Pod 处于 Ready 状态。
仅在隔离测试 namespace 和可丢弃的外部 OceanBase 数据库上运行；脚本会写入数据、删除 Pod 并调整
background 副本数。配置测试通过不代表真实集群验收通过。
