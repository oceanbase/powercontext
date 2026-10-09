---
title: 脚本安装
description: 通过 Bash 和 PowerShell 安装最新或指定版本，独立管理包与运行时镜像，并显式选择 Agent。
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#1892](https://github.com/oceanbase/powercontext/pull/1892)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md)、[RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

提供由发行版维护的 Bash 和 PowerShell 入口，通过 uv 安装 PowerContext。默认版本是所选包索引中的最新稳定版，
`--version` 可以指定准确版本。脚本补齐缺少的 uv 和 Python，安装所选 Runtime profile，并按需调用现有 Agent 集成
适配器，使用与实际安装的 Runtime 对应的 tag。

安装、配置、诊断和服务运行各自承担独立职责。默认安装软件不会配置推理服务、启动 Server 或注册持久服务。显式 `--configure` 和 `--service` 可以通过
现有 CLI 串联这些操作，并使用同一份 `--env-file`。
README 和快速开始将脚本及其安装指南作为首选路径。

# Motivation

正常安装不应要求用户自行协调 Python 发现、uv 安装、包 extras、包索引及集成 Git ref。Python 包、uv 二进制和
Python 发行版也来自不同渠道：只更换 PyPI 索引无法解决 Python 下载不可达的问题。

安装负责放置版本化组件并注册显式选择的集成；配置管理 Server URL、Scope、采集策略等可变值；诊断观察安装结果。
分清这些职责，安装体验就可以独立于配置存储、统一 Hooks、运维工具或服务生命周期改进。

预期结果是每种 shell 一个入口、复用已有依赖、明确的版本选择、显式的宿主选择、可恢复的部分失败，
以及通过实际安装产物进行验证。

# Guide-level explanation

## 安装 Runtime profile

macOS 或 Linux：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

Windows，使用 PowerShell 5.1 或更新版本：

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1)))"
```

默认 `local` profile 安装 CLI、Client 和本地 Server 依赖。`--profile client` 只安装连接已有 Server 所需的 CLI 和
Client 依赖。Profile 与宿主选择相互独立：选择数据库角色不隐含选择 Agent，选择 Codex 也不会更改 Runtime profile。

安装器复用已有 uv，以及兼容的系统 Python 或 uv 管理的 Python；否则在用户目录补齐 uv 和 Python 3.12。
完成后打印实际 Runtime 版本、profile、可执行文件目录、PATH 命令及下一步配置命令，不修改持久 PATH 设置。
继续操作前，先执行打印的 PATH 命令。

## 选择发行版和集成

默认值为 `--version latest`，重复执行会检查已配置索引上的更新稳定版。需要可重复的包版本选择时，指定准确版本：

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

默认安装无需 Git 或宿主选择界面，可以无人值守执行。`--host` 可以重复，用于显式安装集成；这时需要 Git 和
各宿主自己的前置条件。`--no-hosts` 显式表达默认行为，不能与 `--host` 同时使用。安装后需要交互选择宿主时，运行
`powercontext setup select --ref "powercontext-v$(powercontext --version)"`。

已安装的 CLI 提供准确版本，形成 `powercontext-vVERSION`。宿主安装不再次解析 `latest`，也不默认使用 `master`。
宿主安装失败时，保留已安装的 Runtime，脚本返回非零状态，并提示按该 tag 重试 setup；各适配器提供其已有的宿主结果。

个人 macOS/Linux 配置受保护文件后，运行 `powercontext service install --env-file .env`、`powercontext service status`
和 `powercontext doctor --env-file .env`，由原生用户服务管理器负责生命周期。开发、调试、临时使用或缺少 manager 时，
保留前台 `server run`。Client-only 模式按远程连接指南设置地址和认证。持久服务仍由用户显式注册。

## 显式配置与个人服务安装

安装器提供三个可以组合的选项：

| 选项 | 契约 |
| --- | --- |
| `--configure` | 通过控制终端运行 `config init --require-write --output PATH` |
| `--service` | 验证所选文件，安装原生用户服务，检查状态与就绪 |
| `--env-file PATH` | 为配置、服务、诊断和宿主安装选择同一份显式文件 |

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --configure --service --env-file "$HOME/.config/powercontext/powercontext.env"
```

`--configure` 和 `--service` 要求 local profile 及显式文件。无人值守安装省略 `--configure`，提供已有文件。
无效组合、缺少已有文件或交互终端时，在 Runtime 安装前失败。Bash 将 `/dev/tty` 单独交给向导，避免与下载脚本的
stdin 混用；PowerShell 要求交互控制台。普通安装继续支持无人值守。

