---
title: 准备发布版本
description: 通过 Make 更新发布版本，检查独立版本组件，并验证发布产物。
---

# 准备发布版本

使用仓库的 Makefile 更新待发布的 PowerContext 版本及安装说明。Python 发行包的实际版本仍由 Hatch VCS 从
Git 派生。更新待发布版本不会创建 tag、发布软件包或创建 GitHub Release。

下文命令以 `1.2.0` 为操作示例。这些示例不表示当前发布版本，后续发布无需修改本文中的示例数字。

## 更新并检查待发布版本

在发布准备分支的仓库根目录运行：

```bash
make version-bump VERSION=1.2.0
make version-check
git diff --stat
git diff
```

`version-bump` 更新纳入管理的文件，并从 OpenAPI 契约重新生成 Python API 元数据。
它也接受 `1.3.0a1`、`1.3.0b1` 和 `1.3.0rc1` 等预发布版本。纳入管理的安装说明只指明所选版本，
不将其统一称为正式版本。
`version-check` 以 `openapi/powercontext.yaml` 中的 `info.version` 为准检查一致性，不修改文件。
也可以传入包版本或 tag 写法，核对明确的发布目标：

```bash
make version-check VERSION=1.2.0
make version-check VERSION=powercontext-v1.2.0
```

tag 写法用于指定预期版本，不证明该 tag 已存在，也不证明当前检出构建出的包已经是该版本。
提交前检查差异；生成的 Python 文件必须通过生成命令更新，不能手工修改。

正式发布时，在 `website/src/lib/releases.ts` 顶部新增双语条目，填写实际变化、发布日期、安装命令及 GitHub
Release 链接。保留所有已有条目，然后运行：

```bash
make release-check VERSION=1.2.0
```

`release-check` 先运行 `version-check`，安装网站依赖，再检查网站首条记录是否匹配目标版本、是否包含中英文说明，
以及安装命令和发布链接是否使用同一版本。此命令需要仓库指定的 Node/pnpm 环境。网站只列正式版本，因此 alpha、
beta 和 RC 跳过网站条目检查。`make docs-test` 也会通过网站测试运行此项检查。

## 版本位置清单

### 自动同步的版本

| 来源 | 自动管理的内容 |
| --- | --- |
| `openapi/powercontext.yaml` | `info.version`，作为待发布版本。 |
| `release/upgrade-support.json` | 目标发布版本；来源版本和支持策略需要单独进行兼容性审查。 |
| `src/powercontext/http/_generated/` | `operations.py` 的 `API_VERSION` 和 `schema.py` 的 `info.version`，通过 `make api-generate` 重新生成。 |
| `README.md`、`README_CN.md`、`README_JP.md` | 当前版本介绍、固定包版本的安装命令及配套 Agent 集成 tag。 |
| `docs/en/docs/get-started/quickstart.md`、`docs/zh/docs/get-started/quickstart.md` | 当前版本介绍及匹配的安装命令、tag。 |
| `docs/en/docs/get-started/manual-start.md`、`docs/zh/docs/get-started/manual-start.md` | CLI/Server 和 Python Client 安装版本及配套 Agent 集成 tag。 |
| `docs/en/docs/get-started/install-and-run.md`、`docs/zh/docs/get-started/install-and-run.md` | 页面描述、当前版本介绍、包版本和 tag、升级目标及 Python Client 安装命令。 |
| `integrations/dsh/plugins/powercontext/README.md` | Server 安装版本及对应的 `setup dsh --ref` tag。 |

安装指南也包含历史迁移版本，这些引用会保留。新增表示当前发布版本的安装示例时，应同时将其纳入版本工具的
管理路径及匹配规则，使下次发布可以自动更新和检查。

### 从其他来源派生的版本

| 内容 | 来源及处理方式 |
| --- | --- |
| Python wheel/sdist 版本及安装后的 `powercontext --version` | `pyproject.toml` 声明 `dynamic = ["version"]`，Hatch VCS 读取 Git 元数据。最终产物应从目标发布 tag 构建。 |
| Runtime 包元数据 | 读取已安装 Python 发行包的版本，没有另一份需要手工更新的 Runtime 静态版本。 |
| Desktop API 绑定 | 运行 `pnpm --dir desktop generate` 重新生成 OpenAPI 类型和契约摘要，再用 `pnpm --dir desktop generate:check` 检查。Desktop 应用本身使用独立包版本。 |
| Dify 工具契约 | 运行 `uv run python integrations/dify/generate_contract.py` 重新生成，再为同一命令添加 `--check`，检查 API 版本及 Schema。 |
| Docker 和 Bub harness 的构建版本 | 构建输入通过 `POWERCONTEXT_VERSION` 传递选定的包或 Git 版本，不应在 Dockerfile 中加入固定发布版本。 |
| 网站文档与 API 参考 | 从文档和 API 源文件生成，不编辑 `website/content/docs/`、`website/.generated/` 或 `website/out/`。 |
| 依赖锁文件 | 依赖或独立包元数据变化时，由对应包管理器重新生成。仅修改主发布版本，不需要替换锁文件中的版本。 |

