# PowerContext Desktop 预览版

## macOS 原生更新

macOS 的“检查更新…”由 Sparkle 2 提供原生窗口、下载、Ed25519 校验、跳过版本、退出时安装与重启。开发机先运行 `bash desktop/scripts/setup-sparkle.sh` 下载并校验固定版本的原生框架，再将 `desktop/src-tauri/Info.plist.example` 复制为 `Info.plist`，填写 HTTPS appcast 地址和 Sparkle 公钥，最后用 `pnpm --dir desktop desktop:build:macos` 构建。私钥只保存在发布钥匙串或 CI 密钥系统中，不得提交到仓库。

`tauri dev` 不在 `.app` 包内，Sparkle 不会显示更新窗口；请使用已签名、已公证的旧版本应用对真实 appcast 进行验收。

这是 #1654 的 Windows 和 macOS 内部工程预览。已建立 Tauri 2、React、TypeScript 和 Vite 独立工程，提供首页、连接、我的记忆、双语设置及浅色／深色／系统主题。无连接时保存和搜索不可用，不展示示例业务数据。

已实现连接配置、显式激活、身份与就绪检查、精确范围选择和本地诊断。已实现共享笔记表单、FTS 搜索和精确版本阅读；原生交互闭环仍待完整验收。当前不是已签名的正式发行版。

丢弃草稿、移除连接配置和未知写入再次提交均使用应用内双语确认框。初始焦点位于“取消”；Escape 或点击遮罩会取消操作，Tab 保持在对话框内，确认后仅执行所请求的操作。Scope 选择使用相同的模态交互；关闭后焦点返回实际触发按钮，按钮不可用时返回页面标题。

## Local Server 准备说明

