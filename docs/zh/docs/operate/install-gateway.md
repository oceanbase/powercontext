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

## 使用安装脚本

安装 PowerContext 时传入 `--gateway`，脚本会安装版本相同的 Gateway，并完成下文的下载与校验：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

`pc-gateway` 安装在 `powercontext` 所在的目录。`--version` 指定的版本同样适用于 Gateway；`POWERCONTEXT_GATEWAY_DOWNLOAD_URL` 可以指定存放同名压缩包和 `.sha256` 文件的 HTTPS 目录，例如内部镜像。平台不受支持时，脚本在安装任何内容之前退出。下载或校验失败时，CLI 已经安装，但脚本不会安装或替换 `pc-gateway`，并以非零状态退出。该选项需要本地 Server 安装，不能与 `--profile client` 同时使用。

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
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
pc-gateway
```

所有 Gateway 部署（包括组合 UDS 和非 enforcing 推理隐私模式）都要求 Core 设置
`POWERCONTEXT_SERVER_ACCESS_MODE=enforced` 并配置受支持的认证 Provider。关闭认证/访问控制的 Core 无法批准模型发送。
Gateway 默认监听回环地址。同机组合部署时，将 `PC_GATEWAY_CORE_BASE` 替换为
`PC_GATEWAY_CORE_UDS=/path/to/core.sock`；Core 必须已经监听该私有 socket。
`PC_GATEWAY_API_KEY` 保护模型路由，包括回环监听在内的所有模型监听都必须配置。它必须与
`PC_GATEWAY_CORE_TOKEN` 不同；它不替代转发 API、MCP 和 Dashboard 路由所需的 Core 凭据。
`PC_GATEWAY_UPSTREAM_PROFILE` 必须已存在于 Core，并包含服务商 endpoint、模型和 `openai-chat` 协议。
Responses 路由还要设置 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES`，并配置 `openai-responses` 目标。不要设置
`PC_GATEWAY_UPSTREAM_URL*`；如果 `PC_GATEWAY_DEGRADE` 有任何非空值也要移除，这些旧设置都会导致启动失败。
`PC_GATEWAY_CORE_TOKEN` 必须代表服务主体；即使监听回环地址，
也必须配置非空且不同于 Core Token 的 `PC_GATEWAY_API_KEY`。专用 Gateway 主体需要在客户端通过
`X-PowerContext-Scope-ID` 选择的每个 Scope 上获得 `gateway.user`（`gateway.invoke`），而不只是
`PC_GATEWAY_DEFAULT_SCOPE`；同时要在 deployment ID 对应的 Server resource 上获得 `server.observer`
（`server.observe`）。ID 由 `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` 配置，默认值为 `powercontext`。
设置 `PC_GATEWAY_SEDIMENT=on` 时，还需授予 `scope.contributor` 替代 `scope.viewer`；它同时包含 `scope.read` 和
`scope.contribute`。关闭采集时，`scope.viewer` 即可。使用内置可写 Access Provider 时，先携带 Gateway Token
调用 `GET /v1/access/me`，再由 Access 管理员通过 `POST /v1/access/bindings/create` 创建对应绑定；完整命令见
[网关认证与目标要求](deploy-server.md#网关模型目标)。引导 Server Token 是管理员凭据并拥有静态 Scope 角色；
单 Token 部署仍需在客户端选择的每个 Scope 上授予它 `gateway.user`，而不只是默认 Scope。管理员权限覆盖
`server.observer`，静态 Scope 角色覆盖
reader/contributor 权限。下面的 Scope 示例展示模型调用授权及专用身份使用的 reader 角色：

```json
{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"your-scope"},"role":"gateway.user","idempotency_key":"gateway-user-your-scope"}
```

```json
{"subject":{"type":"service","id":"server-token"},"resource":{"type":"scope","scope_id":"your-scope"},"role":"scope.viewer","idempotency_key":"gateway-viewer-your-scope"}
```

设置 `PC_GATEWAY_SEDIMENT=on` 时，请用 `scope.contributor` 替代 `scope.viewer`；还要在
`{"type":"server","deployment_id":"powercontext"}` 上创建 `server.observer` 绑定（替换为实际 deployment ID）。
将 `server-token` 替换为 `PC_GATEWAY_CORE_TOKEN` 对应的 service 主体。完整 curl 命令和引导 Server Token 的区别见
[网关认证与目标要求](deploy-server.md#网关模型目标)。

`curl --fail http://127.0.0.1:8787/healthz` 只检查 Gateway 监听状态。同机 UDS 部署时，通过 Gateway
携带认证请求 `/v1/server-info` 应返回与 Core 相同的 Server 身份。独立 HTTP 模式不转发 Core 路由。
供应商和模型验收与启动、连接检查相互独立。

升级时先停止 Gateway，下载并校验匹配版本，然后替换可执行文件。重启前删除所有
`PC_GATEWAY_UPSTREAM_URL*` 配置，配置 Core 批准的 profile 和相互不同的必填 Gateway/Core token，并为 Gateway
服务身份在相应资源上授予 `gateway.invoke`、`server.observe`，以及（开启采集时）`scope.read` 和
`scope.contribute`。此前按请求选择不同模型的部署需为每个模型
单独配置 Gateway 实例；服务商 endpoint 必须可直连，不能依赖进程环境中的 HTTP 代理。
Server 的数据库维护要求需要单独遵循。保留旧二进制以便回退程序；替换 Gateway 二进制不会回退 Core 数据库。

## 从源码构建

检出对应发布标签，运行 `cargo build --locked --manifest-path gateway/Cargo.toml --release -p pc-gateway-app`。
可执行文件位于 `gateway/target/release/pc-gateway`。未设置分发元数据的源码构建显示 Cargo 包版本及未知提交；
正式发布构建会嵌入发布身份。开发和测试说明见
[Gateway README](https://github.com/oceanbase/powercontext/blob/master/gateway/README.md)。