OpenAPI 版本描述待发布的 API 契约，不覆盖 Hatch VCS。打 tag 前，开发检出的 Python 包版本可以仍是开发版本，
同时 API 契约已经更新为下一次发布的版本。

### 独立版本的组件

这些组件的行为或发行物发生变化时，应按自身发布要求检查。`make version-bump` 不会将它们统一为 PowerContext
主版本。

| 组件 | 版本位置及检查范围 |
| --- | --- |
| Claude Code 插件 | 发布插件更新时，`integrations/claude-code/plugins/powercontext/.claude-plugin/plugin.json` 与 `.claude-plugin/marketplace.json` 的版本必须一致；同时检查断言 manifest 版本的契约测试。 |
| Codex 插件 | `integrations/codex/plugins/powercontext/.codex-plugin/plugin.json` 与同级 `pyproject.toml` 分别描述插件及其 Runtime 包，插件发布时一起检查，并刷新插件的 `uv.lock`。`plugin_version.py` 从 manifest 派生 Hook 和 Scope 绑定请求的 User-Agent 版本。 |
| 通用 Agent Plugin | `integrations/agent-plugin/powercontext/plugin.json`；其包版本与 Agent Plugins schema 版本相互独立。 |
| Hermes 插件 | `integrations/hermes/plugins/{powercontext,powercontext-command}/plugin.yaml`。 |
| MiniMax 和 ZCode 插件 | `integrations/minimax/plugins/powercontext/.minimax-plugin/plugin.json` 与 `integrations/zcode/plugins/powercontext/.zcode-plugin/plugin.json`。 |
| JavaScript 插件 | `integrations/{dsh,opencode,pi}/plugins/powercontext/package.json` 与 `integrations/openclaw/plugins/memory-powercontext/package.json`；OpenClaw 的发现 manifest 没有独立版本字段。 |
| WorkBuddy 集成 | Hook 和传输脚本包含 User-Agent 字符串，没有需要与 Server 对齐的插件 manifest 版本。 |
| Skill Receiver | `src/powercontext/client/skill_receiver.py` 中的 `RECEIVER_VERSION` 提供注册、协调及回执上报时默认使用的 Receiver 身份版本，独立于 Server 版本。 |
| Python 集成包 | `integrations/{bub,langchain,langgraph,opendal,pydantic-ai}/pyproject.toml` 有各自的发行版本；依赖范围表示兼容性，不是当前主发布版本。 |
| 评测及 harness 包 | `evaluation/pyproject.toml`、`evaluation/coding/swebench_pro/web/package.json`、`e2e/bub/pyproject.toml` 有各自的包版本。 |
| 协议及依赖 | Agent Plugins schema 版本、持久化格式版本、OpenAPI 规范版本、API 路径、宿主最低版本及第三方依赖仅随自身契约更新。 |

通过匹配的 `powercontext-v…` 仓库 tag 安装 Agent 集成，可以选中配套的源码修订，不要求每个插件 manifest 的
数字版本都与 Server 相同。

独立发布插件时，还应审计 Python Hook、脚本及 JavaScript `src/errors.ts` 中含版本的 User-Agent 字符串、
对应契约测试、生成的 bundle 和相关锁文件。这些值可能与 manifest 不一致，主发布版本检查不会验证它们。
DSH 的 `pack:release` 先构建再给包 manifest 写入版本，而写版本脚本只修改 manifest；给打包命令传入新版本，
不会更新已经编译进 bundle 的传输版本。

### 历史记录及测试示例

保留 `website/src/lib/releases.ts` 的已有条目，包括日期、安装命令和 GitHub 链接。正式发布时，为新版本单独
增加条目，写明实际的用户可见变化和升级说明。网站只列正式版本；alpha、beta 和 RC 说明放在 GitHub Releases。
版本工具不会自动编写发布说明。

同样保留迁移引入版本、旧版兼容说明、RFC 基线、历史发布素材及用于验证旧版本或任意版本的测试数据。例如，
`1.1.0` 引入标签表迁移、`1.0.0` 之前数据库需要处理迁移，都是历史事实。测试数据中出现 `1.0.0` 并不意味着
它是遗漏更新的当前版本声明。

DSH README 中说明某项能力已包含在 `1.1.0` 的文字同样属于历史说明，仅其当前安装命令中的版本参与同步。

## 发布前验证

