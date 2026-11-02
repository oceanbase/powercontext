---
title: 脚本安装
description: 引导安装 Python 和 uv，选择 PowerContext 版本，并安装匹配的 Agent 集成。
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#1892](https://github.com/oceanbase/powercontext/pull/1892)
- Tracking Issue: [oceanbase/powercontext#1900](https://github.com/oceanbase/powercontext/issues/1900)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md)、[RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

提供通过 uv 安装 PowerContext 的 Bash 和 PowerShell 脚本。脚本复用或安装 Python 和 uv，选择 Runtime profile，并按需安装版本匹配的 Agent 集成。默认包版本为所选索引中的最新稳定版，用户也可以指定准确版本。

配置和个人服务注册仍由用户分步执行现有 CLI 命令。脚本完成后给出这些步骤的指引，具体操作见[安装指南](../docs/get-started/install-and-run.md)。

# Motivation

安装 PowerContext 需要兼容的 Python、uv、正确的包 extras，以及匹配的集成 ref。Python 包、uv 二进制和 Python 发行版还需要分别配置下载来源；仅更换包索引无法解决 Python 下载失败的问题。

安装器负责前置依赖和发行版本选择，复用现有配置、集成和服务命令。用户可以先安装软件，再决定何时启动 Server、使用哪个 Agent 宿主。

# Guide-level explanation

## Runtime profile 与前置依赖

默认的 `local` profile 安装 CLI、Client 和本地 Server 依赖。`--profile client` 安装连接远程 Server 所需的 CLI 和 Client 依赖。Profile 与 Agent 选择相互独立。

安装器复用已有 uv，以及兼容的系统 Python 或 uv 管理的 Python；缺少时在用户目录补齐。默认安装可以无人值守执行，无需管理员权限，完成后打印新终端所需的 PATH 命令。

## 发行版本与 Agent 集成

`--version latest` 选择已配置索引中兼容的最新稳定版。指定准确版本可以固定包版本。例如：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

只有显式传入 `--host` 才运行集成安装。所选宿主需要 Git 和各自的前置依赖；可以选择多个宿主。`--no-hosts` 显式跳过集成，与 `--host` 冲突。

安装器读取已安装的 CLI 版本，把对应的 `powercontext-vVERSION` tag 传给 `powercontext setup`，全部宿主使用同一发行 ref。`--env-file PATH` 把已有连接文件传给宿主安装；安装器不解析或执行该文件。

## 配置与个人服务

安装完成后，用户通过现有 CLI 配置本地 Server、注册个人服务，由原生用户服务管理器在终端关闭后继续运行 Server。Client-only 安装按远程连接指南设置。[安装指南](../docs/get-started/install-and-run.md#配置并安装个人服务)说明配置、服务注册和连接检查的具体步骤。

配置、Server 启动和服务生命周期由各自的现有命令负责。个人服务的 loopback 绑定、管理器支持等约束沿用 [RFC 1299](1299_local_server_availability_and_service_installation.md)。Windows 支持保持试验性。

## 下载来源

包索引、uv 二进制和 Python 发行版分别配置来源。用户可以选择区域或分别指定来源。显式设置优先于自动镜像；自动镜像可以按下文规则回退到官方源。

# Reference-level explanation

## 职责与执行顺序

入口为 `website/public/install.sh` 和 `website/public/install.ps1`，作为网站静态资源发布。两个脚本提供相同的公开选项和失败行为。

| 职责 | 负责方 |
| --- | --- |
| 校验安装参数、准备前置依赖、编排安装步骤 | Shell 脚本 |
| 解析包、选择兼容产物、维护工具环境 | uv |
| 安装选定的 Agent 集成 | 现有 `powercontext setup` 适配器 |
| 配置和运行 Server、诊断状态、管理个人服务 | 现有 CLI 和服务层 |

脚本按以下顺序执行：

1. 校验参数，包括宿主选项冲突及所指定连接文件是否存在。
2. 复用或安装 uv 和 Python，再选择包索引。
3. 在 uv tool 环境中安装所选包 requirement。
4. 通过 `uv tool dir --bin` 找到已安装入口，检查版本和命令可用性。
5. 打印已安装版本和 PATH 指引，再按该版本的 tag 安装选定宿主。
6. 打印本地服务或远程连接指引；宿主安装失败时返回非零状态。

## 前置依赖复用

缺少 uv 时，安装器使用自己维护的固定版本安装，与 PowerContext 版本无关。已有 uv 直接复用，不执行升级。

Python 发现接受 3.11 及以上、低于 4.0 的版本，并排除虚拟环境。没有兼容解释器时，通过 uv 安装 Python 3.12。平台、架构和产物由 uv 选择。`UV_INSTALL_DIR`、`UV_TOOL_DIR` 和 `UV_TOOL_BIN_DIR` 继续控制安装位置。

## 版本和 profile 语义

| 选择 | 包 requirement 与行为 |
| --- | --- |
| `--profile local`（默认） | `powercontext[cli,server]` |
| `--profile client` | `powercontext[cli]`，包含 Client 依赖 |
| `--version latest`（默认） | 未固定版本的 profile requirement，使用 `uv tool install --upgrade --force --prerelease disallow` |
| 准确版本 | 在 profile requirement 后追加 `==VERSION` |

`latest` 指所选解释器及已配置索引兼容的最新稳定版。`--force` 让旧版 uv 重新解析工具环境，避免已有预发布版本绕过稳定版选择。解析失败时保留请求的 requirement 并报告错误。

准确版本采用 `X.Y.Z`，可以追加 `aN`、`bN` 或 `rcN`。该选项不接受 0.1.0 之前的版本、版本范围、源码 ref 或 URL。更换 profile 会替换工具的依赖集合；需要管理其他 extras 的用户使用手动安装路径。

已安装入口必须报告发行版本，准确版本请求必须与其一致。所有 profile 都需要通过 CLI help 和 `capabilities --help`；`local` 还需要通过 `config init --help` 和 `server run --help`。这些检查验证导入和命令可用性；Server 就绪状态在用户完成配置并启动后检查。

## 来源选择与回退

区域选择依次使用 `--region`、`POWERCONTEXT_INSTALL_REGION`、本地命名时区、locale 地区，最后采用全球源。检测只读取本地设置，不调用网络定位服务。

| 组件 | 全球默认源 | 中国区域自动源 | 显式控制 |
| --- | --- | --- | --- |
| PowerContext 及依赖 | PyPI | 清华 PyPI 镜像 | `--index-url`、uv 索引设置及配置文件 |
| uv 安装器及二进制 | Astral 渠道 | USTC release 镜像 | `POWERCONTEXT_UV_INSTALLER_URL`、`UV_DOWNLOAD_URL`、uv 安装器镜像变量 |
| Python | uv 默认渠道 | NJU python-build-standalone 镜像 | `UV_PYTHON_INSTALL_MIRROR`、uv Python 下载配置 |

已有 uv 配置时不自动选择包镜像。`--index-url` 替换默认索引，额外索引沿用 uv 的优先级。认证和包索引协议由 uv 处理。脚本 URL 参数必须使用 HTTPS，且不含凭据；pip 索引变量不会配置 uv。

显式 uv 和 Python 来源不做回退。自动选择时，uv 镜像安装器不可用则回退 Astral；uv 镜像产物可回退官方源。Python 构建 URL 由 uv 提供；脚本检查对应镜像产物，自动镜像失败时使用 uv 默认渠道重试。

自动包镜像在工具安装前接受有超时限制的可用性请求。镜像不可达时回退 PyPI；可达时保持选中，即使缺少请求版本。解析和产物下载错误由 uv 报告，脚本不在工具安装尝试后切换索引，以免文件已改变时又使用另一来源重试替换。索引解析和兼容性检查仍由 uv 负责。

## 失败与恢复

后续步骤失败时，保留已经完成的组件：

| 失败阶段 | 结果与恢复方式 |
| --- | --- |
| 前置依赖安装失败 | 停止并报告下载错误，保留已经安装的前置依赖 |
| 包解析失败，包括准确版本不存在 | 保留已安装工具，报告 uv 错误 |
| 已安装入口验证失败 | 报告包已安装但验证失败，允许指定准确版本重试 |
| 宿主安装失败 | 保留已验证的 Runtime，返回非零状态，并提示使用匹配的发行 ref 重试 setup |

入口验证失败时，旧可执行文件可能已经被替换。安装器不回滚包文件，也不撤销已经完成的宿主操作，只清理自己的临时下载。来源和 PATH 变化局限于安装进程；PowerShell 在 `finally` 恢复临时来源变量。

## 持久化与兼容性

包安装保持 `.env`、凭据、数据目录和数据库 schema 不变，也不停止或重启服务。启动升级后的 Server 时，用户按现有迁移和服务指南操作。Runtime API 及现有配置、诊断、服务命令保持原有契约。

`UV_OFFLINE=1` 禁用安装器探测和 uv 网络访问。缓存重装需要已有 uv、兼容 Python 和全部所需包；缺少前置依赖或产物时明确失败。

## 可执行契约与验收

两个 shell 适配器都必须通过 `tests/fixtures/installation/*.json` 中的共享行为用例。用例通过真实 uv 和小型 fixture wheel，检查公开参数、安装后的能力、匹配的宿主 ref 和退出结果。

验收覆盖：

- 缺少 uv 或 Python 时的引导、复用已有工具与配置，以及包含空格或非 ASCII 字符的路径。
- 最新稳定版、准确版本、显式预发布版，以及解析失败时保留已安装工具。
- Client/local profile 替换、显式宿主选择、宿主部分失败及已有连接文件传递。
- 无人值守和 Bash 管道执行，以及 PowerShell 文件和下载后 scriptblock 入口。
- 安装实际 wheel、配置验证、HTTP 就绪、Memory 持久化、离线缓存重装和重启回读。

原生 CI 矩阵在 Linux、macOS 和 Windows 上运行安装验收；受控索引测试在 Linux 上验证镜像选择和失败处理。平台验收需要原生执行，语法检查和模拟宿主不代表服务管理器或 Agent 工作流可用。发布引用检查保持显式版本示例同步，同时保留 `latest` 默认值。

# Drawbacks

Bash 和 PowerShell 需要分别实现前置依赖与来源选择。自动镜像增加可用性依赖。`latest` 会随所选索引发布新版本而变化，可重复部署需要准确版本。匹配的集成 tag 用于协调版本，各宿主仍需单独验证兼容性。

# Rationale and alternatives

已有 Python 和 uv 的用户可以直接使用 uv 命令；脚本负责补齐这些前置依赖。源码安装仍供开发使用。脚本默认使用 `latest`，避免两次网站发布之间固定的包版本逐渐过时。

Python 安装引擎仍需要 shell 引导。生成 shell 适配器会增加生成器，同时仍需原生测试。初期采用两个较小的适配器，并共享行为用例，可以减少维护环节。

uv 已经维护包环境和安装记录，额外安装账本会重复保存这些状态。如果以后分发独立版本的原始产物，再引入对应的清单和记录。

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) 区分安装、配置和诊断，定义独立的 profile、宿主选择及组件恢复。[RFC 1299](1299_local_server_availability_and_service_installation.md) 定义本方案复用的个人服务生命周期。

[Magpie 安装器](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh) 区分发行源与产物镜像，在替换前验证二进制校验和。它的清单用于直接分发二进制，本方案则把包分发交给 uv。

[Lody daemon 安装器](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md) 解析实际 npm 安装位置并验证安装后的可执行文件，本方案同样检查实际入口。它的 [Agent runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts) 另行管理原始归档及其记录。

[Bub](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907/website/public) 先引导 uv，再运行 Python preset 解析器。本方案同样使用 shell 引导，并在各原生平台测试两个适配器。[uv 工具环境](https://docs.astral.sh/uv/concepts/tools/)、[uv 配置](https://docs.astral.sh/uv/concepts/configuration-files/)和 [Python Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)定义交给 uv 处理的环境与包来源规则。

# Unresolved questions

完整离线包、密码学校验安装清单、独立打包的宿主产物，以及配置存储变化，需要单独设计。本 RFC 保持 Windows 的试验性支持状态。

# Future possibilities

当需要独立组件版本、不可变清单或持久修复记录时，可以考虑安装引擎。离线包可以携带全部所需产物。这些扩展仍保留显式的 Server 启动和服务注册。
