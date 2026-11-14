# DSH Desktop 交接实现与验收记录

## 结论

默认插件对已验证版本的 Codex Desktop 和 Claude Code CLI 开放本机交接预检，Claude Desktop Code 保留安装检查。三个目标此前均在隔离 DSH Desktop 中完成实际接收与精确回执验证；其中 Claude Desktop 的试验预检只适用于固定测试环境。

## 实现范围

| 模块 | 已实现 | 仍需完成 |
| --- | --- | --- |
| 共用 Host | 同一写锁和持久配置指纹下创建默认目标目录；按真实回复事件解析边界；准备、历史、固定、删除、回执与诊断路由 | 在更多受支持版本和认证环境中补充实机覆盖 |
| Codex Desktop | 默认预检；实际 app-server 配置、插件、hook、MCP、权限及精确文件读取探测；可用本机 PowerContext CLI 经同意安装集成 | 从未安装集成开始的实机验收 |
| Claude Desktop Code | 实际 Local 新会话读取 DSH 生成的正式包，通过 PowerContext MCP Continue 和 ACK，DSH 验证精确回执 | 验收包限定到隔离 profile、固定测试回复和 Server，在打开目标后验证。没有受支持的方式验证未来新会话的有效连接、Scope 和读包权限；生产预检未接通 |
| Claude Code CLI | 默认预检；CLI、handler、终端偏好、共享安装器、配置与 MCP 检查；采用已有原生验收 | 自定义终端与权限环境不在默认支持范围 |
| 交接 UI | 回复入口、安装检测、安装同意、可编辑准备、显式重试、关闭恢复、历史、固定、删除、导出、回执状态 | Claude Desktop 默认启动接通后复测 |
| 回执 | 精确 Source 点查、绑定与归属校验、持久历史和保留维护；三个真实目标产生的 ACK 均曾由 DSH 读回 | 启用访问控制的真实接收方归属验收、大量无关历史下的成本测量 |

无资格记录的目标不会因为检测到应用、存在配置文件或模拟测试通过而变成 `ready`。复制或导出已有交付是恢复能力，不能替代三个首期目标的验收。

## 实机范围与结果

测试日期：2026-11-13 至 2026-11-14；macOS 26.6，Apple Silicon。

- DSH：官方 `0.2.2-rc.3` macOS arm64 Desktop，版本和 build 均为 `0.2.2-rc.3`。使用独立 `DSH_HOME`、Desktop profile、Electron user-data-dir 和测试工作区。
- 官方 ZIP 的 SHA-512（十六进制）：`5138565ca0aa8a25278c58228fae79f8673532089297e61b97bd639108b8f0185c0423745d20cd4f8186986258de7bc4aa790b4f72ed59b2fac32eadfe447123`。
- 最终 UI 安装验收归档：`powercontext-dsh-1.5.0-dsh-wave2.7.tgz`，SHA-256 `eea9bdfcd88e721eaff49d2c639786bb9ceb6d5e750a42da8baecf40fb6a53bb`。使用默认适配器。正式启动使用的单目标验收归档也只装入隔离 profile。
- PC Server：使用本地隔离实例。Codex 已配置的 `127.0.0.1:8000` 实例已启动，`/health/ready` 返回 `ready`，访问模式为 `disabled`。DSH 测试 profile 也连接该地址，测试工作区按 DSH 路由解析的 Scope 与 Codex 预检一致。此证据不构成已发布 Server wheel 的资格声明。
- DSH 源端使用本地、固定响应的模型协议测试服务。真实 Desktop 创建并完成会话，响应内容明确标注 fixture；不构成真实模型接收验收。

