# PowerContext Desktop 预览版

这是 #1654 的 Windows 和 macOS 内部工程预览。已建立 Tauri 2、React、TypeScript 和 Vite 独立工程，提供首页、连接、我的记忆、双语设置及浅色／深色／系统主题。无连接时保存和搜索不可用，不展示示例业务数据。

已实现连接配置、显式激活、身份与就绪检查、精确范围选择和本地诊断。已实现共享笔记表单、FTS 搜索和精确版本阅读；原生交互闭环仍待完整验收。当前不是已签名的正式发行版。

丢弃草稿、移除连接配置和未知写入再次提交均使用应用内双语确认框。初始焦点位于“取消”；Escape 或点击遮罩会取消操作，Tab 保持在对话框内，确认后仅执行所请求的操作。Scope 选择使用相同的模态交互；关闭后焦点返回实际触发按钮，按钮不可用时返回页面标题。

## 观察

侧栏提供总览、连接、记忆、观察入口，“设置与诊断”位于侧栏底部。默认进入总览。“观察”包含运行状态、当前 Scope 库存与用量、本次会话三个标签页。数据视图接入前明确显示尚未可用；进入页面或切换标签不会执行连接检查或本机诊断。观察页面沿用现有中英文和主题设置。

原生宿主独立于页面导航，采集有界的内存窗口，分别记录 HTTP 请求和 Memory/Scope 用户操作。可信的类型化 IPC 提供白名单摘要、重置和安全 JSON 预览。窗口最多保留 512 条终态记录，保留取消及未知写入的区别，不包含内容或目标身份。测量单位、覆盖范围、重置和故障隔离见[会话观察契约](../docs/zh/development/desktop-session-observations.md)。本次会话页面和剪贴板流程仍需独立的 UI 集成。


## 统一反馈与当前上下文

各页面采用一致的加载、空状态、不可用和错误呈现。加载本机状态与未连接分开；本机状态读取失败时可“重新加载本机状态”，不会探测 Server 就绪状态。搜索无结果仅表示当前查询没有匹配项，不代表整个 Scope 库存为空。未接入的观察视图继续显示不可用。

错误说明受影响的操作及恢复方式。读取失败可手动重试，精确读取保持原 Artifact 版本；Scope 游标过期后从第一页重新查找。凭据、权限和兼容性错误提示需要修改的配置或授权。保存失败保留草稿；结果未知的写入保留原目标，再次提交仍需现有确认，不自动重放写入。本机诊断失败可重新执行同一项本机检查，不表示远程服务不可用。

全局栏在每个页面展示当前连接、完整 Server 地址、Scope 标题和完整 `scope_id`，可区分同名 Scope。身份、兼容性、存活和就绪状态分别呈现，采用与当前配置版本一致的最近检查事实并显示检查时间；健康状态不代表操作权限。长地址和 ID 在小窗口下可换行。连接菜单支持方向键、Home/End 和 Escape；Scope 对话框约束焦点，关闭后恢复焦点。

“设置与诊断”内提供双语使用说明、对应语言的文档地址和固定问题反馈地址。复制操作显示成功或失败；只读地址框获取焦点时选中全文，便于手动复制。没有 Server 或本机连接状态不可用时仍可访问帮助。Desktop 版本来自原生信息，获取不到时显示“未知”。

连接操作等待期间，全局提示在页面切换后持续显示。仍可浏览其他页面，但记忆编辑、保存、搜索、精确读取及 Scope 切换暂停，直到操作及其原生状态刷新完成，避免晚到的激活结果清空在旧 Scope 下新输入的正文。操作完成后，按需显式选择精确 Scope；现有草稿确认和未知写入保护继续生效。

从全局连接菜单选择已保存配置，会打开对应配置的编辑器，包括已经位于连接页时。存在连接编辑器或记忆草稿时，继续使用原有丢弃确认；取消后保留当前编辑器及正文。选择配置不会检查或激活连接；确认丢弃后再次选择同一配置，可重新载入已保存的值。

Scope 选择也具有应用层等待状态。Escape、关闭按钮及遮罩可以关闭弹窗，但不会取消已提交的原生选择；完成前暂停记忆编辑及再次切换 Scope。触发按钮被禁用时，关闭弹窗将焦点返回页面标题。晚到结果仍更新原生实际状态，但不会清除新连接编辑器的草稿导航保护；弹窗关闭后发生的失败由共享错误反馈显示。

## 已有 Server 的连接流程

