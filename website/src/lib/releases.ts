/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { Language } from './i18n';

export interface Release {
  version: string;
  date: string;
  title: Record<Language, string>;
  summary: Record<Language, string>;
  changes: Record<Language, string[]>;
  installCommand: string;
  githubUrl: string;
}

// Only stable releases belong here; publish pre-release notes on GitHub Releases.
export const releases: Release[] = [
  {
    version: 'v1.4.1',
    date: '2026-10-26',
    title: {
      en: 'Faster recall and Memory writes · Portable archives · More reliable Agent and Desktop workflows',
      zh: '优化检索与记忆写入 · 支持可移植归档 · 完善 Agent 与桌面工作流',
    },
    summary: {
      en: 'PowerContext 1.4.1 reduces repeated database work during recall and Memory writes, removes duplicate Source text from extraction requests, and adds offline archive export and restore. It also expands Kimi Code workflows, improves Desktop navigation and save recovery, and adds dependency and processing observations alongside managed logs.',
      zh: 'PowerContext 1.4.1 减少检索和记忆写入中的重复数据库操作，移除提取请求中重复的 Source 正文，并支持离线归档导出与恢复。同时扩展 Kimi Code 工作流，完善桌面端导航与保存失败后的恢复操作，增加依赖和处理状态观测，并提供托管日志。',
    },
    changes: {
      en: [
        'Reduce repeated database work by consolidating context recall queries, batching Memory lifecycle and authorization checks, reusing exact evidence revisions, and batching cited Experience reads. Scheduled processing skips consumed Scope windows, and Source capture can proceed while Memory extraction is running.',
        'Remove duplicate Source text from Atomic Memory extraction requests and compact repeated bodies in additional model projections. Skip reconciliation when a candidate has no comparable memories, and return invalid reconciliation decisions to the model for correction within the configured retry budget.',
        'Export, inspect, validate, and restore selected complete Scopes as portable .pcb archives. Preserve exact identities, revisions, evidence, and supported review state; validate conflicts before writes and recognize identical records on replay. This is an offline operator workflow, excludes deployment credentials and derived indexes, and does not replace a complete database backup or schema migration.',
        'Complete the Server migration framework through db-migrate status, plan, apply and verify for registered Server storage. Exact source layouts, reviewed plans, maintenance locks and explicit backup policies guard schema and data transitions; incomplete validation cannot report full Server readiness.',
        'Preserve registered source history and original recovery evidence across interrupted upgrades and idempotent retries. Startup validates authoritative records, search projections, embedding profiles and retained history before serving requests.',
        'Display migration steps as v1_4_1_001 through v1_4_1_003 while preserving existing revision IDs and checksums. The 1.3.0 and 1.4.0 source bridges remain separate; registration does not certify every intermediate software release.',
        'Declare twelve 1.3.0 upgrade combinations across SQLite, embedded seekDB, and OceanBase, with vectors and Code enabled or disabled. Publication requires the ten automatic cases to pass against the exact target artifact. The two seekDB cases with Code enabled remain manual-only, unverified, and excluded from supported upgrades; the 1.4.0 bridge is outside this CI matrix. Failed, skipped, or missing automatic cases block publication, and support remains upgrade-required until acceptance and the reviewed support declaration are complete.',
        'Inspect durable Topic Memory processing blocks and explicitly resume eligible work after correcting its cause. Reject Topic Memory semantic mismatches, make the recall embedding budget configurable, and report why preparation fell back from semantic recall.',
        'Expand the experimental Kimi Code integration with workspace Scope bindings and opt-in recommended/full tool profiles, including Handoff, Experience, managed Skill, and Candidate workflows. Initial setup still defaults to the minimal profile; larger profiles require the corresponding Server capabilities and explicit approval for review operations.',
        'Improve Desktop shared navigation, Scope recovery, exact revision reading, and draft preservation after failed or ambiguous saves. Add an unsigned macOS app and disk-image preview and fix Linux packaged navigation. Protocol checks and hosted-runner tests do not establish signed distribution, clean-machine installation, or complete native accessibility coverage.',
        'Add an independent local Ops tool for inspecting, configuring, and managing a prepared personal Server runtime through explicit preflight and apply operations. Runtime downloading, repair, and the Desktop installation bridge remain separate work. Add a Scope CLI command group and text search for Experiences in the web Dashboard.',
        'Add managed rotating log files and worker log aggregation, plus dependency and processing observations. Bound HTTP request bodies and active responses, authenticate MCP calls per HTTP request, preserve path authorization, and make local write ownership atomic. Repair eligible ownerless migrated memories without inventing historical owners.',
        'Aggregate streamed model output so generation can survive gateway idle timeouts while retaining a complete result. Python applications can explicitly inject the optional Jev reranker with its own credentials and pinned model; it is not enabled by default or configured through the Server environment settings.',
        'Guard automatic prompt capture and context preparation, bound long-prompt recall and WorkBuddy Scope discovery, and improve Codex shim resolution and Windows receipt replacement. Reject blank text Sources at creation so they cannot poison later processing; make tool failures identify the rejected field or reason.',
        'Upgrade Server, clients, and Agent integrations together only when the source version and backend have an accepted path for the exact target artifact. Stop writers, back up data and configuration, and review the migration plan. Drain unsupported queued or running 1.3.0 Dream tasks, preserve the historical embedding configuration, and follow the documented recovery procedure after interruption. Complete migration and full Server readiness verification before restarting services.',
      ],
      zh: [
        '合并上下文召回查询，批量处理 Memory 生命周期与授权检查，复用精确证据版本，并批量读取被引用的 Experience，减少重复数据库操作。定时处理跳过已消费的 Scope 窗口，Memory 提取期间仍可采集新的 Source。',
        '移除 Atomic Memory 提取请求中重复的 Source 正文，并压缩其他模型投影中的重复内容。候选记忆没有可比较对象时跳过协调步骤；模型给出无效协调决策时，在配置的重试预算内反馈原因并请求纠正。',
        '以可移植 .pcb 归档导出、检查、验证和恢复选定的完整 Scope，保留精确身份、历史版本、证据与受支持的审核状态；写入前检查冲突，重复恢复识别已有相同记录。这是离线运维操作，不包含部署凭据与派生索引，也不替代完整数据库备份或结构迁移。',
        '完善 Server 迁移框架，通过 db-migrate status、plan、apply、verify 维护已登记的 Server 存储。以精确来源结构、已审阅计划、维护锁和明确备份策略约束结构与数据转换；校验不完整时不能报告完整 Server 就绪。',
        '中断升级和幂等重试保留已登记来源历史与原始恢复证据。启动前检查权威记录、搜索投影、向量配置和必留历史，再开放服务。',
        '迁移步骤显示为 v1_4_1_001 至 v1_4_1_003，保留原有 revision ID 和校验和。1.3.0、1.4.0 来源桥接独立登记，不能据此推导所有中间软件版本均通过验收。',
        '登记真实 1.3.0 到目标产物的十二个升级组合，覆盖 SQLite、嵌入式 seekDB、OceanBase 及向量、Code 开关。发布要求十个自动验收组合针对目标发行物全部通过；两个启用 Code 的 seekDB 组合暂仅支持手动执行，尚未验收，不计入支持的升级范围。1.4.0 桥接也不在该 CI 矩阵内。自动验收失败、跳过或证据缺失均阻止发布；验收和支持声明审阅完成前保留 upgrade-required 策略。',
        '查看持久化的 Topic Memory 处理阻塞，修正原因后显式恢复符合条件的任务；拒绝语义不一致的 Topic Memory。支持配置召回 Embedding 预算，并说明上下文准备为何回退到非语义检索。',
        '试验性 Kimi Code 集成新增工作目录与 Scope 绑定，并提供需显式选择的 recommended/full 工具档位，覆盖 Handoff、Experience、托管 Skill 与 Candidate 工作流。首次安装仍默认 minimal；更多工具需要相应 Server 能力，审核操作仍需显式批准。',
        '完善桌面端共享导航、Scope 恢复、精确版本读取，以及保存失败或结果不确定时的草稿保留。新增未签名的 macOS 应用与磁盘镜像预览，修复 Linux 打包后的导航。协议检查和托管运行器测试不代表已完成签名发行、干净机器安装或完整原生无障碍验收。',
        '新增独立本地 Ops 工具，通过显式预检与执行操作检查、配置和管理已准备好的个人 Server 运行环境。运行环境下载、修复与桌面安装桥接仍属于独立工作；新增 Scope CLI 命令组和网页 Dashboard 的 Experience 文本搜索。',
        '新增托管轮转日志文件、Worker 日志汇聚，以及依赖和处理状态观测。限制 HTTP 请求体和活跃响应规模，按每次 HTTP 请求认证 MCP 调用，保留路径授权约束，并让本地写入与所有权登记原子提交。修复符合条件的迁移后无主记忆，同时保留历史所有权证据的真实边界。',
        '聚合模型流式输出，让生成过程在网关空闲超时限制下持续接收数据并返回完整结果。Python 应用可显式注入 Jev 重排器，并配置独立凭据与固定模型；该能力默认不启用，也不通过 Server 环境变量配置。',
        '为自动提示采集与上下文准备增加边界检查，限制长提示召回和 WorkBuddy Scope 发现，完善 Codex shim 解析与 Windows 回执替换。在创建时拒绝空白文本 Source，避免阻塞后续处理；工具失败时明确指出被拒绝的字段或原因。',
        '只有来源版本与后端具备针对目标发行物的已验收升级路径时，才能一起升级 Server、Client 与 Agent 集成。先停止写入、备份数据与配置并检查迁移计划；排空不兼容的 1.3.0 queued／running Dream 任务，保持历史向量配置，发生中断时遵循文档中的恢复流程。完成迁移和完整 Server 就绪验证后再重启服务。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.4.1"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.4.1',
  },
  {
    version: 'v1.4.0',
    date: '2026-10-19',
    title: {
      en: 'Dream: evolve knowledge · Notes: retain progress · Traceable knowledge changes · Project memory for Kimi Code',
      zh: '梦境，触发知识演进 · 便签，留住当前进展 · 知识变更，有据可回溯 · 让 Kimi Code 接上项目记忆',
    },
    summary: {
      en: 'PowerContext 1.4.0 lets users explicitly start Dream to propose knowledge revisions for review using task outcomes, corrections, and usage feedback, and retain current progress as Working Notes with evidence for Handoff. Browse and compare historical versions, and restore supported content as a new revision with its source and reason recorded. An experimental Kimi Code integration adds project memory saving, search, and relevant context injection in a new session.',
      zh: 'PowerContext 1.4.0 支持显式发起梦境，结合任务结果、纠正与使用反馈提出待审核的知识修订；用便签留住当前进展，为交接保留仍有效的依据；沿历史版本查看、对比内容变化，将支持的历史内容恢复为新修订，并记录来源版本和原因。新增试验性 Kimi Code 集成，支持保存、搜索项目记忆与新会话上下文注入。',
    },
    changes: {
      en: [
        'Dream: evolve knowledge. Use task outcomes, explicit corrections, or usage feedback as a reason to start Dream, selecting the relevant knowledge and evidence for a revision proposal. Propose changes to Atomic Memory, Profile, Topic Memory, Handoff, Prompt configuration, and tags through the unified Candidate review flow. Inspect the proposal and evidence before explicit approval. Available operations depend on Server configuration; Skill revision remains disabled until its trusted evaluation requirement is met. Handoff approval publishes a revision without executing or continuing work.',
        'Notes: retain current progress. Capture findings, progress, and next steps as short-lived Working Notes, and cite still-valid evidence in a Handoff. Supported note kinds cover inspection, abandoned approaches, validation, unresolved questions, and host context. Notes retain exact Source identities and are excluded from automatic generation. Optional expiry prevents new consumption while preserving audit reads; physical cleanup and automatic promotion are not included.',
        'Trace knowledge changes in the Dashboard: browse revision history, compare exact versions, and roll supported historical content forward as a new revision with the source, reason, and actor recorded. Atomic Memory history and comparison are read-only here; restoration uses its separate preview and confirmation flow. New revision timestamps do not invent dates for older records.',
        'Connect Kimi Code to project memory through an experimental integration: save and search memories, read exact revisions, and receive relevant context in a new session. Use an explicitly configured Scope, with setup, diagnostics, native MCP operations, prompt capture, and content-free Hook runtime observations.',
        'Opt in to bounded startup context in Codex and Claude Code. Select active Atomic Memories with the bootstrap-context tag and optionally supply an exact committed Handoff. Delivery receipts support lifecycle deduplication and keep fully delivered exact Memory revisions out of the first ordinary recall; content remains cited, untrusted history and the feature is disabled by default.',
        'Extend Atomic Memory retrieval through the shared Artifact search path, unify Chinese segmentation and English word analysis across supported backends, and configure full-text/vector fusion weights for Atomic Memory and Topic Memory. Preserve supporting Source language during generation, reduce answer-format noise in text queries, and handle long SQLite and OceanBase queries.',
        'Expand DSH to native MCP workflows with catalog checks, Scope enforcement, approval handling, and caller-content secret screening. Refresh generated host guidance while preserving locally edited guidance.',
        'Add scope-local stable handles that permanently name an exact Artifact revision, alongside revision metadata for tracing replacements and restorations. Binding a handle requires Scope administration; resolving it preserves the referenced immutable version.',
        'Add bounded request sanitization for decision backends, including structured subject and evidence values, known sensitive values, and supported provider-key formats. Projection changes do not rewrite authoritative content or decide write policy, and the sanitizer is not a general PII detector. The legacy collection MemoryWriteGate remains unsupported by the Atomic Runtime.',
        'Standardize structured operational logs, metrics, and trace identity, expose extraction diagnostics in CLI capabilities, preserve exact captured Source identities on reads, and pin SQLite content before custom authorization. Improve cancellation cleanup and omit the placeholder x-api-key when Anthropic-compatible requests use an explicitly configured Authorization header.',
        'Add cross-host work-continuity acceptance, offline Memory write-gate evaluation, and Dream generation baselines. Extend Kimi Code and ZCode acceptance workflows and refresh Desktop contract validation. These workflows do not establish a universal quality gain or change Windows Desktop preview qualification.',
        'Upgrade Server, Client, CLI, MCP, and Agent integrations together. Legacy Memory collections and citation models leave online APIs; retained Memory routes return Atomic Memory records, and unsupported collection operations fail explicitly. Back up data, stop writers, and complete offline Atomic Memory conversion and verification. Candidate APIs move from /v1/artifact-candidates/* to /v1/candidates/* without aliases, and Dream requires contract 2. Existing Dream storage needs coordinated migration after draining unfinished runs; the current unified database bundle still reports server_ready=false and does not provide a complete production upgrade path. Do not bypass migration_required or migration_framework_not_ready.',
      ],
      zh: [
        '梦境，触发知识演进：以任务结果、明确纠正或使用反馈为复盘契机，显式发起梦境，对照现有知识与证据提出待审核修订。通过统一 Candidate 流程修订 Atomic Memory、Profile、Topic Memory、Handoff、Prompt 配置及标签，先检查提案与证据，再显式批准。可用操作取决于 Server 配置；Skill 修订在满足可信评测要求前保持关闭。批准 Handoff 会发布新版本，不会执行或续接工作。',
        '便签，留住当前进展：用短期记录承接当前发现、进度和待办，在交接时引用仍有效的依据。支持记录检查结果、放弃的方案、验证证据、未解决问题和 Host 上下文，保留精确 Source 身份，并排除在自动生成之外。可选过期时间会阻止新的消费，同时保留审计读取；不包含物理清理或自动晋升为长期记忆。',
        '知识变更，有据可回溯：在 Dashboard 沿历史版本检查、对比内容变化，按需将支持的历史内容恢复为新修订，记录来源版本、原因和操作者。Atomic Memory 的历史与对比在此只读，恢复使用独立的预览和确认流程。新增修订时间信息不会为旧记录补造日期。',
        '让 Kimi Code 接上项目记忆：通过试验性集成保存、搜索记忆、读取精确历史版本，并在新会话中注入相关上下文。使用显式配置的 Scope，提供安装、诊断、原生 MCP 操作、提示采集及不含内容的 Hook 运行观测。',
        '在 Codex 和 Claude Code 中按需启用有界的会话开场上下文。通过 bootstrap-context 标签选择活跃 Atomic Memory，也可传入一个精确的已提交 Handoff。投递回执支持生命周期去重，并让首次常规召回跳过已完整投递的同一记忆版本；内容始终带有引用、作为不可信历史处理，该功能默认关闭。',
        '将 Atomic Memory 纳入统一 Artifact 检索路径，在支持的后端统一中文分词与英文词语分析，并为 Atomic Memory 和 Topic Memory 配置全文与向量融合权重。生成时保留支撑 Source 的语言，减少回答格式要求对文本查询的干扰，并处理 SQLite 与 OceanBase 长查询。',
        'DSH 扩展原生 MCP 工作流，检查工具目录、执行 Scope 约束、保留审批流程，并筛查调用方内容中的敏感值。更新自动生成的 Host 指引，同时保留本地修改。',
        '新增 Scope 内的稳定句柄，永久指向一个精确 Artifact 版本，并通过修订元数据追踪替换和恢复。绑定句柄需要 Scope 管理权限，解析后仍读取其指向的不可变版本。',
        '为决策后端增加有界的请求脱敏，覆盖结构化主体和证据、已知敏感值及支持的服务商密钥格式。请求投影不会改写权威内容或决定写入策略，脱敏器也不是通用个人信息检测器。Atomic Runtime 仍不支持旧集合式 MemoryWriteGate。',
        '统一结构化运行日志、指标与追踪身份，在 CLI capabilities 中展示提取诊断，精确读取已采集的 Source，并在自定义授权前固定 SQLite 内容快照。改进取消时的连接清理；为 Anthropic 兼容请求显式配置 Authorization 请求头时，不再附加占位 x-api-key。',
        '新增跨 Host 工作连续性验收、Memory 写入门控离线评测及 Dream 生成基线，扩展 Kimi Code、ZCode 验收工作流，并更新 Desktop 契约校验。这些工作流不代表普遍的质量提升，也不改变 Windows Desktop 的预览验收边界。',
        'Server、Client、CLI、MCP 和 Agent 集成需一起升级。旧 Memory 集合与引用模型退出在线 API；保留的 Memory 路由返回 Atomic Memory 记录，不支持的集合操作会明确失败。先备份、停止写入，再完成 Atomic Memory 离线转换与验证。Candidate API 从 /v1/artifact-candidates/* 改为 /v1/candidates/*，不提供别名，Dream 要求契约 2。已有 Dream 存储需先排空未完成任务再协调迁移；当前统一数据库迁移包仍报告 server_ready=false，尚不构成完整的生产升级路径，不应绕过 migration_required 或 migration_framework_not_ready。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.4.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.4.0',
  },
  {
    version: 'v1.3.0',
    date: '2026-10-12',
    title: {
      en: 'Independent Atomic Memory · Govern decision-model writes · Artifact merge and restoration',
      zh: '独立 Atomic Memory · 决策模型写入治理 · Artifact 合并与恢复',
    },
    summary: {
      en: 'PowerContext 1.3.0 lets users manage each memory independently and adds policy controls, privacy protection, and traceable records when decision models assess which information is worth remembering. Users can also merge duplicate content, restore historical versions, and more easily find memories, Experiences, and Skills, alongside improved Server diagnostics and Agent integrations.',
      zh: 'PowerContext 1.3.0 让每条记忆都能独立管理，并为决策模型判断“哪些信息值得记住”加入策略控制、隐私保护和可追踪记录。同时支持合并重复内容、恢复历史版本，更方便地查找记忆、经验与技能，并完善 Server 诊断和 Agent 集成。',
    },
    changes: {
      en: [
        'Create, read, revise, search, forget, merge, and restore independent Atomic Memory Artifacts with immutable content history and explicit lifecycle state. Existing collection databases require the documented offline Atomic Memory migration before use.',
        'Govern decision-model assessment of Memory write evidence with disabled, shadow, advisory, and enforcing modes. The gate remains opt-in; when enabled, it defaults to shadow and no_external_call, preserving writes without calling a model. Raw content evaluation requires explicit local_only configuration and a loopback backend. Scope-isolated durable observations retain policy versions, references, counts, and outcomes without candidate or evidence text; model failures preserve the write path. Enforcing promotion still requires calibration and evaluation; no public audit dashboard or hosted-redacted transport is included.',
        'Share merge and restoration across Artifact families, preserving exact input revisions and historical evidence. Search Artifacts by family.',
        'Add an unsigned Windows Desktop preview for connecting to an existing Server, selecting a Scope, saving notes, full-text search, reading exact revisions, and copying content and references. The preview also supports English/Chinese, themes, and connection diagnostics; complete native interaction and formal release qualification remain unfinished.',
        'Choose automatic, full-text, semantic, or hybrid Memory search in the web Dashboard and inspect the executed mode, matching channels, and retrieval scores. Local Dashboard access can omit authentication; the Dashboard still requires explicit enablement.',
        'Discover Server protocols and capabilities and inspect persistent deployment identity. Expose read-only extraction diagnostics and optional daily RecallEffort metrics; MCP request counts do not establish successful tool execution.',
        'Import Codex sessions, refresh stale setup endpoints safely, bypass proxies for loopback Client transports, and improve DSH Desktop setup, native MCP catalog diagnostics, and handling of recoverable Server rejections.',
        'Extend the Dify integration with 19 HTTP-backed tools and provide Experience and Skill applicability examples. Add compact Handoff continuity hints and improve ZCode lifecycle workflows and runtime diagnostics.',
        'Persist smaller extraction windows after timeouts, stop redispatching exhausted Topic Memory windows, and classify deterministic rejections separately. Score recall sufficiency using vector relevance and keep SQLite vector searches within the supported result limit.',
        'Expand paired continuation evaluation to Claude Code and Pi, task-completion workloads, and MemoryCode dialogues; report intervals, latency, and usage. These evaluation workflows do not imply a universal quality improvement.',
        'Add a limited unified database migration bundle. The bundle covers four registered Artifact tables and explicitly reports server_ready=false; it is not a complete production Server upgrade path.',
        'Upgrade the Server, clients, and Agent integrations together and reopen Agent sessions. Back up existing databases and follow the Atomic Memory migration guide; do not use the limited unified migration bundle as a replacement for the required migration.',
      ],
      zh: [
        '以独立 Atomic Memory Artifact 创建、读取、修订、搜索、遗忘、合并和恢复单条记忆，保留不可变内容历史与显式生命周期状态。已有集合式 Memory 数据库需先按文档完成离线 Atomic Memory 迁移。',
        '为决策模型判断 Memory 写入证据提供 disabled、shadow、advisory、enforcing 四种策略模式。写入门控需显式启用；启用后默认采用 shadow 与 no_external_call，不调用模型、不阻断写入。评估原始内容需显式配置 local_only 和本地回环模型入口。按 Scope 隔离的持久化观测记录保存策略版本、引用、数量与处理结果，不保存候选或证据正文；模型故障时保留写入流程。进入 enforcing 前仍需校准与评测，本次未提供公开审计界面或托管脱敏调用。',
        '在各 Artifact 类型间共享合并与恢复流程，保留精确的输入 Revision 和历史证据；支持按类型搜索 Artifact。',
        '新增未签名的 Windows 桌面预览，连接已有 Server 后可选择 Scope、保存笔记、全文搜索、读取精确历史版本，以及复制正文和引用；支持中英文、主题切换和连接诊断。完整原生交互与正式发行验收尚未完成。',
        '网页 Dashboard 支持选择自动、全文、语义或混合搜索，查看实际执行模式、命中通道与检索得分；本地访问可不配置认证，Dashboard 仍需显式启用。',
        '发现 Server 协议与能力，查看持久化部署身份；提供只读提取诊断和可选的每日 RecallEffort 指标。MCP 请求计数不代表工具执行成功。',
        '导入 Codex 会话，安全刷新过期的 setup 入口，让 Client 本地回环连接绕过代理，并完善 DSH Desktop 配置、原生 MCP 工具目录诊断及可恢复 Server 拒绝的处理。',
        '为 Dify 集成新增 19 个基于 HTTP 的工具，提供 Experience 与 Skill 适用性选择示例；增加精简 Handoff 续接提示，并完善 ZCode 生命周期工作流与运行诊断。',
        '提取超时后持久化较小窗口，停止重复派发已耗尽的 Topic Memory 窗口，并单独记录确定性拒绝；根据向量相关性判断召回充分性，让 SQLite 向量检索遵守结果数量限制。',
        '将续接对照评测扩展到 Claude Code、Pi、任务完成工作负载和 MemoryCode 对话，并报告区间、延迟与用量。这些评测流程不代表所有任务都能获得相同质量提升。',
        '增加范围有限的统一数据库迁移包。迁移包仅覆盖四张已注册 Artifact 表，并明确返回 server_ready=false，不能作为完整生产 Server 升级路径。',
        'Server、客户端和 Agent 集成需一起升级，并重新打开 Agent 会话。先备份已有数据库，再按 Atomic Memory 迁移指南操作；范围有限的统一迁移包不能替代所需迁移。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.3.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.3.0',
  },
  {
    version: 'v1.2.0',
    date: '2026-09-29',
    title: {
      en: 'Understand repository code · Bound Memory growth · Assess write evidence · Generate Experiences and import Skills in Codex',
      zh: '理解仓库代码 · 限制 Memory 增长 · 判断写入证据 · 在 Codex 中生成经验、导入技能',
    },
    summary: {
      en: 'PowerContext 1.2.0 adds optional native repository understanding, Memory capacity controls and an evidence-based write gate, and introduces an experimental ZCode integration. In Codex, users can generate Experience and Skill candidates and import external Skills for review. It also adds paired continuation evaluation with Codex and Bub and automates release-version maintenance.',
      zh: 'PowerContext 1.2.0 新增可选的原生仓库代码理解、Memory 容量控制和基于证据的写入门控，并加入试验性 ZCode 集成。用户可以在 Codex 中生成经验和技能候选，并导入外部技能供审核。本次还提供 Codex 与 Bub 的续接任务对照评测和发布版本维护命令。',
    },
    changes: {
      en: [
        'Index mixed Python, JavaScript, TypeScript, and Go Git worktrees; locate definitions, inspect static relationships and possible affected tests, and read source evidence through CLI, HTTP, Python Client, Runtime, and MCP queries. Code understanding requires the code extra, is disabled by default, and needs no generation or embedding model. Local indexing has been verified on Linux; macOS and Windows local indexing remain unverified.',
        'Refresh code evidence with local index and sync commands. Queries detect changed source content and reject stale evidence; optional prepare_context and Codex or Claude Code hook settings can include bounded code context. Embedded seekdb stores graph data in its database with local evidence files; other deployments use a separate SQLite cache. Changing the backend or database path requires a new index, and static relationships do not prove runtime calls or that tests can be skipped.',
        'Inspect Memory capacity through Runtime, Client, HTTP, or MCP. Default per-Revision budgets are 5,000 active entries, 10,000 manifest entries, and 4 MiB of canonical content; writes that increase an exceeded dimension return memory_capacity_exceeded. Optional in-process compaction can remove eligible untagged inactive entries from the current manifest. Compaction is disabled by default and does not delete historical Revisions or cap total database size.',
        'Optionally assess Memory candidates against their cited evidence before writing. Decision assistance and the Memory write gate are both disabled by default. When enabled, the gate can refuse unsupported or over-budget writes and surface reasons through explicit operations, flush results, and worker diagnostics; decision-backend failures fall back to the existing write path. Held extraction windows still advance their cursor and are not automatically retried. Enable and calibrate the gate separately from extraction.',
        'Update Memory full-text and vector search projections incrementally while reusing unchanged embeddings, preserve canonical Memory error details through HTTP, and fix SQLite Topic Memory publication on builds without RETURNING support.',
        'Use Codex tools to generate or propose Experience and Skill candidates and discover or import external Skills, with explicit review before approval. Bind a checkout to an existing Scope for later sessions, reuse saved credentials for native MCP, and distinguish basic connectivity from full tool coverage in doctor diagnostics. External Skill discovery reads configured roots on the Server host; importing or approving a Skill does not install or execute it.',
        'Connect ZCode CLI and Windows desktop through the new community, experimental integration, including setup, doctor, prompt capture, prepared-context injection, MCP operations, and Handoff workflows. Live coverage includes CLI 0.16.9 and Windows desktop 3.14.3; other desktop versions and direct remote HTTPS ingress remain unverified.',
        'Honor Hermes pre-compression checkpoint API v2 and skip automatic writes outside primary Agent contexts. Keep WorkBuddy recall focused on the submitted turn and bound Scope lookup time; allow DSH cold readiness probes to finish. Improve native service startup waits and avoid background throttling on macOS.',
        'Support embedding providers that reject the dimensions request field while retaining configured vector dimensions. Allow bounded Qwen thinking settings for Topic Memory, and record model usage outside model deadlines so accounting failures do not fail the model operation.',
        'Compare PowerContext off and on across independent continuation sessions with either Codex or Bub, recording host, model, and reasoning settings and grading the final recall step. Add LoCoMo-Plus evaluation and a LongMemEval-V2 smoke adapter. LongMemEval-V2 validation is limited to the smoke workflow; full-tier execution remains unvalidated.',
        'Maintain release references with make version-bump VERSION=1.2.0 and verify consistency with make version-check. Before publishing, make release-check also checks for matching bilingual website release notes and installation links. Package versions remain derived from powercontext-v tags, and existing release notes retain their history.',
        'Upgrade the Server, clients, and Agent plugins together, reopen Agent sessions, and back up existing databases before startup schema updates. Check existing Memory against the new capacity defaults before resuming growth. Code indexing, decision assistance, the write gate, and compaction require explicit opt-in. Older deployments with unfinished Artifact processing migration must complete it before starting the new configuration; Windows remains experimental.',
      ],
      zh: [
        '为 Python、JavaScript、TypeScript 和 Go 混合 Git 工作区建立索引，通过 CLI、HTTP、Python Client、Runtime 和 MCP 查询定义、静态关系、可能受影响的测试及源码证据。代码理解需要安装 code 扩展，默认关闭，无需生成或 Embedding 模型；本地索引已在 Linux 验证，macOS 与 Windows 本地索引尚未验证。',
        '通过本地 index 和 sync 命令刷新代码证据；查询检测源码内容变化并拒绝过期证据。可在 prepare_context 及 Codex、Claude Code Hook 中单独启用有预算限制的代码上下文。嵌入式 seekdb 将图数据存入数据库并保留本地证据文件，其他部署使用独立 SQLite 缓存；切换后端或数据库路径后需重建索引。静态关系不能证明运行时调用，也不能作为跳过测试的依据。',
        '通过 Runtime、Client、HTTP 或 MCP 查看 Memory 容量。每个 Revision 默认限制为 5,000 条活跃条目、10,000 条清单条目和 4 MiB 规范内容；继续增大超限维度的写入返回 memory_capacity_exceeded。可选的进程内压缩可从当前清单移除符合条件且未加标签的失活条目，默认关闭，不删除历史 Revision，也不限制数据库总大小。',
        '可在 Memory 写入前依据候选各自引用的证据进行判断。Decision assistance 和 Memory 写入门控均默认关闭；启用后可拒绝证据不足或超出评估预算的写入，并通过显式操作、flush 结果和 Worker 诊断暴露原因。判断模型故障时回退原有写入流程；被拒绝的提取窗口仍会推进游标，不会自动重试。需独立于提取功能配置并校准门控。',
        '按变更增量更新 Memory 全文与向量检索投影，复用未改变的 Embedding；通过 HTTP 保留规范化的 Memory 错误详情，并修复不支持 RETURNING 的 SQLite 环境中的 Topic Memory 发布。',
        '在 Codex 中生成或提议 Experience、Skill Candidate，发现和导入外部 Skill，并经过显式审核后批准。可将工作区绑定到已有 Scope 供后续会话复用，让原生 MCP 复用已保存凭据，并在 doctor 中区分基础连接与完整工具覆盖。外部 Skill 发现读取 Server 主机配置的目录；导入或批准 Skill 不会安装或执行它。',
        '新增 ZCode CLI 与 Windows 桌面端社区试验性集成，支持 setup、doctor、提示采集、准备好的上下文注入、MCP 操作和 Handoff 工作流。真实运行覆盖 CLI 0.16.9 与 Windows 桌面端 3.14.3；其他桌面版本及远程 HTTPS 直接入口尚未验证。',
        '适配 Hermes 压缩前检查点 API v2，并跳过主 Agent 以外上下文的自动写入；让 WorkBuddy 只围绕本次提交的提示召回并限制 Scope 查询耗时，允许 DSH 冷启动就绪探测完成。改进原生服务启动等待，并避免 macOS 后台节流。',
        '支持不接受请求 dimensions 字段的 Embedding 服务，同时保留已配置的向量维度；允许为 Topic Memory 配置有界的 Qwen thinking 设置，并将模型用量记录移出模型调用超时范围，避免统计故障导致模型操作失败。',
        '使用 Codex 或 Bub 在独立续接会话中对比 PowerContext 关闭和开启的效果，记录 Host、模型及推理配置，并以最后的召回步骤评分；新增 LoCoMo-Plus 评测和 LongMemEval-V2 冒烟适配器。LongMemEval-V2 的验证范围为冒烟工作流，全量运行尚未验证。',
        '通过 make version-bump VERSION=1.2.0 维护发布引用，使用 make version-check 检查一致性；发布前再运行 make release-check，检查匹配的双语网站发布说明及安装链接。Python 包版本仍由 powercontext-v 标签生成，已有发布记录保留原样。',
        'Server、客户端和 Agent 插件需一起升级，重新打开 Agent 会话，并在启动时的数据库结构更新前备份数据。恢复 Memory 增长前先检查已有内容是否超过新增的默认容量；代码索引、Decision assistance、写入门控和压缩均需显式启用。尚未完成 Artifact 处理迁移的旧部署应先完成迁移再启动新配置；Windows 仍为试验性支持。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.2.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.2.0',
  },
  {
    version: 'v1.1.0',
    date: '2026-09-20',
    title: {
      en: 'Expand recall on demand · Review failures and revise Experiences · Unify tag management · Improve Agent handoffs and diagnostics',
      zh: '按需补充检索 · 复盘失败、修订经验 · 统一标签管理 · 增强 Agent 交接与诊断',
    },
    summary: {
      en: 'PowerContext 1.1.0 adds an optional recall sufficiency gate, evidence-based recurring failure records, tags across all built-in Artifact families, and native MiniMax embeddings, alongside expanded Pi and OpenClaw workflows and better Source provenance.',
      zh: 'PowerContext 1.1.0 新增可选召回充分性门控、基于证据的重复失败记录、全部内置 Artifact 类型的标签和 MiniMax 原生 Embedding，并完善 Pi 与 OpenClaw 工作流及 Source 溯源。',
    },
    changes: {
      en: [
        'Optionally expand thin recall with at most two additional searches while preserving Scopes, families, result limits, and byte budgets. The gate is disabled by default. Recognized standalone execution instructions no longer count as lexical evidence; Topic query embedding during preparation falls back to full-text search after 250 ms.',
        'Manage Topic Memory through generic Artifact create, replace, revision, lineage, tag, and publication operations, and customize Topic Memory processing Prompts by Scope.',
        'Organize all seven built-in Artifact families with tags, including Profile and saved Prompt Artifacts. Queries that omit families now cover every built-in family.',
        'Record exact evidence of recurring failures and verified avoidance. Three consecutive recurrences against one Experience revision can trigger a revision proposal for review when the repair surface is Experience content; approval and execution remain explicit.',
        'Use the Pi Full core integration profile for Memory changes, statistics, Topic Memory queries, structured Handoffs, Task Outcomes, candidate generation and inspection, and External Skill discovery and import. Durable writes require confirmation; candidate generation does not approve or install a Skill.',
        'Use OpenClaw /pc commands and work coordination workflows, and follow aligned Agent guidance and layered Skill routing across integrations.',
        'Inspect recalled-context token reduction in Claude Code, OpenCode, and Codex, observe DSH recall and capture status through /pc, and trace Memory writes.',
        'Read Profiles in the personal Dashboard, export the exact rendered Handoff Markdown, and read Experiences through dedicated CLI commands.',
        'Strengthen resource access decisions with shared authorization filters and a transaction snapshot boundary, and reduce database transactions when reading Scope statistics.',
        'Batch SQLite vector completeness checks while detecting stale Memory heads and missing or mismatched vectors.',
        'Use native MiniMax embeddings with separate document and query requests, batching, response validation, and timeout handling.',
        'Preserve registered Source types and identifiers in Artifact provenance, process persisted remote Source evidence for Topic Memory, distinguish missing Memory from pending ownership, support Markdown Handoff Reports over MCP, and extend WorkBuddy prompt Hooks to 30 seconds.',
        'Improve Windows integration setup, command encoding, service inspection, and file URI handling, including native Codex MCP authentication verification. Windows remains experimental.',
        'Upgrade the Server, clients, and Agent integrations together. SourceTypeReference.source_type is now an open string. Back up existing databases and complete startup schema updates with one Server instance before starting other instances. Older deployments with unfinished Artifact processing migration must complete that migration first.',
      ],
      zh: [
        '按需启用召回充分性门控，最多追加两轮搜索，并保留 Scope、类型、条数与字节预算；该功能默认关闭。已识别的独立执行指令不再作为词法证据，准备上下文时的 Topic 查询 Embedding 超过 250 ms 则回退全文检索。',
        '使用通用 Artifact 创建、替换、修订历史、证据链、标签和发布操作管理 Topic Memory，并按 Scope 自定义 Topic Memory 处理 Prompt。',
        '标签覆盖全部七类内置 Artifact，包括 Profile 和已保存的 Prompt；查询省略 families 时覆盖全部内置类型。',
        '记录失败复发与经验证已避免的精确证据；同一 Experience 修订连续复发三次且修复面为经验内容时，可提出待审核修订，批准与执行仍需显式操作。',
        'Pi 达到 Full 核心集成档位，支持 Memory 变更、统计、Topic Memory 查询、结构化 Handoff、Task Outcome、Candidate 生成与查看，以及 External Skill 发现与导入。持久化写入需要确认，生成 Candidate 不会自动批准或安装 Skill。',
        '通过 OpenClaw 的 /pc 命令和工作协调流程延续任务，并在各集成中使用统一的 Agent 指引和分层 Skill 路由。',
        '在 Claude Code、OpenCode 和 Codex 中查看召回上下文的 token 缩减信息，通过 DSH /pc 查看召回与采集状态，并追踪 Memory 写入。',
        '在个人 Dashboard 中读取 Profile，导出与页面内容一致的 Handoff Markdown，并通过专用 CLI 命令读取 Experience。',
        '通过共享权限过滤条件和事务快照边界增强资源访问决策，并减少读取 Scope 统计时使用的数据库事务。',
        '合并 SQLite 向量完整性检查，并识别过期的 Memory 引用及缺失或不匹配的向量。',
        '支持 MiniMax 原生 Embedding，区分文档与查询请求，并提供批量处理、响应校验和超时控制。',
        '保留已注册 Source 的类型与标识，处理远程 Source 的持久化证据，区分 Memory 不存在与所有者信息待写入状态，支持 MCP Markdown Handoff Report，并将 WorkBuddy 提示 Hook 超时扩展到 30 秒。',
        '改进 Windows 集成安装、命令编码、服务检查和文件 URI 处理，包括验证 Codex 原生 MCP 认证。Windows 仍为试验性支持。',
        'Server、客户端和 Agent 集成需一起升级。SourceTypeReference.source_type 改为开放字符串。先备份已有数据库，由一个 Server 实例完成启动时的结构升级后再启动其他实例；尚未完成 Artifact 处理迁移的旧部署需先完成该迁移。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.1.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.1.0',
  },
  {
    version: 'v1.0.0',
    date: '2026-09-10',
    title: {
      en: 'Guided setup, Topic Memory, and reviewed learning',
      zh: '交互式配置、Topic Memory 与审核式经验沉淀',
    },
    summary: {
      en: 'PowerContext 1.0.0 connects guided onboarding with evolving Topic Memory, scoped Profiles, and a reviewed path from Memory to Experience and Skill, while preserving cross-session Memory and Handoff workflows.',
      zh: 'PowerContext 1.0.0 将交互式配置、持续演进的 Topic Memory、按 Scope 管理的 Profile，以及经过审核的 Memory → Experience → Skill 工作流串联起来，延续跨会话记忆与工作交接。',
    },
    changes: {
      en: [
        'Configure storage, model APIs, Server access, background processing, and Agent connections through an English or Chinese wizard. Persist Agent authorization and generate matching setup commands and next steps.',
        'Turn captured Sources into evolving Topic Memory with evidence and revision history; inspect Sources, topics, and processing state in the personal Dashboard. Automatic processing requires configured model APIs.',
        'Maintain scoped Profiles and subject-attributed Sources, customize processing Prompts by Scope, and select how Profiles and Memory are assembled into prepared context.',
        'Organize Artifacts and Memory entries with tags, manage versioned Prompt Artifacts, and discover Scopes and Sources through the public API.',
        'Run durable background processing for Memory, Topic Memory, and Profiles. Use Dream to propose Experience from exact Memory citations and Skill from approved Experiences; inspect and approve Candidates before they become Artifacts. Approval does not install or execute a Skill.',
        'Connect MiniMax through its native plugin, send Claude Code MCP authorization correctly, and inspect effective DSH configuration and recalled-context snapshots through layered diagnostics.',
        'Start with explicit Memory operations without model credentials; use bilingual documentation search, the installation walkthrough, and guided Jupyter tutorials for further workflows.',
        'Strengthen evidence access checks, enforce revoked Scope contributions, validate raw Skill archive paths, and support weak or multiple If-None-Match validators on Artifact reads.',
        'Upgrade the Server, clients, and Agent integrations together and back up existing databases before startup schema changes. The Dashboard is now an opt-in personal viewer requiring static Bearer authentication; Dream and Candidate review use CLI, Client, or HTTP APIs. Server startup loads a local .env unless disabled, and remote plaintext HTTP requires explicit client consent. Windows remains experimental.',
      ],
      zh: [
        '通过交互式向导配置存储、模型 API、Server 访问、后台处理与 Agent 连接，持久化 Agent 授权，并生成匹配版本的安装命令和后续操作步骤。',
        '将采集到的 Source 整理为持续演进的 Topic Memory，保留证据和修订历史；在个人 Dashboard 中查看 Source、主题与处理状态。自动处理需要配置模型 API。',
        '按 Scope 维护 Profile 和带主体归属的 Source，自定义处理 Prompt，并选择 Profile 与 Memory 在准备好的上下文中的组织方式。',
        '使用标签组织 Artifact 与 Memory 条目，管理带版本的 Prompt Artifact，并通过公开 API 发现 Scope 和 Source。',
        '通过持久化后台任务处理 Memory、Topic Memory 和 Profile；使用 Dream 从精确 Memory 引用提炼 Experience，再从已批准的 Experience 生成 Skill。检查并批准 Candidate 后才会写入 Artifact；批准不会安装或执行 Skill。',
        '支持原生插件接入 MiniMax，修复 Claude Code 的 MCP 授权传递，并通过分层诊断检查 DSH 实际生效的配置与召回上下文快照。',
        '无需模型凭据即可使用显式 Memory 操作；通过中英文文档搜索、安装教程和 Jupyter 示例继续学习其他工作流。',
        '增强生成过程中的证据访问检查，执行撤销后的 Scope 写入权限，校验 Skill 压缩包原始路径，并支持 Artifact 读取中的弱 ETag 和多个 If-None-Match 值。',
        'Server、客户端和 Agent 集成需一起升级，并在启动时的数据库结构变更前备份数据。Dashboard 改为显式启用、使用静态 Bearer 认证的个人内容查看器；Dream 与 Candidate 审核通过 CLI、Client 或 HTTP API 操作。Server 启动默认读取本地 .env，远程明文 HTTP 连接需要客户端明确同意。Windows 仍为试验性支持。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==1.0.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v1.0.0',
  },
  {
    version: 'v0.2.0',
    date: '2026-09-07',
    title: {
      en: 'Organize context, control access, and distribute Skills',
      zh: '组织上下文、控制访问、分发 Skill',
    },
    summary: {
      en: 'PowerContext adds Server-owned Scopes, resource-level access control, and a complete Skill package workflow, with native personal services on macOS, Linux, and Windows.',
      zh: 'PowerContext 新增由 Server 管理的 Scope、资源级访问控制和完整的 Skill 包工作流，并支持 macOS、Linux 和 Windows 原生个人服务。',
    },
    changes: {
      en: [
        'Organize Scopes with parent relationships and explicit context references, reuse Agent bindings, and inspect exact, subtree, or all-Scope views in the Dashboard and Handoff Reports.',
        'Apply one access-control boundary across HTTP, MCP, and Dashboard data, with resource roles, revocable Bindings, audit records, and Casbin or AuthZEN adapters.',
        'Review and version complete Skill packages, including scripts and resources; publish to local Codex or Claude Code targets or deliver exact Revisions through a remote Receiver.',
        'Keep a personal Server running through the native service manager on macOS, Linux, or Windows using powercontext service install/status/uninstall.',
        'Create and read Sources, manage Artifact Revisions through scoped REST endpoints, and explore the interactive Scalar API reference.',
        'Configure separate generation, embedding, and reranking endpoints and model settings; inspect process metrics and host-visible integration diagnostics.',
        'Use the redesigned bilingual documentation site, Agent and API tutorials, and benchmark result pages.',
        'Fix sqlite-vec row accumulation on Memory commits, binary cursor-key persistence on Windows, environment-file parsing, and DSH Scope error handling.',
        'Upgrade Server, clients, and integrations together. Unregistered legacy scope_id values now return scope_not_found; older OceanBase identity columns require utf8mb4_bin collation. Review data migration before upgrading an existing database.',
      ],
      zh: [
        '通过父子关系和显式上下文引用组织 Scope，复用 Agent 绑定，并在 Dashboard 和 Handoff Report 中查看单个 Scope、子树或全部 Scope。',
        '为 HTTP、MCP 和 Dashboard 数据统一执行访问控制，支持资源角色、可撤销的 Binding、审计记录，以及 Casbin 或 AuthZEN 适配器。',
        '审核和管理包含脚本及资源文件的完整 Skill 包，将指定 Revision 发布到本机 Codex、Claude Code，或通过远程 Receiver 分发。',
        '通过 powercontext service install/status/uninstall，使用 macOS、Linux 或 Windows 原生服务管理器运行个人 Server。',
        '通过按 Scope 划分的 REST 接口创建和读取 Source、管理 Artifact Revision，并使用 Scalar 交互式 API 参考。',
        '为生成、Embedding 和重排分别配置端点与模型参数，查看进程指标，并在 Agent Host 中获得集成诊断。',
        '使用重新设计的中英文文档网站、Agent 和 API 教程，以及 Benchmark 结果页面。',
        '修复 Memory 提交时 sqlite-vec 行累积、Windows 二进制游标密钥持久化、环境文件解析和 DSH Scope 错误处理问题。',
        'Server、客户端和集成需一起升级。未注册的旧 scope_id 现在返回 scope_not_found；旧 OceanBase 身份列需使用 utf8mb4_bin 排序规则。升级已有数据库前，请先确认数据迁移方案。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==0.2.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v0.2.0',
  },
  {
    version: 'v0.1.0',
    date: '2026-08-31',
    title: {
      en: 'A broader, safer context runtime',
      zh: '覆盖更广、更安全的上下文运行层',
    },
    summary: {
      en: 'This release expands PowerContext across Agent hosts and strengthens local and service deployments.',
      zh: '这个版本扩展了 PowerContext 对 Agent Host 的覆盖，并增强了本地与服务化部署能力。',
    },
    changes: {
      en: [
        'Connect Hermes Agent, OpenCode, Pi Coding Agent, OpenClaw, WorkBuddy, Pydantic AI, LangChain, and LangGraph through official integrations and diagnostics.',
        'Run scheduled processing with tracing, inspect scoped Handoff Reports, and configure PowerContext from the CLI.',
        'Use embedded seekdb or bundled sqlite-vec persistence with explicit embedding dimensions and clearer readiness failures.',
        'Apply safer transport defaults and secret-safe persistence diagnostics.',
      ],
      zh: [
        '通过正式集成和诊断能力连接 Hermes Agent、OpenCode、Pi Coding Agent、OpenClaw、WorkBuddy、Pydantic AI、LangChain 和 LangGraph。',
        '运行带 tracing 的定时处理任务，查看按 scope 划分的 Handoff Report，并通过 CLI 配置 PowerContext。',
        '使用嵌入式 seekdb 或内置 sqlite-vec 持久化，显式指定 embedding 维度，并获得更清晰的 readiness 故障信息。',
        '使用更安全的传输默认值，以及不泄露敏感信息的持久化诊断。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==0.1.0"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/powercontext-v0.1.0',
  },
  {
    version: 'v0.0.2',
    date: '2026-08-20',
    title: {
      en: 'Work continuity across Agents',
      zh: '跨 Agent 的工作连续性',
    },
    summary: {
      en: 'This release adds an evidence-backed workflow for carrying work across sessions and Agent hosts.',
      zh: '这个版本提供基于证据的工作流，用于在不同会话和 Agent Host 之间延续工作。',
    },
    changes: {
      en: [
        'Record a Work Contract, prepare a Handoff, acknowledge receipt, and preserve the Task Outcome.',
        'Use official integrations for Codex, Claude Code, DeepSeek Harness, and Hermes Agent.',
        'Inspect current work through the default Handoff Report, with period comparison and Markdown export.',
        'Handle concurrent Memory changes explicitly and trace bounded context-building stages.',
      ],
      zh: [
        '记录 Work Contract，准备 Handoff，确认接收状态，并保留 Task Outcome。',
        '使用 Codex、Claude Code、DeepSeek Harness 和 Hermes Agent 正式集成。',
        '通过默认启用的 Handoff Report 查看当前工作，并进行周期比较和 Markdown 导出。',
        '显式处理并发 Memory 变更，并追踪有界的上下文构建阶段。',
      ],
    },
    installCommand: 'uv tool install --force "powercontext[cli,server]==0.0.2"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/v0.0.2',
  },
  {
    version: 'v0.0.1',
    date: '2026-08-13',
    title: {
      en: 'First PowerContext release',
      zh: 'PowerContext 首个版本',
    },
    summary: {
      en: 'This release establishes durable, project-scoped context across Agent sessions.',
      zh: '这个版本提供跨 Agent 会话、按项目划分的持久上下文。',
    },
    changes: {
      en: [
        'Remember, search, revise, and retire Memory with citations and revision history.',
        'Connect Codex through the plugin, or use the CLI, Python client, HTTP, and MCP interfaces.',
        'Run the local Server with persistent SQLite storage and no required inference provider.',
      ],
      zh: [
        '通过引用与修订历史记录 Memory，并支持搜索、修订和停用。',
        '通过插件连接 Codex，也可以使用 CLI、Python 客户端、HTTP 和 MCP 接口。',
        '本地 Server 使用 SQLite 持久化存储，基础功能不依赖推理服务。',
      ],
    },
    installCommand: 'uv tool install "powercontext[cli,server]==0.0.1"',
    githubUrl: 'https://github.com/oceanbase/powercontext/releases/tag/v0.0.1',
  },
];