配置 CLI 增加 `--require-write`，保留独立使用时的取消行为。指定该选项时，取消保存返回 130，即使已有有效旧文件，
也不会继续修改服务或宿主。两个入口分别报告配置保存、验证、服务验证以及后续失败。

服务安装复用现有服务层的文件保护、loopback、manager 支持、注册归属、reconcile 和启动规则。
Windows 仍为试验性；入口的显式 `--service` 同时同意登录自启动。完成注册后，还必须通过 `service status` 和
`doctor --env-file` 的就绪诊断；`degraded` 返回非零状态。

`doctor --env-file` 复用严格的 Python 环境加载器，结束后恢复进程设置。Server 文件选择其监听地址，不受 SSH 转发
所用 Client URL 或调用者旧配置影响；Client-only 文件选择 Client URL，显式诊断 `--server-url` 优先。
Shell 不执行文件内容。宿主安装使用 `setup --env-file PATH select`，并绑定实际安装版本对应的 tag。

所选发行版必须提供新增 CLI 选项；旧版缺少时，报告失败阶段并保留 Runtime。发布需要协调包含这些选项的包与网站。
任何文件修改（包括 Scope ID）或升级后，使用原文件重新执行 `service install --env-file`。
配置输出和后续步骤统一推荐这一路径，并在 Scope 创建后提示重新注册。后续失败保留包、已保存文件和已提交的服务注册，
供用户显式恢复。

## 选择下载来源

`--region auto|cn|global` 优先于 `POWERCONTEXT_INSTALL_REGION`。自动模式依次参考本地命名时区、locale 地区，
最后采用全球源，不调用网络定位服务。

| 组件 | 全球默认源 | 中国区域自动源 | 显式控制 |
| --- | --- | --- | --- |
| PowerContext 及依赖 | PyPI | 清华 PyPI 镜像 | `--index-url`、uv 索引设置及配置文件 |
| uv 安装器及二进制 | Astral 渠道 | USTC release 镜像 | `POWERCONTEXT_UV_INSTALLER_URL`、`UV_DOWNLOAD_URL`、uv 安装器镜像变量 |
| Python | uv 默认渠道 | NJU python-build-standalone 镜像 | `UV_PYTHON_INSTALL_MIRROR`、uv Python 下载配置 |

已有 uv 配置时不自动选择包镜像。显式 `--index-url` 只更改默认索引，额外索引沿用 uv 的优先级。
认证和索引解析由 uv 负责。脚本 URL 参数必须使用 HTTPS，且不包含凭据；凭据通过 uv 配置管理。
pip 的索引环境变量不属于 uv 配置。Python 和 uv 下载覆盖项与包索引彼此独立。

# Reference-level explanation

## 安装职责

入口为 `website/public/install.sh` 和 `website/public/install.ps1`，作为网站静态资源发布。
两者支持相同的选项和失败语义。脚本负责引导与编排；uv 负责环境创建和包解析；现有 `powercontext setup` 适配器
负责集成安装。

安装操作由以下输入确定：

```text
包 requirement + Runtime profile + 显式宿主选择 = 安装操作
```

包管理器在替换工具环境前解析 requirement。安装完成后验证 CLI 的准确版本，再用于全部宿主安装。
这样一次操作具有统一的发行版坐标，无需新增包解析器，也不依赖另一个发行清单系统。

保留 `powercontext setup`、`config`、`doctor` 和服务命令。不把领域行为搬到 shell，不引入独立 Python 安装引擎，
也不修改 Runtime 或公共 HTTP API。

## 版本和 profile 语义

- `latest` 向 `uv tool install --upgrade --reinstall-package powercontext --prerelease disallow` 传入未固定版本的 profile requirement。
  它表示所选解释器及已配置源兼容的最新稳定版，不一定是另一镜像上最近上传的版本。重新安装 PowerContext 包，避免已有预发布版本绕过稳定版选择。解析失败直接报告，不改换 requirement。
- 准确版本使用 `==VERSION`，格式为 `X.Y.Z`，可追加 `aN`、`bN` 或 `rcN`。支持显式预发布版本；
  `--version` 不接受源码 ref、版本范围或 URL，也不支持 0.1.0 之前的版本。
