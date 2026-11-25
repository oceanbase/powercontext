---
title: 安装 Gateway
description: 下载、校验并运行独立 Gateway，无需安装 Rust。
---

# 安装 Gateway

Gateway 是名为 `pc-gateway` 的独立可执行文件，需要连接正在运行的 PowerContext Server（Core）；
安装 Python 包不会安装 Gateway。使用 Gateway 分发工作流构建的版本会在同一个
[PowerContext GitHub Release](https://github.com/oceanbase/powercontext/releases) 中提供四个平台的原生压缩包。
没有这些附件的旧版本需要从源码构建。请选择四个平台附件齐全且 **Gateway distribution** 验证成功的版本；
仅有标签不代表分发已完成。

## 支持的平台

| 系统 | 架构 | 压缩包目标 | 运行环境基线 |
| --- | --- | --- | --- |
| Linux | x86_64 | `x86_64-unknown-linux-gnu` | glibc 2.28+ |
| Linux | arm64 | `aarch64-unknown-linux-gnu` | glibc 2.28+ |
| macOS | Intel | `x86_64-apple-darwin` | macOS 15+ |
| macOS | Apple Silicon | `aarch64-apple-darwin` | macOS 15+ |

Linux 压缩包不是 Alpine/musl 构建。macOS 压缩包未签名、未公证，系统可能要求按正常安全机制批准运行。
目前不提供 Windows 压缩包和 Gateway 容器镜像。通过 CLI 运行的订阅通道仍需要在 Gateway 所在主机安装对应 CLI 并登录。

Linux 发布检查覆盖两个架构上的原生 Ubuntu 22.04 环境和 Rocky Linux 8 容器用户空间。
容器共享 runner 的内核，因此这些检查不能证明历史内核或整个旧发行版的兼容性。
Core 对 Python、SQLite 的要求，以及各供应商 CLI 的运行要求，均独立于 Gateway 的 glibc 基线。

### Windows

本次发布不提供原生 Windows 可执行文件。Gateway 目前依赖 Unix socket 传输和 Unix 信号处理，
仅增加 Windows 构建任务不能实现 Windows 支持。
Windows 用户可在 WSL2 中使用 Ubuntu 22.04 或更新版本，并**在 WSL2 内**按下文的 Linux 步骤操作。
Core 和所需的提供方 CLI 也应运行在同一 Linux 环境中。这使用的是 Linux 包，并非原生 Windows 构建；
分发 CI 验证的是 Ubuntu runner，尚未验证 WSL2 宿主集成。

## 下载与校验

以下以 1.6.0 为例，请选择与 Core 相同的发布版本。无需 Rust 编译器或仓库源码。
从选定的 Release 下载压缩包及校验文件，在解压或安装之前完成校验：

```bash
set -eu
version=1.6.0
case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) target=x86_64-unknown-linux-gnu ;;
  Linux/aarch64|Linux/arm64) target=aarch64-unknown-linux-gnu ;;
  Darwin/x86_64) target=x86_64-apple-darwin ;;
  Darwin/arm64) target=aarch64-apple-darwin ;;
  *) echo "Unsupported platform" >&2; exit 1 ;;
esac
archive="powercontext-gateway-$version-$target.tar.gz"
base="https://github.com/oceanbase/powercontext/releases/download/powercontext-v$version"
mkdir -p "gateway-$version-$target"
cd "gateway-$version-$target"
curl --fail --location --output "$archive" "$base/$archive"
curl --fail --location --output "$archive.sha256" "$base/$archive.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum --check "$archive.sha256"
else
  shasum -a 256 --check "$archive.sha256"
fi
tar -xzf "$archive"
./pc-gateway --version
mkdir -p "$HOME/.local/bin"
install -m 755 pc-gateway "$HOME/.local/bin/pc-gateway"
export PATH="$HOME/.local/bin:$PATH"
```

`--version` 显示 PowerContext 发布版本、完整源码提交和 Rust 目标平台；`--help` 无需 Core 即可运行。
压缩包还包含记录二进制 SHA256 和平台基线的 `manifest.json`、项目许可证及 Gateway 文档。
Release 提供覆盖四个平台的 `powercontext-gateway-SHA256SUMS` 和 `powercontext-gateway-manifest.json`。
校验和用于发现字节变化，不是代码签名。若 shell 尚未包含 `$HOME/.local/bin`，请将它加入 PATH。

## 连接 Core

先按 [Server 部署](deploy-server.md) 启动 Core。独立 Gateway 通过 HTTP 连接 Core 时，
配置可访问的 Scope 和匹配的已批准上游 profile，然后运行：

```bash
PC_GATEWAY_CORE_BASE=http://127.0.0.1:8000 \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_DEFAULT_SCOPE=your-scope \
PC_GATEWAY_UPSTREAM_PROFILE=approved-provider \
PC_GATEWAY_UPSTREAM_URL_CHAT=https://provider.example/v1/chat/completions \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
pc-gateway
```

Gateway 默认监听回环地址。同机组合部署时，将 `PC_GATEWAY_CORE_BASE` 替换为
`PC_GATEWAY_CORE_UDS=/path/to/core.sock`；Core 必须已经监听该私有 socket。
`PC_GATEWAY_API_KEY` 保护模型路由，监听非回环地址时必须配置；它不替代转发 API、MCP 和 Dashboard 路由所需的
Core 凭据。详见[认证边界](deploy-server.md#网关认证边界)。

`curl --fail http://127.0.0.1:8787/healthz` 只检查 Gateway 监听状态。同机 UDS 部署时，通过 Gateway
携带认证请求 `/v1/server-info` 应返回与 Core 相同的 Server 身份。独立 HTTP 模式不转发 Core 路由。
供应商和模型验收与启动、连接检查相互独立。

升级时先停止 Gateway，下载并校验匹配版本，替换可执行文件后使用原配置重启。
Server 的数据库维护要求需要单独遵循。保留旧二进制以便回退程序；替换 Gateway 二进制不会回退 Core 数据库。

## 从源码构建

检出对应发布标签，运行 `cargo build --locked --manifest-path gateway/Cargo.toml --release -p pc-gateway-app`。
可执行文件位于 `gateway/target/release/pc-gateway`。未设置分发元数据的源码构建显示 Cargo 包版本及未知提交；
正式发布构建会嵌入发布身份。开发和测试说明见
[Gateway README](https://github.com/oceanbase/powercontext/blob/master/gateway/README.md)。
