---
title: Codex 快速入门
description: 安装 PowerContext，并在多个 Codex 会话之间传递项目上下文。
---

# Codex 快速入门

本教程不要求你自行克隆仓库。你会完成 PowerContext 安装、连接 Codex，并验证 Memory 能够跨会话保留。

## 开始之前

你需要 macOS 或 Linux、`uv`、Codex CLI，以及 PowerContext Git 地址的读取权限。请先确认本机已有的 Git
凭据能够访问该地址。

## 1. 安装和配置

在任意目录执行：

```bash
uv tool install "powercontext[cli,client,server] @ git+https://github.com/oceanbase/powercontext.git@main"
powercontext setup codex --source oceanbase/powercontext --ref main
```

第一条命令安装隔离的应用环境；第二条命令安装 Codex 插件，并准备 PowerContext 用户数据目录。

## 2. 启动 Server

在单独的终端中保持这个进程运行：

```bash
powercontext server run
```

服务默认监听 `http://127.0.0.1:8000`，首次启动时会创建持久化 SQLite 数据库。

在另一个终端检查整个安装：

```bash
powercontext doctor
```

每项都应显示 `ok`。

## 3. 保存交接信息

在一个项目目录中启动新的 Codex 会话。如果 Codex 要求信任 PowerContext Hook，请打开 `/hooks` 并批准。

告诉 Codex：

> 使用 PowerContext 分别保存三条交接信息：成果是“解析器已支持 TOML”；当前状态是“Python 3.11 测试
> 通过”；下一步是“增加错误输入用例”。

Codex 应使用 project-context skill，并在 Memory 写入成功后确认。不要把密钥写入 Memory。

## 4. 恢复并更新

结束当前会话，在同一项目中启动第二个会话，然后告诉 Codex：

> 恢复这个项目的 PowerContext 交接信息。把下一步修订为“记录错误输入的报错”，并停用旧的当前状态。

第二个会话应先恢复三个条目，再进行修改。修订和停用会保留历史，不会覆盖或删除旧版本。

启动第三个会话，然后告诉 Codex：

> 列出这个项目当前有效的 PowerContext Memory。

修订后的下一步应处于有效状态；被停用的当前状态和被替代的旧下一步不应出现在有效条目中。

## 5. 检查降级行为

按 `Ctrl-C` 停止 Server，再让 Codex 执行一项普通任务。PowerContext 可以报告 Memory 不可用，但不能阻塞
任务。此时 `powercontext doctor` 会因 Server 检查失败而返回非零状态，同时仍能报告已安装的包、插件和
数据库。
