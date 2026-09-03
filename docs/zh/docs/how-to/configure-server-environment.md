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

引导式命令会以 `0600` 权限写入私有文件。通过环境或 secret manager 提供 provider 凭据，不要把它们写入命令行参数。

## 2. 检查并校验

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

`config show` 会隐藏已识别的凭据。校验会检查文件和所选 model 设置，但不会输出机密。

## 3. 使用同一份配置启动

```bash
powercontext server run --env-file .env
```

文件中的值会覆盖同名进程变量。文件中没有的继承 `POWERCONTEXT_SERVER_*` 变量会被忽略，因此校验和启动使用同一份
Server 设置。

Server 会按配置启动对应能力。使用 `powercontext ready` 和 `powercontext capabilities` 查看就绪状态和已启用功能。

全部变量、默认值和优先级规则见[配置](../reference/configuration.md)。