### 数据库升级支持范围

`release/upgrade-support.json` 声明目标版本、来源版本选择策略、后端和配置。`automatic_sources` 继续将 1.3.0
作为固定基线，显式 `cases` 为空。共登记 12 个组合：一个来源 × 三种后端 × 向量开关 × Code 开关。
常规发布要求十个自动验收组合全部通过。两个启用 Code 的嵌入式 seekDB 配置因原生 DDL 等待暂时仅手动运行，
尚未验收，不计入支持的升级声明；自动门禁通过不代表完整的十二项验收通过。
目标始终是本次 CI 构建的实际 wheel；后续目标发布版本继续测试 1.3.0，不自动加入中间发行版。
1.4.0 桥接实现和登记仍保留，但不纳入 CI 升级验收；门禁通过不能证明该路径已通过验收。来源 wheel 的公开 PyPI 地址和 SHA-256 与目标产物一起记录。

1.3.0 的来源链为 `s1300 → s1400 → s1401 → s1402`，1.4.0 为 `r1400 → s1400 → s1401 → s1402`。两者共享目标
契约，分别保留冻结的执行历史及日志来源链，以便后续已登记版本继续升级。应用版本不等于 schema 检查点；1.4.0
的 Candidate 已是当前格式，桥接原样保留记录，并创建空保留表以统一目标结构，不虚构旧 Candidate 历史。
登记其他来源需要精确采集、明确桥接和真实验收；这两条已登记路径不能证明其间及目标之前所有发行版均兼容。
当前 `upgrade-required` 策略强制执行三种后端的正向升级验收，同时保持“升级支持尚未验证”的声明。
所有配置均使用 `tests/release_upgrades/test_full_server.py`，迁移能力缺失必须失败。仅支持新建数据库的声明或
正确拒绝旧库，都不能满足门禁。完整迁移实现和验收通过后，审查支持范围，将模式改为 `supported-upgrades`，
并在发布前重新生成安装说明；两种模式都运行相同的必测矩阵。
验收契约和证据要求见仓库中的 `tests/release_upgrades/README.md`。

`make check` 和 `make release-check` 会核对策略及双语安装说明。受管持久化文件变化时，必须审查兼容性影响，
更新摘要和理由。摘要用于触发审查，本身不证明迁移正确。受管文本的哈希会将 CRLF 归一为 LF，避免检出行尾
差异误报兼容性变更；发行包哈希仍覆盖完整原始字节。`make version-bump` 同时更新策略中的目标版本；
支持矩阵变化后，运行 `uv run python scripts/upgrade_release_gate.py --write-docs` 更新安装说明中的支持范围。

将唯一的目标 wheel 和配套源码包构建到 `dist/` 后，执行声明的升级组合验收：

```bash
make upgrade-release-test
uv run python scripts/run_upgrade_release_gate.py --release --release-notes /path/to/release-notes.md
```

发布输入目录必须只有一个 wheel 和一个配套的 `.tar.gz` 源码包；残留发行包和其他文件都会使检查失败，
`uv` 生成的 `.gitignore` 不作为发行产物。门禁检查源码包元数据，并从该源码包实际重建 wheel，逐项比较归档中的
文件内容，仅排除派生的 `RECORD` 哈希清单。因此，同版本但内容过期的源码包也无法绕过检查。归档时间戳和压缩
方式不影响比较；构建工具变化导致内容不一致时，需要重新构建整组产物，不能忽略差异。

门禁验收输入文件的独立副本，在 `publication.json` 中记录实际 SHA-256，并且只有全部通过才将文件输出到证据目录
下的 `validated-dist/`，供 CI 上传。`make publish` 使用 `--release --publish` 在同一次调用中上传这些确切路径；
验收期间原 `dist/` 的变化不会混入上传。未指定 `--publish` 的验收命令不会发布软件包。

发布检查要求实际 wheel 版本与策略一致，发布说明包含安装指南中的完整英文支持声明。每个组合分别安装准确的
旧版发行包和目标 wheel。测试缺失、后端跳过、执行失败或证据缺失都会阻断检查。仅通过静态策略检查不表示
存在升级路径。普通 PR 构建允许使用开发版本；`--release` 必须验证最终 tag 构建的 wheel，不能覆盖版本号。