- `local` 使用 `powercontext[cli,server]`；`client` 使用 `powercontext[cli]`，后者已经包含 Client 依赖。
  使用不同 profile 重跑会替换该工具的依赖集合；自行添加的 extras 应通过文档中的手动安装路径维护。
- `uv tool dir --bin` 中的可执行文件必须报告发行版本；显式请求必须与其一致。所有 profile 都需要通过 CLI help、
  `capabilities --help`；`local` 还需要通过 `config init --help` 和 `server run --help`。
  全部通过后才打印安装成功。这些检查验证导入和命令可用性，不代表 Server 就绪或 Agent 工作流可用。

uv 使用安装器维护的固定引导版本，与 PowerContext 发行版本无关。已有 uv 直接复用，不悄悄升级。
Python 发现排除虚拟环境，避免项目 venv 意外成为安装前提。没有兼容的 Python 3.11+ 时，通过 uv 安装 Python 3.12。
平台、架构和 wheel 兼容性错误由 uv 报告。

## 镜像优先级与恢复

保留用户显式指定的 uv/Python 来源，不做自动回退。中国区域自动选择时，uv 镜像安装器不可用则回退 Astral；
uv 镜像文件下载失败时可以尝试官方源。Python 的准确构建 URL 由 uv 提供，安装前检查对应镜像文件；
自动 Python 镜像安装失败后，使用 uv 默认渠道重试。

中国区域自动包源选择对 PowerContext 索引页发起有超时限制的可用性请求；镜像不可达时，在工具安装前回退 PyPI。
可达镜像即使缺少请求版本也保持选中，由 uv 报告解析失败。全球源和显式来源直接交给 uv。脚本不解释索引 HTML、
wheel 文件名、编码 URL、包兼容性或认证，也不在一次可能已经改变安装文件的工具安装尝试后换源重试。

Shell 环境变化局限于安装器进程；PowerShell 在 `finally` 恢复临时来源变量。不用 shell 解析或改写已有配置文件，
只清理由安装器创建的临时下载目录。保留 `UV_INSTALL_DIR`、`UV_TOOL_DIR` 和 `UV_TOOL_BIN_DIR` 的位置控制。

## 持久化与兼容性

普通包安装不读取或改写 `.env`、凭据、数据目录或数据库 schema，也不停止或重启服务。显式 `--configure`
由 CLI 保存所选文件；显式 `--service` 由原生层 reconcile 自有注册，并按既有契约在需要时重启。
启动升级后的 Server 时，用户按现有升级和迁移指南操作。准确版本重试可以使用缓存；`latest` 则明确允许升级。

`UV_OFFLINE=1` 只支持已有 uv、兼容 Python 和全部依赖时的缓存重装，同时禁用安装器源探测和 uv 网络访问，
不承诺完整离线发行包。后续步骤失败会保留已安装的前置依赖。准确版本不存在导致的解析失败会保留原工具；
但 uv 接受新包后，验证失败可能发生在旧可执行文件已被替换之后。此时明确报告“包已安装，验证失败”，允许通过准确
版本重试，不声称已经回滚。后续宿主安装失败则保留已验证的 Runtime。不提供跨组件回滚，也不修复无关宿主状态。

Windows 保持产品的试验性支持状态。原生验收在 Linux、macOS 和 Windows 执行；具体宿主支持范围仍由各集成的能力契约决定。

## 可执行契约与验收

`tests/fixtures/installation/*.json` 定义两个 shell 适配器共享的公开输入与预期结果。
`tests/test_installation_contract.py` 使用真实 uv 和小型离线 fixture wheel 执行宿主显式选择、profile、准确版本
校验、命令缺失和宿主部分失败用例。用例观察安装后的能力、所选宿主、发行 ref 和退出结果，不固定内部函数边界或
调用顺序。同一组用例在各操作系统原生执行；模拟宿主或服务执行不等于真实原生服务或 Agent 验收。POSIX 控制终端用例覆盖管道安装中的配置请求与取消；
原生服务 CI 通过安装后的脚本验证记忆持久化、文件修改后的重新注册和停止服务后的升级。

`tests/native/test_installation.py` 执行真实 shell 安装器、uv、安装后的 PowerContext CLI 和 HTTP Server。
从被测提交构建 wheel，使用发行版形式的元数据和直接本地文件约束。该约束选定被测产物，不构成独立执行的校验和验证。
验收包括：

