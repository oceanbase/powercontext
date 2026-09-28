---
title: "桌面控制中心交付计划"
description: "桌面控制中心的实施阶段、负责方协调与验收证据。"
---

# 桌面控制中心交付计划

本计划通过 [#1428](https://github.com/oceanbase/powercontext/issues/1428) 跟踪
[RFC 1455](../rfcs/1455-desktop-control-center.md) 的实施。RFC 定义用户行为和架构契约，本计划记录阶段、负责人、
测量及验收证据。排期和调优可以独立演进，不重新定义这些契约。下列条目是要求，不是已经完成的工作或验收结果。
接受 RFC 或发布仅连接预览不关闭 #1428。

源码事实采用 RFC 的基线，即 2026-09-13 核验的上游
[`62e4c821709c18b832c77363fdd428765bee6a96`](https://github.com/oceanbase/powercontext/commit/62e4c821709c18b832c77363fdd428765bee6a96)。
交付时按所选发行物重新核查平台阻塞和负责方能力。

## 负责方协调

D1–D6 和 D8 对应 RFC 中的负责方契约，D7 在本计划中跟踪人员安排与发行验收。对应阶段退出前，落实具名维护者并
关联负责方工作。

| ID | 负责方与相关工作 | 所需契约 | 门槛 |
| --- | --- | --- | --- |
| D1 | Server/API | `server-info`、部署身份生命周期、明确兼容配置 | P1/P2 中依赖兼容性的控制 |
| D2 | Server/Memory | 授权且有界的 entry 列表；历史 UI 交付前的有界 change/history 查询 | P2 完整 Memory 浏览 |
| D3 | 服务/配置，[RFC 1299](../rfcs/1299_local_server_availability_and_service_installation.md) | 非交互结构化修改、受保护输入、归属和恢复 | P3 受管服务/配置修改 |
| D4 | 安装器 [#1406](https://github.com/oceanbase/powercontext/issues/1406)、RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | 核验 bootstrap、计划、锁、持久操作/状态与恢复 | P3 受管安装/升级 |
| D5 | 分发 [#1405](https://github.com/oceanbase/powercontext/issues/1405)、RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | 不可变宿主发行物、兼容性和宿主负责的安装适配器 | P3 所选宿主安装 |
| D6 | 投递 [#1419](https://github.com/oceanbase/powercontext/issues/1419) | 接收方关联、envelope、持久收件箱、精确引用、去重和恢复 | P4 投递消费 |
| D7 | 桌面/发行维护者 | 具名负责人、Windows/宿主验收、支持版本和实测预算 | P0 退出与 P5 发行 |
| D8 | Server/连接器负责方 | 若提供管理功能，需公开的连接器发现、健康与管理操作 | 仅对应的连接器控制 |

## 平台、无障碍与预算

| 平台 | 建议状态 | 验收 |
| --- | --- | --- |
| Windows 11 x64 + SQLite | 首个平台；当前项目 Windows 支持仍为 experimental | 签名标准用户安装、WebView2 有/无、Credential Manager、Task Scheduler/登录、通知/激活、非 ASCII 路径 |
| macOS | 后续 | 具名架构、Keychain、LaunchAgent、签名/notarization、WebView/通知行为 |
| Linux | 按发行版/桌面环境后续验收 | WebKitGTK/系统库、Secret Service、systemd 会话、托盘、包/激活 |

不包含 Windows ARM 和 Windows 嵌入式 seekdb。框架编译通过不代表平台验收。P0 确定 Desktop/Install/Server/
Release 负责人和一个维护中 Agent Host/版本，观察 Windows 加载及显式 capture/recall。“任意维护中集成”不能
通过该门槛；P4 另行明确真实 sender/receiver。

当前源码基线在 Windows 原生类型检查中暴露了处理 worker 的 `Connection`/`PipeConnection` 类型不匹配，以及
测试引用 POSIX 专用 `os.WNOHANG` 的问题。Windows 验收前需要修复或正确限定这些检查的平台范围；按 Linux
目标检查通过不能证明 Windows 已受支持。

P0 Go/No-Go 证据包括：无 Python/Server 时 UI 可用、认证 API 读写、凭据、签名标准用户包、WebView2 bootstrap、
独立服务/登录、安装后的通知冷启动激活。D4/D5 完整 bootstrap 可以留到 P3，但 P0 要记录负责方承诺，并将预览
限制为仅连接。原生阻塞必须解决或重新讨论平台/范围；Electron 备选针对实际外壳/WebView/维护阻塞，不解决安装器缺口。

中英文 UI/文档同步，支持键盘、可见焦点、屏幕阅读器标签、IME 安全输入、高对比度、不只靠颜色的状态，以及
800 × 600 和 200% 缩放下可用的确认/恢复。语言/主题变化保留身份。

测量冷启动、空闲 CPU/唤醒、桌面+WebView+Server 内存、完整安装/下载大小、列表/搜索延迟。P0 记录硬件、OS/
WebView、数据规模、重复次数和 p50/p95，在 P2 扩展前固定数字发行预算。覆盖空/多页、无模型/已配置模型场景。
D7 负责公开预算，当前不宣称性能结果。传输/通知运行上限不能替代测量。

## 交付阶段

| 阶段 | 交付物 | 退出条件 |
| --- | --- | --- |
| P0：架构 | 打包客户端、窄传输、凭据、Windows 安装原型、UI 复用与测量 | D7 负责人/宿主、安全证据、D1/D2 分工、D3–D6 限制 |
| P1：仅连接预览 | 已有本地/远程连接、已测兼容、服务状态、显式 Memory 保存/召回、Agent 诊断 | 验收操作/身份，不承诺未实现安装 |
| P2：管理/授权 | Scope/资产/Source、类型化 Review、精确共享/报告、导入、有范围的 Review 通知、诊断 | D2 完整 Memory 浏览、授权/并发/family 契约 |
| P3：受管安装 | 干净机器安装、所选宿主、服务/配置修改、迁移/升级/恢复/移除 | D3/D4/D5、签名不可变包和归属验收 |
| P4：投递 | 持久收件箱、目标关联、恢复、精确导航、支持的接收动作 | D6、具名 sender/receiver、有界消费者、安装激活 |
| P5：首个正式发行 | Windows 11 x64 完整 #1428 流程 | 全部适用 AC、兼容/支持矩阵、公开预算 |
| P6：更多平台 | 验收后的 macOS/Linux 包 | 每个声明环境重复安装验收 |

依赖具备后 P3/P4 可独立推进；P2 授权不等待投递。复用已有 Tracking Issue，负责方契约与消费者拆成聚焦 PR。
不能把被阻塞的必需 AC 标为不适用来关闭 #1428：完整交付需要一个平台上的受管本地安装、授权远程访问、持久
Handoff 投递、Review/Handoff 通知、恢复、无障碍和保留数据的移除。

## 验收与验证

负责角色：Desktop 负责打包 UI/原生行为，Server 负责公开语义，Install 负责安装器/服务/配置/分发，Delivery
负责 D6，Release 负责签名平台验收。这些是职责，不是已具名人员，D7 在 P0 退出前落实维护者。每项记录包含版本、
环境、fixture、结果和负责人；一次冒烟不能代表某行所有场景通过。

| ID | 阶段/负责方 | 必须观察到的行为 | 验证入口 |
| --- | --- | --- | --- |
| AC-01 | P3/P5 · Install + Desktop | 干净机器核验 runtime/宿主，无模型 Memory 保存/fts 召回成功 | 安装后的首次使用 |
| AC-02 | P1/P3 · Install | 区分过期/外部/占用状态，保留未知归属 | 服务 JSON/原生生命周期 |
| AC-03 | P3/P5 · Desktop + Install | 关闭/退出/重启/登录保留独立服务与工作，遵守所选启动方式 | 安装生命周期/恢复 |
| AC-04 | P3/P5 · Install + Release | 中断升级/签名/就绪失败有持久组件状态和兼容恢复 | 安装器故障/重启 |
| AC-05 | P1/P2 · Server + Desktop | 缺失/旧/未知握手、缺能力、身份变化不猜测支持或改投 | D1 连接 fixture |
| AC-06 | P1 · Desktop | loopback/base path/明文/TLS/重定向/代理限制准确，不转发凭据 | 共享向量/原生传输 |
| AC-07 | P1/P2 · Desktop | 连接/端点/token/Principal 变化隔离响应、游标、草稿和修改目标 | 打包后的并发交互 |
| AC-08 | P2 · Server + Desktop | 无 Scope 列表权限仍可访问精确 Handoff；拒绝 latest/相邻/宽泛/证据泄露 | Access 与桌面流程 |
| AC-09 | P2 · Server + Desktop | 分页/计数前过滤、不安全回退禁止、Review/发布仍授权 | Access/修改契约 |
| AC-10 | P2 · Server + Desktop | 过期版本/citation、重复提交、响应丢失不静默批准/重放 | 修改恢复场景 |
| AC-11 | P4 · Delivery + Desktop | 离线到达、游标过期、撤权/取消恢复精确授权收件箱 | D6 和接收端组合 |
| AC-12 | P0/P4 · Desktop + Release | 安装提示、拒绝许可、突发、退出、过期激活安全导航/回退 | 原生/冷启动激活 |
| AC-13 | P2 · Server + Desktop | 相同/改名/变化文本、BOM/换行、非法/超限、未知导入遵守身份/限制 | 导入/句柄 fixture |
| AC-14 | P0/P2 · Desktop | 恶意内容、伪造 generation/窗口/路径/链接不能执行、读秘密或意外修改 | 打包 capability/CSP |
| AC-15 | P0/P5 · Desktop + Release | 日志、URL、通知、导出、渲染层存储、遥测无秘密标记 | 输出检查 |
| AC-16 | P3/P5 · Install | 移除保留数据、用户编辑和仍引用的独立消费者 | 安装后的移除 |
| AC-17 | P2/P5 · Desktop | 双语、键盘/IME/阅读器/对比度/小窗口/200% 缩放可完成支持动作 | 无障碍流程 |
| AC-18 | P0/P5 · Release | 精确签名包在参考机器测量，P5 达到公开预算 | 基准/支持记录 |
| AC-19 | P2 · Server + Desktop | 大型/变化 Memory 在 D2 下完整有界遍历，D2 前如实限制 | 大 Scope fixture |
| AC-20 | P2 · Server + Desktop | Profile Review、Topic Memory 和未知 family 保持类型化/只读边界 | Family/Review fixture |
| AC-21 | P1/P3 · Server + Install + Desktop | 静态/注入 Provider、通用 401/503、部分轮换保持真实身份/错误/恢复 | 认证/配置流程 |
| AC-22 | P2 · Desktop | 积压、多页、变化/部分覆盖遵守预算，不伪造计数/历史 | 轮询行为 |
| AC-23 | P3 · Install + Release | updater 退出和 stable/preview 共享保留负责方恢复与唯一管理归属 | 打包升级/通道 |
| AC-24 | P0/P2 · Desktop | 无 Python/Server 可安装引导，共享资源不漂移，管理只用公开 API | 桌面构建/Dashboard 回归 |
| AC-25 | P3 · Server + Install | 维护迁移同 ID 恢复，流量前验证，无不兼容回滚 | 迁移/安装恢复 |
| AC-26 | P4 · Delivery + Desktop | 多设备/目标、未注册 Agent 不冒充，不把提示转为接受 | 目标关联 |

实现 PR 运行 `make check` 和相关行为测试；契约变更额外运行 `make api-generate`、`make contract-test`。
复用 Server/Access/传输/迁移/原生服务测试。共享 UI 需要 Dashboard 回归和桌面行为；打包需要真实安装测试。
文档运行 `make docs-test`（Fumadocs），核验标题/导航/链接及中英文阶段/依赖/AC ID 一致。Mock 或文档构建不能
证明原生行为合格。

## 实现调优与测量

实现期间选择并记录数值，RFC 不预先规定运行默认值。区分 Server 已有限制与客户端选择，调优不改变可观察保证。

| 待确定项 | 启用功能前所需证据 |
| --- | --- |
| 轮询间隔、抖动、并发和请求速率 | 提醒延迟与空闲 CPU、唤醒、Server 负载的权衡；手动重试时仍遵守上限 |
| Candidate 页大小、遍历过期与恢复策略 | 遵守 API 限制；覆盖空列表、多页、大积压及持续变更；后续页面可推进或明确显示覆盖限制 |
| 失败退避 | 断连/重连、认证拒绝与 Server 等待要求的行为 |
| 通知元数据容量和保留期 | 有界存储、去重、安全过期/淘汰和冷启动激活，不改变 Server 已读/投递状态 |
| 传输超时、解码响应/导出上限 | 慢响应、大响应、取消与流式处理；按操作恢复而非盲目重放修改 |
| 导入文件/正文上限 | 有界读取和 Unicode 处理；确认前解释限制，超限拒绝而不截断 |

在实现/发行记录中登记选定值、参考环境、数据集和测量结果。通过行为与回归测试覆盖不完整计数、范围变化、积压
推进和通知隐私；除非某个轮询间隔或内部调用次数本身属于公开外部预算，否则不在测试中固定它。

## 协调决策

这些问题都有建议默认方案和明确决策时间。缺失依赖仍是交付前提，不能写成已实现能力，也不妨碍提交设计供评审。

| 通俗问题 | 建议默认方案 | 何时决定 |
| --- | --- | --- |
| 先把哪个系统做好，谁长期维护和发版？ | Windows 11 x64 + SQLite；确定 Desktop/Install/Server/Release 负责人和一个 Agent Host/版本 | 接受 RFC 时确定平台；P0 结束前落实人员/宿主 |
| 和现在的 Dashboard 共用多少界面？ | 资源/规则/翻译/组件，独立客户端管理入口，不强制重写 Dashboard | 接受 RFC 时 |
| 安装器和投递还没好，能不能先发布？ | 先仅连接预览，再管理；P3/P4 独立，完整前不关闭 #1428 | 接受 RFC 时 |
| 第一版能连接哪些远程环境？ | 直连 HTTPS、运维发放 Bearer；暂不做代理/SSO/明文同意 | 接受 RFC 时；扩展需验收适配器 |
| 支持哪些 Server 版本，恢复或克隆后如何识别？ | D1 明确版本/生命周期，1.0.0 作为旧版验收候选 | P0 契约讨论，早于相关控制交付 |
| 安装和投递接口由谁提供，什么时候可用？ | D3–D6 原负责方维护 schema/恢复，桌面不另造替代 | 承诺 P3/P4 前 |
| 整个产品要多快、多省资源？ | 在具名硬件实测，公布含 Python/WebView 的数字预算 | P0 结束、P2 扩展前 |