CI 中的 **Upgrade release gate** 要求真实 1.3.0 数据库在十个必测自动组合中通过公开命令迁移、
完整 Server 就绪验证、目标版本启动、历史数据读取和新增写入。局部迁移验证返回 `server_ready=false` 也会失败。一个后端失败后，仍继续执行其他后端，
分别保存结果和日志。seekDB 直接在 runner 临时目录中以嵌入式启动；PR 和发布工作流均启动隔离的 OceanBase CE
容器，等待 MySQL 租户就绪，归档诊断并在测试失败时也清理容器，不依赖外部数据库凭据。
任一后端无法启动都使检查失败，不能跳过。历史拒绝行为回归不参与正向升级门禁。
仓库管理员需要在分支保护中将该检查设为必需。
默认发布工作流只有验收通过才会上传发行包、发布 PyPI 和上传 Release 附件，并归档验收证据。工作流在
`release.published` 后运行，无法阻止之前手动发布 GitHub Release 页面，因此必须先运行上述命令再发布页面。
`make publish` 要求提供 `UPGRADE_RELEASE_NOTES=/path/to/release-notes.md`，并在上传软件包之前执行相同门禁。

### 手动跳过升级验收发布

**Release** 工作流提供 `workflow_dispatch` 参数 `skip_upgrade_acceptance`，默认 `false`。
只有明确按“仅供新建数据库使用”发布、不声明已有数据库可升级时，才启用此选项。它不修改
`release/upgrade-support.json`，不证明迁移路径可用，也不放宽默认发布流程和 PR 的升级门禁。
启用时，目标版本的策略必须仍为 `upgrade-required`。

对于已经发布的 GitHub Release，例如 `powercontext-v1.4.1`：

1. 在 Release 正文中加入以下英文原文；工作流会检查该声明：

   ```text
   Upgrade acceptance was skipped for this release. Use only new databases; upgrades of existing databases are not supported.
   ```

   中文可同时写明：本次发布跳过了升级验收，仅供新建数据库使用，不支持已有数据库升级。

2. 打开 **Actions → Release → Run workflow**，选择仓库默认分支 `master`，填写
   `release_tag=powercontext-v1.4.1`，勾选 `skip_upgrade_acceptance`。工作流修改需要先合并到默认分支。
   通过参数指定目标 tag，工作流分支选择器仍选默认分支。

   ```bash
   gh workflow run release.yml --repo oceanbase/powercontext --ref master \
     -f release_tag=powercontext-v1.4.1 -f skip_upgrade_acceptance=true
   ```

工作流将已有 tag 解析为提交并构建该提交，不移动 tag，也不打包当前默认分支。声明从 GitHub Release
实时读取，不复用失败运行的旧事件正文。版本一致性、发版引用同步、策略一致性、发行包元数据、源码包
重建对比、干净环境安装、依赖检查以及新库 Server/MCP/Memory 冒烟验证仍须通过，之后才会将这些确切产物
发布到 PyPI 并上传 Release 附件。发布后验证和官网部署继续执行，并使用选定的发布目标。

`release-without-upgrades` 附件记录 tag、源码提交、操作者、运行编号、发行包哈希及
`upgrade_acceptance: skipped`；运行摘要也会注明已有数据库升级不受支持。
未勾选该选项时，仍执行常规自动升级矩阵及支持声明检查。

### 通用发布验证

针对本次发布的完整变化运行检查，包括生成的契约和文档：

```bash
make release-check VERSION=1.2.0
make contract-test
make check
make test
make docs-test
make build
uvx --from twine twine check dist/*
```

网站检查应使用受支持的 Node/pnpm 环境。独立更新插件或适配器时，还需运行对应集成检查。根据实际变化验证
兼容性和数据迁移；版本一致性检查本身不证明向后兼容。

在目标发布 tag 的干净检出中，构建最终产物前检查实际包版本：

```bash
uvx --from hatchling --with hatch-vcs hatchling version
```

输出必须与 tag 表示的包版本一致。不能用 pretend-version 覆盖值来证明该 tag 能生成正确版本。
在独立环境中安装构建产物，并运行 `scripts/ci_release_smoke.py --version <package-version>`，验证 CLI、
Server、MCP 和 SQLite 行为。

仓库的 `.github/workflows/release.yml` 在 GitHub Release **发布**时触发，在构建和发布 PyPI 包前运行
`release-check`，并检查 tag 与 Hatch VCS 版本一致性。随后构建和检查发行包、发布到 PyPI、上传附件并执行发布验证。
仅推送 tag 不会触发该工作流。
创建 tag、发布 GitHub Release 是准备与审阅完成之后的独立发布操作。

发布 tag 存在后，检查 `integrations/capabilities.toml` 中的可用性声明。`released` 条目必须指定已存在的
`release_tag`，且该 tag 应包含声明的实现及证据；这与插件自身版本是不同的概念。支持契约未改变时，试验性集成
继续保持试验性。在本地和 CI 检出中获取所引用的 tag 后再运行集成 manifest 检查，使用
`make integration-manifest-docs` 重新生成双语能力矩阵，并同步相关集成概览和评测说明。不要仅为通过可用性检查
而创建或移动发布 tag。
