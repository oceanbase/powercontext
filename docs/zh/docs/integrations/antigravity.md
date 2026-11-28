---
title: Antigravity
description: 安装 PowerContext Antigravity（AGY）原生插件，配置自动召回、Scope 绑定 MCP 工具与诊断检查。
status: community
---

# Antigravity (AGY)

`community`

本集成面向 Antigravity（`agy` CLI 与 Antigravity IDE），在 `~/.gemini/config/plugins/powercontext` 下安装基于系统 `python3` 纯标准库的薄外壳插件，并将共享生命周期逻辑统一委托给生成的 `powercontext_lifecycle` 模块。

## 前置条件

确认本机已安装 PowerContext、Antigravity，且 `PATH` 中有可用的 `python3`：

```bash
powercontext --version
agy --version
python3 --version
```

插件 Hook 仅依赖 Python 标准库（通过 `python3 -S` 无三方库验证），无需独立 `.venv`。确保服务端与插件来自同一份 PowerContext 代码仓库，避免协议版本不一致。

## 安装或更新插件

```bash
set -a
. ~/.config/powercontext/.env
set +a
powercontext setup antigravity --source /path/to/powercontext
```

安装器会将插件复制到 `~/.gemini/config/plugins/powercontext`，写入 `.powercontext-owned` 标记，同步更新 `powercontext.json`（含 `server_url`、`allow_insecure_http`、`prompt_recall`、`capture_prompts`）与 `mcp_config.json`，并在环境存在 `ANTIGRAVITY_LS_ADDRESS` 与 `ANTIGRAVITY_CSRF_TOKEN` 时自动调用 `RefreshMcpServers` 刷新运行中的 MCP 状态。若目标路径是可识别的开发软链接，安装器会安全替换并保留用户自定义 Skill 指令。

## 诊断安装与连通性

```bash
powercontext doctor antigravity
```

该命令分别检查 Antigravity 客户端、插件目录（含 `prompt_recall` 与 `capture_prompts` 生效开关）、标准库 Python 运行时、协议版本、`hooks.json` 注册与 `python -S` 真实冒烟探针、`mcp_config.json` 与凭据同步及全局重复 MCP 冲突、Hook 诊断状态、Server `/health/ready` 以及后台自动抽取调度状态（`/v1/capabilities`）。

## 插件生命周期行为

- **SessionStart（`hooks/hook.py session-start`）**：通过 `run_session_start` 解析并持久化工作区与会话 Scope。启动上下文默认关闭，需时设置 `POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_CONTEXT=true`。
- **PreInvocation（`hooks/hook.py pre-invocation`）**：将 Antigravity 的 `workspacePaths`、`lastUserInput` 与 `transcriptPath` 翻译为标准 `UserPromptEvent`，按 `invocationNum`、transcript step 与未落盘哈希去重同轮模型唤醒后委托给 `run_user_prompt_submit`，并将返回的上下文及诊断通过 `injectSteps` 输出，不额外叠加信任提示语。若需捕获提问自动提炼记忆，请在 Server `.env` 中配置 `POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60`。
- **PreToolUse（`hooks/bind_tools.py`）**：匹配 `call_mcp_tool|mcp_powercontext.*`，保持 `"decision": "ask"` 权限模型并将参数里的 `scope_id` 改写为工作区绑定的 Scope。
- **Gateway 单开关（`prompt_recall`）**：当模型流量接入 Gateway 后，只需执行 `powercontext setup antigravity --no-prompt-recall`、设置 `POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL=false` 或将 `powercontext.json` 中 `"prompt_recall": false` 即可关闭 Hook 侧 prompt 召回，其余绑定与 MCP 工具保持不变。