| 场景 | 结果与边界 |
| --- | --- |
| Desktop 归档安装与加载 | 通过，PowerContext 显示已连接 |
| Desktop 测试工作区、完成回复、交接入口 | 通过 |
| Desktop 交接图标、真实回复边界与右侧菜单 | 通过；沿用 DSH 原生 Menu、Tooltip 和图标，回复下没有交接文字，页面没有遮罩或居中弹窗 |
| 主菜单和返回定位 | 顶部显示 PowerContext 名称和既有小 Logo；安装检查、预检、交付和回执在同一锚点使用紧凑布局。原始交付编号、摘要和导出收到「交付详情」，结果未知显示操作指引，不展示内部错误码。 |
| Desktop 菜单交互 | Escape 关闭并返回图标焦点；打开安装检查会自动发起三项检测，无逐项按钮，每行只显示检测结果；未开放交接的原因留在主菜单提示中。加载图标、乱序结果和离开取消有定向测试覆盖 |
| 安装检查 | 实测识别 Codex Desktop `26.1106.51428 (26605)` / `codex-cli 0.167.0-alpha.4.1`、Claude Desktop `4.29.0` 和 Claude Code CLI `2.1.323`。仅检查版本，不打开 Claude Desktop Code |
| Codex Desktop，交付 ID 末尾 `db2b` | DSH 菜单预检 → 手动确认交接内容 → 准备正式包 → 打开实际新会话，预填未发送 → 操作者发送 → Codex 校验原包与 Prepared 摘要、读取测试 README、通过实际 MCP 调用 Continue 和 ACK → DSH 刷新显示「接收方已确认」「本次读取：已验证」 |
| Claude Code CLI，交付 ID 末尾 `059a` | DSH 菜单预检并准备独立正式包 → 官方 handler 打开新终端，预填未发送 → 操作者发送 → CLI 读包、校验原包与 Prepared 摘要、读取测试 README、通过实际 MCP 调用 Continue 和 ACK → DSH 刷新显示「接收方已确认」「本次读取：已验证」。ACK 前，操作者提醒 CLI 忽略机器上另一个 QA Server |
| 无生成模型的一键交接，交付 ID 末尾 `04aa` | Server 报告 `handoff_generation: false`。只选一次 Claude Code CLI，DSH 自动预检、保存正式 Prepared 包并打开目标；操作者检查并发送未提交的草稿。CLI 读取正式包、调用 Continue，操作者澄清测试连接和接收方身份后，CLI 写回 ACK。重启 DSH 并换回默认适配器后，从恢复交付打开 `04aa`，刷新显示「接收方已确认」「本次读取：已验证」。 |
| Claude Desktop Code 的初步探测 | 官方 Code 链接打开新 Code 界面并显示未发送的合成探测草稿。自带运行时 `2.1.309` 下载完成后，实际本地会话加载了 PowerContext Continue 和 ACK 的工具定义，并通过 Read 返回隔离测试包的交付 ID 末尾 `04aa`。该包属于 CLI，Desktop 没有对它写回执；Desktop 专属正式交付见 `1022` |

Codex 使用 `1.5.0-dsh-acceptance.2`（归档 SHA-256 `990c2d65f318e688828ce4bf31b94dcb17ade61fc657f034bec5f2bb7bb45f6e`），CLI 使用 `1.5.0-dsh-acceptance.3`（归档 SHA-256 `d2b42f54599bebd753bea08290173545edc4b34a82124e1953a85f67f0b8d1e0`）。每个归档只为该机器的实测配置接入一个目标。两个 ACK 均由接收 Agent 调用 MCP 写入，Server 的精确点查结果与各自的回执 ID、Scope 和 Prepared 摘要一致，归属显示为「接收方自行报告」。源端走的是手动确认准备路径；这组实测未覆盖自动生成、首次安装 PowerContext 或启用访问控制的归属。

一键交接使用 `1.5.0-dsh-acceptance.4`（归档 SHA-256 `05d81082e1493c26cfff859c3c67d5d7fe5f82342c1e6f9b08b8e3bec37cfd86`）。交付中未生成任务结论，保留原始请求、所选回复、已有观察和遗漏。仍未实测启用模型生成的路径。

实测发现 Codex app-server 同一配置的字段顺序可随进程改变，且最后一步读包不需要重查插件目录。当前代码稳定排序配置指纹，单独执行精确文件读取；当前 45 秒预检和 60 秒启动重校验预算在 `31d5` 实机通过。

## 三目标同轮复测

2026-11-14，使用同一个隔离 DSH Desktop profile 和新建的本地 `:8000` 测试 Server，访问控制及模型生成关闭。旧包绑定原 Server，不用于本轮验收。每份新包各有自己的交付、启动尝试和回执。

| 目标 | 交付末尾 | DSH Desktop 到目标及回执 |
| --- | --- | --- |
| Codex Desktop | `31d5` | 一键预检、准备、启动；用户确认未发送草稿后发送。Codex 校验文件和摘要、Continue，在用户确认本次测试连接后写 ACK；DSH 显示「接收方已确认」「本次读取：已验证」 |
| Claude Desktop Code | `1022` | 一键准备正式包、通过官方链接打开新 Code 会话。用户确认测试目录并发送草稿。Desktop 校验原包、读取测试 README，使用实际 MCP Continue 和 ACK；DSH 在重启后按历史记录读回并验证 |
| Claude Code CLI | `7f09` | 一键准备正式包、从官方 handler 打开新终端，用户确认并发送草稿。实际 CLI 校验并读取原包，Continue 成功并以同一 Scope、摘要和回执 ID 写入 ACK。DSH 在重启后按历史记录读回并验证 |

`acceptance.5` 使用实际 Codex 预检，`acceptance.6` 使用 Claude Desktop 固定验收适配器，`acceptance.7` 使用实际 CLI 探测和已测终端读包根目录。Claude Desktop 的本轮预检输入来自验收装配，仅允许固定的合成回复、测试目录、Server、Scope 和包路径；正式接收与 ACK 由实际新会话执行。它不证明正常配置下对尚未创建的下一次会话可预检。

三个目标的本地隔离菜单归档为 `powercontext-dsh-1.5.0-dsh-acceptance.8.tgz`（SHA-256 `fb37ccadc9b2aa6fdca9d787e1631f386c1c54493623bea82cdf8f82d6574139`），只装入隔离 profile。三个入口均可选；历史中分别验证了 `1022` 和 `7f09`，`31d5` 已在原发起界面验证。默认工厂未加入这些验收配置。六个定向测试文件共 53 项通过；文档构建通过，验证 1121 个公开页面及站内链接。

