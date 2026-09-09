---
status: evaluation
title: 评测集成
description: Bub 的评测用途与能力边界。
---

# 评测集成

`evaluation`: Bub 仅用于评测。 仓库将其归为 `evaluation_harness`，不计入 8 个 Agent Host 或 3 个 Python 框架适配器。
当前能力状态为 `master_only`。

Bub 插件通过 Python Client 连接 Server，提供 Memory 搜索、写入和上下文准备；
模型调用前可准备上下文。事件采集默认关闭，启用后可将已完成的事件作为 Sources，并定期 flush。
它没有 Handoff 能力声明。

安装和采集开关见 [Bub 集成说明](https://github.com/oceanbase/powercontext/blob/master/integrations/bub/README.md)。
评测方法、结果与限制见[基准测试](/zh/benchmarks/)。评测结果不代表所有 Agent 集成具有相同的能力或稳定性。
