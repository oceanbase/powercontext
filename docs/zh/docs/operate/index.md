---
title: 部署与运维
description: 持续运行 Server，查找部署、监控与恢复步骤。
---

# 部署与运维

按当前运维任务选择入口：

| 任务 | 指南 |
| --- | --- |
| 持续运行个人 Server、部署容器或启用认证 | [部署 Server](deploy-server.md) |
| 升级或切换后台监督模式 | [迁移 Artifact 处理状态](artifact-processing-migration.md) |
| 查看日志、指标和 Trace | [可观测性](observability.md) |
| 将 Trace 发送到分析服务 | [Phoenix](trace-with-phoenix.md)、[Langfuse](trace-with-langfuse.md) |
| 诊断失败并恢复服务或数据 | [诊断与恢复](troubleshoot.md) |
| 查找默认值和环境变量 | [配置](configuration.md) |

原生个人服务在 Linux 使用 systemd，在 macOS 使用 LaunchAgent，在 Windows 使用 Task Scheduler。
Windows 支持为 `experimental`，安装时需遵循相应服务和环境文件要求。