保存配置、检查连接和激活连接是独立操作，分别显示进度与完成反馈。保存不会激活连接；检查完成后须核对各项事实。兼容性与身份验证通过的激活操作会打开共享的精确 Scope 选择器，查找和选择仍分别受逐项操作资格限制。请选择 Server 返回的目标范围，以完整 ID 区分同名 Scope；按钮的无障碍描述也包含该 ID。激活引导完成选择后进入记忆页，并将焦点移至页面标题；关闭引导会将焦点返回激活按钮，不会选择范围。

从其他位置打开全局 Scope 选择器，选择后仍停留在当前页面，焦点返回选择器。取消草稿确认会保留当前输入；确认切换且选择成功后，会丢弃旧记忆草稿或连接编辑草稿。被判为过期的原生状态不能打开激活引导或触发后续 Scope 导航。空结果提供修改标题、申请访问权限或选择其他连接的恢复方向；加载、权限、认证及兼容性错误继续分别呈现。

重新打开 Desktop 只加载已保存配置，不会激活私有会话。本地设置入口继续指向 Local Server 说明；经批准的安装结果与默认 Scope 交接仍需与 #2117 一起完成。

## Local Server 准备说明

默认启动页仍为总览。记忆页未连接时，“本地设置”进入并聚焦“设置与诊断 → Local Server”；“连接已有 Server”进入现有连接编辑器。选择任一路径都不会安装软件、激活连接、执行诊断或探测就绪状态。

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

1. 在连接页添加名称和 Server 地址。HTTP 只允许字面 loopback 地址，其他地址必须 HTTPS；可保留反向代理路径前缀。
2. 明确选择未认证本机服务或 Bearer。凭据可保存到 Windows 凭据管理器、macOS Keychain 或 Linux Secret Service（GNOME Keyring/KWallet）；也可以明确选择仅本次会话。如果系统凭据库不可用或被锁定，保存会失败，秘密不会写入连接配置 JSON；此时只能选择仅本次会话或取消。地址或信任变化后需要重新配置凭据。自定义 CA 不会关闭证书校验。
3. 核对部署版本后选择已验证兼容配置，具体构建摘要和范围见 [验证说明](VALIDATION.md)。选择配置不能证明远端二进制身份。
4. 保存后点击“使用此连接”。查看其他配置不会自动切换当前连接。分别查看可达性、就绪、身份和能力，不把其中一项当作资源授权。
5. 通过顶部 Scope 下拉按钮按标题分页查找范围，每页最多 50 项。选择结果后更新当前范围并关闭弹窗；不创建范围或修改 Agent 绑定。修改查询取消旧请求，切换连接或身份后旧结果失效。

连接配置存放在应用数据目录，JSON 不包含秘密。重启后需要重新验证连接和选择范围；仅会话凭据不保留。移除连接仅移除此桌面的配置和自有凭据，不停止 Server 或删除业务数据。

## 原生发现结果

连接检查通过现有原生传输调用公开的 `get_server_info`。`CheckReport.discovery` 返回 `supported`、`missing`、`denied`、`unavailable`、`invalid` 或 `incompatible`，并包含观察到的 Server 元数据、安全错误，以及逐项操作的 `qualified`／`unsupported`／`unverified` 状态和原因。兼容的发现协议 minor 版本可增加响应及其嵌套版本、功能对象的可选字段。

操作资格仍要求明确选择与当前契约匹配的原生验证记录。仅发现接口的 404 允许沿用该记录已独立验证的旧版操作；401、403、503、无效响应、不兼容产品或必要 major 版本不会触发旧版回退。缺失功能组、不支持的功能版本和缺失 operation ID 分别限制受影响的操作。Artifact 精确读取需要独立资格，不能从 `memory.explicit` 推断。发现元数据不授予权限，也不证明远端二进制身份。

当前连接检查和匿名身份复核仍会调用 readiness。检查结果记录观察到的 `server_id`；这不代表已经实现部署身份隔离或显式 readiness 分离。

连接页分别展示发现状态和兼容性结论，说明访问被拒绝、暂时不可用时的恢复方法，以及产品或 major 版本不兼容的原因。发现检查失败时业务操作保持不可用，但不会删除已选择的资格记录。修正连接或服务后，须明确重新检查连接，重新判定操作可用性。仅发现接口的 404 适用上述已验证旧版策略。

保存、搜索、精确版本读取、范围查找和范围选择分别要求 `supportedOperations` 包含各自的操作，总体兼容性结论不会启用其他操作。不可用的操作会说明功能缺失、功能版本不兼容、未声明操作支持或缺少独立验证记录的原因。其他独立支持的操作仍可使用，总览也逐项展示记忆功能的可用性。原生层在请求时执行的授权和兼容性检查仍是最终依据。

工程边界：

