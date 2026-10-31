---
title: "桌面范围统计"
description: "精确范围的原生统计、隔离与兼容资格。"
---

# 桌面范围统计

## 存量与用量视图

统计需要显式选择 `sqlite-stats-27404931-v1` 配置；独立验证的
`sqlite-atomic-dd62723d-v1` 配置仅覆盖 Memory 操作。

“观察 → 当前 Scope 库存与用量”包含统计视图。启用已验证兼容的连接、选择精确
范围及 UTC 周期后，点击“加载统计”。加载是显式操作；打开视图不会自动恢复
连接或发起统计请求。共享布局在 Observation 库存面板挂载 `Statistics`
组件，传入当前状态、语言、上下文切换保护及状态刷新回调。

视图显示 Server 返回的 `as_of`、精确范围覆盖及含首尾的 UTC 日期。存量是当前
快照。模型请求、输入／输出 token、用途分组和每日数据保留返回的零值及空值；
空 token 显示“未知”。页面不补齐缺失行，也不从已记录调用推断提供商完整上报。
召回覆盖展示准备、就绪及可比较次数、估算器及版本、基线、召回 token 和有符号的
减少量。这些是估算，不是账单节省或答案质量指标。

每个图表都有带标题及行列头的等价表格。图表和表格的滚动区域可通过键盘聚焦，
适用于窄布局；文案支持中英文。切换连接、范围或周期会清除旧视图，并忽略迟到的
成功或失败响应。统计失败不禁用独立的 Memory 操作。

## 原生边界

桌面通过原生 `get_stats` 命令调用 `POST /v1/stats`。渲染层只提供当前
generation 和 `today`、`7d`、`30d` 周期；原生层取得当前 Scope，构造精确、
单范围的 selection。渲染层不能传入地址、范围列表或 all/subtree 模式。

连接必须具有匹配合同且已验证该操作的兼容资格。每次读取沿用连接边界的身份
复核策略，保留 Server 的授权失败结果，并遵守现有传输超时与 1 MiB 响应上限。
权限拒绝后不会扩大查询范围。响应的 selection、范围 ID 和周期必须与请求一致。
按照公共合同，合计及各范围的召回估算器均可为空。

统计有独立的取消通道。新的统计读取、`cancel_stats_reads` 或 generation
变化都会取消待完成统计；Memory 读取的取消不会影响统计。已提交的 Memory
写入继续绑定原目标。

操作目录和可达 wire 类型通过 `pnpm --dir desktop generate` 生成；IPC 声明通过
Rust `export_ipc` example 生成。原生的精确选择投影复用具体生成字段类型，同时
保留可空估算器并禁止扩大 selection。不改变 Server API 或历史资格记录。

已验证的 Server、合同、wheel 标识和实际验证边界见
[资格记录](https://github.com/oceanbase/powercontext/blob/master/desktop/VALIDATION.md#current-scope-statistics-qualification)。
模拟测试验证本地处理；构建 wheel 的独立夹具另行验证实际 SQLite 协议行为。
记录另行列明已执行的 macOS ARM64 打包调试应用流程，以及仍待验证的 Windows
安装界面、Intel、输入法和屏幕阅读器 gate。Windows 安装流程将页面表格与独立的
真实 Server 响应进行比较。
