---
status: community
title: MiniMax Code
description: 安装 MiniMax 原生插件，区分包发现、Server 访问与宿主实际连接。
---

# MiniMax Code

`community` · 实验性

PowerContext 安装 MiniMax 原生插件，包含 `powercontext-project-context` Skill 和 streamable HTTP MCP 连接。MCP 会话由 MiniMax 管理。插件不负责启动 Server，也不会自动采集全部对话。

## 前提与版本

需要 Python 3.11+、PowerContext CLI 和运行中的 PowerContext Server。CLI、Server 和插件应来自同一仓库 revision。使用发布版本时，通过 `--ref` 指定与 Server 匹配的仓库 tag；插件 manifest 版本独立于 Python 包版本。远程 ref 沿用现有分支和标签约定；安装指定 commit 时，先在本地检出，再使用 `--source`。

原生测试覆盖官方 npm CLI `@minimax-ai/code` 的 0.6.9 和 0.7.2，环境为 Linux x86_64、Node.js 22.23.3。这两个版本的仅插件 MCP 路径均尚未通过可靠性验收：宿主可能报告插件已连接，但模型无法获取工具，包括通过 `tool_search` 发现工具。升级至 0.7.2，或 setup、doctor 通过，都不能排除这一限制。桌面端发现和其他平台需要单独验收。具体命令、诊断产物和证据边界见[验收记录](https://github.com/oceanbase/powercontext/blob/master/integrations/minimax/acceptance.md)。

已验证的官方 0.7.2 安装流程同时保留 setup 创建的已启用私有 `powercontext` MCP 条目和完整插件：通过私有连接提供 MCP 工具，并保留插件 Skill。在官方 npm 版本中删除或禁用私有条目后，会依赖尚未通过验收的仅插件路径。setup 会保留既有禁用偏好；如果选择使用这条已验证路径，请在 `mcp.json` 中显式启用该私有条目，再运行 `powercontext config agent minimax` 并重启 MiniMax。

## 安装与选择目标

```bash
powercontext setup minimax

# 仓库根目录或原生插件目录
powercontext setup minimax --source /path/to/powercontext

# 使用包含插件、且与 Server 匹配的分支或标签
powercontext setup minimax --source oceanbase/powercontext --ref <matching-tag>

# 显式指定 MiniMax 数据根目录和可执行启动程序
powercontext setup minimax --source /path/to/powercontext \
  --minimax-data-dir "/path/to/MiniMax data" \
  --minimax-command /path/to/mcode
```

插件安装到 `<数据根目录>/plugins/powercontext`，包含隐藏文件、Skill 引用、图标和许可证。目录优先级为 `--minimax-data-dir`、非空 `MINIMAX_DATA_DIR`、非空 `MAVIS_DATA_DIR`、已保存的安装目标。均未提供时，安装器通过所选 CLI 的 `plugin marketplace list --json` 查询本地目录，遵循宿主的 profile/build 选择。缺少 CLI 时默认使用 `~/.minimax`；此时若设置了 `MAVIS_PROFILE`，必须显式提供数据根目录。旧版或自定义 build 的 marketplace 输出无法识别时，也需要 `--minimax-data-dir`。

启动程序依次来自 `--minimax-command`、相同数据根目录保存的启动程序、PATH 中的 `mcode`。显式或已保存的启动程序无效时，在安装前失败。启动 MiniMax 时应使用所选数据目录。原生发现检查在该安装的临时快照中执行，并带入本地插件的启用状态。setup 保存目标，供后续 `doctor minimax` 和 `config agent minimax` 使用；目录环境变量仍优先于保存的目标。

交互式 `powercontext setup` 包含 MiniMax，多宿主安装复用相同行为：

```bash
powercontext setup codex minimax --source /path/to/powercontext --json
powercontext doctor minimax --json
```

## 使用仅插件路径的源码修复

[MiniMax 就绪性修复](https://github.com/hidb4ai/minimax-code/commit/0ea69bd04c90cc151cae2eff9d5b7b4465d3c7dc)会在捕获本轮工具前，等待插件 MCP 发现及工具清单发布完成。该 Linux 源码构建已在原失败环境通过仅插件路径调用，包括工具清单延迟 6 秒和 11 秒的场景。这是基于 MiniMax 公开 0.7.2 同步分支的固定社区源码构建；官方 npm 包尚未包含此补丁。上游接纳进度见[问题单 #570](https://github.com/MiniMax-AI/minimax-code/issues/570)。

使用 Node.js 22.23.3 和仓库固定的 pnpm 版本构建启动器：

```bash
git clone --branch fix/plugin-mcp-turn-readiness-0.7.2 https://github.com/hidb4ai/minimax-code.git
cd minimax-code
git checkout 0ea69bd04c90cc151cae2eff9d5b7b4465d3c7dc
pnpm install --frozen-lockfile
pnpm build
powercontext setup minimax --source /path/to/powercontext \
  --minimax-data-dir "/path/to/MiniMax data" \
  --minimax-command "$PWD/dist/cli.js"
```

启动同一个 `dist/cli.js`，并将 `MINIMAX_DATA_DIR` 设为所选数据目录。setup 会保存该启动器，供后续配置和诊断使用。选择禁用私有条目以运行仅插件路径前，应保持插件启用，并同步好 URL 和凭据。切回未修复的启动器后，仍受官方版本的验收限制。源码 revision 和验证范围见[验收记录](https://github.com/oceanbase/powercontext/blob/master/integrations/minimax/acceptance.md)。

## 配置受保护或远程 Server

setup 默认连接本地 Server，或保留原生 MCP 配置中的现有 endpoint。切换连接时使用：

```bash
powercontext setup minimax --source /path/to/powercontext \
  --server-url https://memory.example
```

通过进程环境或 setup 的 `--env-file` 提供 `POWERCONTEXT_MINIMAX_AUTHORIZATION`，也可使用共享的 `POWERCONTEXT_CLIENT_API_TOKEN`。安装器在 `<数据根目录>/mcp.json` 中保存字面量 Bearer header，并把已启用私有连接的设置同步到安装后的 `plugins/powercontext/powercontext.mcp.json`。源码分发包和安装记录不包含凭据。输入凭据时避免泄露到 shell 历史。在 Linux/macOS 上，新建或更新的这两个连接文件仅向所有者开放；未改变的禁用私有配置保留已有权限。Windows 上应使用适当的 ACL 保护这两个安装后的连接文件。

setup 只合并 PowerContext server 条目，保留其他 MCP server、未知字段、自定义 header 和已有禁用偏好。已有凭据会被保留。私有条目缺失或禁用时，升级也会保留插件当前的连接设置。私有条目已禁用时，只有通过 `--server-url` 或受支持的 setup URL 环境变量、环境文件显式指定端点，才会修改其 URL。条目无变化时，整个私有配置文件逐字节保留。只提供新凭据时，在原 URL 上轮换该条目的凭据，不自动启用它。setup/config 结果通过 `private_mcp_state` 标明私有条目状态；禁用条目需要用户启用后才生效，doctor 继续检查实际生效的插件内连接。把带认证条目迁移到另一 endpoint 时，必须显式提供替换凭据，避免自动转发旧凭据。私有配置无法解析时，在插件安装前失败。

MiniMax 独立暴露私有连接和插件内连接，工具名可能分别包含 `powercontext` 和 `powercontext_2`。私有条目启用时，setup/config 同步两套连接的 URL 和 header，让工具统一访问所选 Server。私有条目禁用且未显式指定端点时，保留插件内连接；仅轮换禁用条目的凭据不会把该凭据复制到活动插件连接。显式指定端点时，也会配置活动插件连接。doctor 对不一致的已启用连接报告失败，需要重新执行 setup 或 `config agent minimax` 进行同步。配置应使用字面量 URL 和 header；受支持的私有配置不展开 `${TOKEN}` 占位符。setup 的远程 HTTP 同意只控制 PowerContext 自身的配置和诊断请求，不会绕过 MiniMax 的原生 HTTP/TLS 限制，建议使用 HTTPS。doctor 从原生配置读取 endpoint 和 header，不把全局 PowerContext token 当成宿主已有凭据。

## 重复安装、升级与恢复

使用目标 source/ref 重复执行 setup。安装器在 PowerContext 数据根目录保存受管理文件的摘要，并按 MiniMax 数据目录区分安装目标。升级更新这些文件、删除未被修改的旧版受管理文件，并保留用户新增文件和其他插件。受管理文件被修改，或新版路径与用户文件、目录、作为父路径的文件冲突时，在替换前报告冲突。发布前检查暂存包；用户自建文件不会被加入受管清单。

与待安装包完全一致的手工安装可被接管，额外用户文件会保留。内容不同的未管理安装会被拒绝。请先备份到 MiniMax 的 `plugins` 目录之外，保留私有连接配置，并处理自定义修改后重试。不要在另一个直接子目录中保留重复的 PowerContext 插件。

包发布失败时恢复原包和归属记录；连接发布失败时恢复原私有配置和共享配置。若后续连接配置失败，setup 会单独说明插件安装是否已完成。中断后若出现恢复备份，应按提示恢复后再重试。

## 理解验证结果

- `package`：原生 manifest 及其本地引用资源完整。
- `native`：MiniMax 加载本地 `powercontext` 插件及其启用状态的临时快照，检查版本匹配，并具有至少所需的 Skill/MCP 数量。数量不证明具体资源名称或 MCP 会话已经建立。命令成功但列表遗漏插件时，该检查仍失败。
- `server_liveness` / `server_readiness`：所选 Server 可访问并报告就绪状态。
- `server_access`：CLI 使用生效的原生 header 访问 Server info，不代表 MiniMax 已连接或调用工具。

缺少 CLI 时，复制的文件会报告为已安装，原生验证为降级，整体退出码非零。桌面安装需要安装受支持 CLI，或通过 `--minimax-command` 提供现有可执行启动程序；仅复制文件不等于桌面端验收。doctor 不安装包、不改写配置、不启用插件，也不初始化真实数据目录。原生 CLI 只在私有临时快照内维护运行记录，完成后删除快照。验证需要临时空间保存插件及用于读取启用状态的原生数据库/WAL 副本；不会把无关会话和 provider 状态导入诊断运行时。不支持的原生状态格式会明确报错。首次 setup 之前，使用原生启动程序进行诊断时应提供 `--minimax-data-dir` 或目录环境变量；doctor 不会通过启动真实家目录中的宿主来探测目标。

在 `/plugins` 中按 `Ctrl+R` 刷新 Local 列表，或使用同一数据目录重启 MiniMax。升级保留用户禁用状态。需要启用时，为所选启动程序设置目标 `MINIMAX_DATA_DIR`，执行 `plugin enable powercontext@local --json`，然后重新运行 doctor。修改已经使用过的连接后，应重启 MiniMax。宿主中的实际 MCP 工具结果需要与 CLI 检查分开验证。
