---
title: Desktop 发布
description: 发布、回下载验证并转正已经验收的 Desktop 安装包和本机设置资源。
---

# Desktop 发布

[#2112 的 09-B](https://github.com/oceanbase/powercontext/issues/2112) 负责发布已经验收的文件。
09-A 提供正常产品构建的原始候选清单，[09-C](managed-resource-production.md) 提供固定版本的资源输入。
平台维护者分别负责 Windows 正式签名和装机验收（[#2108](https://github.com/oceanbase/powercontext/issues/2108)），
以及 macOS 签名、公证、附加公证票据和装机验收（[#2109](https://github.com/oceanbase/powercontext/issues/2109)）。
构建通过、Actions 制品、测试夹具和未签名候选都不能当作正式发布资格。

## 设置发布边界

发布维护者只从受保护的上游 `master` 手动触发 **Desktop release publication**。为
`desktop-release` 环境设置仅允许受保护分支、要求审核人审批、禁止自审以及禁止管理员绕过。
指定发布审核人和各平台验收负责人。保护 `desktop-v*` 标签，防止移动和删除；触发前，将标签指向
`master` 上已批准的 Desktop 提交。流程不主动创建或移动标签，在写入前和回下载后核对标签与提交。
只有审批后的任务获得 `contents: write` 工作流令牌。09-B 不需要签名密钥或个人令牌，
与 Python 发布工作流及其标签独立。

从标签指向的提交上成功手动运行的 `desktop-candidate.yml` 中，选择小型制品
`desktop-candidate-manifest-<version>-<source_sha>`。它只包含原始 `desktop-release-candidate.json`；
完整候选保留供签名和核查。最终文件通过上游 `master` 成功运行的平台签名交接流程提交，
使用名为 `desktop-signed-release-handoff` 的不可变 Actions 制品，例如由受保护的签名工作流用
`actions/upload-artifact` 上传。09-B 不实现签名、不生成平台验收记录，也不接受任意 URL 或来自其他分支、仓库的文件来代替此交接。

## 准备最终文件

Windows x64、macOS arm64 和 macOS x86_64 必须齐全。每个平台都要提交安装包、Server 归档与目录清单、
独立 Ops 归档与包清单，以及预置描述文件。只有 [#2111](https://github.com/oceanbase/powercontext/issues/2111)
以及对应候选和资源生产方能提供同样齐备的合格清单时，才选入 Ubuntu x86_64。Linux 未合格时直接省略，
不要对外宣称支持。

先组装最终资源和目录清单，再签名并验收依赖它们的安装包。macOS 的嵌套代码须在冻结归档和清单前签名。
Server 目录清单使用最终的 `https://github.com/oceanbase/powercontext/releases/download/<tag>/<server-archive>`，
并允许 GitHub 和 `https://release-assets.githubusercontent.com` 两个来源。重新计算目录清单、helper
和预置描述文件的摘要，嵌入最终摘要后签名。从最终安装的应用和独立 Ops 包中读取三个实际摘要并记录；
候选目录清单及其中的占位来源均不可用于发布。在无系统 Python 的干净机上，用选定的 Server 资格配置
验证本机设置、首次使用和恢复。09-B 核对记录与最终字节的绑定，审核人仍须检查平台实际签名和装机验收报告。

签名交接 ZIP 只包含平铺的普通文件，不含目录项，解压体积最多 6 GiB。使用 09-A 各候选路径的文件名
作为正式资产名。其 `desktop-release.json` 包含：

| 对象 | 字段 |
| --- | --- |
| 根 | `format_version: 1`、`kind: "qualified-desktop-release"`、`release_tag`、Desktop `source_sha`、`candidate_run_id`、`candidate_artifact_id`、`candidate_manifest_sha256`、按目标 ID 索引的 `targets`。 |
| 各目标 | `assets` 按 `desktop`、`server_archive`、`server_catalog`、`ops_archive`、`ops_provision`、`ops_bundle_manifest` 索引；各项有 `name`、整数字节数 `size`、小写 `sha256`。`candidate_asset_sha256` 按相同角色记录原始候选摘要。 |
| 各目标的 `qualification` | `kind: "installed-product"`、`signing`（`authenticode`、`developer-id-notarized-stapled` 或 `linux-qualified`）、`clean_machine_provisioning: "passed"`、全量最终 `asset_sha256`、`installed_catalog_sha256`、`installed_ops_provision_sha256`、`installed_ops_bundle_manifest_sha256`、`os_version`、`webview_version`、`server_version`、`runtime_version`（固定 Python 版本）、`limitations`、`report`（上游 issue、评论或 Actions run URL）以及 `server`。 |
| `qualification.server` | `kind: "qualified-product-server"`、候选清单中匹配的产品资格 `id`、上游 `report` URL、`wheel_sha256`、`contract_sha256`、确切的 Server `source_sha`（可以与 Desktop 提交不同）。 |

审核人应检查 Windows 签名者和 macOS Developer ID、公证结果确实覆盖最终安装包及所需可执行资源。
流程会拒绝字节仍与未签名候选相同的 Windows/macOS 安装包。兼容记录必须覆盖实际 Server wheel 和 Desktop 操作。09-A 没有匹配的产品资格记录时，
先对真实输入进行验收；不要改写候选清单或替换 wheel。

## 预发布、重试和转正

1. 手动触发 `prerelease`，输入已有标签、Desktop 提交、候选和签名交接的 run ID 与 artifact ID，
   以及 `desktop-release.json` 的 SHA-256。只读任务先校验输入，在运行摘要列出最终摘要和报告。
   独立审核人检查报告后审批环境。
2. 写入任务先补齐 Release 草稿。同一身份重跑时，已存在且体积和 SHA-256 都相同的文件会保留。
   仅在草稿上允许移除 GitHub 失败上传留下的零字节 `starter` 项。已上传的文件从不替换。
   遇到意外资产、身份或摘要时，停止并排查。如果验收过的字节需要修改，使用新的 Desktop 版本和标签。
   重试期间保留最终签名文件和证据，Actions 制品会过期。
3. 草稿资产齐备且匹配后，才开放为公开 **prerelease**。流程在新目录中从真实公开资产 URL
   无凭据地逐个下载全部文件，包括两份清单和生成的 `SHA256SUMS.txt`，检查体积及 SHA-256。
   回下载失败时保留同一 prerelease；重跑只复验，不改动公开资产。
4. 在每个纳入目标上，从该 prerelease 下载安装包，在干净机器上用已发布资源完成本机设置。
   记录实际安装包 URL 和测试使用的摘要，保留报告。重新交接**同一批最终文件和字节完全相同的发布清单**，
   只增加 `desktop-provisioning-evidence.json`。其根字段为 `format_version: 1`、
   `kind: "downloaded-desktop-provisioning"`、整数 `release_id`、`release_manifest_sha256`，
   以及包含所有已发布目标的 `targets`。各目标有 `download_url`、所有最终角色的
   `asset_sha256`、`clean_machine_provisioning: "passed"` 和上游 `report` URL。
5. 手动触发 `promote`，输入相同发布身份、新交接 ID 和已批准的回下载验收记录 SHA-256。
   独立审核人检查实际下载安装后的证据。流程再次复验公开文件，然后取消 prerelease 标记。
   Release 说明记录验收摘要和链接，资产及标签保持原样。运行失败或取消后不会自动转正。

保留已发布的标签和资源资产，供固定 URL 和摘要的安装程序使用。验收证据须在 Actions 制品到期后继续归档。不要手动替换、删除文件或复用标签来修复发布。
确需撤回时，先与平台和 Installer 负责人安排用户支持和恢复。此流程不自行装机；
本地模拟 HTTP 测试也不能替代 #2112 的真实预发布或下载安装验收。
