---
title: Codex 快速入门
description: 安装 PowerContext，并在多个 Codex 会话之间传递项目上下文。
---

# Codex 快速入门

本教程不要求你自行克隆仓库。完成后，你会在同一个项目的第二个 Codex 会话中读取、修订和停用第一个会话保存的
Memory。

## 开始之前

你需要 macOS 或 Linux、`uv`、Codex CLI，以及 PowerContext Git 地址的读取权限。请先确认本机已有的 Git
凭据能够访问该地址。

## 1. 安装工具和插件

在任意目录执行：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
```

第一条命令安装隔离的应用环境；第二条命令安装 Codex 插件，并准备 PowerContext 用户数据目录。详细的安装、升级和
数据位置说明见[安装和运行](../how-to/install-and-run.md)。

## 2. 启动 Server

在单独的终端中保持这个进程运行：

```bash
powercontext server run
```

服务默认监听 `http://127.0.0.1:8000`，并在首次启动时创建持久化 SQLite 数据库。保持此终端运行。

在另一个终端检查整个安装：

```bash
powercontext doctor
powercontext doctor codex
```

两个命令的每项检查都应显示 `ok`。第一个命令检查安装包和 Server；第二个命令只检查可选的 Codex 集成。出现
`degraded` 或 `failed` 时，请先阅读[排查问题](../how-to/troubleshoot.md)。

## 3. 保存项目 Memory

在一个项目目录中启动新的 Codex 会话。如果 Codex 要求信任 PowerContext Hook，请打开 `/hooks` 并批准。

告诉 Codex：

> 使用 PowerContext 分别保存三条项目 Memory：成果是“解析器已支持 TOML”；当前状态是“Python 3.11 测试
> 通过”；下一步是“增加错误输入用例”。

Codex 应使用 project-context skill，并在 Memory 写入成功后确认。不要把密钥写入 Memory。

## 4. 在下一次会话中读取并更新

结束当前会话，在同一项目中启动第二个会话，然后告诉 Codex：

> 列出这个项目当前有效的 PowerContext Memory。把下一步修订为“记录错误输入的报错”，并停用旧的当前状态。

第二个会话应先列出三个有效条目，再进行修改。修订和停用会保留历史，不会覆盖或删除旧版本。

启动第三个会话，然后告诉 Codex：

> 列出这个项目当前有效的 PowerContext Memory。

修订后的下一步应处于有效状态；被停用的当前状态和被替代的旧下一步不应出现在有效条目中。这说明项目 scope 在多个
Codex 会话之间保持一致。

这里验证的是长期 Memory。Memory 与临时 Handoff 的区别见[理解 Memory 和 Handoff](../explanation/memory-and-handoff.md)；
若要把当前工作以完整交接包传给另一个任务、会话或模型，请使用[在 Codex 中交接工作](../how-to/handoff-with-codex.md)。

## 5. 检查降级行为

按 `Ctrl-C` 停止 Server，再让 Codex 执行一项普通任务。PowerContext 可以报告 Memory 不可用，但不能阻塞
任务。此时 `powercontext doctor` 会报告 liveness 失败、跳过 readiness，同时仍能报告已安装的包；
`powercontext doctor codex` 会继续独立报告 Codex 集成状态。
如果只有已配置的推理服务失败，Server 会继续接收流量并把 readiness 报告为 `degraded`；`doctor` 会在不读取
provider 凭据的前提下显示该非 `ok` 状态。
