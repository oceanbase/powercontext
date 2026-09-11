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