- 全球源及中国区域源下缺少 uv/Python 的安装、已有工具与配置、包含空格和中文的路径；
- 通过受控包索引解析真实稳定版和预发布版 wheel、默认升级、指定版本，以及版本不存在时保留原安装；
- Client-only 安装及 profile 切换，安装过程不创建本地 Server 状态；
- `.env` 生成与验证、就绪检查、Memory 保存与搜索、离线缓存重装及重启后的回读；
- 显式索引失败、宿主选项冲突、无人值守与 Bash 管道安装，以及 PowerShell 下载脚本后执行 scriptblock 的入口。

独立 CI 矩阵在三个操作系统执行上述套件。发布引用检查保持脚本的 `latest` 默认值，同时更新显式版本示例。
网站验证检查文档链接和静态构建。语法检查本身不证明 Windows 或 macOS 安装可用；这些测试也不声称
真实 Agent 宿主已完成采集与召回工作流。

[可复现的第一性原理和消融研究](https://github.com/PsiACE/powercontext/tree/feat/installation-contract/experiments/installation)
固定上游源码、安装器基线、uv 版本、受控输入和
观测结果。它们支持以下决策：删除隐式宿主选择仍可保留显式宿主失败语义；基于文件名的预检查会拒绝合法编码包 URL；
只有预发布版本的索引需要显式稳定版策略；包安装成功后入口仍可能不可用。研究与持续维护的产品验收分开，分别说明
实际执行范围和限制。

# Drawbacks

Bash 和 PowerShell 会重复部分编排和来源选择策略。自动镜像增加外部可用性依赖。
`latest` 会随时间变化，可重复部署需要指定版本。匹配 tag 能协调发行版，但不能单独证明宿主兼容性。

# Rationale and alternatives

保留直接运行 uv 命令的路径，供已有 Python 和 uv 的用户使用；它无法引导干净机器。
源码安装适合开发，不作为默认发行渠道。把公共脚本固定在某个 PowerContext 版本，会使新用户在网站重新发布前
一直安装过期版本。

没有 Python 的机器仍需要为 Python 引擎提供引导。生成 shell 适配器仍需原生行为测试，还会增加生成器和版本管理。
共享契约用例可以提供可执行的一致性约束，无需分发新的运行时资源。仅当具体策略确有需要时再引入引擎或生成器。

分发独立版本的原始产物时，不可变清单和额外安装账本有其用途。这里由 uv 管理 wheel 解析、工具环境及自身记录；
安装器观察实际入口，通过显式的 uv 安装重试修复组件，避免同一安装状态出现两个权威来源。

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) 定义了安装、配置和诊断的职责分离，
独立的 Runtime profile 与宿主选择、组件级恢复和显式服务注册。
[RFC 1299](1299_local_server_availability_and_service_installation.md) 定义个人服务生命周期。

[Magpie 安装器](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh)
将权威发行源与产物镜像分开，在替换二进制前检查校验和。它直接拥有原始二进制的分发职责，因此需要这层验证；
其清单不能替代 Python 包标准。

[Lody daemon 安装契约](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md)
解析并执行实际 npm 安装位置，检查替换后的就绪状态。它另有直接管理原始压缩包的
[Agent runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts)，
因此拥有清单和完成记录。本方案采用实际可执行文件检查，服务就绪仍归服务边界负责。其公开 tag-release 工作流不发布安装器。

[Bub](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907/website/public) 先引导 uv，再运行
Python preset 解析器。它对终端和 macOS Bash 的修复支持显式宿主安装与原生测试的必要性。
[uv 工具环境](https://docs.astral.sh/uv/concepts/tools/)及
[配置规则](https://docs.astral.sh/uv/concepts/configuration-files/)定义复用的环境与来源语义；
[Python Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)定义包索引解释规则。
Shell 正则无法实现这些契约。

# Unresolved questions

接受本契约无需额外的跨子系统决策。完整离线包、带密码学校验的安装清单、独立打包的宿主产物以及配置存储变化，
需要各自设计。推广前必须保持平台矩阵通过，Windows 产品支持仍为试验性。

# Future possibilities

当安装计划需要不可变组件清单、独立集成版本或持久组件修复记录时，脚本可以引导发行版维护的独立安装引擎。
离线包也可以显式携带全部所需产物。这些扩展继续保持 Server 启动与服务注册的显式同意。

个人服务引导工作关联 [Issue #1900](https://github.com/oceanbase/powercontext/issues/1900)。
