---
title: 桌面端产品资源
description: 为产品构建准备固定摘要的独立 Ops 运行时和长期可下载的 Server 资源目录。
---

# 桌面端产品资源

[候选工作流](desktop-candidate-assembly.md) 产出未取得发布资格的构件。产品打包还需要随应用携带独立 Ops 运行时，并让 Server 资源目录指向获准的长期下载地址。`scripts/prepare_desktop_product.py` 准备这些输入，不发布资源、不安装 Server，也不将组装结果视为平台验收。

## 准备精确输入

使用目标原生平台的完整[资源生产器输出](managed-resource-production.md)：`candidate.json`、`server/catalog.json`、`server/server.tar.gz` 和展开的 `ops/`。这里要求生产器的原始输出目录，不是文件已重命名的桌面端汇总构件。保留生产证据，并从可信生产运行取得 `candidate.json` 的 SHA-256；与不可信下载一起取得的校验文件不能独立建立信任。

从 `desktop/src-tauri/src/connections/compatibility.json` 选择已纳入仓库、ID 不以 `ci-` 开头的资格记录。Server 提交、实际 wheel 摘要、HTTP 契约摘要和操作覆盖范围必须与资源及 Desktop 检出版本匹配。没有匹配记录时，先按 [#2101](https://github.com/oceanbase/powercontext/issues/2101) 对该精确 Server 构建取得资格，不能复制测试记录或直接修改未验收候选的标签。

在客户端使用前，将准确的 Server 归档发布到获准的不可变 HTTPS 构件地址。允许列表应包含初始来源和经过批准的重定向来源。带签名查询参数的临时地址、占位域名、Actions 构件下载和可变的 `latest` 路径不适用。脚本拒绝查询参数和保留的占位域名；地址的不可变性、来源策略、保留期及实际可下载性仍由发布负责人确认。脚本不发网络请求。Ops 继续使用现有归档大小、摘要、文件清单检查和中断下载恢复能力。

在每个原生构建平台，从仓库根目录执行：

```sh
uv run --locked --no-dev --group resource-build python scripts/prepare_desktop_product.py \
  --resources /absolute/producer-output \
  --candidate-sha256 <可信生产运行的candidate摘要> \
  --output /absolute/product-resources \
  --version <桌面端X.Y.Z> \
  --server-url <已批准的不可变Server归档HTTPS地址> \
  --allowed-origin <获准的HTTPS来源> \
  --qualification <内置产品资格记录ID>
```

重定向来源通过重复 `--allowed-origin` 提供。每次使用全新输出目录。脚本验证生产绑定、实际 Server 归档字节、完整 Ops 文件及可执行入口、目标身份和精确产品资格；复制 helper，更新目录的 URL 和资格引用，固定新目录及 Ops 清单摘要，全部验证后才原子发布暂存目录。输入保持不变。输出包括：

- 应用资源目录所需的 `ops/`、`server/catalog.json` 和 `ops-provision.json`。
- `tauri.product.conf.json`，包含产品标识 `com.powercontext.desktop`、选定的 Desktop 版本，以及包含隐藏文件的显式资源映射。构建结束前保持暂存目录的绝对位置不变。
- `product-resources.json`，记录生产和资源绑定及选定资格。签名和安装验收明确标记为 `not_performed`；它不是正式发布清单。

从 `desktop/` 执行，不启用 `ci-fixtures` 或预览特性：

```sh
pnpm install --frozen-lockfile
pnpm tauri build --config /absolute/product-resources/tauri.product.conf.json --ci --no-sign -- --locked
```

Tauri 自动应用仓库中的 macOS 平台配置；Windows 使用仓库中的当前用户 NSIS 配置。不要同时合并 `tauri.ops.conf.json`，后者仅为旧的外部预配置 helper 暂存摘要。未签名构建只能检查组装，不能证明干净机安装、服务生命周期或发布资格。签名改变嵌套代码字节时，应重新生成生产清单与绑定、重新准备最终资源，再签外层应用。[#2108](https://github.com/oceanbase/powercontext/issues/2108) 和 [#2109](https://github.com/oceanbase/powercontext/issues/2109) 负责平台验收与签名；[#2112](https://github.com/oceanbase/powercontext/issues/2112) 负责发布最终验收过的字节。

## 运行时部署和恢复

首次使用 Ops 时，原生代码先按应用固定摘要验证随包 `ops/`，再复制到操作系统当前用户本地数据目录下的 `PowerContext/Ops/bundles/<清单SHA-256>`。使用私有临时目录和跨进程锁，完成复制和校验后才重命名到位。并发部署返回 `busy`，可以重试。解释器来自验证后的目录，用户不需要全局安装 Python 或 `uv`。Server 安装和服务变更仍通过现有安装 UI 及计划／批准流程执行。

匹配且完整的缓存会直接复用。缓存损坏时，只有完整替换副本通过校验，旧副本才移到 `damaged-<uuid>`。随包资源损坏时，即使旧 helper 存在也拒绝执行，应重新安装可信 Desktop 包修复来源。不含 `ops/` 的应用保留旧的 `PowerContext/Ops/bundle` 行为；空目录或损坏的 `ops/` 不会触发回退。

卸载 Desktop 时必须保留独立 helper 副本、操作引用／日志、Server 注册和用户数据。旧摘要目录和损坏副本保留，此实现不自动清理，也不授权删除可能仍在运行的旧版本。因此，每个不同 helper 版本占一份空间，另加隔离的损坏副本。每次启动读取完整资源清单，包括随包来源与已部署 helper；不宣称满足启动性能预算或通过干净机验收。现有 Ops 下载、修复、状态、取消和恢复契约保持不变。
