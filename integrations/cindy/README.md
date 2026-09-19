# Cindy 集成

这是供本地调试的实验性 Ghost 插件，兼容目标为 Cindy 0.1.76 或更新版本。兼容依据是该正式版本源码中的
Manifest v3、Node JSON-RPC worker、`node.secretBindings` 和 `sessionContext`；Electron 安装、保险库 UI
及各 Harness 的实际可用性需要在目标桌面端验证。

插件源码位于 `plugins/powercontext/`。`main.js` 负责 Cindy 工具协议，`node/worker.cjs` 负责有界 HTTP 请求、
Scope 绑定和响应校验；不依赖 npm 安装步骤或外部 Node 包。Cindy 使用自带的 Worker 运行时，本机无需另装 Node。
开发检查脚本使用 Node 22+；集成冒烟检查还需要仓库的 `uv` 开发环境。

## 打包和安装

从仓库根目录执行：

```bash
bash integrations/cindy/scripts/package.sh
```

产物为 `dist/powercontext-cindy-0.1.0.cindy`。脚本需要 `zip`，仅打包列出的插件源码和 LICENSE，
不会递归包含本地配置、测试或凭证。根目录 `MANUAL.md` 由 `read_manual` 工具按需读取；它不是
`manual.items` 目录，不会被错误注册为仅允许 Markdown 文件的手册树。

在 Cindy 中导入 `.cindy` 文件，确认插件启用，打开插件设置。默认 Server URL 为
`http://127.0.0.1:8000`。生产使用的 Server 应独立配置并启动，例如：

```bash
uv run powercontext server run
```

在 Cindy 中依次调用 `status`、`list_scopes`、`bind_scope`、`remember_memory` 和 `search_memory`。
没有合适的 Scope 时先用 `create_scope` 创建。完整调用示例见 [插件手册](plugins/powercontext/MANUAL.md)。

## 验证

从仓库根目录执行：

```bash
node --test integrations/cindy/tests/*.test.cjs
uv sync --locked
node integrations/cindy/scripts/smoke.mjs
```

第一条检查客户端行为及模拟的 Cindy 消息桥。冒烟脚本创建临时目录、全新 SQLite 数据库和随机端口的
Bearer 鉴权 Server，将真实 `main.js` 经模拟 Host 桥接至独立 Node worker 进程，再访问真实 HTTP API。
它验证鉴权、Scope 创建/绑定/隔离、Memory 保存/全文检索、PreparedContext、Source 幂等采集与冲突，
结束时关闭自己启动的进程并清理临时数据库，不使用已有 PowerContext 数据。

已有 Cindy 官方插件仓 checkout 时，可额外检查 Manifest：

```bash
node /path/to/cindy-official-plugins/scripts/validate-plugin-manifest.mjs \
  integrations/cindy/plugins/powercontext
```

此命令只检查清单形状，不证明桌面端安装成功。实机还需验证：导入产物、插件工具发现、设置保存、
保险库凭证注入、通知去重，以及分别在 Cindy 的 Claude Code、Codex 和 Pi 中执行手册示例。

## 行为边界

- 提供显式工具，没有自动捕获、消息改写或自动回合注入。
- Scope 默认为会话绑定优先、目录绑定其次；设置页显式 Scope 覆盖两者。没有绑定时失败，不使用全局默认 Scope。
- URL 与凭证绑定；只允许 HTTPS 或环回 HTTP，非环回 HTTP 需要地址对应的显式同意；不跟随重定向。
- 凭证只进入对应的 Node RPC 请求，不进入普通 KV、Agent 参数、结果或日志。切换匿名模式不会自动删除保险库凭证。
- 单次工具操作的 HTTP 总期限为 10 秒，响应上限为 1 MiB。PreparedContext 另按调用方字节预算完整校验。
- 采集和保存均为用户意图驱动；未增加自动重试或额外的插件确认弹窗。调用仍受 Cindy 和 Server 各自的授权控制。
- 设置页和手册使用英文；Manifest 发现信息和工具说明提供英文、简体中文。
- 此调试集成尚未加入 PowerContext `setup`、安装目录或已验证能力清单，也不包含 Handoff、Dream 或 Candidate 审核工具。

接口依据：[Cindy 0.1.76 作者手册](https://github.com/makecindy/cindy/blob/v0.1.76/apps/desktop/src/main/cindy-brain/forge.ts)、
[官方插件编写指南](https://github.com/makecindy/cindy-official-plugins/blob/5a7d99e7b2383cabb4ebf3ddbbe5aef490ff8001/docs/plugin-authoring.zh-CN.md)。
