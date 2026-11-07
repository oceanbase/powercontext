---
title: 托管运行时资源生产
description: 为 Windows x64、macOS arm64 与 x86_64 生成固定版本的 Server 和独立 Ops 候选资源。
---

# 托管运行时资源生产

候选资源生产器为[托管安装](../docs/operate/managed-local-server.md)生成固定输入：完整的 Server Python 与依赖树，以及单独的
[Ops 工具包](../docs/operate/local-server-ops.md)。最终用户不需要在系统中安装 Python、uv、Git 或编译器。这是
[#2112](https://github.com/oceanbase/powercontext/issues/2112) 的资源生产部分；Desktop 安装包组装与公开发布另行完成。

所有输出都明确标记为**未资格验收的候选资源**。脚本不接受把构建直接标为生产合格的开关。构建与 CI 冒烟通过不代表完成了签名、
公证、干净机器运行或 Task Scheduler/LaunchAgent 生命周期验收。脚本不会发布资源，也不会推测生产下载地址。

## 固定输入与构建主机

分别在 Windows x86_64、macOS arm64 和 macOS x86_64 上原生构建，不需要先完成 Linux 构建，也不支持交叉编译。构建主机需要
Git、curl、uv 0.13.5 与锁定的 `resource-build` 依赖组。该引导环境可导入 Ops 资源/模型契约，不安装 Server 传输层、cryptography、
jieba 或 maturin。先安装完整 `server`/`ops` extra 会在固定构建器运行前触发环境中的 Intel cryptography 源码构建。
开发环境的工具和可编辑安装不会复制到资源中。

`scripts/managed_resource_interpreters.json` 固定 CPython **3.12.16**、Astral python-build-standalone 标签 **20261026**，
以及每个目标平台的完整 install-only 下载地址与 SHA-256：

| 目标 | 上游产物后缀 | SHA-256 |
| --- | --- | --- |
| `windows-x86_64` | `x86_64-pc-windows-msvc-install_only.tar.gz` | `590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5` |
| `macos-arm64` | `aarch64-apple-darwin-install_only.tar.gz` | `fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e` |
| `macos-x86_64` | `x86_64-apple-darwin-install_only.tar.gz` | `a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3` |

这些文件来自上游 [20261026 发布](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026)，使用其文档定义的
[安装目录树](https://docs.astral.sh/python-build-standalone/distributions/)。生产器保留解释器与各依赖在树内的许可说明，并在两个资源中
生成 `resource-licenses.json`，记录声明、许可文件与实际 wheel 摘要的关系。该索引不是法律合规认证。

准备脚本要求精确的 40 位 Git 源提交与规范化包版本。项目 wheel 直接从 `git archive` 快照构建，未提交文件、可编辑安装和开发环境
不会混入。构建时使用显式 setuptools-scm 版本覆盖，并核对生成的 wheel 与安装后元数据。`inputs.json` 记录源提交、源码归档、
`uv.lock`、`pyproject.toml`、项目 wheel、解释器、构建工具闭包及所有下载/构建 wheel 的精确身份。执行生产器的 Git 提交与脚本摘要
单独记录，不能与被打包的 Server 源提交混为一谈。`matches_committed_files=false` 表示执行了工作区中的生产器，不能称为已提交的发布构建器。

运行依赖从该源提交锁定的 `ops` 与 `cli` extra 导出；`ops` 已包含 `server`。准备阶段按固定解释器的真实 wheel 标签和环境标记选择
兼容产物，再核对原始 `uv.lock` 中的大小和摘要。只有 `jieba==0.42.1` 可以使用锁定的 sdist，因为它没有上游 wheel。准备脚本先核对源码
摘要，再用 `scripts/managed_resource_build_tools.json` 中完整固定版本与摘要的工具闭包构建，不启用构建隔离，也不允许构建后端额外下载依赖。
项目 wheel 是另一个明确的源码构建；Intel macOS 还对精确锁定的 `cryptography==50.0.0` 使用下述专门源码构建路径。
其他没有兼容 wheel 的依赖会明确失败，不会自动放开源码编译。

准备阶段可以联网。最终资源生产只安装冻结的 `requirements.lock` 与 wheelhouse，使用离线、仅 wheel 和强制摘要校验。固定工具输入并不
保证任意主机/工具链上的源码构建都产生相同字节，因此必须保留实际 wheel 及摘要。相同冻结输入的重复资源生产必须生成相同归档和清单。

### Intel cryptography 源码构建

cryptography 上游从 [49.0.0 移除了 x86_64 macOS 支持](https://cryptography.io/en/50.0.2/changelog/#v49-0-0)。生产器保留源锁文件中的
`50.0.0`，不降级使用旧 wheel。独立的 `build_intel_managed_wheel.py` 验证精确 sdist 后，使用以下固定原生输入构建：

| 输入 | 固定身份 |
| --- | --- |
| Rust | `1.95.0`、`x86_64-apple-darwin` 分发包，SHA-256 `998af750fe2c28f0dbf193edc496c9a7407850486968f40ac11a8c8adfb7cfa6` |
| OpenSSL | `4.0.3` 源码，SHA-256 `325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9` |
| 构建后端 | `maturin==1.14.1` 固定 Intel wheel、已有固定 setuptools，以及原生锁定的 cffi/pycparser wheel |
| 编译器/SDK | Xcode `16.4` build `16F6`、macOS SDK `15.5`、部署目标 `10.13` |

所有下载地址与摘要固定在 `scripts/managed_resource_intel_build.json`。Rust 仅安装到一次性构建目录，OpenSSL 在该目录内静态构建，
资源不引用或携带 Homebrew 库。外部 Cargo crate 必须在固定源码的 `Cargo.lock` 中具有 crates.io 校验和，`cargo vendor --locked`
核对获取结果，随后使用 vendored 依赖和 Cargo 离线模式。后端仅从离线固定摘要 wheel 安装，其裸 `maturin` 命令必须解析至该 wheel
在任务构建环境中的精确二进制文件。继承的目标前缀 OpenSSL 设置及 Python/后端/编译器选项无法重定向固定输入。
生成的 wheel 保留 OpenSSL 与 Rust 依赖许可文件。
`otool` 拒绝非系统动态依赖及运行时搜索路径；三个平台安装后的探测都会实际执行 AES-GCM 往返和 Ed25519 签名/验签。
源码、工具、SDK 身份和链接检查结果写入 `inputs.json` 后再冻结摘要。

Intel 构建主机须已安装该 Xcode，并为当前进程设置 `DEVELOPER_DIR=/Applications/Xcode_16.4.app/Contents/Developer`；CI 仅设置环境变量，
不修改系统全局 Xcode 选择。该构建针对上游已停止支持的平台，编译和冒烟通过不会恢复上游支持，也不能证明生产维护或安全资格。
Intel CI 任务必须实际通过，不会因没有 wheel 而跳过；不同编译器/SDK 身份之间不宣称源码构建字节一致。

## 生成候选资源

使用当前已提交的生产器检出，再指定确切的包源码提交和版本。以下 macOS arm64 命令可直接执行，冻结当前提交并赋予明确的候选版本：

```bash
uv sync --locked --no-dev --group resource-build --python 3.12 --no-build-package cryptography --no-build-package jieba
package_source_sha="$(git rev-parse HEAD)"
uv run --no-sync python scripts/prepare_managed_resources.py \
  --target macos-arm64 \
  --source-ref "$package_source_sha" \
  --server-version "0.0.0+candidate.$package_source_sha" \
  --output build/resource-inputs \
  --download-cache build/resource-downloads
```

源提交必须存在于当前检出中，且具有兼容的 `uv.lock` 和包构建契约。Intel macOS 使用 `macos-x86_64`。Windows PowerShell 对应命令：

```powershell
uv sync --locked --no-dev --group resource-build --python 3.12 --no-build-package cryptography --no-build-package jieba
$packageSourceSha = (git rev-parse HEAD).Trim()
uv run --no-sync python scripts/prepare_managed_resources.py --target windows-x86_64 --source-ref $packageSourceSha --server-version "0.0.0+candidate.$packageSourceSha" --output build/resource-inputs --download-cache build/resource-downloads
```

`--runtime-archive` 可复用已经下载的解释器归档，仍必须满足同一个固定摘要。
可选下载缓存位于显式指定的任务目录，按摘要寻址，每次命中都会重新校验。已经存在但损坏或未完成的输入必须先检查，不会直接复用。

保存准备脚本输出的 `inputs_sha256`，再传给离线生产器：

```bash
uv run --no-sync python scripts/produce_managed_resources.py \
  --inputs build/resource-inputs \
  --inputs-sha256 <准备阶段输出的摘要> \
  --server-url https://candidate.invalid/server.tar.gz \
  --output build/resource-candidate
uv run --no-sync python scripts/ci_managed_resource_smoke.py \
  --candidate build/resource-candidate --serve --evidence-dir build/resource-evidence
```

`candidate.invalid` 是保留的测试域名，不能作为真实下载位置。验证 HTTP 交付时，应由操作者提供受控的固定 HTTPS 候选地址；生产器不负责
上传或查找地址。冒烟工具把实际候选归档放入仍会校验摘要的本地缓存，再使用真实 `ResourceCatalog` 和 `ResourceStore` 回读，不宣称发生了
HTTP 下载。输入与输出目录不能互相包含；已有输出绝不覆盖。失败或未完成的构建保留供检查，重试应使用新目录。大型输入、输出和缓存不可提交。

冒烟失败时，会在临时目录清理前，将有界的测试 stdout/stderr 和异常类型摘要保存到显式证据目录。每个日志最多保留末尾 64 KiB，
不在控制台内联打印原始异常内容或子进程 stdout。未指定 `--evidence-dir` 时，默认持久目录是候选目录旁的 `<候选目录名>-evidence`。

## 输出与启动契约

| 输出 | 用途 |
| --- | --- |
| `server/server.tar.gz` | 可重复、无链接的 Server 目录树，包含自身 Python 和完整安装依赖闭包。 |
| `server/catalog.json`、`server/catalog.sha256` | 精确的 `ResourceCatalog`/`ResourceSpec` 字节及摘要；清单逐文件记录路径、大小、SHA-256 和执行位。 |
| `server/resource.json` | 完整来源/输入溯源与生成的资源规格。 |
| `ops/` | 经现有 `build_ops_bundle.py` 构建的独立 `runtime/`、`packages/`、`entry.py` 与兼容的 `bundle.json`。 |
| `ops.tar.gz`、`ops-resource.json` | 可重复 Ops 归档及完整文件大小/摘要/执行位清单，包含 `bundle.json`。 |
| `ops-provision.json` | Desktop 分发输入，固定精确的 `ops/bundle.json` 和 `server/catalog.json` 摘要，使用 `binding_mode=managed_catalog`。 |
| `candidate.json` | 未验收状态、源码/包/输出绑定、生产器身份，以及在 Windows 上执行时的真实路径测量。 |

资格引用以 `unqualified-candidate:sha256:` 开头，绑定冻结的源码/包输入与实际输出，不构成平台验收证据。跟随不可信产物提供的校验和不是
信任根。Desktop 打包器须在受信任分发中固定描述文件，并把独立维护工具部署至受保护的当前用户 `PowerContext/Ops/bundle`，与 Desktop
安装和托管 Server 分离。catalog 打包位置为 `resource_dir/server/catalog.json`。生成上述目录本身不会安装 Desktop 或部署维护工具。

Server 用精确的内置解释器运行 `-I -B -m powercontext.service.launcher`。Windows catalog 选择 `pythonw.exe`，并同时清点配套的
`python.exe`；Ops 使用控制台解释器，继续采用 `-I -S -B entry.py` 和原有清单格式。安装器生成的控制台包装器含绝对构建路径，无法迁移，
因此在冻结前删除。包含构建输入时间戳的可变 uv 安装器缓存记录也会移除，再同步修正发行版 `RECORD`。
可导入模块、原生库、wheel 数据和许可文件保留；人工 CLI 可调用内置模块的 `main` 入口。

解释器归档中的链接不会直接解压为链接。仅将指向树内普通文件的安全相对别名物化为独立普通文件，并记录来源。目录别名、外部/绝对链接、
循环、特殊文件、歧义路径和 reparse point 都被拒绝。归档顺序、时间戳、权限、所有者和 gzip/tar 元数据统一，验证冻结资源时禁用字节码生成。

Windows 生产会按普通 Win32 UTF-16 文件/目录上限核对当前用户真实的托管最终目录、`staging/resource-<32 位十六进制>` 目录及独立 Ops
最终目录。Server 还调用消费者实际使用的目标路径校验器，不启用系统级长路径设置。短构建路径通过不代表其他用户目录也合格；消费者仍拒绝
不支持的真实安装路径。

## CI 与证据范围

`.github/workflows/managed-resource-candidates.yml` 在 `windows-2025`、`macos-15`（arm64）和 `macos-15-intel` 上实际生产。
每个矩阵任务按精确源提交取得固定输入，迁移同一组摘要输入后构建两次，比较归档与清单字节、拒绝输出覆盖/版本不符，并通过真实 catalog/归档消费者回读。
安装后的 Server 在隔离模式下导入依赖，通过精确 service launcher 启动一次性 SQLite Server 并验证就绪。独立 Ops 冒烟使用被污染的
Python 环境变量，检查模块来源，并在 Server Python 缺失时通过原生服务管理器的只读命令检查状态，不注册任何服务。
CI 在成功时保存明确标记为未资格验收的候选资源。失败时保留有界诊断、输入身份和测试证据，不把部分运行时或下载缓存作为失败诊断上传；
任务失败不能算合格资源。

本地聚焦检查：

```bash
uv run --no-sync pytest -q tests/test_managed_resource_producer.py tests/test_ops_resources.py
```

没有显式准备的原生输入时，开销较大的原生生产测试跳过。将 `POWERCONTEXT_RESOURCE_TEST_INPUTS` 设置为准备目录，将
`POWERCONTEXT_RESOURCE_TEST_INPUTS_SHA256` 设置为可信的 `inputs.json` 摘要，即可启用同一路径。
`POWERCONTEXT_RESOURCE_TEST_OUTPUT` 可指定首个保留候选目录，该目录不能已经存在。合成 Windows 归档/路径测试仅证明布局与拒绝逻辑，
不能证明 Windows 上实际执行成功。

正式组装/发布仍需验证 Windows 签名、macOS 签名与公证、没有开发工具的干净标准用户 Windows 11/macOS 机器、登录/重启/服务生命周期，
以及修复和移除时保留配置与数据。签名会改变解释器或原生库字节。必须先完成真实签名，再冻结合格产物的文件清单、归档、catalog、bundle
清单和部署描述文件摘要，并重新生成、验证整个身份链。不得用未签名候选摘要证明另一组签名产物。