默认启动页为总览。顶部“开始使用 PowerContext”引导提供“查看本机设置”和“添加连接”。前者打开并聚焦同一个“设置与诊断 → Local Server”准备说明页；当前预览尚未接入安装和恢复，相关集成仍由 [#2306](https://github.com/oceanbase/powercontext/pull/2306) 推进。后者进入现有连接编辑器。已保存配置可通过选择器精确打开，核对、检查后再明确启用；重新打开 Desktop 不会恢复私有连接或 Scope。

连接激活后，引导打开共享的精确 Scope 选择器，只有明确选择成功才进入记忆页。已有 Scope 时，显示实际连接和完整 `scope_id`，提供现有保存、搜索、精确读取和复制流程的入口，并说明不可用操作。“连接之后”三步卡描述操作路径，不追踪首次记忆完成情况、使用次数或完整库存。“收起引导”会收起教程并保留下一步操作和恢复入口，“重新查看引导”可展开；该偏好仅保留到 Desktop 关闭。入口沿用键盘焦点、草稿丢弃确认、中英文和主题设置。

记忆页未连接时，“本地设置”进入并聚焦“设置与诊断 → Local Server”；“连接已有 Server”进入现有连接编辑器。选择任一路径都不会安装软件、激活连接、执行诊断或探测就绪状态。

也可直接进入设置查看 Local Server 说明；即使原生状态正在加载或不可用，说明仍可访问。区域明确标注 Desktop 安装、恢复及服务管理尚未接入，不据此判断本机是否已安装 Server 或服务是否运行。侧栏设置入口保持在底部。

请按[安装与运行](../docs/zh/docs/get-started/install-and-run.md)及[部署 Server](../docs/zh/docs/operate/deploy-server.md)独立准备 Server，并遵守文档中的平台适用范围。确认实际地址、认证方式和经过验证的 Desktop 兼容配置，再保存、显式激活连接并选择精确 Scope。Server 已运行不代表 Desktop 兼容性已验证；已有独立运行的 Server 无需重复安装。应用内“使用帮助”可复制本说明的文档地址。

## 直接运行安装版

在本分支的 GitHub Actions「Desktop validation」成功运行中，下载 `desktop-windows-internal-unsigned` 产物并解压，找到 `src-tauri/target/release/bundle/nsis/` 中的安装程序。对应 `.artifacts/windows-smoke.json` 记录提交、安装包 SHA-256 和签名状态，可用 `Get-FileHash -Algorithm SHA256 <安装包路径>` 核对下载文件。当前产物未签名，供内部验证。

运行安装程序后，从开始菜单打开「PowerContext Desktop Preview」。不需要先启动 Vite、Python 或本地 Server；首次打开没有活动连接，按下一节连接已有 Server 后才能保存和搜索。没有可用 Server 时仍可查看界面与设置。开发模式请使用下方命令。

在 Windows「已安装的应用」中卸载 Desktop。卸载不负责删除 Server、业务数据库或 Agent 配置；不要把清理 Server 数据作为卸载桌面的步骤。

同一通道重复启动会唤醒已有窗口。应用运行期间独占连接配置存储，防止多个实例互相覆盖已保存的连接。旧保存任务完成后，不会清除新页面或新 Scope 中草稿的未保存提醒。

## 开发与构建

Windows 11 x64 上需要 Node 24.14.1、pnpm 11.13.1、Rust 1.95.0 MSVC、JDK 11 或更高版本（确保 `java` 位于 `PATH` 中）、Visual Studio C++ Build Tools、Windows SDK 和 WebView2。安装后的用户程序不依赖 Node、Rust、Python 或本地 Server。

`generate` 和 `generate:check` 会启动基于 Java 的 OpenAPI Generator。首次运行时，`openapi-generator-cli` 可能从 Maven Central 下载固定版本的 Generator JAR，因此首次生成或漂移检查需要网络访问；后续运行可以复用已下载的 JAR。

从仓库根目录执行：

```powershell
rustup toolchain install 1.95.0-x86_64-pc-windows-msvc --profile minimal --component clippy --component rustfmt
$env:RUSTUP_TOOLCHAIN = '1.95.0-x86_64-pc-windows-msvc'
pnpm --dir desktop install --frozen-lockfile
pnpm --dir desktop desktop:dev
```

`pnpm --dir desktop build` 只构建前端。`pnpm --dir desktop desktop:build` 构建原生 release 程序和当前用户 NSIS 安装包，输出到 `desktop/src-tauri/target/release/bundle/nsis/`。完整检查入口见 [英文 README](README.md)。

缺少 WebView2 时，安装器配置为下载 Microsoft bootstrapper，需要网络。标准用户、无 WebView2、无 Python 的干净机器场景必须在隔离环境验证，不能通过卸载开发机依赖来模拟。

## 连接已有 Server

从首页选择「已有服务」，或在「服务与 Agent」打开连接对话框。填写服务地址并选择认证方式。远程服务使用 HTTPS，本机 HTTP 使用字面回环地址。选择与部署匹配的[已验证兼容配置](src-tauri/src/connections/compatibility.json)。Bearer 凭据可保存在系统凭据库，也可仅用于本次会话。

确认连接后，在顶部空间菜单选择可访问的空间。桌面端核对目标和凭据，通过对应资格校验的操作才可使用。

## 显式就绪探测与连接身份

保存、检查、激活、刷新及普通 Scope／Memory 操作均不探测模型或依赖的就绪状态。
检查已保存配置后，可点击带有说明的“探测就绪状态”执行可选诊断；探测时间与连接检查时间分别记录。
探测失败不会授予或撤销业务权限、激活连接、切换 Scope 或丢弃输入。打开页面和重开 Desktop 均不会执行此操作。
探测等待期间，包括离开连接页后，独立获准的 Scope 和 Memory 操作仍可使用；诊断错误仅关联被探测的连接配置版本。

公开操作 `get_connection_identity` 在不探测 readiness、不要求 `server.observe` 的情况下返回实际部署、请求 Principal、
认证状态和 Access 模式。匿名 loopback 配置要求该操作有匹配的验证记录，且明确返回认证／Access 已禁用、Principal 为空。
通用 503、选择“无 token”或先前的 readiness 结果均不能确立匿名身份。旧 Server 缺少已验证的此操作时，匿名使用需要升级；
独立验证过的已认证旧版配置继续复核 Principal。身份／认证失败不会触发降级。部署、Principal、认证或 Access 模式变化会使
活动上下文失效。能力观察被拒绝与已验证、逐项获得授权的 Memory 操作分别处理。

## 原生发现结果

连接检查通过现有原生传输调用公开的 `get_server_info`。`CheckReport.discovery` 返回 `supported`、`missing`、`denied`、`unavailable`、`invalid` 或 `incompatible`，并包含观察到的 Server 元数据、安全错误，以及逐项操作的 `qualified`／`unsupported`／`unverified` 状态和原因。兼容的发现协议 minor 版本可增加响应及其嵌套版本、功能对象的可选字段。

操作资格要求明确选择与当前契约匹配的配置。如果配置的 Bearer 客户端列表包含当前 Desktop 契约摘要，也可以使用该配置。这要求使用 Bearer 模式并取得有效 Principal，适用配置中记录的操作集。仅发现接口的 404 允许沿用该配置已验证的旧版操作；401、403、503、无效响应、不兼容产品或必要 major 版本不会触发旧版回退。缺失功能组、不支持的功能版本和缺失 operation ID 分别限制受影响的操作。Artifact 精确读取需要独立资格，不能从 `memory.explicit` 推断。发现元数据不授予权限，也不证明远端二进制身份。

当前配置必须使用 `get_connection_identity`；发现响应未声明身份操作时，不能降级为旧版 Principal 检查。已验证的观测接口返回 403 时，成功的轻量身份检查仍可保留独立获授权的操作。Scope 列举、默认范围查找、范围选择、Memory 和统计均通过该身份接口，在发送请求前和返回私有结果前复核部署、Principal、认证和 Access 模式。身份变化或复核失败会清除活动上下文，使 Scope 选择、游标和进行中的读取失效；旧版配置保留发现接口和 Principal 的复核约束。须明确重新连接以建立新上下文。仅软件包版本变化不视为部署变化。

已提交写入保留原始端点、`serverId`（已验证旧版 Server 为 null）、Principal、Scope 和代次。写入后的身份复核失败时，即使 HTTP 返回成功也按结果未知处理：不返回私有正文或引用，不自动重放。写入恢复反馈显示原部署；断连后原始输入在当前窗口内作为只读恢复草稿保留，可明确复制或丢弃，不会填入新目标的表单，也不持久化；关闭窗口会丢失草稿。确认离开页面会丢弃草稿，晚到响应不能恢复已明确丢弃的输入。旧写入晚到不会使新激活的上下文失效。已验证旧版会话要求发现接口持续返回 404；发现接口出现后须明确重新检查，不能静默获得部署身份。连接复查失败会移除旧缓存报告，避免继续显示过期的验证结果。

应用从调用原生提交开始，在内存中独立保留每次输入，不依赖表单是否仍存在。因此，并发读取触发自动断连时，即使写入还在等待回执，仍有只读副本可恢复。恢复界面区分进行中、结果未知、保存被拒绝和未取得回执的尝试。重试不会丢弃先前的未知输入，包括重试的写请求尚未发出就失败的情况。每次尝试有独立的本地标识：成功回执只清理该次输入，保存被拒绝时仍保留正文供明确恢复，明确丢弃只移除所选副本，确认离开页面则丢弃所有副本；晚到回执不能恢复已丢弃输入或清除另一尝试。每次输入最多 8192 UTF-8 字节；尚未成功保存的尝试会保留副本，直到成功、明确丢弃或关闭窗口。副本仅含用户输入及观察到的原目标元数据，不含 Server 返回的私有正文，不重放，也不持久化。

这些检查观察请求前后的身份；公开写入契约没有以 Server 身份为条件的前置约束，因此不能原子性阻止验证与发送之间的端点替换，也不能发现两次观察之间发生后又恢复的替换。旧版 Server 没有持久部署隔离保障；发现信息也不证明二进制身份。连接检查和普通身份复核不调用 readiness；就绪探测由独立诊断动作显式触发。

连接页分别展示发现状态和兼容性结论，说明已独立验证的观测权限拒绝，以及产品或 major 版本不兼容的原因。其他发现失败时业务操作保持不可用，但不会删除已选择的资格记录。修正连接或服务后，须明确重新检查连接。仅发现接口的 404 适用上述已验证旧版策略。就绪探测等待期间仍可检查或启用其他连接；切换上下文会使旧诊断结果失效，但不保证停止 Server 已在执行的工作。

保存、搜索、精确版本读取、范围查找和范围选择分别要求 `supportedOperations` 包含各自的操作，总体兼容性结论不会启用其他操作。不可用的操作会说明功能缺失、功能版本不兼容、未声明操作支持或缺少独立验证记录的原因。其他独立支持的操作仍可使用，总览也逐项展示记忆功能的可用性。原生层在请求时执行的授权和兼容性检查仍是最终依据。

工程边界：

- OpenAPI 自动派生的 TypeScript schema 和十三项 operation 映射，包含契约摘要及漂移检查。
- `desktop/openapitools.json` 固定 OpenAPI Generator CLI 版本，Rust 模板位于 `desktop/scripts/openapi-generator/rust/`。修改 OpenAPI 合同或模板后，在仓库根目录执行 `pnpm --dir desktop generate`，不要手动编辑生成的 `src-tauri/src/transport/wire.rs`；CI 通过 `pnpm --dir desktop generate:check` 检查生成结果是否漂移。
- Rust 自动派生的 IPC 类型与安全错误；凭据只写请求必须显式选择持久化或仅会话，成功回执只含存储模式，不含秘密或凭据标识。
- Windows Credential Manager、macOS Keychain 和 Linux Secret Service 原生适配器；保存失败直接报错，只有显式选择才采用仅会话内存，绝不把秘密降级写入连接配置。秘密类型不支持序列化，不实现 Debug。
- 使用系统证书信任的原生 HTTPS；可为单个客户端添加显式 CA，继续校验主机名。
- HTTP 仅允许 loopback，拒绝地址中的用户信息、查询、片段、反斜杠及路径逃逸；不继承代理、不跟随重定向。
- 连接超时 5 秒、完整请求超时 15 秒、响应上限 1 MiB、每个客户端最多 4 个并发读取；超限明确失败。
- 主窗口可调用明确列出的连接、Scope、记忆和诊断命令；没有通用 fetch、shell、文件、数据库或读取秘密的 IPC。

应用不启动 Server、不管理服务、不持久化正文或查询；关闭窗口即退出。当前语言和主题也仅保留于会话内。

[安全边界](SECURITY.md)记录具体约束。构建成功、Mock 测试和当前用户机器上的运行都不能代替标准用户、签名、通知冷启动激活、独立服务和 Agent 宿主验收。

## 本机 CLI 诊断

设置中的诊断只检查当前电脑，与远程连接状态分开。程序不从 PATH 自动选择 CLI，也不安装或启动 Server。集成检查可能运行临时 Agent 辅助进程；结果不代表实际 capture/recall 已验证。

显式信任某个本地 PowerContext 安装后，在 `%APPDATA%/com.powercontext.desktop.preview/diagnostic-cli.json` 写入下面的配置。`executable` 必须是 `powercontext.exe` 的绝对路径，`sha256` 是该文件经核对的 SHA-256。无需配置版本号；已有配置中的 `version` 字段会被忽略。兼容性由诊断响应是否符合适配器支持的输出格式决定。程序文件发生变化时，需要更新登记的摘要。不要把文件摘要匹配当作发布者签名认证；Python 安装及其依赖也必须来自你信任的环境。

```json
{
  "executable": "C:/trusted/powercontext/Scripts/powercontext.exe",
  "sha256": "填写经核对的64位SHA256摘要",
  "source": "explicit_local_installation"
}
```

在 Linux 上，配置文件放在 `$XDG_DATA_HOME/com.powercontext.desktop.preview/diagnostic-cli.json`；如果没有设置 `XDG_DATA_HOME`，则使用 `~/.local/share/com.powercontext.desktop.preview/diagnostic-cli.json`。`executable` 必须是名为 `powercontext` 的文件的绝对路径（不是 `powercontext.exe`），`sha256` 必须是经核对的 64 位摘要，`source` 必须为 `explicit_local_installation`。

Desktop 在每次诊断前检查固定路径和摘要，然后只允许 `service status --json` 或 `doctor codex claude-code dsh openclaw opencode pi hermes workbuddy kimi-code zcode --json`。收到符合格式的诊断响应后，尝试读取实际 CLI 版本用于显示；版本查询失败时显示“未知”，不阻止有效诊断结果。版本查询最长 15 秒，服务检查最长 20 秒，集成检查最长 60 秒；每次进程调用的 stdout/stderr 合计最多 256 KiB。Windows 辅助进程运行在专属 Windows Job 中；Linux 和 macOS 辅助进程运行在专属 POSIX 进程组中。完成、超时或取消时会清理辅助进程及其后代，不影响其他无关进程。界面只显示允许的状态字段，不显示任意 detail、路径或原始输出。合法 JSON 配合退出码 1 仍可显示不健康结果。

未配置或未通过核验时，本机诊断不可用，独立远程业务仍可使用。进程树清理和隔离真实 CLI 测试已通过；安装后交互与完整 S2 资格仍待验收。

## 保存、搜索和阅读记忆

在「记忆」中点击「录入记忆」，确认目标服务和空间后保存。输入关键词可搜索当前空间，服务每次最多返回十条结果。打开结果可读取对应的精确版本。如果保存结果未知，先在原空间搜索确认是否已经写入，再决定是否重试。

## 当前范围的存量与用量

Server 源码为 `7d89a97a` 时，选择 `sqlite-identity-stats-7d89a97a-v1`，在同一固定 wheel 上
同时验证身份、Memory 和统计。

连接支持空间统计时，首页展示当前空间的存量快照、已记录
的 UTC 模型用量和召回 token 估算。缺失 token 数保持“未知”；估算不代表账单
节省或质量提升。每个图表均有等价表格，滚动区域可通过键盘聚焦。统计错误不禁用
独立的 Memory 操作。详见[开发契约](../docs/zh/development/desktop-statistics.md)。

## Windows 安装包冒烟验证

Windows GitHub Actions 构建未签名的 NSIS 安装包，并将其安装到含中文字符的临时路径。测试通过与 WebView2 版本匹配且带有 Microsoft 签名的 WebDriver，确认打包应用能渲染首次使用界面。随后卸载桌面端，核对安装目录外的文件完整性。

`desktop-windows-internal-unsigned` 工件包含安装包、冒烟报告、截图和驱动日志。如需复验预览工作流的已有安装包，在 `Desktop validation` 中填入该次运行的 `installer_run` ID。任务会在安装前核对原提交、文件大小和 SHA-256。

连接、记忆保存、搜索和精确读回由独立的[原生 SQLite Server 验证](tests/real_server.py)覆盖。

客户端契约匹配时，`sqlite-atomic-112db11e-v1` 和 `sqlite-atomic-3cefa45a-v1` 可在 Bearer 模式下使用，并保留原操作集、已保存的选择和 Principal 复核。匿名使用需要升级 Server。已保存的不匹配配置仍会显示，须明确替换。对于 Server 源码 `7d89a97a`，请选择 `sqlite-identity-stats-7d89a97a-v1`。配置中的源码、wheel 和契约必须与实际部署一致，然后检查并显式激活连接。`ci-fixtures` 配置仅用于临时测试。

## 在 macOS 上构建

使用 macOS 12 或更高版本、Xcode 命令行工具（`xcode-select --install`）、Node **24.14.1**、pnpm **11.13.1**、Rust **1.95.0** 和 **JDK 11 或更高版本（确保 `java` 位于 `PATH` 中）**。请在目标架构（Apple Silicon 或 Intel）上构建。

`lint` 和 `build` 包含 `generate:check`，会启动 Java OpenAPI Generator。首次运行可能从 Maven Central 下载固定版本的 JAR，因此需要网络访问；后续运行可以复用已下载的 JAR。

```sh
cd desktop
pnpm install --frozen-lockfile
pnpm desktop:dev
# Or build the app bundle and disk image:
bash scripts/setup-sparkle.sh
pnpm desktop:build:macos
```

应用位于 `src-tauri/target/release/bundle/macos/`，安装镜像位于 `src-tauri/target/release/bundle/dmg/`。打开 DMG 并把应用拖入「应用程序」后启动 **PowerContext Desktop**。记忆操作需要已有独立 Server。Bearer 凭据可持久保存到 macOS 钥匙串，也可显式选择仅本次会话。移除应用不会删除 Server 数据或钥匙串凭据；如需一并删除凭据，请先在应用内移除已保存的连接配置。

构建产物是未签名的内部预览。Developer ID 签名、公证、干净机器上的 Gatekeeper、辅助功能权限、真实输入法输入以及安装版钥匙串弹窗仍需单独验收。macOS CI 任务会测试 Apple Silicon 和 Intel 构建，但任务本身的存在不代表运行已通过。

macOS 本机诊断读取 `~/Library/Application Support/com.powercontext.desktop.preview/diagnostic-cli.json`。登记与 Windows 使用相同的 JSON 契约：绝对路径的 `powercontext` 可执行文件、经核对的 SHA-256 摘要和 `source: "explicit_local_installation"`。渲染进程无法提供可执行文件或参数。原生适配器只执行经过核验的 launcher 的私有只读副本。请使用自包含可执行文件，或解释器路径为绝对路径的 launcher；以自身位置查找依赖的 launcher 不适用。CLI 不能依赖 shell 配置或交互式终端。诊断沿用既有的输出上限与超时，并在完成、超时和取消时终止自己的私有进程组。应用正常退出前会拒绝新诊断、取消当前诊断，并等待进程组清理和 launcher 回收完成。应用不会安装或管理 LaunchAgent。
