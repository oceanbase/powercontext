---
title: 集成目录
description: 查看构建原生集成所使用的配置。
---

# 集成目录

分发配置就是集成目录。语言、原生事件、操作、效果和失败策略，直接从构建器使用的输入读取：

```bash
uv run python scripts/build_agent_distributions.py --list
powercontext doctor integrations --json
```

前者报告已声明的绑定，后者报告已安装宿主的状态。当前工具与权限以宿主实际目录为准。
模板、setup 和新增宿主的方法见[插件架构与分发](../../development/plugin-distribution.md)。