- OpenAPI 自动派生的 TypeScript schema 和十一项 operation 映射，包含契约摘要及漂移检查。
- `desktop/openapitools.json` 固定 OpenAPI Generator CLI 版本，Rust 模板位于 `desktop/scripts/openapi-generator/rust/`。修改 OpenAPI 合同或模板后，在仓库根目录执行 `pnpm --dir desktop generate`，不要手动编辑生成的 `src-tauri/src/transport/wire.rs`；CI 通过 `pnpm --dir desktop generate:check` 检查生成结果是否漂移。
- Rust 自动派生的 IPC 类型与安全错误；凭据只写请求必须显式选择持久化或仅会话，成功回执只含存储模式，不含秘密或凭据标识。
- Windows Credential Manager、macOS Keychain 和 Linux Secret Service 原生适配器；保存失败直接报错，只有显式选择才采用仅会话内存，绝不把秘密降级写入连接配置。秘密类型不支持序列化，不实现 Debug。
- 使用系统证书信任的原生 HTTPS；可为单个客户端添加显式 CA，继续校验主机名。
- HTTP 仅允许 loopback，拒绝地址中的用户信息、查询、片段、反斜杠及路径逃逸；不继承代理、不跟随重定向。
- 连接超时 5 秒、完整请求超时 15 秒、响应上限 1 MiB、每个客户端最多 4 个并发读取；超限明确失败。
- 主窗口可调用明确列出的连接、Scope、记忆和诊断命令；没有通用 fetch、shell、文件、数据库或读取秘密的 IPC。

应用不启动 Server、不管理服务、不持久化正文或查询；关闭窗口即退出。当前语言和主题也仅保留于会话内。

[安全边界](SECURITY.md)记录具体约束；[验证说明](VALIDATION.md)列出测试范围和仍未满足的 P0 门槛。构建成功、Mock 测试和当前用户机器上的运行都不能代替标准用户、签名、通知冷启动激活、独立服务和 Agent 宿主验收。

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

Desktop 在每次诊断前检查固定路径和摘要，然后只允许 `service status --json` 或 `doctor codex claude-code dsh openclaw opencode pi hermes workbuddy kimi-code zcode --json`。收到符合格式的诊断响应后，尝试读取实际 CLI 版本用于显示；版本查询失败时显示“未知”，不阻止有效诊断结果。版本查询最长 15 秒，服务检查最长 20 秒，集成检查最长 60 秒；每次进程调用的 stdout/stderr 合计最多 256 KiB。窗口隐藏，进程及其后代归属于本次专属 Windows Job，结束或超时后清理。界面只显示允许的状态字段，不显示任意 detail、路径或原始输出。合法 JSON 配合退出码 1 仍可显示不健康结果。

未配置或未通过核验时，本机诊断不可用，独立远程业务仍可使用。进程树清理和隔离真实 CLI 测试已通过；安装后交互与完整 S2 资格仍待验收。

## 保存、查找和阅读

明确启用合格连接并选择精确范围后，在首页填写笔记，或在「我的记忆」中点击「记一条」。提交前会显示目标连接和范围。Enter 只换行，点击保存才提交。正文是纯文本，保守限制为原始文本最多 8192 个 UTF-8 字节，不会截断；规范化由 Server 完成，以返回的正文为准。

全文搜索只在当前范围执行，每次最多返回 10 条，不代表完整目录、历史或总数。阅读按返回的 Atomic Memory 引用（`{family: "atomic-memory", artifact_id, revision}`）读取该精确版本，不自动改读最新版本。复制按钮分别复制纯文本正文和引用 JSON。

保存成功但没有返回记忆记录时，只报告操作成功，不编造引用。请求发出后的超时或中断可能意味着「结果未知」：先核对原连接和范围，再决定是否重新提交；相同正文不能证明属于这次操作。应用不会自动重试写入，也没有离线队列。切换连接或范围后隐藏旧结果，本次会话只保留最小操作状态和原目标。测试范围与剩余验收见 [验证说明](VALIDATION.md)。

[验证说明](VALIDATION.md)列出可复现的检查方式和待完成的平台、产品验收项。

## 远程安装包验收

Windows GitHub Actions 会构建未签名安装包，安装到临时中文路径，并通过匹配且验证 Microsoft 签名的 WebDriver 操作实际安装的 WebView2 页面。测试使用独立 SQLite Server，覆盖启用连接、精确范围选择、中文多行笔记保存、全文查找、精确阅读，以及正文和引用复制后的粘贴核对。Server 使用合成数据，测试结束后清理。

