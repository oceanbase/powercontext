---
title: 配置 Server 环境
description: 通过显式环境文件生成、检查、校验并运行 PowerContext。
---

# 配置 Server 环境

当 Server 需要推理、调度、存储或部署设置时，使用显式环境文件。

## 1. 生成文件

```bash
powercontext config init --output .env
```

引导式命令会以 `0600` 权限写入私有文件，不会在部署过程中询问 model 或 provider 凭据。默认文件可以直接启动 Server；
如果需要自动抽取、模型生成或向量检索，请在文件中补充对应的 model、credential、embedding profile ID 和 dimension。

如果 `--force` 会删除已有的 model、embedding、推理调度或 provider 凭据配置，命令会明确提示影响，并要求一次默认选择
“否”的确认。用户确认后，命令会先创建权限为 `0600` 的备份，再替换原文件。

在 macOS 和 Linux 上，引导式命令会以 `0600` 权限写入私有文件。通过环境或 secret manager 提供 provider 凭据，不要把它们写入命令行参数。

Windows 支持为 `experimental`。将文件用于个人服务前，按[部署 Server](../operate/deploy-server.md)限制其 ACL。

## 2. 检查并校验

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

`config show` 会隐藏已识别的凭据。校验接受只包含 Server 设置的最小环境文件；配置 inference model 或依赖 inference
的 Runtime 功能时，还会检查 Runtime 组装，但不会输出机密。

## 3. 使用同一份配置启动

```bash
powercontext server run --env-file .env
```

文件中的值会覆盖同名进程变量。文件中没有的继承 `POWERCONTEXT_SERVER_*` 变量会被忽略，因此校验和启动使用同一份
Server 设置。

Server 会按配置启动对应能力。使用 `powercontext ready` 和 `powercontext capabilities` 查看就绪状态和已启用功能。

全部变量、默认值和优先级规则见[配置](../operate/configuration.md)。
