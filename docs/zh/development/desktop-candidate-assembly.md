---
title: 桌面端候选组装
description: 为 Windows x64 和两种 macOS 架构组装身份固定、尚未取得发布资格的桌面端和受管资源候选。
---

# 桌面端候选组装

[Issue #2112](https://github.com/oceanbase/powercontext/issues/2112) 将候选组装、受管资源生产和发布拆开。**Desktop candidate assembly** 工作流使用正常产品配置打包，并复用[受管资源生产器](managed-resource-production.md)。输出是供检查和交接的未签名 Actions 构件，不能作为正式发布件，也不能证明已通过安装验收。

## 构建身份

发布负责人在上游明确选择 `master` 提交，手动触发 `desktop-candidate.yml`，并提供：

- `version`：桌面端的精确 `X.Y.Z` 版本。计划使用独立于 Python 发布的 `desktop-vX.Y.Z` 标签，应用标识为 `com.powercontext.desktop`。
- `server_version`：Server 的精确 Python 包版本（例如 `1.5.1.dev25+g63e9665c6`）。生产器从指定的 Server 提交按锁定输入构建，版本可与桌面端不同，构建过程不会从包索引推断版本。
- `source_sha`：完整 Git 提交，必须与工作流实际检出的提交一致。如果标签已经存在，它必须指向同一提交。候选组装不会创建标签。
- `server_source_sha`：完整 Server 提交，可选用较早且已有资格记录的版本。wheel 及依赖锁来自该提交，Desktop 打包及生产器工具仍绑定 `source_sha`。

桌面端版本各段为 0 至 65535 的整数，且无前导零。工作流从干净目录构建 release 包，不启用 `ci-fixtures`，并嵌入生产器的精确目录和 Ops 摘要。Windows x64（NSIS）、macOS arm64（DMG）和 macOS x64（DMG）均为必需。候选不包含 Linux。以后如需发布 Linux，由 [#2111](https://github.com/oceanbase/powercontext/issues/2111) 提供独立验收过的构件，不影响三个必需目标的组装。

## 候选交接

从同一次成功运行下载 `desktop-candidate-<version>-<source_sha>`。汇总任务只接收同一次运行中指定平台任务的产物，不接收内部预览包、测试资格清单或其他运行的上传件。每个目标目录下的安装包为 `powercontext-desktop-<version>-<target>.exe` 或 `.dmg`。同目录还包含 Server 和 Ops 归档、资源目录、配置描述、输入锁文件、生产清单、实际 Server wheel 和烟测记录。目标标识为 `windows-x86_64`、`macos-arm64` 和 `macos-x86_64`。

`desktop-release-candidate.json` 包含 `schema_version: 1`、`version`、`tag`、`source_sha`、`server_source_sha`、`server_version`、`status: unqualified_candidate` 和 `publishable: false`。`contract` 记录桌面端 HTTP 契约摘要、操作集，以及随候选交接的操作与兼容性文件。`targets` 中每个目标有 `package`、`resources`、`provenance`、`qualification` 和 `runtime`。`assets` 中的四种发布角色为 `desktop`、`server_archive`、`server_catalog` 和 `ops_archive`。文件条目包含相对候选根目录的 `name`、字节数 `size` 和小写 `sha256`。Ops 配置描述和 bundle 清单位于 `provenance`，文件名按目标区分。`qualification.producer_ref` 始终指向未取得资格的生产记录。`matching_product_records` 只列出与实际 wheel、源码、契约及操作集匹配的内置资格记录；空列表表示仍需补产品协议资格。即使有匹配记录，也不能视为安装包通过验收。

交接发布时选择 `desktop-candidate-manifest-<version>-<source_sha>`，其中只有字节完全相同的 `desktop-release-candidate.json`。运行摘要列出 `candidate_run_id`、`candidate_full_artifact_id`、`candidate_manifest_artifact_id` 和 `candidate_manifest_sha256`。在发布交接中用清单构件 ID 填写 `candidate_artifact_id`，发布流程即可单独取清单，无需下载大型归档。两种 Actions 构件均保留 14 天。

如需检查完整候选，从可信运行的摘要取得候选清单 SHA-256，再校验下载件：

```sh
uv run --locked --no-dev --group resource-build python scripts/desktop_candidate.py verify --root <下载目录> \
  --version <桌面版本> --server-version <Server版本> --source-sha <桌面提交> \
  --server-source-sha <Server提交> \
  --manifest-sha256 <运行摘要中的SHA-256>
```

摘要绑定清单和构件的实际字节。信任仍来自对应上游提交的这条工作流及其成功运行，应记录运行和构件身份。同名重跑的字节可能不同，不能悄悄替换已经接受的候选。Actions 候选保留 14 天，不能作为已安装客户端的资源下载渠道。

## 发布前验收

CI 消费实际目录和归档，用随附 Python 启动一次性的 SQLite Server，并通过独立 Ops 入口检查本机状态。这不包括真实 HTTP 下载或注册服务。候选资源目录特意指向 `candidate.invalid`。

最终平台打包负责人负责 Windows x64 签名和 macOS 双架构签名、公证，将获准的独立 Ops helper 安装到[当前用户固定位置](../docs/operate/local-server-ops.md#配置桥接资源)。卸载 Desktop 必须保留独立 helper、服务状态和用户数据。Windows 保留供 Server 服务使用的 `pythonw.exe` 及用于控制台的 `python.exe`。候选包嵌入资源摘要但不安装独立 helper。helper 配置、干净机安装修复、重启或登录、原生服务生命周期由 [#2108](https://github.com/oceanbase/powercontext/issues/2108) 和 [#2109](https://github.com/oceanbase/powercontext/issues/2109) 负责验收。

最终平台构建应使用经批准的不可变资源 URL；签名改变嵌套代码字节后，重新生成受影响的文件清单、归档、资源目录和配置摘要。把最终摘要嵌入 Desktop 后再签外层包。记录候选清单摘要，并单独记录经过验收的最终包与资源摘要；候选与签名件的字节可以不同。最终验收须在同一 Desktop 和 Server 组合上覆盖身份、Scope 与 Memory、统计、运行观察，以及本机设置直到默认 Scope。不得用重建 wheel 或测试专用构件替代 [#2101](https://github.com/oceanbase/powercontext/issues/2101) 资格。发布和回下载使用同一批最终签名并通过验收的字节。