不使用 Web 页面结果替代 Desktop 验收。

## 自动验证

目标切换与语言跟随：在装有 `acceptance.9` 的隔离 DSH Desktop 中，用户确认从「交接记录」打开旧交付后，点「更换目标」可重新选择三个 Agent；对新回复交接到 Claude Desktop Code 时，接收端预填了中文未发送草稿。该轮只验证改选和草稿，没有再次发送草稿或验证 ACK。

可编辑短草稿的自动回归以改写后的草稿运行实际 Claude 接收钩子，再经 MCP 查询连接身份、Continue 和 ACK，由 DSH 读回本次精确回执。工具调用由测试驱动；下方原生复测另外验证真实接收方执行这些调用。

### 编辑草稿后的原生复测

使用同一隔离 DSH Desktop、`acceptance.10`（SHA-256 `f20c8b9450e6e21b8f32633b38f4c97f5808d41b2cd68ab499d83266dc483c51`）和访问控制关闭的本地 Server。操作者在短草稿的原路径后追加只读检查 README 和指定末行的要求；包内没有这些标记。验收检查实际回复和 DSH 中该精确交付的回执，测试 README 未改变。

| 目标 | 交付末尾 | 结果 |
| --- | --- | --- |
| Claude Desktop Code | `1fea` | 返回 README 标题与 `DESKTOP-486`，Server 收到原 Prepared 的 ACK，DSH 显示「接收方已确认」「本次读取：已验证」 |
| Claude Code CLI | `d093` | 返回 README 标题与 `CLI-739`，Server 收到原 Prepared 的 ACK，从 DSH 交接记录刷新原交付后显示「接收方已确认」「本次读取：已验证」 |
| Codex Desktop | `5cd2` | 返回 README 标题与 `CODEX-814`，Server 记录 Continue 和原 Prepared 的 ACK，DSH 显示「接收方已确认」「本次读取：已验证」 |

Codex 的复测使用重新信任的接收钩子。该钩子接受编辑器在提交时对路径开头 `~` 增加的 Markdown 转义，同时保留原有的私有目录、文件哈希、工作目录和 Scope 校验。

回归覆盖进行中改选、已打开后改选、从历史删除后用当前回复重新交接，以及晚到的准备结果不再启动旧目标。三个原生目标的中文草稿均通过链接编码测试，插件写入的交接说明遵循当前语言并保留原始证据。模型生成的语言偏好已通过 HTTP 到生成器的测试。

提交分支基于 #2602 的 Host 接线。该基线通过 751 项插件测试、11 项真实 Server 集成测试以及安装包测试；定向 Python 测试为 191 项通过、3 项跳过。回执层的 69 项契约测试与 Desktop 四种本地协议情形也通过。

工作目录为仓库根目录，插件测试命令在 `integrations/dsh/plugins/powercontext` 执行。

| 检查 | 结果 |
| --- | --- |
| `pnpm test --maxWorkers=1` | 43 个文件、744 项通过 |
| 一键交接回归 | 无模型自动准备、边界读取、菜单交互和挂载 4 个文件共 78 项通过；覆盖取消后不启动、显式恢复与准确回执 |
| 最终集成定向回归 | 10 个文件共 81 项串行通过（UI、共享交接、Codex 和 Claude 适配器）。首次并行运行有一项 Claude Desktop doctor 用例超过 5 秒限时，未改限时或断言。UI 测试使用 DSH primitives 测试替身，菜单外观、自动检测结果和返回定位另由 Desktop 验收。最终安装列表精简后复跑 31 项 UI 测试通过 |
| 正式交接回归 | Codex 运行时及适配器、共享交接、回执、接收提示 5 个文件共 46 项通过；最终包安装检查通过 |
| 三个 handoff/receipt e2e 文件 | 3 项通过，使用真实隔离 Server；目标与 OS 启动使用测试替身 |
| `make contract-test` | 68 项通过 |
| `make check` | 通过，含 Ruff、ty 和 61 项集成 manifest 检查 |
| `pnpm test:package` | 通过；归档中的两个运行 bundle 与最终工作区文件逐字节一致 |
| `make docs-test` | 通过，静态导出及 1121 个公开页面内部链接校验通过 |
| 定向严格 TypeScript 检查 | Host、UI 和生产适配器通过；不是全项目 tsc 通过声明 |

日志及本机测试包位于本地忽略目录 `.powercontext/wave2-qa/`。测试用临时目录、配置和固定模型响应不进入发布包。

## 默认接线验收

在 macOS 26.6 arm64 的官方 DSH Desktop `0.2.2-rc.3` 隔离 profile 安装按发布流程打包的插件。Codex Desktop 和 Claude Code CLI 可选择，Claude Desktop Code 保持禁用。选择 Codex 后，真实预检通过，自动准备了完整交接包，DSH 记录 macOS 接受了启动请求（交付 ID 末尾 `1497`）。此轮未发送草稿，接续与精确回执沿用上方 `5cd2` 的实机证据；Claude Code CLI 沿用 `d093` 等已有实机验收，没有再次启动。首次安装和认证模式仍需单独验收。
