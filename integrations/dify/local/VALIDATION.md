# 本地部署验收状态

验证日期：2026-09-10。Dify 基线 `06f58809`，分支 `feat/agent-v2-mem`；PC 基线
`4043710abc5410f17c50d7e2a11d889732fbccd9`，分支 `feat/dify-plugins`，均包含当前未提交的集成实现。
daemon：`langgenius/dify-plugin-daemon:0.6.10-local`，镜像 ID
`sha256:34412a22d1e1d1a6c73727fef88cffbcdd9be7a21bf9ad3076b4bf5c1b88fd9b`；SDK：0.10.2。
PC 使用独立 SQLite；Dify 使用独立 PostgreSQL 17 和 Redis 7.4.2。

| 场景 | 结果 |
| --- | --- |
| Dify 数据库完整迁移、API 和 worker 启动 | 通过 |
| Web 登录页实际编译与 HTTP 请求 | 200 |
| Agent runtime 控制 API、sandbox 服务启动 | 通过 |
| PC 真实 HTTP readiness | 200，ready |
| 两个插件通过真实 daemon 调试注册 | 通过 |
| Dify 凭据验证经过插件到 PC Server | 通过 |
| Console 工作流经工具插件写记忆、召回 | 通过 |
| 不同业务作用域隔离、缺失绑定不回退 | 通过 |
| 工作流采集 Source，HTTP 读回验证脱敏 | 通过 |
| 本地发布的 Service API，两个真实 EndUser 跨请求隔离 | 通过 |
| V2 配置保存与重新加载，凭据引用保留、密钥不进入配置 | 通过 |
| 真实模型驱动的传统 Agent 与 V2 自动召回、回写 | 待配置模型 |
| 采集 → 模型提取 → 下一轮召回 | 待配置 PC 推理模型 |
| 长上下文压缩、模型流取消、V2 暂停与恢复 | 待模型场景验收 |
| 正式 `.difypkg` 安装与市场发布 | 未执行 |

以上已通过项目均来自实际本地服务调用。自动化单元测试中的模拟 daemon/模型，不计入本表的部署验收结果。
实际模型调用完成之前，这份结果不能作为完整 Agent 联调或发布验收通过的依据。

## 如何得到部署结果

测试调用链为：本地 Console/Service API → Dify 工作流 → plugin daemon → SDK 插件进程 → PC HTTP Server → SQLite。
两个插件通过 SDK 的 remote debugging 连接实际 daemon。模型供应商尚未配置；这组工作流只有 Start、Tool、End 节点。

- `live_workflow.py` 创建独立业务 Scope 和绑定，通过 Console API 保存并执行工作流，读取 SSE 的
  `workflow_finished` 与工具 JSON 输出。向 `first` 写入随机标记后，同一 Scope 返回 `ready` 且包含标记；
  `second` 返回 `empty`；`unbound` 返回工具级 `error/not_found`。采集带测试 `api_key` 的事件后，从 PC HTTP API
  读回 Source，确认原值不存在且包含 `[REDACTED]`。工具返回结构化错误时，Dify 节点本身可以仍是 `succeeded`，
  因此脚本同时检查工具结果。
- `live_users.py` 在本地发布测试工作流，通过 `/v1/workflows/run` 分别使用 Alice、Bob 两个调用者。
  从运行详情取得各自实际 `EndUser.id` 并绑定 Scope。Alice 写入后能跨请求召回，Bob 返回空结果。
  这验证了身份路由与 Scope 隔离；不等于使用共享 PC 凭据建立了独立授权边界。
- `live_native_config.py` 通过真实 Agent composer API 保存并重新读取外部记忆配置，检查凭据引用保留，
  响应中不含 PC token。该测试不执行模型，也不能证明 V2 自动回写已在部署环境完成。

部署证据在本地忽略目录 `.powercontext-e2e/dify/` 中：`workflow-events.json`、`user-events.json`、
`native-config.json`，以及 `live-workflow.log`、`live-users.log`。原始记录包含本地实例标识，不应随插件发布。
在隔离环境复跑方法见 [README](README.md)。这些脚本会修改专用验收应用的工作流或 Agent 配置。

## 自动化测试与浏览器修复验证

2026-09-10 复核结果：

- PC 根目录执行 `make dify-test`：Ruff、ty 通过，插件测试 11 项通过，HTTP/SQLite 联动测试 1 项通过。
  后者使用真实 PC ASGI 应用和 SQLite，但提取器是确定性的测试实现；传统 Agent 测试使用模拟模型响应。
- Dify `web/` 执行 `pnpm exec vp test run --project unit service/console`：3 个文件、57 项测试通过。
  新增用例验证默认取消原因、自定义取消原因不会产生日志误报，网络故障仍拒绝请求并记录错误。
- Dify 根目录执行 `pnpm exec vp check web/service/console/browser.ts web/service/console/client.spec.ts`：通过。

浏览器中的 `Console AbortError: signal is aborted without reason` 来自 TanStack Query 取消请求后，
`web/service/console/browser.ts` 无条件执行 `console.error`。修复只跳过与该请求已取消 signal 的 `reason`
一致的错误日志，仍将拒绝交给调用者。修复前取消回归用例失败；修复后通过。实际浏览器刷新并在测试工作流和
集成页面之间切换后，未再观察到该 AbortError。浏览器仍观察到 Marketplace 请求的独立 `Failed to fetch`，
不属于此次取消误报修复的验收范围。