运行结果、界面截图和驱动日志与安装包一起保存在 `desktop-windows-internal-unsigned` 工件中；失败或尚未执行的步骤不计为验收通过。该脚本仅允许在 GitHub Windows runner 执行，不需要操作你的电脑。托管 runner 不等同于干净的 Windows 11 普通用户环境，也不能代替真实输入法、读屏软件和 Agent 宿主验收。

调试安装后测试时，可以手动运行 `Desktop validation`，将 `installer_run` 填为已有 Desktop CI 的运行 ID。流水线会核对原安装包的提交、摘要和大小，再复用该包测试；报告分别记录测试脚本和安装包的提交。这种诊断不代替最终完整构建验收。

界面验收还检查两个独立连接之间的内容隔离、断开重连、移除非活动配置后保留 Server 数据，以及真实写入响应丢失后的“结果未知”和不自动重试。各场景是否已通过，以对应运行报告为准。

仅在 CI 执行的生命周期场景会先通过安装版保存测试记忆，再强制结束本次测试启动的 Desktop，独立检查 Server 是否仍能读取原记忆并完成新的写入和读取。结果以对应生命周期报告为准；这不代替正常关窗或卸载保留数据库的验收。

安装版边界测试覆盖 8192 字节 Unicode 正文、超限输入禁止提交、搜索无结果和最多 10 条的提示，以及未保存草稿时取消或确认断开连接的行为。是否通过以对应远程报告为准。

从早期预览版升级后，请打开「连接」并选中已保存的连接。与当前 API 契约匹配的配置显示在「此 Desktop 构建可用」中；先核对已测试的 Server 构建与实际部署，再明确选择、保存、检查并使用该连接。新连接默认不选择兼容配置。

历史配置在契约不匹配时显示为不可选择的记录。已保存的不匹配或不可用配置仍会显示并说明原因，须明确替换，不会静默升级。当前 API 契约尚无经过独立验证的 Server 记录，因此普通构建中的业务操作保持不可用。现有记录保留各自原始的 Server 源码、契约、wheel 和操作身份。`ci-fixtures` 构建仅用于临时验收测试，不代表实际部署已通过兼容性验证。资格证据和剩余要求见[验证说明](VALIDATION.md#compatibility-scope)。

## 在 macOS 上构建

使用 macOS 12 或更高版本、Xcode 命令行工具（`xcode-select --install`）、Node **24.14.1**、pnpm **11.13.1**、Rust **1.95.0** 和 **JDK 11 或更高版本（确保 `java` 位于 `PATH` 中）**。请在目标架构（Apple Silicon 或 Intel）上构建。

`lint` 和 `build` 包含 `generate:check`，会启动 Java OpenAPI Generator。首次运行可能从 Maven Central 下载固定版本的 JAR，因此需要网络访问；后续运行可以复用已下载的 JAR。

```sh
cd desktop
pnpm install --frozen-lockfile
pnpm desktop:dev
# 或直接构建应用包与磁盘镜像：
pnpm desktop:build
```

应用位于 `src-tauri/target/release/bundle/macos/`，安装镜像位于 `src-tauri/target/release/bundle/dmg/`。打开 DMG 并把应用拖入「应用程序」后启动 **PowerContext Desktop Preview**。记忆操作需要已有独立 Server。Bearer 凭据可持久保存到 macOS 钥匙串，也可显式选择仅本次会话。移除应用不会删除 Server 数据或钥匙串凭据；如需一并删除凭据，请先在应用内移除已保存的连接配置。

构建产物是未签名的内部预览。Developer ID 签名、公证、干净机器上的 Gatekeeper、辅助功能权限、真实输入法输入以及安装版钥匙串弹窗仍需单独验收。macOS CI 任务会测试 Apple Silicon 和 Intel 构建，但任务本身的存在不代表运行已通过。

macOS 本机诊断读取 `~/Library/Application Support/com.powercontext.desktop.preview/diagnostic-cli.json`。登记与 Windows 使用相同的 JSON 契约：绝对路径的 `powercontext` 可执行文件、经核对的 SHA-256 摘要和 `source: "explicit_local_installation"`。渲染进程无法提供可执行文件或参数。原生适配器只执行经过核验的 launcher 的私有只读副本。请使用自包含可执行文件，或解释器路径为绝对路径的 launcher；以自身位置查找依赖的 launcher 不适用。CLI 不能依赖 shell 配置或交互式终端。诊断沿用既有的输出上限与超时，并在完成、超时和取消时终止自己的私有进程组。应用正常退出前会拒绝新诊断、取消当前诊断，并等待进程组清理和 launcher 回收完成。应用不会安装或管理 LaunchAgent。
