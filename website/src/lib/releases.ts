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

export const releases: Release[] = [
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
        'Use embedded seekDB or bundled sqlite-vec persistence with explicit embedding dimensions and clearer readiness failures.',
        'Apply safer transport defaults and secret-safe persistence diagnostics.',
      ],
      zh: [
        '通过正式集成和诊断能力连接 Hermes Agent、OpenCode、Pi Coding Agent、OpenClaw、WorkBuddy、Pydantic AI、LangChain 和 LangGraph。',
        '运行带 tracing 的定时处理任务，查看按 scope 划分的 Handoff Report，并通过 CLI 配置 PowerContext。',
        '使用嵌入式 seekDB 或内置 sqlite-vec 持久化，显式指定 embedding 维度，并获得更清晰的 readiness 故障信息。',
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
